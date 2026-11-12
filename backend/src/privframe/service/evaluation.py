"""The least an evaluation needs: what a score is, where evaluators are registered, and how scores are averaged.

Which metrics each part of the pipeline is judged by is being designed separately. This module only fixes the
shape, so that design can be plugged in without touching the experiment code or the screens:

- a **score** is one line: a metric id, a number, and how to read it (label, unit, which direction is better);
- an **evaluator** is a function from a finished run to scores, registered under an id for the sections it looks at.
  The ones registered here run when an experiment run completes;
- scores computed elsewhere are stored through the API under the id of whatever produced them;
- **aggregation** is the mean per metric, with the number of values behind it.

The screens draw whatever metrics they find. Adding an evaluator is `register(Evaluator(...))`.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

Direction = str | None  # "higher" | "lower" | None (not a better-or-worse number)


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    unit: str = ""  # "ratio" (0–1), "count", "sec", "usd", or free text
    direction: Direction = None


@dataclass(frozen=True)
class RunContext:
    """What an evaluator may look at: the run's results per section (as the experiment reports them), the graph
    state it ended with, the dataset's notes on the video, and the run's directory."""

    sections: tuple[str, ...]
    results: dict[str, Any]
    state: dict[str, Any]
    item: dict[str, Any]
    directory: Path


@dataclass(frozen=True)
class Evaluator:
    id: str
    label: str
    description: str
    sections: tuple[str, ...]  # runs whose range covers one of these are evaluated
    metrics: tuple[Metric, ...]
    evaluate: Callable[[RunContext], list[dict[str, Any]]]


_REGISTRY: dict[str, Evaluator] = {}


def register(evaluator: Evaluator) -> Evaluator:
    _REGISTRY[evaluator.id] = evaluator
    return evaluator


def evaluators() -> list[Evaluator]:
    return list(_REGISTRY.values())


def describe() -> list[dict[str, Any]]:
    return [{"id": e.id, "label": e.label, "description": e.description, "sections": list(e.sections), "metrics": [{"id": m.id, "label": m.label, "unit": m.unit, "direction": m.direction} for m in e.metrics]} for e in evaluators()]


def score(metric: Metric, value: float, *, evaluator: str, section: str | None = None, detail: str = "") -> dict[str, Any]:
    return {"metric": metric.id, "label": metric.label, "value": round(float(value), 4), "unit": metric.unit, "direction": metric.direction, "section": section, "evaluator": evaluator, "detail": detail}


def clean(raw: dict[str, Any], evaluator: str) -> dict[str, Any]:
    """A score as given from outside, in the stored shape. Raises ValueError when it has no metric or number."""
    metric = str(raw.get("metric") or "").strip()
    if not metric:
        raise ValueError("점수에 지표 이름(metric)이 없습니다.")
    try:
        value = float(raw["value"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"'{metric}'의 값(value)이 수가 아닙니다.") from error
    direction = raw.get("direction")
    if direction not in ("higher", "lower", None):
        raise ValueError(f"'{metric}'의 direction은 higher · lower 가운데 하나이거나 없어야 합니다.")
    return {"metric": metric, "label": str(raw.get("label") or metric), "value": round(value, 6), "unit": str(raw.get("unit") or ""), "direction": direction, "section": raw.get("section") or None, "evaluator": evaluator, "detail": str(raw.get("detail") or "")}


def evaluate(context: RunContext) -> tuple[list[dict[str, Any]], list[str]]:
    """The scores of every registered evaluator that looks at one of the run's sections, and the evaluators
    that failed (a failing evaluator must not lose the run its result)."""
    scores: list[dict[str, Any]] = []
    failed: list[str] = []
    for evaluator in evaluators():
        if not set(evaluator.sections) & set(context.sections):
            continue
        try:
            scores += evaluator.evaluate(context)
        except Exception as error:  # noqa: BLE001
            failed.append(f"{evaluator.label}: {type(error).__name__}: {error}")
    return scores, failed


def merge(existing: list[dict[str, Any]], new: list[dict[str, Any]], evaluator_ids: Iterable[str]) -> list[dict[str, Any]]:
    """`existing` with the scores of the given evaluators replaced by `new`."""
    replaced = set(evaluator_ids)
    return [*[s for s in existing if s["evaluator"] not in replaced], *new]


def aggregate(groups: list[list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Mean per metric over groups of scores (one group per video: a metric scored several times in a group
    counts once, with its mean). `n` is the number of groups that had the metric."""
    collected: dict[str, dict[str, Any]] = {}
    for group in groups:
        per_metric: dict[str, list[dict[str, Any]]] = {}
        for item in group:
            per_metric.setdefault(item["metric"], []).append(item)
        for metric, items in per_metric.items():
            entry = collected.setdefault(metric, {"label": items[-1]["label"], "unit": items[-1]["unit"], "direction": items[-1]["direction"], "values": []})
            entry["values"].append(sum(i["value"] for i in items) / len(items))
    return {metric: {"label": e["label"], "unit": e["unit"], "direction": e["direction"], "mean": round(sum(e["values"]) / len(e["values"]), 4), "n": len(e["values"])} for metric, e in collected.items()}


# ---------------------------------------------------------------------------------------------------------
# the one evaluator that existed before the evaluation design: what was found against the dataset's notes


def plain(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).lower() if not ch.isspace())


def same_text(a: str, b: str) -> bool:
    a, b = plain(a), plain(b)
    return bool(a) and bool(b) and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.8)


TEXTS = Metric("expected.texts", "글자 인식", "ratio", "higher")
COUNTS = {"people": Metric("expected.people", "인물 수 일치", "ratio", "higher"), "plates": Metric("expected.plates", "번호판 수 일치", "ratio", "higher"), "scenes": Metric("expected.scenes", "장면 수 일치", "ratio", "higher")}


def _expected(context: RunContext) -> list[dict[str, Any]]:
    expected, found = context.item.get("expected"), context.results.get("perception")
    if not expected or not found:
        return []
    out = []
    wanted = [str(t) for t in expected.get("texts") or []]
    if wanted:
        hit = [t for t in wanted if any(same_text(t, got) for got in found["texts"])]
        missing = [t for t in wanted if t not in hit]
        out.append(score(TEXTS, len(hit) / len(wanted), evaluator="expected", section="perception", detail=f"{len(hit)} / {len(wanted)}" + (f" · 못 찾음: {', '.join(missing)}" if missing else "")))
    for key, metric in COUNTS.items():
        if key in expected:
            out.append(score(metric, 1.0 if found[key] == expected[key] else 0.0, evaluator="expected", section="perception", detail=f"{found[key]} (정답 {expected[key]})"))
    return out


register(Evaluator(id="expected", label="데이터셋 정답과 비교", description="영상 정보 추출이 찾은 글자 · 인물 수 · 번호판 수 · 장면 수를 데이터셋에 적어 둔 값과 비교한다. 정답을 적어 둔 영상에만 점수가 나온다.", sections=("perception",), metrics=(TEXTS, *COUNTS.values()), evaluate=_expected))
