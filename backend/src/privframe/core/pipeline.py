"""Pipeline catalog and profiles (ADR-0009), read from configs/pipeline.

A profile picks one catalog candidate per part (slot) and sets the budget parameters. The Lab (frontend/mock)
reads and writes the same files; `config_hash` here must match the Lab's value for the same choices.
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

AdapterKind = Literal["api", "local", "builtin", "option"]
FRAME_SLOTS = ("vlm.scene", "vlm.person", "vlm.inspect", "perception.ocr")


@dataclass(frozen=True)
class Adapter:
    id: str
    label: str
    kind: AdapterKind
    ref: str
    addable: bool = True
    lookup: str | None = None
    example: str | None = None


@dataclass(frozen=True)
class Candidate:
    id: str
    label: str
    adapter: str
    ref: str | None = None
    short: str | None = None
    license: str | None = None
    price: tuple[float, float] | None = None  # USD per 1M tokens (in, out)
    caps: tuple[str, ...] = ()
    tier: str | None = None
    lawirko: float | None = None
    planned: bool = False
    note: str | None = None
    source: Literal["file", "lab"] = "file"
    options: dict[str, Any] = field(default_factory=dict)  # adapter-specific settings (e.g. query_prefix)
    added: str | None = None  # day the Lab recorded the candidate (source: lab)


@dataclass(frozen=True)
class Slot:
    id: str
    stage: str
    label: str
    used_by: tuple[str, ...]
    adapters: tuple[str, ...]
    requires: tuple[str, ...]
    default: str
    candidates: tuple[Candidate, ...]

    def candidate(self, candidate_id: str) -> Candidate | None:
        return next((c for c in self.candidates if c.id == candidate_id), None)


@dataclass(frozen=True)
class Param:
    id: str
    stage: str
    label: str
    unit: str
    default: float
    min: float
    max: float
    step: float = 1


@dataclass(frozen=True)
class Stage:
    id: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class Catalog:
    adapters: dict[str, Adapter]
    slots: tuple[Slot, ...]
    params: tuple[Param, ...]
    stages: tuple[Stage, ...] = ()  # file order; a slot names its stage in Slot.stage

    def slot(self, slot_id: str) -> Slot:
        for s in self.slots:
            if s.id == slot_id:
                return s
        raise KeyError(f"catalog has no part '{slot_id}'")


@dataclass(frozen=True)
class ProfileSpec:
    id: str
    name: str
    description: str = ""
    base: str | None = None
    selections: dict[str, str] = field(default_factory=dict)
    params: dict[str, float] = field(default_factory=dict)
    source: Literal["file", "lab"] = "file"  # lab = written by the Lab's profile editor


@dataclass(frozen=True)
class Part:
    slot: Slot
    candidate: Candidate
    kind: AdapterKind

    @property
    def vendor(self) -> str | None:
        if self.candidate.adapter == "openrouter" and self.candidate.ref and "/" in self.candidate.ref:
            return self.candidate.ref.split("/")[0]
        return {"anthropic": "anthropic", "gemini-api": "google", "openai-embeddings": "openai"}.get(self.candidate.adapter)


@dataclass(frozen=True)
class ResolvedProfile:
    id: str
    name: str
    parts: dict[str, Part]
    params: dict[str, float]
    config_hash: str

    def part(self, slot_id: str) -> Part:
        return self.parts[slot_id]

    @property
    def frames_leave_device(self) -> bool:
        return any(self.parts[s].kind == "api" for s in FRAME_SLOTS if s in self.parts)


def _num(value: Any) -> float | int:
    """JavaScript has one number type; integral floats must serialize like the Lab does ("1", not "1.0")."""
    number = float(value)
    return int(number) if number.is_integer() else number


_FAST_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml reads the catalog about eight times faster
_catalogs: dict[Path, tuple[tuple[int, int, int], Catalog]] = {}


def load_catalog(pipeline_dir: Path) -> Catalog:
    """The catalog, parsed once per version of the file. The result is shared: read it, do not change it."""
    file = pipeline_dir / "catalog.yaml"
    stat = file.stat()
    stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    hit = _catalogs.get(file)
    if hit is None or hit[0] != stamp:
        hit = _catalogs[file] = (stamp, _parse_catalog(file))
    return hit[1]


def _parse_catalog(file: Path) -> Catalog:
    raw = yaml.load(file.read_text(encoding="utf-8"), Loader=_FAST_LOADER) or {}  # noqa: S506 - a safe loader
    adapters = {
        key: Adapter(
            id=key,
            label=str(a.get("label", key)),
            kind=a.get("kind", "local"),
            ref=str(a.get("ref", "")),
            addable=a.get("addable", True) is not False,
            lookup=a.get("lookup"),
            example=None if a.get("example") is None else str(a["example"]),
        )
        for key, a in (raw.get("adapters") or {}).items()
    }
    slots: list[Slot] = []
    for stage in raw.get("stages") or []:
        for s in stage.get("slots") or []:
            candidates = tuple(
                Candidate(
                    id=str(c["id"]),
                    label=str(c.get("label", c["id"])),
                    adapter=str(c["adapter"]),
                    ref=None if c.get("ref") is None else str(c["ref"]),
                    short=c.get("short"),
                    license=c.get("license"),
                    price=(float(c["price"]["in"]), float(c["price"].get("out", 0))) if c.get("price") else None,
                    caps=tuple(c.get("caps") or ()),
                    tier=c.get("tier"),
                    lawirko=c.get("lawirko"),
                    planned=c.get("status") == "planned",
                    note=c.get("note"),
                    source="lab" if c.get("source") == "lab" else "file",
                    options=dict(c.get("options") or {}),
                    added=None if c.get("added") is None else str(c["added"]),
                )
                for c in s.get("candidates") or []
            )
            slots.append(
                Slot(
                    id=str(s["id"]),
                    stage=str(stage["id"]),
                    label=str(s.get("label", s["id"])),
                    used_by=tuple(s.get("used_by") or ()),
                    adapters=tuple(s.get("adapters") or ()),
                    requires=tuple(s.get("requires") or ()),
                    default=str(s["default"]),
                    candidates=candidates,
                )
            )
    params = tuple(
        Param(
            id=str(p["id"]),
            stage=str(p.get("stage", "")),
            label=str(p.get("label", p["id"])),
            unit=str(p.get("unit", "")),
            default=_num(p.get("default", 0)),
            min=_num(p.get("min", 0)),
            max=_num(p.get("max", 100)),
            step=_num(p.get("step", 1)),
        )
        for p in raw.get("params") or []
    )
    stages = tuple(
        Stage(str(st["id"]), str(st.get("label", st["id"])), str(st.get("description") or ""))
        for st in raw.get("stages") or []
    )
    return Catalog(adapters=adapters, slots=tuple(slots), params=params, stages=stages)


def load_profile(pipeline_dir: Path, profile_id: str) -> ProfileSpec:
    path = pipeline_dir / "profiles" / f"{profile_id}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ProfileSpec(
        id=str(raw.get("id", profile_id)),
        name=str(raw.get("name", raw.get("id", profile_id))),
        description=str(raw.get("description") or ""),
        base=raw.get("base"),
        selections={str(k): str(v) for k, v in (raw.get("selections") or {}).items()},
        params={str(k): _num(v) for k, v in (raw.get("params") or {}).items()},
        source="lab" if raw.get("source") == "lab" else "file",
    )


def list_profiles(pipeline_dir: Path) -> list[str]:
    return sorted(p.stem for p in (pipeline_dir / "profiles").glob("*.yaml"))


def service_profiles(pipeline_dir: Path) -> dict[str, str]:
    raw = yaml.safe_load((pipeline_dir / "service.yaml").read_text(encoding="utf-8")) or {}
    default = str(raw.get("default", "baseline-v2"))
    return {"default": default, "local_only": str(raw.get("local_only", default))}


def config_hash(selections: dict[str, str], params: dict[str, float]) -> str:
    """Same algorithm as frontend/mock/pipeline.ts: sha256 of the sorted choices as compact JSON, 8 hex chars."""
    body = json.dumps(
        {"s": dict(sorted(selections.items())), "p": {k: _num(v) for k, v in sorted(params.items())}},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]


def resolve(catalog: Catalog, spec: ProfileSpec, overrides: dict[str, str] | None = None, params: dict[str, float] | None = None) -> ResolvedProfile:
    """The profile with every part chosen. `overrides` (part → candidate) and `params` replace the profile's
    own choices for one run, e.g. a variant of an experiment."""
    overrides = overrides or {}
    parts: dict[str, Part] = {}
    for slot in catalog.slots:
        wanted = overrides.get(slot.id) or spec.selections.get(slot.id) or slot.default
        candidate = slot.candidate(wanted) or slot.candidate(slot.default) or slot.candidates[0]
        kind = catalog.adapters[candidate.adapter].kind if candidate.adapter in catalog.adapters else "local"
        parts[slot.id] = Part(slot=slot, candidate=candidate, kind=kind)
    given = {**spec.params, **(params or {})}
    params = {p.id: _num(given.get(p.id, p.default)) for p in catalog.params}
    digest = config_hash({k: v.candidate.id for k, v in parts.items()}, params)
    return ResolvedProfile(id=spec.id, name=spec.name, parts=parts, params=params, config_hash=digest)
