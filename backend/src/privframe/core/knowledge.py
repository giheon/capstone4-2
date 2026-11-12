"""Reference knowledge the agents may consult: the seed bundle, the non-binding issue patterns, and evidence
handling (design §5.2 · §5.5 · §9). None of it decides anything; the corpus contents come from whatever index
exists (corpus selection is a separate decision).

Evidence ids are `<doc_id>#<unit_id>` (e.g. `pipa#제17조`): readable, stable, and checkable against the corpus,
so a citation can never drift from the document it points at.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .corpus import CorpusStore
from .domain import AUTHORITY_LABEL, Citation, VideoUnderstanding
from .settings import ROOT

KNOWLEDGE_DIR = ROOT / "configs" / "knowledge"
KIND_LABEL = {"law": "법령", "admrul": "고시", "prec": "판례", "detc": "헌재 결정", "expc": "법령해석례", "ppc": "개인정보위 결정", "guideline": "안내서", "mediation": "분쟁조정 사례"}


def evidence_id(doc_id: str, unit_id: str) -> str:
    return f"{doc_id}#{unit_id}"


def make_evidence(doc_id: str, unit_id: str, citation: str, unit_label: str, kind: str, authority: int | None, text: str) -> dict[str, Any]:
    return {
        "id": evidence_id(doc_id, unit_id),
        "doc_id": doc_id,
        "unit_id": unit_id,
        "label": f"{citation} {unit_label}".strip(),
        "kind": kind,
        "authority": authority if authority in (1, 2, 3) else 3,
        "text": " ".join(text.split())[:320],
    }


def citation_of(evidence: dict[str, Any]) -> Citation:
    return Citation(id=evidence["id"], label=evidence["label"], authority=evidence["authority"], kind=KIND_LABEL.get(evidence["kind"], evidence["kind"]))


def citations_for(ids: list[str], evidence: dict[str, dict[str, Any]]) -> list[Citation]:
    seen: dict[str, Citation] = {}
    for item in ids:
        if item in evidence and item not in seen:
            seen[item] = citation_of(evidence[item])
    return sorted(seen.values(), key=lambda c: (c.authority, c.label))


def distribution(items: list[dict[str, Any]]) -> str:
    """'규범 3 · 해석 2 · 참고 1' — lets the agent see which kinds of sources a search returned."""
    counts: dict[int, int] = {}
    for item in items:
        counts[item["authority"]] = counts.get(item["authority"], 0) + 1
    return " · ".join(f"{AUTHORITY_LABEL[a]} {n}" for a, n in sorted(counts.items())) or "결과 없음"


# ---------- seed bundle ----------


@dataclass
class Seed:
    evidence: list[dict[str, Any]]
    texts: dict[str, str]  # evidence id → text given to the agent
    missing: list[str]

    def as_prompt(self) -> str:
        return "\n\n".join(f"[{e['id']}] {e['label']} · {AUTHORITY_LABEL[e['authority']]}\n{self.texts[e['id']]}" for e in self.evidence)


def load_seed(store: CorpusStore, path: Path | None = None) -> Seed:
    """Resolve the seed bundle against the corpus that is actually present; absent items are reported, not faked."""
    data = yaml.safe_load((path or KNOWLEDGE_DIR / "seed.yaml").read_text(encoding="utf-8")) or {}
    limit = int(data.get("max_chars_per_unit", 1600))
    evidence: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    missing: list[str] = []
    for entry in data.get("bundle") or []:
        record = store.find(str(entry.get("doc", "")))
        document = store.load_document(record.doc_id) if record and record.index else None
        if record is None or document is None:
            missing.append(str(entry.get("doc")))
            continue
        for unit_id in entry.get("units") or []:
            unit = document.unit(str(unit_id))
            if unit is None:
                missing.append(f"{record.cite} {unit_id}")
                continue
            item = make_evidence(record.doc_id, unit.unit_id, record.cite, unit.label, record.kind, record.authority, unit.text)
            evidence.append(item)
            texts[item["id"]] = unit.text if len(unit.text) <= limit else unit.text[:limit] + " …(이하 생략: get_document로 전문 열람)"
    return Seed(evidence, texts, missing)


# ---------- reference patterns ----------


@lru_cache(maxsize=4)
def _patterns_file(path: str, mtime: float) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_patterns(path: Path | None = None) -> dict[str, Any]:
    file = path or KNOWLEDGE_DIR / "issue_patterns.yaml"
    return _patterns_file(str(file), file.stat().st_mtime)


def observation_signals(understanding: VideoUnderstanding, facts: list[dict[str, Any]]) -> set[str]:
    """Which reference patterns the observation hints at. Signals are hints for the checklist and the coverage
    question, never a classification the agent must follow."""
    signals = {"always"}
    known = {f["key"]: f["value"] for f in facts}
    others = [p for p in understanding.persons if known.get(f"{p.id}.is_uploader") != "yes"]
    if others:
        signals.add("third_party")
    if len(understanding.persons) >= 4:
        signals.add("many_people")
    if understanding.texts:
        signals.add("text")
    if understanding.plates:
        signals.add("plate")
    if known.get("file.location_metadata") == "present":
        signals.add("location_metadata")
    for person in understanding.persons:
        if str(person.attributes.get("age_band", "")) in ("child", "teen"):
            signals.add("minor")
    for segment in understanding.segments:
        description = segment.description or {}
        if description.get("minors_present") is True:
            signals.add("minor")
        if description.get("sensitivity_flags"):
            signals.add("sensitive")
        if str(description.get("place_type", "")) in ("private_home", "restroom", "changing_room", "hospital_room"):
            signals.add("private_space")
    return signals


def checklist_text(understanding: VideoUnderstanding, facts: list[dict[str, Any]]) -> str:
    data = load_patterns()
    signals = observation_signals(understanding, facts)
    hinted = [p for p in data.get("patterns", []) if p["trigger"] in signals and p["trigger"] != "always"]
    lines = ["참고 패턴 (비구속, 분류를 강제하지 않음):"]
    lines += [f"- {p['id']} {p['name']}: {p['hint']}" for p in data.get("patterns", [])]
    lines.append("")
    lines.append("이 영상의 관찰과 맞닿는 패턴: " + (", ".join(f"{p['id']} {p['name']}" for p in hinted) or "없음"))
    return "\n".join(lines)


def uncovered_patterns(understanding: VideoUnderstanding, facts: list[dict[str, Any]], analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Patterns the observation hints at that no analysis seems to consider (coverage check, design §5.2)."""
    data = load_patterns()
    signals = observation_signals(understanding, facts) - {"always"}
    text = " ".join(f"{a.get('question_of_law', '')} {a.get('reasoning_ko', '')} {a.get('pattern_ref') or ''}" for a in analyses)
    out = []
    for pattern in data.get("patterns", []):
        if pattern["trigger"] not in signals:
            continue
        if pattern["id"] in text or any(word in text for word in pattern.get("keywords", [])):
            continue
        out.append(pattern)
    return out


def fact_vocabulary() -> str:
    data = load_patterns()
    return "\n".join(f"- {f['key']} ({f['label']}): " + " | ".join(f"{k}={v}" for k, v in f.get("options", {}).items()) for f in data.get("fact_keys", []))
