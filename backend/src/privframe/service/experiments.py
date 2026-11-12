"""Section experiments (FR-14 · FR-17): a growing set of runs over one range of pipeline sections, compared by
what each combination of parts found, how long it took and what it cost.

An experiment fixes the range and the base profile. Runs are added to it, a launch at a time: each run is one
video with one combination (the base profile with some parts or values swapped). A run does not start from the
beginning when an earlier run of the same video has the same parts in front of the place the run has to start
at: it continues that run from its checkpoint, so nothing before runs again and what the user answered there
stays answered. Inside perception a step whose inputs did not change takes its stored result (core/artifacts.py).

Nothing is answered for the user. A section that asks the user (core/stages.py) makes the experiment
interactive: a launch then adds exactly one run, the run waits where it asks, and the person answers in the
service screens. Because every run of such a range starts at or in front of its first asking section, each run
is answered anew; an asking section in front of the range is never run again, its answers come with the
checkpoint. When no run has those answers yet, the launch is refused and says what is missing.

Runs are ordinary cases (`exp_…`): each one is in the Lab's run list with its own topology. An experiment is one
JSON file under data/experiments. A run that waits for the user outlives the server: the experiment takes its
result when the case is finished, whenever that is.
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from pathlib import Path
from typing import Any

from ..core.util import new_id, now_iso, write_json
from ..core import stages
from ..core.pipeline import load_catalog, load_profile, resolve
from ..core.stages import SECTIONS, Section
from .errors import ServiceError
from . import evaluation
from .datasets import DatasetStore
from .evaluation import plain as _plain
from .runner import TERMINAL, WAITING_FOR, Case, RunError, RunManager

MAX_VARIANTS = 12  # combinations in one launch
MAX_RUNS = 60  # runs in one launch
DEFAULT_COST_CAP_USD = 5.0  # what all runs of an experiment may cost together
MAX_COST_CAP_USD = 100.0
LIMITS = {"maxVariants": MAX_VARIANTS, "maxRuns": MAX_RUNS, "costCapUsdDefault": DEFAULT_COST_CAP_USD, "costCapUsdMax": MAX_COST_CAP_USD}
SETTLED = ("completed", "failed", "skipped")
PAUSE_NOTES = {
    "budget": "이 실행의 비용 상한에 도달해 멈췄습니다. 서비스 화면에서 상한을 올리고 계속할 수 있습니다.",
    "restart": "서버가 다시 시작되어 멈췄습니다. 서비스 화면에서 계속할 수 있습니다.",
    "user": "실행을 멈췄습니다. 서비스 화면에서 계속할 수 있습니다.",
}


class ExperimentError(ServiceError):
    pass


def _chosen(profile: Any, slot_id: str) -> str:
    """The catalog candidate a profile has in a part (a scripted stand-in remembers the one it replaces)."""
    candidate = profile.part(slot_id).candidate
    return candidate.ref or candidate.id if candidate.adapter == "fixture" else candidate.id


def _jaccard(a: set[str], b: set[str]) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def _key(overrides: dict[str, str], params: dict[str, float]) -> str:
    """What identifies a combination inside an experiment: its differences from the base profile."""
    parts = [f"{slot}={candidate}" for slot, candidate in sorted(overrides.items())] + [f"{param}={float(value):g}" for param, value in sorted(params.items())]
    return "|".join(parts) or "base"


def _mean(values: list[float], digits: int = 3) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def diverge(a: Any, b: Any) -> int:
    """The index of the first section in which two resolved profiles differ in a part or a value
    (the number of sections when they differ in none)."""
    for index, section in enumerate(SECTIONS):
        if any(_chosen(a, slot) != _chosen(b, slot) for slot in section.slots) or any(float(a.params[param]) != float(b.params[param]) for param in section.params):
            return index
    return len(SECTIONS)


# ---------------------------------------------------------------------------------------------------------
# what a run produced


def intervals(events: list[dict[str, Any]]) -> tuple[dict[str, list[tuple[int, int]]], list[tuple[int, int]]]:
    """When each node ran and when the run waited for the user, in milliseconds of the run's clock."""
    started: dict[tuple[str, str], int] = {}
    spans: dict[str, list[tuple[int, int]]] = {}
    asked: dict[str, int] = {}
    waits: list[tuple[int, int]] = []
    for event in events:
        kind = event["kind"]
        if kind == "node.start":
            started[(event["node"], str(event.get("task")))] = event["t"]
        elif kind == "node.end":
            begin = started.pop((event["node"], str(event.get("task"))), None)
            if begin is not None:
                spans.setdefault(event["node"], []).append((begin, event["t"]))
        elif kind == "interrupt":
            asked[str(event.get("id"))] = event["t"]
        elif kind == "resume":
            begin = asked.pop(str(event.get("id")), None)
            if begin is not None:
                waits.append((begin, event["t"]))
    if events:
        waits += [(begin, events[-1]["t"]) for begin in asked.values()]  # still waiting
    return spans, waits


def node_times(events: list[dict[str, Any]]) -> dict[str, float]:
    """Seconds each node worked: from its start to its end, without the time it waited for the user."""
    spans, waits = intervals(events)
    seconds: dict[str, float] = {}
    for node, runs in spans.items():
        worked = 0
        for a, b in runs:
            worked += (b - a) - sum(max(0, min(b, y) - max(a, x)) for x, y in waits)
        seconds[node] = max(0.0, worked / 1000)
    return seconds


def step_rows(case: Case, sections: list[Section], labels: dict[str, str]) -> list[dict[str, Any]]:
    """Time and cost of each step of the sections, and whether it took a stored result."""
    events = list(case.events)
    seconds = node_times(events)
    calls = [e for e in events if e["kind"] == "llm"]
    reused = {e["writer"]: e for e in events if e["kind"] == "state" and e.get("channel") == "reuse" and e.get("step") != "checkpoint"}
    rows = []
    for section in sections:
        for node in section.steps:
            if node not in seconds and node not in reused:
                continue
            cost = sum(float(e.get("costUsd") or 0) for e in calls if e["node"] == node or e["node"].startswith(node + "/"))
            hit = reused.get(node)
            rows.append({"node": node, "section": section.id, "label": labels.get(node, node), "sec": round(seconds.get(node, 0.0), 2), "costUsd": round(cost, 5), "reused": hit is not None,
                         "savedSec": round(float(hit.get("savedSec") or 0), 2) if hit else 0.0, "savedCostUsd": round(float(hit.get("savedCostUsd") or 0), 5) if hit else 0.0})  # fmt: skip
    return rows


def run_metrics(runs: RunManager, case: Case, sections: list[Section], labels: dict[str, str]) -> dict[str, Any]:
    """Numbers and results of one run, restricted to the sections of the experiment. Times are working time:
    what the run waited for the user is reported apart (`waitSec`)."""
    events = list(case.events)
    seconds = node_times(events)
    _, waits = intervals(events)
    waited = sum(b - a for a, b in waits) / 1000
    reused = {e["writer"] for e in events if e["kind"] == "state" and e.get("channel") == "reuse" and e.get("step") != "checkpoint"}
    fork = next((e for e in events if e["kind"] == "state" and e.get("channel") == "reuse" and e.get("step") == "checkpoint"), None)
    steps = step_rows(case, sections, labels)
    inside = [node for section in sections for node in section.nodes]
    ranged = {step["node"] for step in steps}
    calls = [e for e in events if e["kind"] == "llm" and any(e["node"] == node or e["node"].startswith(node + "/") for node in inside)]  # the range's own calls
    out: dict[str, Any] = {
        "status": case.status,
        "elapsedSec": round(max(0.0, (events[-1]["t"] if events else 0) / 1000 - waited), 2),
        "waitSec": round(waited, 2),
        # time, cost and calls of the range itself; what the run spent on its way to the range is not compared
        "sectionSec": round(sum(seconds.get(node, 0.0) for node in inside), 2),
        "costUsd": round(sum(float(e.get("costUsd") or 0) for e in calls), 5),
        "runCostUsd": round(float(case.detail["costUsd"]), 5),  # the whole run, for the experiment's cost cap
        "llmCalls": len(calls),
        "toolCalls": sum(1 for e in events if e["kind"] == "tool.call"),
        "tokensIn": sum(int(e.get("tokensIn") or 0) for e in calls),
        "tokensOut": sum(int(e.get("tokensOut") or 0) for e in calls),
        # what reuse saved inside the range: stored step results taken instead of computed
        "savedSec": round(sum(step["savedSec"] for step in steps), 2),
        "savedCostUsd": round(sum(step["savedCostUsd"] for step in steps), 5),
        "steps": steps,
        # `before`: steps in front of the range that were not computed either (the run started from a checkpoint, or
        # took stored results on its way to the range); they are not part of the comparison
        "reuse": {"checkpoint": fork.get("entry") if fork else None, "steps": sorted(node for node in reused if node in ranged), "before": sorted(node for node in reused if node not in ranged)},
    }
    ids = {section.id for section in sections}
    state = runs.state(case)
    if "perception" in ids:
        out["perception"] = _perception(case)
    if "analysis" in ids:
        out["analysis"] = _analysis(state, events)
    if "plan" in ids:
        out["plan"] = _plan(state, events)
    if "review" in ids:
        out["review"] = _review(state)
    if "render" in ids:
        out["render"] = _render(state)
    return out


def _perception(case: Case) -> dict[str, Any] | None:
    file = case.dir / "understanding.json"
    if not file.exists():
        return None
    data = json.loads(file.read_text(encoding="utf-8"))
    tracks = data.get("tracks") or []
    segments = data.get("segments") or []
    described = [s.get("description") or {} for s in segments]
    return {
        "scenes": len(segments),
        "people": len(data.get("persons") or []),
        "plates": len(data.get("plates") or []),
        "faceTracks": sum(1 for t in tracks if t["kind"] == "face"),
        "bodyTracks": sum(1 for t in tracks if t["kind"] == "person"),
        "texts": [t["text"] for t in data.get("texts") or []],
        "samples": sum(len(t["points"]) for t in tracks),
        "describedScenes": sum(1 for d in described if d),
        "describedPersons": sum(1 for p in data.get("persons") or [] if p.get("attributes")),
        "sceneLabels": [str(d.get("label_ko") or "") for d in described],
        "placeTypes": [str(d.get("place_type") or "") for d in described],
        "peopleCounts": [d.get("people_count") for d in described],
        "visibleSec": {key: round(sum(b - a for a, b in ranges), 2) for key, ranges in (data.get("visible") or {}).items()},
    }


def _analysis(state: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    analyses = state.get("analyses")
    if analyses is None:
        return None
    requirements = state.get("requirements") or []
    tools = [e for e in events if e["kind"] == "tool.call"]
    cited = {c["id"] for item in (*analyses, *requirements) for c in item.get("citations", [])}
    return {
        "analyses": len(analyses),
        "applies": sum(1 for a in analyses if a["applicability"] == "applies"),
        "uncertain": sum(1 for a in analyses if a["applicability"] == "uncertain"),
        "notApplies": sum(1 for a in analyses if a["applicability"] == "not_applies"),
        "requirements": len(requirements),
        "protected": sorted({f"{target}:{r['scope']}" for r in requirements for target in r["targets"]}),
        "questions": sum(1 for q in state.get("questions") or [] if q["status"] != "removed"),
        "searches": sum(1 for e in tools if e.get("tool") == "search_law"),
        "toolCalls": len(tools),
        "citations": len(cited),
        "unresolved": len(state.get("unresolved") or []),
    }


def _plan(state: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    plan = state.get("plan")
    if not plan:
        return None
    decisions = plan["decisions"]
    return {
        "decisions": len(decisions),
        "redacted": sorted(f"{d['targetId']}:{d['scope']}" for d in decisions if d["method"] != "keep"),
        "kept": sorted(f"{d['targetId']}:{d['scope']}" for d in decisions if d["method"] == "keep"),
        "unsatisfied": len(plan.get("unsatisfied") or []),
        "attempts": max(1, sum(1 for e in events if e["kind"] == "llm" and e["node"] == "plan")),
    }


def _render(state: dict[str, Any]) -> dict[str, Any] | None:
    rows = state.get("verification")
    if rows is None:
        return None
    return {
        "rows": len(rows),
        "passed": sum(1 for r in rows if r["verdict"] == "pass"),
        "failed": [r["check"] for r in rows if r["verdict"] == "fail"],
        "outputBytes": (state.get("output") or {}).get("sizeBytes"),
        "modifiedFrames": (state.get("output") or {}).get("modifiedFrames"),
        "repairRounds": state.get("repair_rounds", 0),
    }


def _review(state: dict[str, Any]) -> dict[str, Any] | None:
    if "findings" not in state:  # the audit has not looked at the plan yet
        return None
    decisions = (state.get("plan") or {}).get("decisions") or []
    return {
        "edits": len(state.get("edits") or []),  # what the user changed in review and in the audit
        "findings": len(state.get("findings") or []),
        "auditRounds": state.get("audit_rounds", 0),
        "redacted": sorted(f"{d['targetId']}:{d['scope']}" for d in decisions if d["method"] != "keep"),
    }


def against_base(metrics: dict[str, Any], base: dict[str, Any]) -> dict[str, Any] | None:
    """How far a run's result agrees with the base run's, on the last section both produced."""
    for name, field, label in (("review", "redacted", "검토 뒤 가리기로 한 대상"), ("plan", "redacted", "가리기로 한 대상"), ("analysis", "protected", "보호 요건의 대상")):
        mine, theirs = metrics.get(name), base.get(name)
        if mine and theirs:
            a, b = set(mine[field]), set(theirs[field])
            return {"basis": label, "agreement": round(_jaccard(a, b), 3), "onlyBase": sorted(b - a), "onlyHere": sorted(a - b)}
    mine, theirs = metrics.get("perception"), base.get("perception")
    if mine and theirs:
        a, b = {_plain(t) for t in mine["texts"]}, {_plain(t) for t in theirs["texts"]}
        same = [mine[key] == theirs[key] for key in ("people", "plates", "scenes")]
        different = [name for name, ok in zip(("인물 수", "번호판 수", "장면 수"), same) if not ok]
        parts = [_jaccard(a, b), *[float(ok) for ok in same]]
        # what the VLM said of each scene: the place and how many people (free-text labels are not compared)
        if mine["scenes"] == theirs["scenes"] and mine["scenes"]:
            agree = 0
            for index, (place_a, place_b, count_a, count_b) in enumerate(zip(mine["placeTypes"], theirs["placeTypes"], mine["peopleCounts"], theirs["peopleCounts"]), start=1):
                if place_a != place_b:
                    different.append(f"장면 {index} 장소 ({place_b or '없음'} → {place_a or '없음'})")
                if count_a != count_b:
                    different.append(f"장면 {index} 사람 수 ({count_b} → {count_a})")
                agree += (place_a == place_b) + (count_a == count_b)
            parts.append(agree / (2 * mine["scenes"]))
        return {"basis": "찾은 글자 · 인물 · 번호판 · 장면 수 · 장면 설명", "agreement": round(sum(parts) / len(parts), 3), "onlyBase": sorted(t for t in theirs["texts"] if _plain(t) not in a), "onlyHere": sorted(t for t in mine["texts"] if _plain(t) not in b), "different": different}
    return None


# ---------------------------------------------------------------------------------------------------------
# experiments


class ExperimentManager:
    def __init__(self, runs: RunManager, datasets: DatasetStore, root: Path, labels: dict[str, str] | None = None, check: Any = None):
        self.runs = runs
        self.datasets = datasets
        self.root = root
        self.labels = labels or {}
        # check(profile id, overrides, params) → the issues the Lab's profile check reports for that combination
        self.check = check
        self._lock = threading.RLock()
        self._experiments: dict[str, dict[str, Any]] = {}
        self._stop: set[str] = set()
        self._workers: dict[str, threading.Thread] = {}
        self._reached: dict[tuple[str, int], bool] = {}  # (case, section index) → the case has a checkpoint in front of that section
        self._load()

    # ---------- store ----------

    def _load(self) -> None:
        for file in sorted(self.root.glob("*.json")):
            try:
                experiment = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            changed = False
            for run in experiment["runs"]:
                if run["status"] == "queued":  # the server stopped before it started; started runs are followed through their cases
                    run.update(status="skipped", error="서버가 멈춰 시작하지 못했습니다.", finishedAt=now_iso())
                    changed = True
            self._experiments[experiment["id"]] = experiment
            if changed:
                self._save(experiment)

    def _save(self, experiment: dict[str, Any]) -> None:
        with self._lock:
            self._derive(experiment)
            write_json(self.root / f"{experiment['id']}.json", experiment)

    def _touch(self, experiment: dict[str, Any]) -> None:
        experiment["updatedAt"] = now_iso()
        self._save(experiment)

    def _find(self, experiment_id: str) -> dict[str, Any]:
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise ExperimentError(404, "실험을 찾을 수 없습니다.")
        return experiment

    def _refresh(self, experiment: dict[str, Any]) -> None:
        """Bring the runs that have a case up to what their cases have done since."""
        changed = False
        for run in experiment["runs"]:
            if run["status"] in ("running", "waiting"):
                changed = self._sync(experiment, run) or changed
        if changed:
            self._touch(experiment)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            keys = ("id", "title", "createdAt", "updatedAt", "status", "from", "to", "interactive", "baseProfile", "datasets", "progress", "costUsd", "costCapUsd")
            out = []
            for experiment in sorted(self._experiments.values(), key=lambda e: e["updatedAt"], reverse=True):
                self._refresh(experiment)
                out.append({**{k: experiment.get(k) for k in keys}, "variants": len(experiment["variants"]), "items": len(experiment["items"])})
            return out

    def get(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            experiment = self._find(experiment_id)
            self._refresh(experiment)
            return json.loads(json.dumps(experiment))

    def edit(self, experiment_id: str, *, title: str | None = None, cost_cap_usd: float | None = None) -> dict[str, Any]:
        with self._lock:
            experiment = self._find(experiment_id)
            if title is not None:
                if not title.strip():
                    raise ExperimentError(422, "실험 이름을 적어 주세요.")
                experiment["title"] = title.strip()
            if cost_cap_usd is not None:
                if not 0 < cost_cap_usd <= MAX_COST_CAP_USD:
                    raise ExperimentError(422, f"비용 상한은 0보다 크고 ${MAX_COST_CAP_USD:g} 이하여야 합니다.")
                experiment["costCapUsd"] = round(float(cost_cap_usd), 2)
            self._touch(experiment)
        return self.get(experiment_id)

    def delete(self, experiment_id: str) -> None:
        with self._lock:
            experiment = self._find(experiment_id)
            self._refresh(experiment)
            if any(run["status"] in ("queued", "running") for run in experiment["runs"]):
                raise ExperimentError(409, "실행 중인 실험은 먼저 멈춰야 지울 수 있습니다.")
            del self._experiments[experiment_id]
            (self.root / f"{experiment_id}.json").unlink(missing_ok=True)  # the runs stay in the Lab's run list

    def delete_run(self, experiment_id: str, run_id: str) -> dict[str, Any]:
        """Take a run out of the comparison. Its case stays in the Lab's run list."""
        with self._lock:
            experiment = self._find(experiment_id)
            run = self._run(experiment, run_id)
            self._sync(experiment, run)
            if run["status"] in ("queued", "running"):
                raise ExperimentError(409, "실행 중이거나 차례를 기다리는 실행은 지울 수 없습니다. 실험을 멈춘 뒤 지우세요.")
            experiment["runs"].remove(run)
            used = {r["variant"] for r in experiment["runs"]}
            experiment["variants"] = [v for v in experiment["variants"] if v["id"] in used or v["base"]]
            experiment["items"] = [i for i in experiment["items"] if any(r["item"] == i["key"] for r in experiment["runs"])]
            self._touch(experiment)
        return self.get(experiment_id)

    def stop(self, experiment_id: str) -> dict[str, Any]:
        """Stop what is running or queued. A run that waits for the user stays as it is."""
        with self._lock:
            experiment = self._find(experiment_id)
            self._refresh(experiment)
            active = [run for run in experiment["runs"] if run["status"] in ("queued", "running")]
            if not active:
                raise ExperimentError(409, "실행 중인 실행이 없습니다.")
            self._stop.add(experiment_id)
            for run in active:
                if run["status"] == "running" and run.get("caseId"):
                    run["stopRequested"] = True
                    try:
                        self.runs.pause(self.runs.get(run["caseId"]))
                    except RunError:
                        pass
            self._ensure_worker(experiment_id)  # skips what is queued and clears the stop
        return self.get(experiment_id)

    def set_scores(self, experiment_id: str, run_id: str, evaluator: str, scores: list[dict[str, Any]]) -> dict[str, Any]:
        """Store scores computed outside for a run, replacing what the same evaluator stored before."""
        evaluator = evaluator.strip()
        if not evaluator:
            raise ExperimentError(422, "점수를 낸 평가기의 이름(evaluator)을 적어 주세요.")
        try:
            cleaned = [evaluation.clean(raw, evaluator) for raw in scores]
        except ValueError as error:
            raise ExperimentError(422, str(error)) from error
        with self._lock:
            experiment = self._find(experiment_id)
            run = self._run(experiment, run_id)
            if run["status"] not in ("completed", "failed"):
                raise ExperimentError(409, "끝난 실행에만 점수를 넣을 수 있습니다.")
            run["scores"] = evaluation.merge(run.get("scores") or [], cleaned, [evaluator])
            self._touch(experiment)
        return self.get(experiment_id)

    @staticmethod
    def _run(experiment: dict[str, Any], run_id: str) -> dict[str, Any]:
        run = next((r for r in experiment["runs"] if r["id"] == run_id), None)
        if run is None:
            raise ExperimentError(404, "그 실행은 이 실험에 없습니다.")
        return run

    # ---------- what can be chosen ----------

    def sections(self) -> list[dict[str, Any]]:
        """The sections with their steps and what they ask the user; each step says which parts and parameters it
        reads and which steps it follows, so the screen can tell what a change makes run again without copying the rules."""

        def step(node: str) -> dict[str, Any]:
            spec = stages.STEPS.get(node)
            return {"node": node, "label": self.labels.get(node, node), "slots": list(spec.slots) if spec else [], "params": list(spec.params) if spec else [], "after": list(spec.after) if spec else [], "stored": bool(spec and spec.stored)}

        return [{"id": s.id, "label": s.label, "description": s.description, "entry": s.entry[0], "exit": s.last, "interactions": [{"kind": kind, "label": stages.ASK_LABELS[kind]} for kind in s.asks],
                 "slots": list(s.slots), "params": list(s.params), "steps": [step(node) for node in s.steps]} for s in SECTIONS]  # fmt: skip

    def _variants(self, base_id: str, sections: list[Section], request: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The combinations a launch asks for: the product of the candidates and values chosen per part. A part
        that is not named keeps the base profile's choice, so an empty request is the base profile itself."""
        directory = self.runs.settings.pipeline_dir
        catalog = load_catalog(directory)
        spec = load_profile(directory, base_id.removesuffix("-fixture"))
        base = resolve(catalog, spec)  # the catalog's own view: a fixture run still compares the candidates that were chosen
        allowed_slots = {slot for section in sections for slot in section.slots}
        allowed_params = {param for section in sections for param in section.params}
        choices: list[tuple[str, str, list[Any]]] = []
        for slot_id, candidates in (request.get("matrix") or {}).items():
            if slot_id not in allowed_slots:
                raise ExperimentError(422, f"'{slot_id}'은(는) 고른 구간에서 쓰는 부품이 아닙니다.")
            slot = catalog.slot(slot_id)
            for candidate_id in candidates:
                candidate = slot.candidate(candidate_id)
                if candidate is None or candidate.planned:
                    raise ExperimentError(422, f"'{slot.label}'에 '{candidate_id}' 후보가 없거나 아직 고를 수 없습니다.")
            options = list(dict.fromkeys(candidates))
            if options:
                choices.append(("slot", slot_id, options))
        known = {p.id: p for p in catalog.params}
        for param_id, values in (request.get("paramMatrix") or {}).items():
            if param_id not in allowed_params or param_id not in known:
                raise ExperimentError(422, f"'{param_id}'은(는) 고른 구간의 설정값이 아닙니다.")
            options = list(dict.fromkeys(float(v) for v in values))
            if any(not known[param_id].min <= v <= known[param_id].max for v in options):
                raise ExperimentError(422, f"'{known[param_id].label}'은(는) {known[param_id].min:g}~{known[param_id].max:g} 사이여야 합니다.")
            if options:
                choices.append(("param", param_id, options))
        ranged = {node for section in sections for node in section.steps}  # only steps of the range are part of the experiment
        by_key = {variant["key"]: variant for variant in existing}
        variants: dict[str, dict[str, Any]] = {}
        for combination in itertools.product(*[options for _, _, options in choices]):
            overrides = {name: value for (kind, name, _), value in zip(choices, combination) if kind == "slot" and base.part(name).candidate.id != value}
            params = {name: value for (kind, name, _), value in zip(choices, combination) if kind == "param" and float(base.params[name]) != value}
            key = _key(overrides, params)
            if key in variants:
                continue
            if key in by_key:
                variants[key] = {**by_key[key], "problem": None}
                continue
            changed = [*overrides, *params]
            parts = []
            for name, value in overrides.items():
                slot, candidate = catalog.slot(name), catalog.slot(name).candidate(value)
                parts.append(f"{slot.label} {candidate.short or candidate.label}")
            parts += [f"{known[name].label} {value:g}" for name, value in params.items()]
            label = " · ".join(parts) or f"기준 · {spec.name}"
            profile = resolve(catalog, spec, overrides or None, params or None)
            errors = [i for i in (self.check(base_id, overrides, params) if self.check else []) if i["level"] == "error"]
            first = stages.first_changed(changed, sections)
            variants[key] = {"id": None, "key": key, "label": label, "base": not changed, "overrides": overrides, "params": params, "changed": changed, "firstChanged": first.id if first else None,
                             "recomputes": sorted(stages.affected_steps(changed) & ranged) if changed else [], "configHash": profile.config_hash, "framesLeaveDevice": profile.frames_leave_device,
                             "problem": f"'{label}' 조합은 실행할 수 없습니다: {errors[0]['text']}" if errors else None}  # fmt: skip
        return list(variants.values())

    # ---------- planning: where each run starts ----------

    def _profile(self, memo: dict[Any, Any], profile_id: str, overrides: dict[str, str], params: dict[str, float]) -> Any:
        key = (profile_id, tuple(sorted(overrides.items())), tuple(sorted(params.items())))
        if key not in memo:
            memo[key] = self.runs.resolve_profile(profile_id, overrides or None, params or None)
        return memo[key]

    def _reaches(self, case: Case, index: int) -> bool:
        """Whether the case has a checkpoint in front of section `index`."""
        key = (case.id, index)
        if key not in self._reached:
            found = self.runs.checkpoint_before(case, SECTIONS[index].entry) is not None
            if not found and (case.status not in TERMINAL or case.running):
                return False  # it may still get there: do not remember the answer
            self._reached[key] = found
        return self._reached[key]

    def _plan_run(self, experiment: dict[str, Any], item: dict[str, Any], variant: dict[str, Any], source_id: str | None, memo: dict[Any, Any]) -> dict[str, Any]:
        """Where a run of `variant` on `item` starts, and what it will ask."""
        base_id = experiment["baseProfile"]["id"]
        profile = self._profile(memo, base_id, variant["overrides"], variant["params"])
        first, last = stages.ORDER.index(experiment["from"]), stages.ORDER.index(experiment["to"])
        gate = next((index for index in range(first, last + 1) if SECTIONS[index].asks), None)  # every run starts at or in front of it
        plan: dict[str, Any] = {"item": item["key"], "itemTitle": item["title"], "variant": variant["key"], "start": "scratch", "source": None, "startSection": SECTIONS[0].id, "forkedAt": None, "candidates": [], "problem": variant.get("problem")}
        mine = {run["caseId"] for run in experiment.get("runs") or [] if run.get("caseId")}
        found: list[tuple[int, Case]] = []
        refusal = None
        for case in self.runs.list():  # newest first
            wanted = case.id == source_id
            if not item.get("sha256") or self.runs.video_hash(case) != item["sha256"]:
                refusal = "다른 영상의 실행입니다." if wanted else refusal
                continue
            try:
                theirs = self._profile(memo, case.meta["profileId"], case.meta.get("overrides") or {}, case.meta.get("params") or {})
            except Exception:  # noqa: BLE001 - a run made with a profile that no longer exists cannot be compared
                refusal = "그 실행의 프로필을 지금은 읽을 수 없습니다." if wanted else refusal
                continue
            differs = diverge(theirs, profile)
            if differs < first:
                refusal = f"'{SECTIONS[differs].label}'의 부품이 이 실험의 기준 프로필과 다릅니다. 그 차이를 비교하려면 '{SECTIONS[differs].label}'부터 구간에 넣으세요." if wanted else refusal
                continue
            start = min(differs, gate if gate is not None else len(SECTIONS))
            if start > last:
                start = first  # nothing in the range differs: the range itself is what runs again
            if start == 0:
                refusal = f"이 조합은 '{SECTIONS[0].label}'부터 달라 이어 갈 지점이 없습니다." if wanted else refusal
                continue  # the first section starts from the beginning anyway
            if self.runs.auto_answered(case) and any(section.asks for section in SECTIONS[:start]):
                refusal = "질문에 자동으로 답한 실행이라 그 답을 이어 쓰지 않습니다." if wanted else refusal
                continue
            if not self._reaches(case, start):
                refusal = f"'{SECTIONS[start].label}' 앞까지 진행한 적이 없습니다." if wanted else refusal
                continue
            found.append((start, case))
        # the latest starting point first; then a run that worked out what lies in front of it itself (not one that
        # only continued from the same point), a run of this experiment, the newest
        found.sort(key=lambda pair: (pair[0], pair[1].meta.get("forkEntry") not in SECTIONS[pair[0]].entry, pair[1].id in mine, pair[1].detail["createdAt"]), reverse=True)
        plan["candidates"] = [{"caseId": case.id, "title": case.detail["title"], "startedAt": case.detail["createdAt"], "startSection": SECTIONS[start].id} for start, case in found[:8]]
        chosen = next((pair for pair in found if pair[1].id == source_id), None) if source_id else (found[0] if found else None)
        if chosen:
            start, case = chosen
            plan.update(start="fork", source={"caseId": case.id, "title": case.detail["title"], "startedAt": case.detail["createdAt"]}, startSection=SECTIONS[start].id, forkedAt=SECTIONS[start].entry[0])
        else:
            start = 0
            asked_before = [section for section in SECTIONS[:first] if section.asks]
            if source_id:
                plan["problem"] = plan["problem"] or f"지정한 실행에서는 이어 갈 수 없습니다: {refusal or '실행을 찾을 수 없습니다.'}"
            elif asked_before:
                plan["problem"] = plan["problem"] or (
                    f"'{item['title']}'에는 '{asked_before[-1].label}'까지 마친 실행이 없습니다(기준 프로필과 같은 부품으로). "
                    f"그 구간에서 받은 답을 이어 써야 하므로, '{asked_before[-1].label}'을(를) 포함한 구간 실험을 먼저 실행하거나 서비스에서 이 영상을 그 단계까지 진행하세요."
                )
        if not item.get("sendable") and variant["framesLeaveDevice"]:
            plan["problem"] = plan["problem"] or f"'{item['datasetName']}'은(는) 외부 전송 금지 데이터셋인데, 이 조합은 프레임을 외부 모델로 보냅니다."
        asks = [kind for section in SECTIONS[start : last + 1] for kind in section.asks]
        plan.update(interactive=bool(asks), asks=asks)
        return plan

    def _items(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        items = self.datasets.items([str(d) for d in request.get("datasets") or []])
        wanted = set(request.get("items") or [])
        return [item for item in items if not wanted or item["key"] in wanted]

    def _plan(self, experiment: dict[str, Any], request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """What a launch would run: the plan the screen shows, and the combinations and videos behind it."""
        sections = stages.span(experiment["from"], experiment["to"])
        variants = self._variants(experiment["baseProfile"]["id"], sections, request, experiment.get("variants") or [])
        items = self._items(request)
        source_id = str(request.get("source") or "") or None
        memo: dict[Any, Any] = {}
        runs = [self._plan_run(experiment, item, variant, source_id, memo) for item in items for variant in variants]
        problems: list[str] = []
        asks = stages.asks(sections)
        if not items:
            problems.append("실험할 영상을 하나 이상 고르세요.")
        if len(variants) > MAX_VARIANTS:
            problems.append(f"조합이 {len(variants)}개입니다. 한 번에 {MAX_VARIANTS}개까지 추가합니다. 후보를 줄이세요.")
        if len(runs) > MAX_RUNS:
            problems.append(f"실행이 {len(runs)}건입니다. 한 번에 {MAX_RUNS}건까지 추가합니다.")
        if asks and len(runs) > 1:
            names = " · ".join(dict.fromkeys(f"{section.label}의 {stages.ASK_LABELS[kind]}" for section, kind in asks))
            problems.append(f"이 구간은 사용자에게 묻습니다({names}). 답을 대신하지 않으므로 실행은 한 번에 1건(조합 1개 × 영상 1개)만 추가합니다. 지금은 조합 {len(variants)}개 × 영상 {len(items)}개입니다.")
        if source_id and len(runs) > 1:
            problems.append("이어 갈 실행은 실행 1건을 추가할 때만 지정할 수 있습니다.")
        problems += list(dict.fromkeys(run["problem"] for run in runs if run["problem"]))
        plan = {
            "from": experiment["from"],
            "to": experiment["to"],
            "sections": [section.id for section in sections],
            "interactive": bool(asks),
            "interactions": stages.interactions(sections),
            "variants": [{k: variant.get(k) for k in ("key", "id", "label", "base", "overrides", "params", "changed", "firstChanged", "recomputes", "framesLeaveDevice")} for variant in variants],
            "runs": runs,
            "problems": problems,
        }
        return plan, variants, items

    def _shell(self, request: dict[str, Any]) -> dict[str, Any]:
        """An experiment that does not exist yet, from the range and base profile of a request."""
        try:
            sections = stages.span(str(request.get("from") or ""), str(request.get("to") or request.get("from") or ""))
        except ValueError as error:
            raise ExperimentError(422, str(error) if "구간" in str(error) else "구간을 고르세요.") from error
        base_id = str(request.get("baseProfile") or self.runs.service_profile("anonymized_upload"))
        try:
            spec = load_profile(self.runs.settings.pipeline_dir, base_id.removesuffix("-fixture"))
            config_hash = self.runs.resolve_profile(base_id).config_hash
        except (OSError, KeyError) as error:
            raise ExperimentError(404, f"프로필 '{base_id}'을(를) 찾을 수 없습니다.") from error
        asks = stages.asks(sections)
        return {"from": sections[0].id, "to": sections[-1].id, "sections": [s.id for s in sections], "interactive": bool(asks), "interactions": stages.interactions(sections),
                "baseProfile": {"id": base_id, "name": spec.name, "configHash": config_hash}, "variants": [], "items": [], "runs": []}  # fmt: skip

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        """What a launch would do, without doing it: for a new experiment, or for one that exists (`experiment`)."""
        with self._lock:
            experiment = self._find(str(request["experiment"])) if request.get("experiment") else self._shell(request)
            return self._plan(experiment, request)[0]

    # ---------- starting ----------

    def start(self, request: dict[str, Any]) -> dict[str, Any]:
        """A new experiment with its first runs."""
        with self._lock:
            shell = self._shell(request)
            sections = stages.span(shell["from"], shell["to"])
            experiment_id = new_id("sx")
            title = str(request.get("title") or "").strip() or sections[0].label + (f" → {sections[-1].label}" if len(sections) > 1 else "")
            experiment = {"id": experiment_id, "title": title, "createdAt": now_iso(), "updatedAt": now_iso(), **shell, "costCapUsd": min(float(request.get("costCapUsd") or DEFAULT_COST_CAP_USD), MAX_COST_CAP_USD)}
            self._add(experiment, request)  # refuses before anything is stored
            self._experiments[experiment_id] = experiment
            self._touch(experiment)
            self._ensure_worker(experiment_id)
        return self.get(experiment_id)

    def launch(self, experiment_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """Add runs to an experiment."""
        with self._lock:
            experiment = self._find(experiment_id)
            self._add(experiment, request)
            self._touch(experiment)
            self._ensure_worker(experiment_id)
        return self.get(experiment_id)

    def _add(self, experiment: dict[str, Any], request: dict[str, Any]) -> None:
        plan, variants, items = self._plan(experiment, request)
        if plan["problems"]:
            more = f" (외 {len(plan['problems']) - 1}건)" if len(plan["problems"]) > 1 else ""
            raise ExperimentError(422, plan["problems"][0] + more)
        ids: dict[str, str] = {}
        for variant in variants:
            if variant["id"] is None:
                taken = {v["id"] for v in experiment["variants"]}
                variant["id"] = "base" if variant["base"] else next(f"v{n}" for n in itertools.count(1) if f"v{n}" not in taken)
                experiment["variants"].append({k: v for k, v in variant.items() if k != "problem"})
            ids[variant["key"]] = variant["id"]
        experiment["variants"].sort(key=lambda v: not v["base"])  # the base combination first, the others in the order they were added
        known = {item["key"] for item in experiment["items"]}
        experiment["items"] += [{k: item.get(k) for k in ("key", "dataset", "datasetName", "id", "title", "filename", "durationSec", "expected", "sendable")} for item in items if item["key"] not in known]
        number = max([int(run["id"][1:]) for run in experiment["runs"]], default=0)
        for planned in plan["runs"]:
            number += 1
            experiment["runs"].append({"id": f"r{number}", "item": planned["item"], "variant": ids[planned["variant"]], "status": "queued", "createdAt": now_iso(), "startedAt": None, "finishedAt": None, "caseId": None, "sourceCaseId": None,
                                       "sourceWanted": str(request.get("source") or "") or None, "start": None, "forkedAt": None, "startSection": None, "interactive": planned["interactive"], "autoAnswered": False, "waitingFor": None, "error": None, "metrics": None, "scores": []})  # fmt: skip

    # ---------- running ----------

    def _ensure_worker(self, experiment_id: str) -> None:
        worker = self._workers.get(experiment_id)
        if worker is None or not worker.is_alive():
            worker = threading.Thread(target=self._work, args=(experiment_id,), name=f"experiment-{experiment_id}", daemon=True)
            self._workers[experiment_id] = worker
            worker.start()

    def _work(self, experiment_id: str) -> None:
        """Start the queued runs one after another. A run that comes to wait for the user is left to the
        person (its result is taken when its case is finished) and the next one starts."""
        while True:
            with self._lock:
                experiment = self._experiments.get(experiment_id)
                run = next((r for r in experiment["runs"] if r["status"] == "queued"), None) if experiment else None
                if run is None:
                    self._stop.discard(experiment_id)
                    self._workers.pop(experiment_id, None)
                    return
                reason = "실험을 멈춤" if experiment_id in self._stop else self._over_cap(experiment)
                if reason:
                    run.update(status="skipped", error=reason, finishedAt=now_iso())
                    self._touch(experiment)
                    continue
                prepared = self._attempt(experiment, run, lambda: self._begin(experiment, run))
            if prepared is None:
                continue
            plan, make = prepared
            # making the case copies and hashes the video: outside the lock, so the experiment screens keep answering
            case = self._attempt(experiment, run, make)
            if case is None:
                continue
            with self._lock:
                if experiment_id in self._stop:  # stopped while its case was being made
                    self.runs.discard(case)
                    run.update(status="skipped", error="실험을 멈춤", finishedAt=now_iso())
                else:
                    self._attempt(experiment, run, lambda: self._started(run, plan, case))
                self._touch(experiment)
            while True:
                time.sleep(0.1)
                with self._lock:
                    if not any(r is run for r in experiment["runs"]) or run["status"] != "running":
                        break
                    if self._sync(experiment, run):
                        self._touch(experiment)

    def _attempt(self, experiment: dict[str, Any], run: dict[str, Any], step: Any) -> Any:
        """Do one step of starting a run. A run that cannot start is recorded as failed (None comes back); it
        must not end the worker."""
        try:
            return step()
        except ServiceError as error:
            failure = str(error)
        except Exception as error:  # noqa: BLE001
            failure = f"{type(error).__name__}: {error}"
        with self._lock:
            run.update(status="failed", error=failure, finishedAt=now_iso())
            self._touch(experiment)
        return None

    def _over_cap(self, experiment: dict[str, Any]) -> str | None:
        spent = sum(self._spent(run) for run in experiment["runs"])
        if spent >= experiment["costCapUsd"]:
            return f"실험 비용 상한 ${experiment['costCapUsd']:.2f}에 도달했습니다(지금까지 ${spent:.2f}). 상한을 올리고 다시 추가하세요."
        return None

    def _begin(self, experiment: dict[str, Any], run: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        """Decide where a queued run starts: from the checkpoint of an earlier run when there is one to continue.
        Returns the plan and what makes the run's case (registered, not started)."""
        variant = next(v for v in experiment["variants"] if v["id"] == run["variant"])
        source = next((item for item in self.datasets.items([run["item"].split("/", 1)[0]]) if item["key"] == run["item"]), None)
        if source is None:
            raise ExperimentError(409, "이 영상이 데이터셋에서 지워졌습니다.")
        plan = self._plan_run(experiment, source, variant, run.get("sourceWanted"), {})
        if plan["problem"]:
            raise ExperimentError(409, plan["problem"])
        shared = {
            "profile_id": experiment["baseProfile"]["id"],
            "title": f"{experiment['title']} · {source['title']} · {variant['label']}",
            "stop": stages.stop_at(experiment["to"]) or None,
            "kind": "exp",
            "experiment": experiment["id"],
            "notes": {"variant": variant["label"], "interactive": plan["interactive"]},  # for the run list
        }
        overrides, params = dict(variant["overrides"]), dict(variant["params"])
        if plan["start"] == "fork":
            base, entry = self.runs.get(plan["source"]["caseId"]), stages.BY_ID[plan["startSection"]].entry
            return plan, lambda: self.runs.fork(base, entry, overrides=overrides, params=params, **shared)
        return plan, lambda: self.runs.create(Path(source["video"]), source["filename"], memo=source.get("memo", ""), overrides=overrides or None, params=params or None, sample=source["dataset"] == "sample", start=False, **shared)

    def _started(self, run: dict[str, Any], plan: dict[str, Any], case: Case) -> None:
        """Record the case of a run and start it."""
        if plan["start"] == "fork":
            run.update(sourceCaseId=plan["source"]["caseId"], forkedAt=case.meta["forkEntry"])
        run.update(status="running", caseId=case.id, start=plan["start"], startSection=plan["startSection"], interactive=plan["interactive"], startedAt=now_iso())
        self.runs.begin(case)

    def _sync(self, experiment: dict[str, Any], run: dict[str, Any]) -> bool:
        """Bring a started run up to what its case has done. Returns whether anything changed."""
        before = (run["status"], run.get("waitingFor"), run.get("error"))
        try:
            case = self.runs.get(run["caseId"])
        except RunError:
            run.update(status="failed", waitingFor=None, error="이 실행의 기록이 지워졌습니다.", finishedAt=now_iso())
            return True
        status = case.status
        if case.running and status not in TERMINAL:
            run.update(status="running", waitingFor=None, error=None)
        elif status in TERMINAL:
            self._finish(experiment, run, case)
        elif status == "PAUSED":
            reason = case.detail.get("pauseReason")
            if reason == "user" and run.pop("stopRequested", False):
                self._finish(experiment, run, case, skipped="실험을 멈춤")
            else:
                run.update(status="waiting", waitingFor="paused", error=PAUSE_NOTES.get(reason, PAUSE_NOTES["user"]))
        elif case.waiting is not None:
            run.update(status="waiting", waitingFor=WAITING_FOR.get(status, case.waiting["payload"]), error=None)
        return before != (run["status"], run.get("waitingFor"), run.get("error"))

    def _finish(self, experiment: dict[str, Any], run: dict[str, Any], case: Case, skipped: str | None = None) -> None:
        sections = stages.span(experiment["from"], experiment["to"])
        metrics = run_metrics(self.runs, case, sections, self.labels)
        if run.get("forkedAt") and run.get("sourceCaseId") and run.get("startSection"):
            # the sections of the range in front of the starting point were not run: what the continued run
            # spent on them (or would have, where it took stored results itself) is what continuing saved
            order = [section.id for section in sections]
            try:
                rows = step_rows(self.runs.get(run["sourceCaseId"]), sections, self.labels)
            except RunError:
                rows = []
            ahead = [row for row in rows if run["startSection"] in order and order.index(row["section"]) < order.index(run["startSection"])]
            metrics["reuse"]["checkpointSavedSec"] = round(sum(row["sec"] + row["savedSec"] for row in ahead), 2)
            metrics["reuse"]["checkpointSavedCostUsd"] = round(sum(row["costUsd"] + row["savedCostUsd"] for row in ahead), 5)
            metrics["savedSec"] = round(metrics["savedSec"] + metrics["reuse"]["checkpointSavedSec"], 2)
            metrics["savedCostUsd"] = round(metrics["savedCostUsd"] + metrics["reuse"]["checkpointSavedCostUsd"], 5)
        run.update(metrics=metrics, waitingFor=None, finishedAt=now_iso())
        if skipped:
            run.update(status="skipped", error=skipped)
        elif case.status == "COMPLETED":
            item = next((i for i in experiment["items"] if i["key"] == run["item"]), {})
            context = evaluation.RunContext(sections=tuple(section.id for section in sections), results=metrics, state=self.runs.state(case), item=item, directory=case.dir)
            scores, failed = evaluation.evaluate(context)
            run.update(status="completed", error=None, scores=evaluation.merge(run.get("scores") or [], scores, [e.id for e in evaluation.evaluators()]))
            if failed:
                run["evaluationErrors"] = failed
        else:
            run.update(status="failed", error=case.detail.get("error") or "실행이 실패했습니다.")

    @staticmethod
    def _spent(run: dict[str, Any]) -> float:
        """What a run cost in all, including steps in front of the experiment's range."""
        metrics = run.get("metrics") or {}
        return float(metrics.get("runCostUsd") or 0)

    # ---------- comparison ----------

    def _derive(self, experiment: dict[str, Any]) -> None:
        """Everything that follows from the runs: comparisons with the base combination, the summary, and the
        experiment's own status."""
        runs = experiment["runs"]
        base = next((v["id"] for v in experiment["variants"] if v["base"]), None)
        latest_base: dict[str, dict[str, Any]] = {}
        for run in runs:
            if run["variant"] == base and run["status"] == "completed" and run.get("metrics"):
                latest_base[run["item"]] = run["metrics"]  # runs are in the order they were added
        for run in runs:
            if run["status"] == "completed" and run.get("metrics"):
                reference = latest_base.get(run["item"])
                run["metrics"]["vsBase"] = against_base(run["metrics"], reference) if reference is not None and run["variant"] != base else None
        experiment["summary"] = [self._summary(experiment, variant) for variant in experiment["variants"]]
        experiment["datasets"] = list(dict.fromkeys(item["dataset"] for item in experiment["items"]))
        experiment["costUsd"] = round(sum(self._spent(run) for run in runs), 5)
        waiting = sum(1 for run in runs if run["status"] == "waiting")
        experiment["progress"] = {"done": sum(1 for run in runs if run["status"] in SETTLED), "total": len(runs), "waiting": waiting}
        experiment["status"] = "running" if any(run["status"] in ("queued", "running") for run in runs) else "waiting" if waiting else "idle"

    @staticmethod
    def _summary(experiment: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
        runs = [run for run in experiment["runs"] if run["variant"] == variant["id"]]
        done = [run for run in runs if run["status"] == "completed" and run.get("metrics")]
        dataset_of = {item["key"]: (item["dataset"], item["datasetName"]) for item in experiment["items"]}

        def figures(completed: list[dict[str, Any]]) -> dict[str, Any]:
            """Means over videos: each video counts once, with the mean of its completed runs."""
            cells: dict[str, list[dict[str, Any]]] = {}
            for run in completed:
                cells.setdefault(run["item"], []).append(run)

            def over_items(value: Any, digits: int = 3) -> float | None:
                per_item = [sum(values) / len(values) for values in ([v for v in (value(run["metrics"]) for run in group) if v is not None] for group in cells.values()) if values]
                return _mean(per_item, digits)

            return {
                "items": len(cells),
                "completed": len(completed),
                "sectionSecMean": over_items(lambda m: m["sectionSec"]),
                "elapsedSecMean": over_items(lambda m: m["elapsedSec"]),
                "costUsdMean": over_items(lambda m: m["costUsd"], 5),
                # what the range would have taken and cost with nothing reused: spent plus saved. Combinations
                # that reused different amounts are compared on these
                "fullSecMean": over_items(lambda m: m["sectionSec"] + m["savedSec"]),
                "fullCostUsdMean": over_items(lambda m: m["costUsd"] + m.get("savedCostUsd", 0.0), 5),
                "agreementMean": over_items(lambda m: (m.get("vsBase") or {}).get("agreement")),
                "scores": evaluation.aggregate([[score for run in group for score in run.get("scores") or []] for group in cells.values()]),
            }

        by_dataset = []
        for dataset, name in dict.fromkeys(dataset_of.get(run["item"], ("", "")) for run in runs):
            group = [run for run in done if dataset_of.get(run["item"], ("", ""))[0] == dataset]
            by_dataset.append({"dataset": dataset, "datasetName": name, **{k: v for k, v in figures(group).items() if k != "elapsedSecMean"}})
        metrics = [run["metrics"] for run in done]
        return {
            "variant": variant["id"],
            "runs": len(runs),
            "failed": sum(1 for run in runs if run["status"] == "failed"),
            "skipped": sum(1 for run in runs if run["status"] == "skipped"),
            "waiting": sum(1 for run in runs if run["status"] == "waiting"),
            **figures(done),
            # totals over its completed runs
            "costUsdTotal": round(sum(m["costUsd"] for m in metrics), 5),
            "savedSecTotal": round(sum(m["savedSec"] for m in metrics), 2),
            "savedCostUsdTotal": round(sum(m["savedCostUsd"] for m in metrics), 5),
            "llmCallsTotal": sum(m["llmCalls"] for m in metrics),
            "byDataset": by_dataset,
        }
