"""Pipeline catalog, profiles and the service pointer as the Lab API serves them (ADR-0009, design §10.8).

Python port of frontend/mock/pipeline.ts. Reading goes through privframe.core.pipeline, so the Lab and the
pipeline see the same catalog; this module adds the wire shapes of frontend/src/api/contract.ts, the profile
checks and the writes. The YAML files are hand-edited: a write is a text edit at positions the YAML parser
reports, so only the lines that have to change do and every other byte of the file stays as it was.
"""
import json
import math
import os
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml

from privframe.core import pipeline as core
from privframe.core.pipeline import FRAME_SLOTS
from privframe.core.settings import ROOT
from privframe.service.errors import ServiceError

ServiceTarget = Literal["default", "local_only"]
SERVICE_TARGETS = ("default", "local_only")
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{1,47}")
CAPS = ("tools", "json", "image", "video")
CAP_LABEL = {"tools": "도구 호출", "json": "엄격한 JSON 출력", "image": "이미지 입력", "video": "영상 입력"}
TIERS = ("frontier", "efficient")
REVIEW_LICENSE = re.compile(r"AGPL|비상용|non-commercial", re.IGNORECASE)
PROFILE_HEADER = "# 프로필: catalog.yaml의 부품마다 후보 하나를 고른 조합. 실험실 > 프로필에서 만들고 고칠 수 있다."
BROKEN_CATALOG = "카탈로그 구조를 읽을 수 없습니다."
READ_ERRORS = (OSError, yaml.YAMLError, LookupError, TypeError, ValueError, AttributeError)


class PipelineError(ServiceError):
    """A refused request with the HTTP status the Lab API answers: 400, 404, 409, or 500 for a broken file."""


class PipelineStore:
    """configs/pipeline as the Lab reads and edits it; public methods return contract.ts (camelCase) dicts."""

    def __init__(self, directory: Path, display: str | None = None):
        self.dir = Path(directory)
        self.catalog_file = self.dir / "catalog.yaml"
        self.profiles_dir = self.dir / "profiles"
        self.service_file = self.dir / "service.yaml"
        self._display = _repo_relative(self.dir) if display is None else display  # what the Lab shows
        self._cache: dict[Path, tuple[tuple[int, int, int], Any]] = {}
        self._lock = threading.RLock()  # one writer at a time; readers only ever see whole files
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def display(self, file: Path) -> str:
        return str(PurePosixPath(self._display) / file.relative_to(self.dir).as_posix())

    def _cached(self, file: Path, load: Callable[[], Any]) -> tuple[Any, int]:
        """The parsed file and its mtime (ns); parsed again only when the file changed on disk."""
        stat = file.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        hit = self._cache.get(file)
        if hit is None or hit[0] != stamp:
            hit = self._cache[file] = (stamp, load())
        return hit[1], stamp[0]

    def _write(self, file: Path, text: str) -> None:
        tmp = file.with_name(f"{file.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, file)  # atomic: a concurrent reader gets the old or the new file, never half of one
        self._cache.pop(file, None)

    # ---------- catalog ----------

    def _catalog(self) -> tuple[core.Catalog, int]:
        try:
            return self._cached(self.catalog_file, lambda: core.load_catalog(self.dir))
        except READ_ERRORS as error:
            message = f"카탈로그 파일을 읽을 수 없습니다 ({self.display(self.catalog_file)}): {error}"
            raise PipelineError(500, message) from error

    def _slot(self, slot_id: Any) -> core.Slot:
        slot = next((s for s in self._catalog()[0].slots if s.id == slot_id), None)
        if slot is None:
            raise PipelineError(404, f"카탈로그에 '{slot_id}' 부품이 없습니다.")
        return slot

    def catalog(self) -> dict:
        catalog, mtime = self._catalog()
        stages = [
            {"id": st.id, "label": st.label, "description": st.description,
             "slots": [_slot_wire(s) for s in catalog.slots if s.stage == st.id]}
            for st in catalog.stages
        ]
        params = [
            {"id": p.id, "stage": p.stage, "label": p.label, "unit": p.unit,
             "default": p.default, "min": p.min, "max": p.max, "step": p.step}
            for p in catalog.params
        ]
        adapters = [
            _compact(id=a.id, label=a.label, kind=a.kind, ref=a.ref, example=a.example,
                     lookup="openrouter" if a.lookup == "openrouter" else None, addable=a.addable)
            for a in catalog.adapters.values()
        ]
        return {"path": self.display(self.catalog_file), "modifiedAt": _iso(mtime),
                "adapters": adapters, "stages": stages, "params": params}

    def slots(self) -> list[dict]:
        return [_slot_wire(s) for s in self._catalog()[0].slots]

    def slot(self, slot_id: str) -> dict:
        return _slot_wire(self._slot(slot_id))

    def add_candidate(self, request: dict) -> dict:
        """Append a model of an already registered adapter family to a part's candidates (source: lab)."""
        with self._lock:
            slot = self._slot(request.get("slot"))
            wanted = request.get("adapter")
            adapter = self._catalog()[0].adapters.get(wanted) if isinstance(wanted, str) else None
            if adapter is None or adapter.id not in slot.adapters:
                raise PipelineError(400, f"'{slot.label}'에는 '{wanted}' 계열을 쓸 수 없습니다.")
            if not adapter.addable:
                raise PipelineError(400, f"'{adapter.label}' 후보는 코드에서만 추가할 수 있습니다.")
            ref = _text(request.get("ref")).strip()
            label = _text(request.get("label")).strip() or ref
            if not ref:
                raise PipelineError(400, "모델 ID(ref)를 입력하세요.")
            if any(c.ref == ref for c in slot.candidates):
                raise PipelineError(409, f"'{ref}'는 이미 '{slot.label}' 후보에 있습니다.")
            candidate_id = _slugify(ref)
            if not ID_PATTERN.fullmatch(candidate_id):
                raise PipelineError(400, "모델 ID로 후보 키를 만들 수 없습니다. 영문·숫자로 된 ID를 쓰세요.")
            if slot.candidate(candidate_id):
                raise PipelineError(409, f"'{candidate_id}' 키를 가진 후보가 이미 있습니다.")
            caps = _caps(request.get("caps") or [])
            missing = [c for c in _caps(slot.requires) if c not in caps]
            if missing:
                needed = ", ".join(CAP_LABEL[c] for c in missing)
                raise PipelineError(400, f"'{slot.label}'에는 {needed} 기능이 필요합니다.")
            price = _request_price(request.get("price"))
            if adapter.kind == "api" and price is None:
                raise PipelineError(400, "외부 API 모델은 단가가 필요합니다 (USD / 1M 토큰).")

            entry = [("id", _scalar(candidate_id)), ("label", _scalar(label)),
                     ("adapter", _scalar(adapter.id)), ("ref", _scalar(ref))]
            if price:
                entry.append(("price", f"{{in: {_js_number(price['in'])}, out: {_js_number(price['out'])}}}"))
            if caps:
                entry.append(("caps", f"[{', '.join(caps)}]"))
            if request.get("tier") in TIERS:
                entry.append(("tier", request["tier"]))
            for key in ("license", "note"):
                if value := _text(request.get(key)).strip():
                    entry.append((key, _scalar(value)))
            entry += [("source", "lab"), ("added", datetime.now(UTC).date().isoformat())]

            text = _read(self.catalog_file)
            updated, index = _insert_candidate(text, slot.id, entry)
            if not _differ_by_one_candidate(text, updated, slot.id, index):
                raise PipelineError(500, BROKEN_CATALOG)
            self._write(self.catalog_file, updated)
            return next(c for c in self.slot(slot.id)["candidates"] if c["id"] == candidate_id)

    def remove_candidate(self, slot_id: str, candidate_id: str) -> None:
        with self._lock:
            slot = self._slot(slot_id)
            candidate = slot.candidate(candidate_id)
            if candidate is None:
                raise PipelineError(404, "후보를 찾을 수 없습니다.")
            if candidate.source != "lab":
                raise PipelineError(409, "파일에 직접 적은 후보는 catalog.yaml에서 지우세요.")
            if slot.default == candidate_id:
                raise PipelineError(409, "부품의 기본 후보는 지울 수 없습니다.")
            users = [p.id for _, p, _ in self._profiles() if p.selections.get(slot_id) == candidate_id]
            if users:
                raise PipelineError(409, f"프로필 {', '.join(users)}이(가) 이 후보를 쓰고 있습니다.")
            text = _read(self.catalog_file)
            updated, index = _delete_candidate(text, slot.id, candidate_id)
            if not _differ_by_one_candidate(updated, text, slot.id, index):
                raise PipelineError(500, BROKEN_CATALOG)
            self._write(self.catalog_file, updated)

    # ---------- profiles ----------

    def _profiles(self) -> list[tuple[Path, core.ProfileSpec, int]]:
        found = []
        for stem in sorted(core.list_profiles(self.dir), key=lambda s: f"{s}.yaml"):  # file-name order
            file = self.profiles_dir / f"{stem}.yaml"
            try:
                spec, mtime = self._cached(file, lambda: core.load_profile(self.dir, stem))
            except READ_ERRORS:
                continue  # an unreadable profile file is skipped rather than breaking the whole Lab
            found.append((file, spec, mtime))
        return found

    def _profile(self, profile_id: str) -> tuple[Path, core.ProfileSpec, int]:
        found = next((p for p in self._profiles() if p[1].id == profile_id), None)
        if found is None:
            raise PipelineError(404, f"프로필 '{profile_id}'을(를) 찾을 수 없습니다.")
        return found

    def _stored(self, file: Path, spec: core.ProfileSpec, mtime: int) -> dict:
        wire = _compact(id=spec.id, name=spec.name, description=spec.description,
                        base=_text(spec.base) if spec.base else None,
                        selections=dict(spec.selections), params=dict(spec.params))
        return {"spec": wire, "path": self.display(file), "source": spec.source, "updatedAt": _iso(mtime)}

    def list_profiles(self) -> list[dict]:
        return [self._stored(*found) for found in self._profiles()]

    def get_profile(self, profile_id: str) -> dict:
        return self._stored(*self._profile(profile_id))

    def save_profile(self, spec: dict, mode: Literal["create", "update"]) -> dict:
        with self._lock:
            profile_id = _text(spec.get("id")).strip()
            if not ID_PATTERN.fullmatch(profile_id):
                raise PipelineError(400, "프로필 ID는 영문 소문자·숫자·'-'로 2~48자여야 합니다.")
            file = self.profiles_dir / f"{profile_id}.yaml"
            exists = file.exists()
            if mode == "create" and exists:
                raise PipelineError(409, f"'{profile_id}' 프로필이 이미 있습니다.")
            if mode == "update" and not exists:
                raise PipelineError(404, f"프로필 '{profile_id}'을(를) 찾을 수 없습니다.")
            previous = self._profile(profile_id)[1] if exists else None
            normalized = self._normalize({**spec, "id": profile_id})
            errors = [issue["text"] for issue in self.issues(normalized) if issue["level"] == "error"]
            if errors:
                raise PipelineError(400, " / ".join(errors))
            edited = _edit_profile(_read(file), previous, normalized) if previous else None
            source = previous.source if previous else "lab"
            self._write(file, edited or self._render_profile(normalized, source))
            return self.get_profile(profile_id)

    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            file, spec, _ = self._profile(profile_id)
            if spec.id in self.service().values():
                raise PipelineError(409, "서비스가 쓰고 있는 프로필은 지울 수 없습니다. 다른 프로필을 먼저 승격하세요.")
            file.unlink()
            self._cache.pop(file, None)

    def _normalize(self, spec: dict) -> dict:
        """Every part and parameter of the catalog gets a value: the given one or the catalog default."""
        catalog = self._catalog()[0]
        given, numbers = spec.get("selections") or {}, spec.get("params") or {}
        params = {}
        for p in catalog.params:
            value = _number(numbers[p.id]) if p.id in numbers else None
            params[p.id] = p.default if value is None else value
        return _compact(
            id=spec["id"],
            name=_text(spec.get("name")).strip() or spec["id"],
            description=_text(spec.get("description")).strip(),
            base=(_text(spec["base"]) or None) if spec.get("base") else None,
            selections={s.id: _text(given.get(s.id), s.default) for s in catalog.slots},
            params=params,
        )

    def _render_profile(self, spec: dict, source: str) -> str:
        """A profile file in the layout of the hand-written ones: parts by stage, then the parameters."""
        catalog = self._catalog()[0]
        lines = [PROFILE_HEADER] + [f"{key}: {_scalar(spec[key])}" for key in ("id", "name", "description")]
        if spec.get("base"):
            lines.append(f"base: {_scalar(spec['base'])}")
        lines += [f"source: {source}", "selections:"]
        for stage in catalog.stages:
            lines.append(f"  # {stage.label}")
            slots = [s for s in catalog.slots if s.stage == stage.id]
            lines += [f"  {s.id}: {_scalar(spec['selections'][s.id])}" for s in slots]
        lines.append("params:")
        lines += [f"  {p.id}: {_scalar(spec['params'][p.id])}" for p in catalog.params]
        return "\n".join(lines) + "\n"

    # ---------- service pointer ----------

    def service(self) -> dict[str, str]:
        try:
            return dict(self._cached(self.service_file, lambda: core.service_profiles(self.dir))[0])
        except READ_ERRORS as error:
            message = f"서비스 설정 파일을 읽을 수 없습니다 ({self.display(self.service_file)}): {error}"
            raise PipelineError(500, message) from error

    def promote(self, profile_id: str, target: ServiceTarget) -> None:
        """Point the service at a profile: one value of service.yaml changes."""
        with self._lock:
            self._profile(profile_id)
            if target not in SERVICE_TARGETS:
                raise PipelineError(400, f"'{target}'은(는) 서비스 적용 대상이 아닙니다.")
            text = _read(self.service_file) if self.service_file.exists() else ""
            updated = _set_top_level(text, target, _scalar(profile_id))
            try:
                intact = yaml.safe_load(updated) == {**(yaml.safe_load(text) or {}), target: profile_id}
            except READ_ERRORS:
                intact = False
            if not intact:
                raise PipelineError(500, f"서비스 설정 파일을 고칠 수 없습니다 ({self.display(self.service_file)}).")
            self._write(self.service_file, updated)

    # ---------- resolution and checks ----------

    def _spec(self, spec: dict) -> core.ProfileSpec:
        selections = {k: v for k, v in (spec.get("selections") or {}).items() if v is not None}
        params = {k: n for k, v in (spec.get("params") or {}).items() if (n := _number(v)) is not None}
        return core.ProfileSpec(id=_text(spec.get("id")), name=_text(spec.get("name")),
                                selections=selections, params=params)

    def resolve(self, spec: dict, overrides: dict | None = None) -> dict:
        """The candidate each part runs with (override → profile → catalog default) and the config hash."""
        resolved = core.resolve(self._catalog()[0], self._spec(spec), overrides)
        part = {slot_id: _part_wire(p) for slot_id, p in resolved.parts.items()}
        components = [{k: v for k, v in p.items() if k in COMPONENT_KEYS} for p in part.values()]
        return {"id": spec.get("id"), "name": spec.get("name"), "configHash": resolved.config_hash,
                "components": components, "part": part, "params": dict(resolved.params)}

    def resolved(self, spec: dict) -> core.ResolvedProfile:
        """The profile a spec (stored, or a draft from the editor) resolves to, as the pipeline runs it."""
        return core.resolve(self._catalog()[0], self._spec(spec))

    def frames_leave_device(self, spec: dict) -> bool:
        """Some video frame or crop goes to an external API (a FRAME_SLOTS part runs on an api adapter)."""
        return self.resolved(spec).frames_leave_device

    def api_local_counts(self, spec: dict) -> dict[str, int]:
        """How many parts call an external API and how many run on this machine (ProfileEstimate)."""
        kinds = [c["kind"] for c in self.resolve(spec)["components"]]
        return {"api": kinds.count("api"), "local": kinds.count("local")}

    def issues(self, spec: dict) -> list[dict]:
        """What the Lab shows before saving: errors block saving and running, warnings and notes do not."""
        catalog = self._catalog()[0]
        selections, numbers = spec.get("selections") or {}, spec.get("params") or {}
        part = self.resolve(spec)["part"]
        issues: list[dict] = []

        def add(level: str, slot: str | None, text: str) -> None:
            issues.append(_compact(level=level, slot=slot, text=text))

        def chosen(slot_id: str, key: str = "candidate") -> Any:
            return part.get(slot_id, {}).get(key)

        for slot in catalog.slots:
            if slot.id not in selections:
                add("info", slot.id, f"카탈로그에 새로 생긴 부품 '{slot.label}' — 기본 후보를 씁니다.")
                continue
            wanted = selections[slot.id]
            c = slot.candidate(wanted) if isinstance(wanted, str) else None
            if c is None:
                shown = wanted if isinstance(wanted, str) else json.dumps(wanted, ensure_ascii=False)
                add("error", slot.id, f"'{slot.label}'의 후보 '{shown}'이(가) 카탈로그에 없습니다.")
                continue
            if c.planned:
                add("error", slot.id, f"'{slot.label}'의 '{c.label}'은(는) 아직 구현되지 않았습니다.")
            missing = [cap for cap in _caps(slot.requires) if cap not in c.caps]
            if missing and c.adapter != "none":
                needed = ", ".join(CAP_LABEL[m] for m in missing)
                add("error", slot.id, f"'{slot.label}'에는 {needed} 기능이 필요한데 '{c.label}'은(는) 지원하지 않습니다.")
            license_ = _text(c.license)
            if REVIEW_LICENSE.search(license_):
                add("warn", slot.id, f"'{c.label}' 라이선스({license_})는 서비스 전환 전에 검토해야 합니다.")

        mode = chosen("vlm.input_mode")
        cloud = [s for s in FRAME_SLOTS if chosen(s, "kind") == "api"]
        if mode == "v3" and cloud:
            names = ", ".join(part[s]["slotLabel"] for s in cloud)
            add("error", "vlm.input_mode", f"V-3(로컬만)인데 {names}이(가) 외부 API입니다.")
        if mode == "v1" and cloud:
            add("warn", "vlm.input_mode", "V-1은 원본 프레임을 외부로 보냅니다. 동의받은 영상이나 합성 영상에만 쓰세요.")
        if chosen("llm.auditor", "adapter") == "none":
            add("warn", "llm.auditor", "감사를 생략하면 문신 같은 잔여 식별자를 놓칠 수 있습니다.")
        elif vendor := chosen("llm.auditor", "vendor"):
            same = [s for s in ("llm.analyst", "llm.planner") if chosen(s, "vendor") == vendor]
            if same:
                names = " · ".join(part[s]["slotLabel"] for s in same)
                add("warn", "llm.auditor", f"감사자가 {names}와 같은 회사 모델이라 독립성이 약해집니다.")
        if chosen("agent.questions") == "disabled":
            add("warn", "agent.questions", "동적 질문을 끄면 모르는 사실을 보수적으로 가정해 과잉 처리가 늘 수 있습니다.")
        if chosen("rag.retrieval") == "dense":
            add("info", "rag.bm25_tokenizer", "dense만 쓰므로 BM25 토크나이저는 쓰이지 않습니다.")
        if chosen("rag.seed") == "disabled":
            add("info", "rag.seed", "시드 묶음 없이 핵심 조문부터 검색하므로 검색 호출이 늘어납니다.")
        for p in catalog.params:
            if p.id not in numbers:
                continue
            value = _number(numbers[p.id])
            if value is None or value < p.min or value > p.max:
                add("error", None, f"{p.label}은(는) {_js_number(p.min)}~{_js_number(p.max)} 사이여야 합니다.")
        return issues


# ---------- wire shapes (contract.ts) ----------

COMPONENT_KEYS = ("slot", "stage", "slotLabel", "candidate", "label", "short", "kind", "tier", "usedBy")


def _compact(**fields: Any) -> dict:
    """A JSON object without the keys JavaScript would leave undefined."""
    return {key: value for key, value in fields.items() if value is not None}


def _caps(values: Any) -> list[str]:
    return [c for c in values or () if c in CAPS]


def _candidate_wire(c: core.Candidate) -> dict:
    price = None if c.price is None else {"in": _number(c.price[0]), "out": _number(c.price[1])}
    lawirko = c.lawirko if isinstance(c.lawirko, int | float) and not isinstance(c.lawirko, bool) else None
    return _compact(
        id=c.id, label=c.label, short=_optional(c.short), adapter=c.adapter, ref=c.ref,
        license=_optional(c.license), price=price, caps=_caps(c.caps),
        tier=c.tier if c.tier in TIERS else None, lawirko=lawirko, status="planned" if c.planned else None,
        note=_optional(c.note), source=c.source, addedAt=c.added,
    )


def _slot_wire(slot: core.Slot) -> dict:
    return {"id": slot.id, "stage": slot.stage, "label": slot.label,
            "usedBy": [_text(v) for v in slot.used_by], "adapters": [_text(v) for v in slot.adapters],
            "requires": _caps(slot.requires), "default": slot.default,
            "candidates": [_candidate_wire(c) for c in slot.candidates]}


def _part_wire(part: core.Part) -> dict:
    """RunComponent followed by what the engine needs to act on the chosen candidate (ResolvedPart)."""
    slot, c = part.slot, _candidate_wire(part.candidate)
    return _compact(
        slot=slot.id, stage=slot.stage, slotLabel=slot.label, candidate=c["id"], label=c["label"],
        short=c.get("short", c["label"]), kind=part.kind, tier=c.get("tier"),
        usedBy=[_text(v) for v in slot.used_by], adapter=c["adapter"], ref=c.get("ref"),
        license=c.get("license"), price=c.get("price"), caps=c["caps"], lawirko=c.get("lawirko"),
        planned=part.candidate.planned, vendor=part.vendor,
    )


def _repo_relative(directory: Path) -> str:
    """configs/pipeline for the repository's own folder, the path as given for any other."""
    try:
        return directory.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return directory.as_posix()


def _iso(mtime_ns: int) -> str:
    """A file time as JavaScript's Date.toISOString() prints it."""
    moment = datetime.fromtimestamp(mtime_ns // 1_000_000 / 1000, UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------- JavaScript-compatible values ----------

def _js_number(value: float | int) -> str:
    """A number as JavaScript prints it: 2.0 → 2, 0.30 → 0.3, 1e-07 → 1e-7."""
    text = repr(float(value))  # shortest digits that read back as the same double, as in JavaScript
    mantissa, _, exponent = text.partition("e")
    if exponent and not -7 < int(exponent) < 21:
        return f"{mantissa}e{int(exponent):+d}"
    return (format(Decimal(text), "f") if exponent else text).removesuffix(".0")


def _number(value: Any) -> float | int | None:
    """JavaScript's Number(value) for a JSON value; None where JavaScript has NaN or an infinity."""
    if value is None:
        return 0
    if isinstance(value, str):
        try:
            value = float(value) if value.strip() else 0
        except ValueError:
            return None
    if isinstance(value, int):
        return int(value)
    if not isinstance(value, float) or not math.isfinite(value):
        return None
    return int(value) if value.is_integer() else value


def _text(value: Any, fallback: str = "") -> str:
    """A string stays, a number prints as in JavaScript, anything else is the fallback (pipeline.ts `str`)."""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _js_number(value)
    return fallback


def _optional(value: Any) -> str | None:
    return None if value is None else _text(value)


def _request_price(price: Any) -> dict | None:
    if not isinstance(price, dict):
        return None
    pair = [price.get("in"), price.get("out")]
    if not all(isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in pair):
        return None
    return {"in": max(0, pair[0]), "out": max(0, pair[1])}


def _slugify(ref: str) -> str:
    last = ([piece for piece in ref.strip().split("/") if piece] or [ref])[-1].lower()
    last = re.sub(r"\.(onnx|pt|pth|safetensors)\Z", "", last)
    return re.sub(r"^-+|-+\Z", "", re.sub(r"[^a-z0-9._-]+", "-", last))[:48]


# ---------- YAML text ----------
# Scalars are written the way the Lab's `yaml` package writes them (plain when possible, else quoted), with
# one addition: text that PyYAML (YAML 1.1) would read as a boolean, number or date is quoted too, so the
# Python reader and the TypeScript one get the same string back.

_NOT_PLAIN = re.compile(
    r"""^[\n\t ,\[\]{}#&*!|>'"%@`]|^[?-]\Z|^[?-][ \t]|[\n:][ \t]|[ \t]\n|[\n\t ]#|[\n\t :]\Z"""
)
_YAML12_TYPED = re.compile(
    r"(?:~|[Nn]ull|NULL)?|[Tt]rue|TRUE|[Ff]alse|FALSE|0o[0-7]+|[-+]?[0-9]+|0x[0-9a-fA-F]+"
    r"|[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN"
    r"|[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)[eE][-+]?[0-9]+|[-+]?(?:\.[0-9]+|[0-9]+\.[0-9]*)"
)
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")
_YAML11 = yaml.resolver.Resolver()
_FAST_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_STR_TAG = "tag:yaml.org,2002:str"


def _scalar(value: Any) -> str:
    """One YAML scalar on one line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _js_number(value)
    text = str(value)
    if _CONTROL.search(text):  # line breaks and control characters are escaped: the entry stays on one line
        return _CONTROL.sub(lambda m: f"\\u{ord(m.group()):04x}", json.dumps(text, ensure_ascii=False))
    typed = _YAML12_TYPED.fullmatch(text) or _YAML11.resolve(yaml.ScalarNode, text, (True, False)) != _STR_TAG
    if not (_NOT_PLAIN.search(text) or typed):
        return text
    if '"' in text and "'" not in text:
        return "'" + text + "'"
    return json.dumps(text, ensure_ascii=False)


def _read(file: Path) -> str:
    return file.read_bytes().decode("utf-8")  # bytes, so line endings reach the edit untouched


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _line_end(text: str, index: int) -> int:
    """Index where the line holding `index` ends (before its line break)."""
    end = text.find("\n", index)
    end = len(text) if end < 0 else end
    return end - 1 if end > index and text[end - 1] == "\r" else end


def _content_end(node: yaml.Node, text: str) -> int:
    """Index just past the node's last character (a block node's own end mark sits on the following token)."""
    while isinstance(node, yaml.CollectionNode) and not node.flow_style and node.value:
        last = node.value[-1]
        node = last[1] if isinstance(last, tuple) else last
    return len(text[: node.end_mark.index].rstrip())


def _child(node: yaml.Node | None, key: str) -> tuple[yaml.Node, yaml.Node] | None:
    """Key node and value node of a mapping entry."""
    if isinstance(node, yaml.MappingNode):
        for key_node, value in node.value:
            if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
                return key_node, value
    return None


def _items(pair: tuple[yaml.Node, yaml.Node] | None) -> list[yaml.Node]:
    return pair[1].value if pair and isinstance(pair[1], yaml.SequenceNode) else []


def _candidates_node(text: str, slot_id: str) -> tuple[yaml.Node, yaml.Node]:
    """`candidates` key and value nodes of a slot in catalog.yaml."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        raise PipelineError(500, BROKEN_CATALOG) from error
    for stage in _items(_child(root, "stages")):
        for slot in _items(_child(stage, "slots")):
            found = _child(slot, "id")
            if found and isinstance(found[1], yaml.ScalarNode) and found[1].value == slot_id:
                pair = _child(slot, "candidates")
                block_list = pair and isinstance(pair[1], yaml.SequenceNode) and not pair[1].flow_style
                if pair is None or (pair[1].value and not block_list):
                    raise PipelineError(500, BROKEN_CATALOG)  # candidates must be a block list (or empty)
                return pair
    raise PipelineError(404, f"카탈로그에 '{slot_id}' 부품이 없습니다.")


def _insert_candidate(text: str, slot_id: str, entry: list[tuple[str, str]]) -> tuple[str, int]:
    """catalog.yaml with one more entry at the end of the slot's candidates, and the entry's index."""
    key, seq = _candidates_node(text, slot_id)
    newline = _newline(text)
    items = seq.value if isinstance(seq, yaml.SequenceNode) else []
    indent = " " * (seq.start_mark.column if items else key.start_mark.column + 2)
    block = newline.join(f"{indent}{'  ' if i else '- '}{k}: {v}" for i, (k, v) in enumerate(entry))
    at = _line_end(text, _content_end(seq, text) if items else key.end_mark.index)
    if items:
        return text[:at] + newline + block + text[at:], len(items)
    colon = text.index(":", key.end_mark.index) + 1  # `candidates: []` or `candidates:` becomes a block list
    return text[:colon] + text[max(colon, seq.end_mark.index) : at] + newline + block + text[at:], 0


def _delete_candidate(text: str, slot_id: str, candidate_id: str) -> tuple[str, int]:
    """catalog.yaml without the lines of one candidate entry, and the index the entry had."""
    key, seq = _candidates_node(text, slot_id)
    for index, item in enumerate(seq.value or []):
        found = _child(item, "id")
        if found and isinstance(found[1], yaml.ScalarNode) and found[1].value == candidate_id:
            break
    else:
        raise PipelineError(404, "후보를 찾을 수 없습니다.")
    dash = text.rfind("-", 0, item.start_mark.index)
    start = text.rfind("\n", 0, dash) + 1
    if dash < 0 or text[start:dash].strip():
        raise PipelineError(500, BROKEN_CATALOG)
    stop = text.find("\n", _content_end(item, text)) + 1 or len(text)
    head, tail = text[:start], text[stop:]
    if not tail and not text.endswith("\n"):  # the entry closed a file that has no final line break
        head = head.removesuffix("\n").removesuffix("\r")
    if len(seq.value) == 1:  # nothing left under the key
        colon = text.index(":", key.end_mark.index) + 1
        head = head[:colon] + " []" + head[colon:]
    return head + tail, index


def _differ_by_one_candidate(shorter: str, longer: str, slot_id: str, index: int) -> bool:
    """True when both texts read as the same catalog but for one more candidate at `index` in `longer`."""
    try:
        old, new = (yaml.load(text, Loader=_FAST_LOADER) for text in (shorter, longer))
        slots = [s for data in (old, new) for st in data["stages"] for s in st.get("slots") or []
                 if str(s.get("id")) == slot_id]
        few, many = (list(s.get("candidates") or []) for s in slots)
        del many[index]
        slots[0]["candidates"] = slots[1]["candidates"] = None
        return few == many and old == new
    except READ_ERRORS:
        return False


def _replace_value(text: str, value: yaml.Node, scalar: str) -> str:
    """The text with the value of one `key: value` entry rewritten where it stands."""
    start = value.start_mark.index
    end = max(start, _content_end(value, text))  # start == end: `key:` had no value
    return text[:start] + (" " if start == end else "") + scalar + text[end:]


def _set_top_level(text: str, key: str, scalar: str) -> str:
    """The text with one top-level `key: value` changed (or appended when the key is new)."""
    try:
        pair = _child(yaml.compose(text, Loader=yaml.SafeLoader), key)
    except yaml.YAMLError:
        pair = None
    if pair is None:
        newline = _newline(text)
        return text + ("" if not text or text.endswith("\n") else newline) + f"{key}: {scalar}{newline}"
    return _replace_value(text, pair[1], scalar)


def _edit_profile(text: str, previous: core.ProfileSpec, spec: dict) -> str | None:
    """The profile file with only the changed values rewritten where they stand, so comments, order and the
    spelling of every other value (0.30 stays 0.30) survive. None when the file does not list exactly the
    keys of `spec` or the edit would not read back: then the file has to be laid out again."""
    was = {("id",): previous.id, ("name",): previous.name, ("description",): previous.description,
           ("base",): previous.base, **{("selections", k): v for k, v in previous.selections.items()},
           **{("params", k): v for k, v in previous.params.items()}}
    now = {(k,): spec[k] for k in ("id", "name", "description", "base") if k in spec}
    now |= {(section, k): v for section in ("selections", "params") for k, v in spec[section].items()}
    try:
        root, data = yaml.compose(text, Loader=yaml.SafeLoader), yaml.safe_load(text)
        nodes = {}
        for key, value in _block_entries(root):
            if key.value in ("selections", "params"):
                nodes |= {(key.value, k.value): v for k, v in _block_entries(value)}
            elif key.value in ("id", "name", "description", "base"):
                nodes[(key.value,)] = value
        changed = [path for path in now if was.get(path) != now[path]]
        if set(nodes) != set(now) or not all(isinstance(nodes[path], yaml.ScalarNode) for path in changed):
            return None
        for path in sorted(changed, key=lambda path: nodes[path].start_mark.index, reverse=True):
            text = _replace_value(text, nodes[path], _scalar(now[path]))
            (data if len(path) == 1 else data[path[0]])[path[-1]] = now[path]
        return text if yaml.safe_load(text) == data else None
    except READ_ERRORS:
        return None


def _block_entries(node: yaml.Node | None) -> list[tuple[yaml.ScalarNode, yaml.Node]]:
    """Entries of a block-style mapping (flow style has other quoting rules, so it is not edited in place)."""
    if not isinstance(node, yaml.MappingNode) or node.flow_style:
        return []
    return [(key, value) for key, value in node.value if isinstance(key, yaml.ScalarNode)]
