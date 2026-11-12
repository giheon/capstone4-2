"""Views of a VideoUnderstanding for the rest of the case: redaction targets, the text the agents read,
facts inferred from observation, and the identity confirmation question (design §7.2 · §5.7)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import write_atomic
from .domain import Fact, IdentityCandidate, IdentityQuestion, QuestionOption, Target, VideoAsset, VideoMeta, VideoUnderstanding, merge_ranges
from .geometry import Timeline, seen, visible_ranges
from .video import load_times

LOCATION_TARGET = "meta-location"  # the file's location tags as a redaction target

AGE_LABEL = {"child": "아동으로 보임", "teen": "청소년으로 보임", "adult": "성인으로 보임", "senior": "노년으로 보임"}
TEXT_KIND_LABEL = {"nametag": "명찰", "sign": "간판", "caption": "자막", "document": "문서", "screen": "화면", "other": "글자"}


def save(understanding: VideoUnderstanding, path: Path) -> None:
    """Replace the file rather than write into it: a forked case may share it with the case it came from."""
    write_atomic(path, understanding.model_dump_json())


def load(path: str | Path) -> VideoUnderstanding:
    return VideoUnderstanding.model_validate_json(Path(path).read_text(encoding="utf-8"))


def timeline(asset: VideoAsset, understanding: VideoUnderstanding) -> Timeline:
    """The original's frame times with this analysis' sampling interval and scene cuts."""
    return Timeline.build(load_times(asset), asset.meta.duration_s, understanding.sample_interval, [(s.start_frame, s.end_frame) for s in understanding.segments])


def with_coverage(understanding: VideoUnderstanding, asset: VideoAsset) -> VideoUnderstanding:
    """Fill in `visible` (which frames redacting each target covers). Call after the tracks or the grouping change."""
    understanding.visible = visible_ranges(understanding, timeline(asset, understanding))
    return understanding


def timecode(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def _ranges_text(ranges: list[tuple[float, float]]) -> str:
    return ", ".join(f"{timecode(a)}–{timecode(b)}" for a, b in ranges) or "구간 없음"


def person_number(person_id: str) -> str:
    return person_id.rsplit("-", 1)[-1]


def build_targets(understanding: VideoUnderstanding, meta: VideoMeta | None = None) -> list[Target]:
    """Everything a decision can act on: each person, each recognised text, each licence plate, and the file's
    location tags when it has any."""
    targets: list[Target] = []
    for person in understanding.persons:
        tracks = [t for t in understanding.tracks if t.id in set(person.face_tracks) | set(person.body_tracks)]
        attributes = person.attributes or {}
        details = [d for d in [AGE_LABEL.get(str(attributes.get("age_band", ""))), attributes.get("description_ko")] if d]
        if not person.face_tracks:
            details.append("얼굴은 검출되지 않음")
        cues = [*person.cues, *[str(c) for c in attributes.get("identifiability_cues") or []]]
        targets.append(
            Target(
                id=person.id,
                kind="person",
                label=f"인물 {person_number(person.id)}",
                detail=" · ".join(details) or "인물",
                ranges=merge_ranges([*seen(understanding, person.id, "face"), *seen(understanding, person.id, "body")]),
                cues=cues,
                tracks=[t.id for t in tracks],
                personal=True,
            )
        )
    for text in understanding.texts:
        tracks = [t for t in understanding.tracks if t.id in set(text.tracks)]
        attached = text.attached_person
        targets.append(
            Target(
                id=text.id,
                kind="text",
                label=f'{TEXT_KIND_LABEL.get(text.kind, "글자")} "{text.text}"',
                detail=(f"인물 {person_number(attached)}에 붙은 글자" if attached else "화면 속 글자") + f" · 인식 신뢰도 {text.confidence:.2f}",
                ranges=seen(understanding, text.id, "text"),
                cues=[],
                attached_to=attached,
                tracks=[t.id for t in tracks],
                personal=attached is not None or text.kind in ("nametag", "caption"),
            )
        )
    for plate in understanding.plates:
        tracks = [t for t in understanding.tracks if t.id in set(plate.tracks)]
        targets.append(
            Target(
                id=plate.id,
                kind="plate",
                label=f"번호판 {plate.id.rsplit('-', 1)[-1]}",
                detail="차량 번호판" + (f' "{plate.text}"' if plate.text else ""),
                ranges=seen(understanding, plate.id, "plate"),
                tracks=[t.id for t in tracks],
                personal=True,
            )
        )
    if meta is not None and meta.location_tags:
        targets.append(
            Target(
                id=LOCATION_TARGET,
                kind="metadata",
                label="촬영 위치 정보",
                detail=f"파일 메타데이터에 촬영 위치 좌표가 기록되어 있음 (태그 {len(meta.location_tags)}개). 화면에는 보이지 않지만 파일을 받은 사람은 읽을 수 있음",
                ranges=[],
                personal=True,
            )
        )
    return targets


def observation_text(understanding: VideoUnderstanding, targets: list[dict[str, Any]], duration: float) -> str:
    """The observation summary the agents read. Plain text, with the ids they must use."""
    lines = [f"[영상] 길이 {timecode(duration)} · 장면 {len(understanding.segments)}개 · 인물 {len(understanding.persons)}명 · 글자 {len(understanding.texts)}곳 · 번호판 {len(understanding.plates)}개"]
    lines.append("[장면]")
    for segment in understanding.segments:
        d = segment.description or {}
        if d:
            flags = ", ".join(d.get("sensitivity_flags") or []) or "없음"
            minors = {True: "있음", False: "없음"}.get(d.get("minors_present"), "판단 불가")
            lines.append(
                f"- {segment.id} {timecode(segment.start)}–{timecode(segment.end)} \"{segment.label or d.get('label_ko', '')}\": 장소 {d.get('place_type', '?')} · {d.get('setting', '')} · 활동: {d.get('activity', '')} · "
                f"사람 {d.get('people_count', '?')}명 · 미성년자 {minors} · 민감 맥락 {flags} · 카메라 인지 {d.get('camera_awareness', 'unknown')} · 글자: {d.get('visible_text_summary') or '없음'} (신뢰도 {float(d.get('confidence') or 0):.2f})"
            )
        else:
            lines.append(f"- {segment.id} {timecode(segment.start)}–{timecode(segment.end)}: 장면 설명 없음")
    lines.append("[대상] (요건과 질문에는 아래 ID를 쓴다)")
    for target in targets:
        cues = f" · 식별 단서: {', '.join(target['cues'])}" if target.get("cues") else ""
        attached = f" · {target['attachedTo']}에 부착" if target.get("attachedTo") else ""
        where = "파일 전체에 적용 (화면에 보이지 않음)" if target["kind"] == "metadata" else f"보이는 구간 {_ranges_text(target['ranges'])}"
        lines.append(f"- {target['id']} ({target['label']}): {target['detail']}{attached} · {where}{cues}")
    if not targets:
        lines.append("- 검출된 대상이 없습니다.")
    return "\n".join(lines)


def inferred_facts(understanding: VideoUnderstanding) -> list[Fact]:
    """Facts the observation supports with some confidence. They are marked inferred so the user can correct them."""
    facts: list[Fact] = []
    described = [s for s in understanding.segments if s.description]
    if described:
        main = max(described, key=lambda s: s.end - s.start)
        place = str(main.description.get("place_type") or "")
        if place and place != "other":
            facts.append(
                Fact(key="scene.place_type", label="촬영 장소", value=place, value_label=str(main.description.get("setting") or place), provenance="inferred", confidence=float(main.description.get("confidence") or 0.5), source=f"장면 설명 ({main.id})", editable=True)
            )
        minors = [s.description.get("minors_present") for s in described]
        if any(m is True for m in minors) or all(m is False for m in minors):
            present = any(m is True for m in minors)
            facts.append(
                Fact(key="scene.minors_present", label="미성년자 포함", value="yes" if present else "no", value_label="미성년자로 보이는 사람이 있음" if present else "미성년자로 보이는 사람이 없음", provenance="inferred", confidence=0.7, source="장면 설명", editable=True)
            )
    return facts


def file_facts(meta: VideoMeta) -> list[Fact]:
    """Facts read from the file itself rather than from the picture."""
    if not meta.location_tags:
        return []
    return [Fact(key="file.location_metadata", label="촬영 위치 메타데이터", value="present", value_label="파일에 촬영 위치 좌표가 기록되어 있음", provenance="inferred", confidence=1.0, source="파일 메타데이터", editable=False)]


def identity_question(understanding: VideoUnderstanding) -> IdentityQuestion | None:
    """Ask about the first pair of person groups the grouping step could not decide (one question at a time)."""
    if not understanding.ambiguities:
        return None
    item = understanding.ambiguities[0]
    number_a, number_b = person_number(item.a), person_number(item.b)
    return IdentityQuestion(
        id=item.id,
        prompt=f"{timecode(item.at_a)}의 인물 {number_a}과(와) {timecode(item.at_b)}의 인물 {number_b}은(는) 같은 사람인가요?",
        detail=f"얼굴이 비슷하지만 확신할 수 없습니다 (유사도 {item.similarity:.2f}). 같은 사람이면 한 사람으로 묶어 같은 처리를 적용합니다.",
        candidates=[
            IdentityCandidate(label=f"인물 {number_a}", at=round(item.at_a, 2), target=item.a, note="첫 번째 후보"),
            IdentityCandidate(label=f"인물 {number_b}", at=round(item.at_b, 2), target=item.b, note="두 번째 후보"),
        ],
        options=[
            QuestionOption(value="same", label="같은 사람", detail="한 사람으로 묶습니다"),
            QuestionOption(value="different", label="다른 사람", detail="따로 판단합니다"),
            QuestionOption(value="unknown", label="모르겠음", detail="보수적으로 같은 사람처럼 처리합니다"),
        ],
    )


def perception_summary(understanding: VideoUnderstanding) -> dict[str, int]:
    return {"scenes": len(understanding.segments), "people": len(understanding.persons), "plates": len(understanding.plates), "texts": len(understanding.texts), "tracks": len(understanding.tracks)}

