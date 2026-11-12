"""Vision-language calls: scene description, person attributes and on-demand visual inspection (design §7.3 · §7.4).

Input privacy follows the profile's `vlm.input_mode`:
- v2 (default): every tracked face, and every face a low-threshold detector still finds, is blurred before a
  frame leaves this machine.
- v1: frames are sent as they are (consented footage and zero-retention endpoints only).
- v3: nothing may leave the machine, so an API model is refused.

Frames are sent outside LangChain so that images never reach LangSmith traces (design §11.4); only token
counts and the parsed answer are reported. Two routes, by the candidate's adapter: OpenRouter (plain HTTP) and
the Anthropic API (its SDK, with the answer constrained to a JSON schema).
"""
from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import numpy as np

from .domain import Box, PersonGroup, SegmentInfo, Track, VideoAsset
from .llm import CLAUDE_MAX_TOKENS, MissingCredential, anthropic_key, provider_preferences, refusal_fallback
from .runtime import ModelCallError, Services, activity
from .settings import secret
from .video import encode_jpeg, read_frame

OPENROUTER_CHAT = "https://openrouter.ai/api/v1/chat/completions"
FaceFinder = Callable[[np.ndarray], list[Box]]  # extra faces on a proxy-sized image, in that image's pixels

SCENE_PROMPT = """이 이미지들은 한 영상의 한 장면에서 시간 순서로 뽑은 프레임입니다{anonymized}.
장면을 관찰해 아래 JSON 하나만 답하세요. 보이는 것만 적고 추측은 confidence를 낮추세요.
{{
  "label_ko": "장면을 부르는 짧은 이름 (12자 이내, 예: 카페 인터뷰)",
  "place_type": "cafe|restaurant|store|office|school|hospital|street|park|transport|vehicle|home|restroom|event|outdoor|other 중 하나",
  "setting": "장소와 상황 한 문장",
  "activity": "사람들이 하는 일 한 문장",
  "people_count": 보이는 사람 수(정수),
  "minors_present": true|false|null(판단 불가),
  "sensitivity_flags": ["건강", "종교", "정치", "노동조합", "범죄", "성적 맥락", "사적 공간" 중 해당하는 것만],
  "camera_awareness": "aware|unaware|mixed|unknown",
  "visible_text_summary": "화면에 보이는 글자(간판·명찰·자막·번호판)의 요약, 없으면 빈 문자열",
  "summary_ko": "장면 전체 두세 문장",
  "confidence": 0과 1 사이 숫자
}}"""

PERSON_PROMPT = """이 이미지들은 영상 속 같은 사람 한 명을 잘라 낸 것입니다{anonymized}.
아래 JSON 하나만 답하세요. 신원을 추측하지 말고 보이는 특징만 적으세요.
{{
  "age_band": "child|teen|adult|senior|unknown",
  "role_cues": ["역할을 짐작하게 하는 단서 (예: 유니폼, 마이크, 명찰, 계산대 뒤)"],
  "identifiability_cues": ["얼굴 외에 이 사람을 알아보게 할 수 있는 특징 (예: 문신, 독특한 복장, 소지품)"],
  "description_ko": "외형과 행동 한 문장",
  "confidence": 0과 1 사이 숫자
}}"""

INSPECT_PROMPT = """영상의 일부 프레임입니다{anonymized}. 질문에 보이는 것만으로 답하세요.
질문: {question}
아래 JSON 하나만 답하세요.
{{"answer": "한두 문장의 답", "verdict": "yes|no|unclear", "confidence": 0과 1 사이 숫자}}"""


def blur_boxes(image: np.ndarray, boxes: list[Box], scale: float = 1.5) -> np.ndarray:
    """Heavily blur boxes (pixels of `image`) so a face cannot be recognised; returns a copy."""
    import cv2

    out = image.copy()
    height, width = out.shape[:2]
    for x, y, w, h in boxes:
        cx, cy = x + w / 2, y + h / 2
        w2, h2 = w * scale, h * scale
        x0, y0 = int(max(0, cx - w2 / 2)), int(max(0, cy - h2 / 2))
        x1, y1 = int(min(width, cx + w2 / 2)), int(min(height, cy + h2 / 2))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        region = out[y0:y1, x0:x1]
        small = cv2.resize(region, (max(1, (x1 - x0) // 12), max(1, (y1 - y0) // 12)), interpolation=cv2.INTER_AREA)
        out[y0:y1, x0:x1] = cv2.GaussianBlur(cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR), (0, 0), 6)
    return out


def box_at(track: Track, frame: int, tolerance: int = 6) -> Box | None:
    """The track's box at the sample nearest to `frame`, if one is close enough."""
    best = min(track.points, key=lambda p: abs(p.f - frame), default=None)
    return best.box if best is not None and abs(best.f - frame) <= tolerance else None


class Vision:
    """VLM access for one case: knows the video, the face tracks to anonymise, and the profile's privacy mode."""

    def __init__(self, services: Services, asset: VideoAsset, tracks: list[Track], face_finder: FaceFinder | None = None):
        self.services = services
        self.asset = asset
        self.faces = [t for t in tracks if t.kind == "face"]
        self.face_finder = face_finder
        self.mode = services.part("vlm.input_mode").candidate.id

    # ---------- frames ----------

    def frame(self, index: int, crop: Box | None = None) -> np.ndarray:
        """A proxy frame prepared for sending: faces blurred unless the mode is v1; optionally cropped
        (crop in original display pixels)."""
        image = read_frame(self.asset, index, "proxy")
        scale = self.asset.proxy.scale
        if self.mode != "v1":
            boxes = [tuple(v * scale for v in box) for track in self.faces if (box := box_at(track, index)) is not None]
            if self.face_finder is not None:
                boxes += self.face_finder(image)
            image = blur_boxes(image, boxes)  # type: ignore[arg-type]
        if crop is not None:
            x, y, w, h = (v * scale for v in crop)
            pad_x, pad_y = w * 0.15, h * 0.15
            x0, y0 = int(max(0, x - pad_x)), int(max(0, y - pad_y))
            x1, y1 = int(min(image.shape[1], x + w + pad_x)), int(min(image.shape[0], y + h + pad_y))
            if x1 - x0 >= 8 and y1 - y0 >= 8:
                image = image[y0:y1, x0:x1]
        return image

    def _note(self) -> str:
        return "" if self.mode == "v1" else " (사람 얼굴은 개인정보 보호를 위해 흐리게 처리되어 있습니다)"

    # ---------- calls ----------

    def describe_segment(self, segment: SegmentInfo) -> dict[str, Any]:
        """A scene description, or {} when the model could not give one: the analyst can still look at the
        scene itself with inspect_visual, so one failed description must not end the case."""
        if self.services.is_fixture("vlm.scene"):
            return self.services.fixture.scene(segment)
        images = [self.frame(index) for index in segment.keyframes[:8]]
        try:
            return self._ask("vlm.scene", SCENE_PROMPT.format(anonymized=self._note()), images, node="perceive/describe_segments", agent="perceive")
        except ModelCallError as error:
            activity(f"장면 {segment.id}의 설명을 받지 못했습니다. 설명 없이 진행합니다. ({error})", "warn")
            return {}

    def describe_person(self, person: PersonGroup, tracks: list[Track]) -> dict[str, Any]:
        if self.services.is_fixture("vlm.person"):
            return self.services.fixture.person(person)
        wanted = set(person.body_tracks or person.face_tracks)
        own = [t for t in tracks if t.id in wanted]
        points = sorted((p for t in own for p in t.points), key=lambda p: -(p.box[2] * p.box[3]))
        picks = points[:1] + points[len(points) // 2 : len(points) // 2 + 1] if len(points) > 1 else points
        images = []
        for point in picks[:2]:
            crop = point.box if person.body_tracks else _around_face(point.box, self.asset)
            images.append(self.frame(point.f, crop))
        if not images:
            return {}
        try:
            return self._ask("vlm.person", PERSON_PROMPT.format(anonymized=self._note()), images, node="perceive/describe_persons", agent="perceive")
        except ModelCallError as error:
            activity(f"{person.id}의 속성 설명을 받지 못했습니다. 설명 없이 진행합니다. ({error})", "warn")
            return {}

    def inspect(self, question: str, *, frames: list[tuple[int, Box | None]], node: str, agent: str) -> dict[str, Any]:
        if self.services.is_fixture("vlm.inspect"):
            return self.services.fixture.inspect(question)
        images = [self.frame(index, crop) for index, crop in frames[:4]]
        if not images:
            return {"answer": "볼 수 있는 프레임이 없습니다.", "verdict": "unclear", "confidence": 0.0}
        return self._ask("vlm.inspect", INSPECT_PROMPT.format(anonymized=self._note(), question=question), images, node=node, agent=agent)

    def _ask(self, slot: str, prompt: str, images: list[np.ndarray], *, node: str, agent: str) -> dict[str, Any]:
        services = self.services
        part = services.part(slot)
        if self.mode == "v3" and part.kind == "api":
            raise ModelCallError(f"'이 컴퓨터에서만 처리' 방식(V-3)인데 {part.slot.label}이(가) 외부 모델({part.candidate.label})입니다.")
        route = {"openrouter": self._openrouter, "anthropic": self._claude}.get(part.candidate.adapter)
        if route is None:
            raise ModelCallError(f"{part.slot.label}의 '{part.candidate.label}'({part.candidate.adapter}) 어댑터는 아직 구현되지 않았습니다.")
        services.check_budget()
        encoded = [base64.b64encode(encode_jpeg(image, quality=80, max_side=768)).decode() for image in images]
        last_error = ""
        for attempt in range(3):
            started = time.perf_counter()
            try:
                reply = route(part.candidate.ref or "", slot, prompt, encoded, attempt)
            except _Retry as error:
                last_error = str(error)
                continue
            services.charge(node, agent, slot, reply.tokens_in, reply.tokens_out, ms=(time.perf_counter() - started) * 1000, usd=reply.usd)
            try:
                start, end = reply.text.find("{"), reply.text.rfind("}")
                parsed = json.loads(reply.text[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except Exception:  # noqa: BLE001
                pass
            last_error = f"JSON으로 읽을 수 없는 응답 (finish_reason={reply.finish}, {len(reply.text)}자)"
        raise ModelCallError(f"{part.candidate.label} 호출 실패 ({attempt + 1}회 시도): {last_error}")

    def _openrouter(self, model: str, slot: str, prompt: str, images: list[str], attempt: int) -> _Reply:
        key = secret(self.services.settings.openrouter_api_key)
        if not key:
            raise ModelCallError("OPENROUTER_API_KEY가 없습니다. 저장소 루트의 .env에 넣으세요.")
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}} for data in images]
        body = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "provider": provider_preferences(self.services.settings),
            # a reasoning VLM can spend a small budget on thinking and return nothing (M3 smoke): low effort, and
            # more room once an attempt ran out of it before the answer
            "reasoning": {"effort": "low"},
            "max_tokens": 1500 if attempt == 0 else 3000,
            "usage": {"include": True},
        }
        try:
            response = httpx.post(OPENROUTER_CHAT, json=body, headers={"Authorization": f"Bearer {key}", "X-Title": "PrivFrame"}, timeout=120)
            data = response.json()
        except Exception as error:  # noqa: BLE001
            raise _Retry(f"{type(error).__name__}: {error}") from error
        if response.status_code != 200 or data.get("error"):
            raise _Retry(str((data.get("error") or {}).get("message") or response.status_code))
        usage = data.get("usage") or {}
        choice = (data.get("choices") or [{}])[0]
        return _Reply((choice.get("message") or {}).get("content") or "", str(choice.get("finish_reason")), int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), usage.get("cost"))

    def _claude(self, model: str, slot: str, prompt: str, images: list[str], attempt: int) -> _Reply:
        import anthropic

        try:
            client = anthropic.Anthropic(api_key=anthropic_key(self.services.settings), timeout=120.0, max_retries=2)
        except MissingCredential as error:
            raise ModelCallError(str(error)) from error
        content: list[dict[str, Any]] = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}} for data in images]
        content.append({"type": "text", "text": prompt})
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": CLAUDE_MAX_TOKENS,
            "messages": [{"role": "user", "content": content}],
            # describing what is visible needs little deliberation; the schema makes the answer valid JSON
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": ANSWER_SCHEMAS[slot]}},
        }
        extras = refusal_fallback(model)
        try:
            # the refusal fallback is a beta field, so requests that carry it go to the beta endpoint
            response = client.beta.messages.create(**request, **extras) if extras else client.messages.create(**request)
        except (anthropic.BadRequestError, anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as error:
            raise ModelCallError(f"Anthropic API가 요청을 받지 않았습니다: {error.message}") from error
        except anthropic.RateLimitError as error:
            raise _Retry(f"요청 한도 초과: {error.message}") from error
        except anthropic.APIStatusError as error:
            raise _Retry(f"Anthropic API 오류 {error.status_code}: {error.message}") from error
        except anthropic.APIConnectionError as error:
            raise _Retry(f"Anthropic API에 연결하지 못했습니다: {error}") from error
        if response.stop_reason == "refusal":
            raise ModelCallError("모델이 이 프레임에 대한 응답을 거절했습니다.")
        text = next((block.text for block in response.content if block.type == "text"), "")
        usage = response.usage
        tokens_in = (usage.input_tokens or 0) + (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
        return _Reply(text, str(response.stop_reason), tokens_in, usage.output_tokens or 0, None)


class _Retry(Exception):
    """One attempt failed in a way another attempt may not."""


class _Reply:
    __slots__ = ("text", "finish", "tokens_in", "tokens_out", "usd")

    def __init__(self, text: str, finish: str, tokens_in: int, tokens_out: int, usd: float | None):
        self.text, self.finish, self.tokens_in, self.tokens_out, self.usd = text, finish, tokens_in, tokens_out, usd


def _object(**properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_TEXT, _NUMBER = {"type": "string"}, {"type": "number"}
_TEXTS = {"type": "array", "items": {"type": "string"}}
# The answers the three prompts above ask for, as JSON schemas for routes that can enforce one.
ANSWER_SCHEMAS: dict[str, dict[str, Any]] = {
    "vlm.scene": _object(
        label_ko=_TEXT,
        place_type={"type": "string", "enum": ["cafe", "restaurant", "store", "office", "school", "hospital", "street", "park", "transport", "vehicle", "home", "restroom", "event", "outdoor", "other"]},
        setting=_TEXT,
        activity=_TEXT,
        people_count={"type": "integer"},
        minors_present={"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        sensitivity_flags=_TEXTS,
        camera_awareness={"type": "string", "enum": ["aware", "unaware", "mixed", "unknown"]},
        visible_text_summary=_TEXT,
        summary_ko=_TEXT,
        confidence=_NUMBER,
    ),
    "vlm.person": _object(
        age_band={"type": "string", "enum": ["child", "teen", "adult", "senior", "unknown"]},
        role_cues=_TEXTS,
        identifiability_cues=_TEXTS,
        description_ko=_TEXT,
        confidence=_NUMBER,
    ),
    "vlm.inspect": _object(answer=_TEXT, verdict={"type": "string", "enum": ["yes", "no", "unclear"]}, confidence=_NUMBER),
}


def _around_face(face: Box, asset: VideoAsset) -> Box:
    """An upper-body area guessed from a face box, for people without a body track."""
    x, y, w, h = face
    x0, y0 = max(0.0, x - w * 1.2), max(0.0, y - h * 0.6)
    x1, y1 = min(float(asset.meta.display_width), x + w * 2.2), min(float(asset.meta.display_height), y + h * 4.0)
    return (x0, y0, x1 - x0, y1 - y0)
