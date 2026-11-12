"""Runs case graphs for the service: one LangGraph thread per case, executed in a worker thread.

A run streams until it finishes, fails, pauses, or reaches an interrupt (a question for the user). The graph
state is checkpointed in SQLite, the normalized run events are appended to `events.jsonl`, and the snapshot
the service screens read is kept in `case.json`, so a case survives a server restart and continues from its
last checkpoint. Celery is not involved: M4 runs cases in-process (service separation is M6).

Two things let a run cover only part of the pipeline (experiments, FR-14): a case can end at a given point
(`stop`: after or in front of named nodes), and `fork` starts a new case from another case's checkpoint in
front of a node, with other parts chosen. Steps before that point are not run again, and what the user answered
there is part of the checkpoint; within perception, steps whose inputs did not change take their stored result
(core/artifacts.py).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..core import stages
from ..core.util import new_id, now_iso, write_atomic, write_json
from ..core.artifacts import ArtifactStore, place
from ..core.events import RunEventNormalizer
from ..core.graph import build_case_graph
from ..core.llm import cost_usd
from ..core.pipeline import ResolvedProfile, load_catalog, load_profile, resolve, service_profiles
from ..core.runtime import BudgetExceeded, CaseContext, CostMeter, Services, tracer_callbacks
from ..core.settings import CoreSettings
from ..core.video import VideoError, probe, sha256_file
from .errors import ServiceError
from . import topology

MODES = ["tasks", "checkpoints", "updates", "custom"]
STATUS_BY_NODE = {
    "ingest": "ANALYZING",
    "perceive": "ANALYZING",
    "legal_analyst": "ANALYZING_LEGAL",
    "plan": "PLANNING",
    "review_guide": "PLANNING",
    "auditor": "AUDITING",
    "render_original": "RENDERING",
    "verify_output": "RENDERING",
    "report": "RENDERING",
}
STATUS_BY_INTERRUPT = {"identity": "AWAITING_IDENTITY", "questions": "AWAITING_ANSWERS", "review": "AWAITING_REVIEW", "audit": "AWAITING_AUDIT_CONFIRM"}
TERMINAL = {"COMPLETED", "FAILED"}
# what a case shows of each part of the pipeline: a fork keeps what lies before its entry node and clears the rest
DETAIL_FROM = {
    "perceive": ["perception", "segments", "targets", "identity", "questions", "analyses", "requirements", "plan", "findings", "renderSteps", "report"],
    "await_identity": ["identity", "questions", "analyses", "requirements", "plan", "findings", "renderSteps", "report"],
    "legal_analyst": ["questions", "analyses", "requirements", "plan", "findings", "renderSteps", "report"],
    "plan": ["plan", "findings", "renderSteps", "report"],
    "await_review": ["findings", "renderSteps", "report"],
    "auditor": ["findings", "renderSteps", "report"],
    "render_original": ["renderSteps", "report"],
}
WAITING_FOR = {"AWAITING_IDENTITY": "identity", "AWAITING_ANSWERS": "questions", "AWAITING_REVIEW": "review", "AWAITING_AUDIT_CONFIRM": "audit", "PAUSED": "paused"}
EMPTY = {"perception": None, "identity": None, "plan": None, "report": None}  # the rest are lists
ROLE_SLOTS = {"analyst": "llm.analyst", "planner": "llm.planner", "review_guide": "llm.review_guide", "auditor": "llm.auditor", "report": "llm.report", "vlm": "vlm.scene", "vlm_person": "vlm.person", "inspect": "vlm.inspect", "ocr": "perception.ocr"}


class RunError(ServiceError):
    pass


def video_contract(meta: Any, sample: bool = False) -> dict[str, Any]:
    """VideoMeta → the web contract's VideoMeta."""
    if meta.vfr:
        fps = "가변"
    else:
        num, _, den = meta.avg_frame_rate.partition("/")
        fps = f"{float(num) / float(den or 1):.2f}".rstrip("0").rstrip(".") if num and float(den or 1) else meta.avg_frame_rate
    return {
        "filename": meta.filename,
        "sizeBytes": meta.size_bytes,
        "durationSec": round(meta.duration_s, 2),
        "width": meta.display_width,
        "height": meta.display_height,
        "fps": fps,
        "codec": f"{meta.codec.upper()} {meta.profile}".strip(),
        "audio": meta.audio,
        "frameCount": meta.frame_count,
        "sample": sample,
    }


def components(profile: ResolvedProfile) -> list[dict[str, Any]]:
    out = []
    for slot_id, part in profile.parts.items():
        candidate = part.candidate
        item = {"slot": slot_id, "stage": part.slot.stage, "slotLabel": part.slot.label, "candidate": candidate.id, "label": candidate.label, "short": candidate.short or candidate.label, "kind": part.kind, "usedBy": list(part.slot.used_by)}
        if candidate.tier:
            item["tier"] = candidate.tier
        out.append(item)
    return out


def _read_events(log: Path) -> list[dict[str, Any]]:
    """The events of a case's log. A last line cut off when the server stopped is dropped, and the file is
    written again without it so the next event starts on a line of its own."""
    if not log.exists():
        return []
    events: list[dict[str, Any]] = []
    lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            break
    if len(events) != len(lines):
        write_atomic(log, "".join(json.dumps(event, ensure_ascii=False, default=str) + "\n" for event in events))
    return events


class Case:
    """One case: its persisted snapshot, its event log and the worker currently running it."""

    def __init__(self, directory: Path, meta: dict[str, Any], detail: dict[str, Any]):
        self.dir = directory
        self.meta = meta
        self.detail = detail
        self.events: list[dict[str, Any]] = []
        self.waiting: dict[str, Any] | None = None
        self.lock = threading.RLock()
        self.listeners: set[Callable[[dict[str, Any]], None]] = set()
        self.thread: threading.Thread | None = None
        self.stop = False
        self.normalizer: RunEventNormalizer | None = None
        self.services: Services | None = None
        self.stats = {"llm": 0, "tools": 0, "questions": 0}

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def status(self) -> str:
        return self.detail["status"]

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def save(self) -> None:
        with self.lock:
            write_json(self.dir / "case.json", {"meta": self.meta, "detail": self.detail, "waiting": self.waiting, "stats": self.stats})

    def summary(self) -> dict[str, Any]:
        keys = ("id", "title", "createdAt", "status", "video", "upload", "mode", "costUsd", "costCapUsd", "autopilot", "forkedFrom", "stoppedAfter", "experimentId")
        return {k: self.detail[k] for k in keys if k in self.detail}

    def run_summary(self) -> dict[str, Any]:
        last = self.events[-1]["t"] if self.events else 0
        summary = {
            "id": self.id,
            "title": self.detail["title"],
            "status": self.status,
            "profile": self.meta["profileName"],
            "profileId": self.meta["profileId"],
            "startedAt": self.detail["createdAt"],
            "elapsedSec": round(last / 1000, 1),
            "costUsd": self.detail["costUsd"],
            "llmCalls": self.stats["llm"],
            "toolCalls": self.stats["tools"],
            "questions": self.stats["questions"],
            "live": self.status not in TERMINAL and self.status != "PAUSED",
            "autopilot": bool(self.detail.get("autopilot")),
            "source": "live",
            "scenario": self.meta.get("scenario") or self.detail["video"]["filename"],
            # where the run came from and how far it went, when that applies
            "forkedFrom": self.detail.get("forkedFrom"),
            "forkedAt": self.meta.get("forkEntry"),
            "stoppedAfter": self.detail.get("stoppedAfter"),
            "experimentId": self.meta.get("experiment"),
            "variant": self.meta.get("variant"),  # the combination of an experiment this run is
            # an experiment run the user answers, and what it is waiting for now (a run that answers for itself never waits)
            # (cases made before the experiment recorded it: an experiment run that did not answer for itself)
            "interactive": self.meta.get("interactive", bool(self.meta.get("experiment")) and not self.meta.get("autopilot")) or None,
            "waitingFor": None if self.meta.get("autopilot") else WAITING_FOR.get(self.status),
        }
        return {key: value for key, value in summary.items() if value is not None}


class RunManager:
    def __init__(self, settings: CoreSettings | None = None, *, cases_dir: Path | None = None, fixture: Any = None, profile_resolver: Callable[[str], ResolvedProfile] | None = None, max_parallel: int = 2):
        self.settings = settings or CoreSettings()
        self.cases_dir = cases_dir or (self.settings.data_dir / "cases")
        self.cases_dir.mkdir(parents=True, exist_ok=True)
        self.fixture = fixture
        self._resolver = profile_resolver
        self._cases: dict[str, Case] = {}
        self._lock = threading.RLock()
        self._slots = threading.Semaphore(max_parallel)
        self._connection = sqlite3.connect(self.cases_dir / "checkpoints.sqlite", check_same_thread=False)
        self.saver = SqliteSaver(self._connection)
        self.graph = build_case_graph(self.saver)
        self.artifacts = ArtifactStore(self.cases_dir.parent / "artifacts")
        self._load()

    # ---------- profiles ----------

    def resolve_profile(self, profile_id: str, overrides: dict[str, str] | None = None, params: dict[str, float] | None = None) -> ResolvedProfile:
        if self._resolver and not overrides and not params:
            return self._resolver(profile_id)
        directory = self.settings.pipeline_dir
        profile = resolve(load_catalog(directory), load_profile(directory, profile_id.removesuffix("-fixture")), overrides, params)
        if self.fixture is not None:
            from ..core.fixture import as_fixture

            return as_fixture(profile)
        return profile

    def profile_of(self, case: Case) -> ResolvedProfile:
        return self.resolve_profile(case.meta["profileId"], case.meta.get("overrides") or None, case.meta.get("params") or None)

    def service_profile(self, mode: str) -> str:
        """The profile an upload from the service screens runs with: configs/pipeline/service.yaml, unless this
        server was started with PRIVFRAME_SERVICE_PROFILE (a development setting, e.g. to run on another
        provider's credits; it does not apply to "이 컴퓨터에서만 처리")."""
        pointers = service_profiles(self.settings.pipeline_dir)
        if mode == "local_only":
            return pointers["local_only"]
        return os.environ.get("PRIVFRAME_SERVICE_PROFILE") or pointers["default"]

    # ---------- registry ----------

    def _load(self) -> None:
        for file in sorted(self.cases_dir.glob("*/case.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            case = Case(file.parent, data["meta"], data["detail"])
            case.waiting = data.get("waiting")
            case.stats = data.get("stats") or case.stats
            case.events = _read_events(file.parent / "events.jsonl")
            if case.status not in TERMINAL and case.waiting is None and case.status != "PAUSED":
                # the server stopped while this case was running; its last checkpoint is intact
                case.meta["pausedFrom"] = case.status
                case.detail["status"] = "PAUSED"
                case.detail["pauseReason"] = "restart"
                case.save()
            self._cases[case.id] = case

    def get(self, case_id: str) -> Case:
        case = self._cases.get(case_id)
        if case is None:
            raise RunError(404, "해당 작업을 찾을 수 없습니다.")
        return case

    def list(self) -> list[Case]:
        return sorted(self._cases.values(), key=lambda c: c.detail["createdAt"], reverse=True)

    # ---------- creating ----------

    def create(self, source: Path, filename: str, *, memo: str = "", mode: str = "anonymized_upload", profile_id: str | None = None, overrides: dict[str, str] | None = None, params: dict[str, float] | None = None, autopilot: bool = False, autopilot_answers: dict[str, str] | None = None, title: str | None = None, sample: bool = False, move: bool = False, start: bool = True, kind: str = "case", forked_from: str | None = None, stop: dict[str, Any] | None = None, experiment: str | None = None, notes: dict[str, Any] | None = None) -> Case:
        """Register a case for a video file (copied or moved into the case directory) and start it.
        `stop` says where a run of part of the pipeline ends (core/stages.py `stop_at`); `notes` are facts the
        caller wants recorded with the case (an experiment: which combination it is, whether it asks)."""
        mode = "local_only" if mode == "local_only" else "anonymized_upload"
        profile_id = profile_id or self.service_profile(mode)
        profile = self.resolve_profile(profile_id, overrides, params)
        case_id = new_id(kind)
        directory = self.cases_dir / case_id
        directory.mkdir(parents=True)
        original = directory / f"original{Path(filename).suffix.lower() or '.mp4'}"
        try:
            (shutil.move if move else shutil.copy2)(str(source), original)
            meta, _ = probe(original, filename)
        except BaseException as error:
            shutil.rmtree(directory, ignore_errors=True)  # nothing of a refused upload stays behind
            if not isinstance(error, VideoError):
                raise
            # the tool's own message names server paths; the uploader only needs to know the file is unusable
            reason = "영상 스트림이 없는 파일입니다" if "영상 스트림이 없는" in str(error) else "영상 파일이 아니거나 손상된 파일입니다"
            raise RunError(400, f"영상을 읽을 수 없습니다: {reason}.") from error
        cap = float(profile.params.get("case.cost_cap_usd", 1.0))  # a guard against a runaway loop, not the cost target
        record = {"id": case_id, "profileId": profile.id, "profileName": profile.name, "configHash": profile.config_hash, "overrides": overrides or {}, "params": params or {}, "original": str(original), "filename": filename, "memo": memo, "mode": mode, "autopilot": autopilot, "autopilotAnswers": autopilot_answers or {}, "scenario": title, **self._stop_meta(stop), "experiment": experiment, "kind": kind, "sha256": meta.sha256, "autoAnswered": False, **(notes or {})}
        detail = {
            "id": case_id,
            "title": title or filename,
            "createdAt": now_iso(),
            "status": "QUEUED",
            "video": video_contract(meta, sample),
            "mode": mode,
            "costUsd": 0.0,
            "costCapUsd": cap,
            "autopilot": autopilot,
            "memo": memo,
            "facts": [],
            "perception": None,
            "segments": [],
            "targets": [],
            "identity": None,
            "questions": [],
            "analyses": [],
            "requirements": [],
            "plan": None,
            "findings": [],
            "renderSteps": [],
            "report": None,
            "activity": [],
            "media": {"proxy": f"/api/cases/{case_id}/media/proxy", "frame": f"/api/cases/{case_id}/frame", "geometry": f"/api/cases/{case_id}/geometry", "ready": False},
        }
        if forked_from:
            detail["forkedFrom"] = forked_from
        if experiment:
            detail["experimentId"] = experiment
        case = Case(directory, record, detail)
        with self._lock:
            self._cases[case_id] = case
        case.save()
        if start:
            self.begin(case)
        return case

    # ---------- forking ----------

    @staticmethod
    def _stop_meta(stop: dict[str, Any] | None) -> dict[str, Any]:
        stop = stop or {}
        return {"stopAfter": list(stop.get("after") or []), "stopBefore": list(stop.get("before") or []), "stopShown": stop.get("shown") or ""}

    def video_hash(self, case: Case) -> str:
        """The content hash of the case's original video (cases made before it was recorded are hashed once)."""
        if not case.meta.get("sha256"):
            original = Path(case.meta["original"])
            case.meta["sha256"] = sha256_file(original) if original.exists() else ""
            case.save()
        return case.meta["sha256"]

    @staticmethod
    def auto_answered(case: Case) -> bool:
        """Whether something the case was asked (by itself, or by the run it continued) was answered for the
        user. Cases from before this was recorded count as answered automatically when they ran unattended."""
        return bool(case.meta.get("autoAnswered", case.meta.get("autopilot")))

    def checkpoint_before(self, case: Case, nodes: str | tuple[str, ...]) -> Any:
        """The first checkpoint of the case at which one of `nodes` was about to run, or None when none ever was."""
        wanted = {nodes} if isinstance(nodes, str) else set(nodes)
        found = None
        for snapshot in self.graph.get_state_history({"configurable": {"thread_id": case.id}}):
            if wanted & set(snapshot.next):
                found = snapshot  # history is newest first: keep going to the earliest
        return found

    def fork(self, base: Case, entry: str | tuple[str, ...], *, profile_id: str, overrides: dict[str, str] | None = None, params: dict[str, float] | None = None, title: str, stop: dict[str, Any] | None = None, kind: str = "exp", experiment: str | None = None, notes: dict[str, Any] | None = None) -> Case:
        """A new case that continues `base` from its first checkpoint in front of one of the nodes `entry`.
        It is registered, not started (`begin`).

        The checkpoint is copied into a new thread (the base case is not touched) and the files the earlier
        steps wrote are brought into the new case directory. Nothing before the entry runs again, and what
        the user answered there stays answered. The new case runs with `profile_id` and exactly the given
        overrides and values, and it never answers for the user: a question that comes up from here on waits.
        """
        snapshot = self.checkpoint_before(base, entry)
        if snapshot is None:
            raise RunError(409, f"실행 {base.id}은(는) '{entry if isinstance(entry, str) else entry[0]}' 단계에 도달한 적이 없어 거기서 이어 갈 수 없습니다.")
        entry = next(node for node in snapshot.next if node in ({entry} if isinstance(entry, str) else set(entry)))
        source = self.saver.get_tuple(snapshot.config)
        if source is None:
            raise RunError(404, "체크포인트를 찾을 수 없습니다.")
        overrides, params = dict(overrides or {}), dict(params or {})
        profile = self.resolve_profile(profile_id, overrides or None, params or None)
        case_id = new_id(kind)
        directory = self.cases_dir / case_id
        directory.mkdir(parents=True)
        for file in sorted(base.dir.rglob("*")):
            relative = file.relative_to(base.dir)
            # what the steps before the entry wrote; outputs, reports and the base's own records stay behind
            if file.is_file() and relative.parts[0] not in ("output",) and relative.name not in ("case.json", "events.jsonl", "report.json", "report.html", "regions.json") and not relative.name.startswith("."):
                place(file, directory / relative)

        def moved(value: Any) -> Any:
            """Paths inside the copied state point into the new case directory."""
            if isinstance(value, str):
                return str(directory) + value[len(str(base.dir)) :] if value.startswith(str(base.dir)) else value
            if isinstance(value, dict):
                return {k: moved(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return type(value)(moved(v) for v in value)
            return value

        checkpoint = copy.deepcopy(source.checkpoint)
        checkpoint["channel_values"] = moved(checkpoint["channel_values"])
        self.saver.put({"configurable": {"thread_id": case_id, "checkpoint_ns": ""}}, checkpoint, {**source.metadata, "source": "fork"}, checkpoint["channel_versions"])

        cleared = DETAIL_FROM.get(entry, DETAIL_FROM["perceive"])
        detail = copy.deepcopy(base.detail)
        detail.update({key: copy.deepcopy(EMPTY.get(key, [])) for key in cleared})
        detail.update(
            id=case_id,
            title=title,
            createdAt=now_iso(),
            status="QUEUED",
            costUsd=0.0,
            costCapUsd=float(profile.params.get("case.cost_cap_usd", 1.0)),
            autopilot=False,
            activity=[],
            forkedFrom=base.id,
            media={"proxy": f"/api/cases/{case_id}/media/proxy", "frame": f"/api/cases/{case_id}/frame", "geometry": f"/api/cases/{case_id}/geometry", "ready": (directory / "proxy.mp4").exists()},
        )
        for key in ("error", "pauseReason", "stoppedAfter", "experimentId"):
            detail.pop(key, None)
        if experiment:
            detail["experimentId"] = experiment
        original = next(iter(sorted(directory.glob("original.*"))), Path(base.meta["original"]))
        # the answers that come with the checkpoint count as a person's unless the base answered them itself
        answered_itself = self.auto_answered(base) and stages.asks_before(entry)
        record = {**base.meta, "id": case_id, "profileId": profile.id, "profileName": profile.name, "configHash": profile.config_hash, "overrides": overrides, "params": params, "original": str(original), "autopilot": False, "autopilotAnswers": {}, **self._stop_meta(stop), "experiment": experiment, "kind": kind, "forkEntry": entry, "workedSec": 0.0, "sha256": self.video_hash(base), "autoAnswered": answered_itself}
        for key in ("pausedFrom", "variant", "interactive", "forkCheckpoint", "autopilotIdentity"):
            record.pop(key, None)
        record.update(notes or {})
        case = Case(directory, record, detail)
        case.stats = {"llm": 0, "tools": 0, "questions": 0}
        with self._lock:
            self._cases[case_id] = case
        case.save()
        return case

    def begin(self, case: Case) -> None:
        """Start a case that was registered with `start=False`."""
        self._launch(case, self._first_input(case), first=True)

    def discard(self, case: Case) -> None:
        """Take back a case that was registered with `start=False` and never started."""
        with self._lock:
            self._cases.pop(case.id, None)
        self.saver.delete_thread(case.id)  # a fork has a copied checkpoint
        shutil.rmtree(case.dir, ignore_errors=True)

    @staticmethod
    def _first_input(case: Case) -> dict[str, Any] | None:
        """What the graph is given when the case starts: the upload, or nothing for a fork (its checkpoint has it)."""
        if case.meta.get("forkEntry"):
            return None
        return {"upload": {"path": case.meta["original"], "filename": case.meta["filename"], "memo": case.meta.get("memo", ""), "mode": case.meta["mode"]}}

    # ---------- events and snapshot ----------

    def _sink(self, case: Case) -> Callable[[dict[str, Any]], None]:
        log = case.dir / "events.jsonl"

        def sink(event: dict[str, Any]) -> None:
            with case.lock:
                case.events.append(event)
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
                kind = event["kind"]
                if kind == "activity":
                    case.detail["activity"].append({"seq": event["seq"], "t": event["t"], "text": event["text"], "tone": event["tone"]})
                elif kind == "llm":
                    case.stats["llm"] += 1
                    spent = case.services.cost.spent if case.services else case.detail["costUsd"] + float(event.get("costUsd") or 0)
                    case.detail["costUsd"] = round(spent, 5)
                elif kind == "tool.call":
                    case.stats["tools"] += 1
                elif kind == "node.start" and event["node"] in STATUS_BY_NODE:
                    self._status(case, STATUS_BY_NODE[event["node"]])
                elif kind == "node.end" and event["node"] == "perceive/make_proxy":
                    case.detail["media"]["ready"] = True
                listeners = list(case.listeners)
            for listener in listeners:
                listener(event)

        return sink

    def _status(self, case: Case, status: str) -> None:
        with case.lock:
            if case.detail["status"] == status:
                return
            case.detail["status"] = status
            if status != "PAUSED":
                case.detail.pop("pauseReason", None)
        if case.normalizer:
            case.normalizer.emit("run.status", status=status)
        case.save()

    def _patch(self, case: Case) -> Callable[[dict[str, Any]], None]:
        def apply(fields: dict[str, Any]) -> None:
            with case.lock:
                case.detail.update(fields)
            case.save()

        return apply

    def subscribe(self, case: Case, since: int, listener: Callable[[dict[str, Any]], None]) -> tuple[list[dict[str, Any]], Callable[[], None]]:
        """Events after `since` plus a live subscription, taken together so nothing falls between them."""
        with case.lock:
            backlog = [e for e in case.events if e["seq"] > since]
            case.listeners.add(listener)
        return backlog, lambda: case.listeners.discard(listener)

    # ---------- running ----------

    def _services(self, case: Case) -> Services:
        profile = self.profile_of(case)
        if case.services is None or case.services.profile.config_hash != profile.config_hash:
            case.services = Services(settings=self.settings, profile=profile, cost=CostMeter(case.detail["costCapUsd"], case.detail["costUsd"]), fixture=self.fixture, worked_s=float(case.meta.get("workedSec", 0.0)))
        case.services.cost.cap_usd = case.detail["costCapUsd"]
        return case.services

    def _normalizer(self, case: Case, services: Services) -> RunEventNormalizer:
        if case.normalizer is None:
            analyst = services.part("llm.analyst")
            last = case.events[-1] if case.events else {"seq": 0, "t": 0}
            case.normalizer = RunEventNormalizer(
                agents={"legal_analyst": (analyst.candidate.label, lambda i, o, c, w: cost_usd(analyst, i, o, c, w))},
                transitions=topology.transitions(),
                joins=topology.JOINS,
                auto_state=False,
                sink=self._sink(case),
                on_detail=self._patch(case),
                on_agent_cost=services.cost.add,
                seq0=last["seq"],
                t0_ms=last["t"],
            )
            if case.waiting:
                case.normalizer.pending_interrupt(case.waiting["id"], case.waiting["node"])
        return case.normalizer

    def _launch(self, case: Case, graph_input: Any, *, first: bool = False) -> None:
        with case.lock:
            if case.running:
                raise RunError(409, "이 작업은 이미 실행 중입니다.")
            case.stop = False
            case.thread = threading.Thread(target=self._run, args=(case, graph_input, first), name=f"case-{case.id}", daemon=True)
            case.thread.start()

    def _config(self, case: Case, services: Services) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": case.id},
            "recursion_limit": 200,
            "callbacks": tracer_callbacks(self.settings),
            "run_name": f"case {case.id}",
            "tags": ["privframe", f"profile:{services.profile.id}"],
            "metadata": {"case_id": case.id, "thread_id": case.id, "profile": services.profile.id, "config_hash": services.profile.config_hash, "privacy_mode": case.meta["mode"], "title": case.detail["title"], "experiment": case.meta.get("experiment"), "forked_from": case.detail.get("forkedFrom"), "fork_entry": case.meta.get("forkEntry")},
        }

    def _run(self, case: Case, graph_input: Any, first: bool) -> None:
        with self._slots:
            services = None
            try:
                services = self._services(case)
                services.segment_started = time.monotonic()
                norm = self._normalizer(case, services)
                if first:
                    profile = services.profile
                    norm.run_start(case.detail["title"], profile.name, profile.config_hash, {role: profile.part(slot).candidate.label for role, slot in ROLE_SLOTS.items()}, profileId=profile.id, components=components(profile), costCapUsd=case.detail["costCapUsd"], forkedFrom=case.detail.get("forkedFrom"))
                    if case.meta.get("forkEntry"):
                        norm.emit("activity", text=f"실행 {case.detail['forkedFrom']}의 '{topology.node_labels().get(case.meta['forkEntry'], case.meta['forkEntry'])}' 직전 체크포인트에서 이어 갑니다. 그 앞 단계는 다시 실행하지 않고, 거기서 받은 답도 그대로 씁니다.", tone="info")
                        norm.emit("state", channel="reuse", writer="__start__", summary=f"체크포인트 분기 · {case.meta['forkEntry']} 앞까지의 결과 재사용", step="checkpoint", source=case.detail["forkedFrom"], entry=case.meta["forkEntry"])
                config = self._config(case, services)
                context = CaseContext(case_id=case.id, workdir=case.dir, services=services, artifacts=self.artifacts)
                while True:
                    for part in self.graph.stream(graph_input, config, context=context, stream_mode=MODES, subgraphs=True, durability="sync", interrupt_after=case.meta.get("stopAfter") or None, interrupt_before=case.meta.get("stopBefore") or None):
                        norm.feed(*part)
                        if case.stop:
                            self._pause(case, "user", "요청에 따라 멈췄습니다. 계속을 누르면 이어서 진행합니다.")
                            return
                    asked = self._settle(case, config)
                    if asked is None or not case.meta.get("autopilot"):
                        return
                    # a demo run: answer and go on in this worker, so nobody else can answer in between
                    graph_input = Command(resume=self._answer_itself(case, asked))
            except BudgetExceeded as error:
                self._pause(case, "budget", f"{error} 상한을 올리고 계속할 수 있습니다.")
            except Exception as error:  # noqa: BLE001 - a failed case must be reported, not crash the worker
                self._fail(case, f"{type(error).__name__}: {error}")
            finally:
                if services is not None and services.segment_started is not None:
                    services.worked_s += time.monotonic() - services.segment_started
                    services.segment_started = None
                    case.meta["workedSec"] = round(services.worked_s, 2)
                    case.save()

    def _settle(self, case: Case, config: dict[str, Any]) -> dict[str, Any] | None:
        """After the stream ends: waiting for the user (what it asks comes back), or finished."""
        snapshot = self.graph.get_state(config)
        pending = [item for task in snapshot.tasks for item in task.interrupts]
        norm = case.normalizer
        assert norm is not None
        if pending:
            item = pending[0]
            value = item.value if isinstance(item.value, dict) else {"payload": "questions", "summary": str(item.value)}
            kind = value.get("payload", "questions")
            node = norm.interrupted_node(item.id) or (snapshot.next[0] if snapshot.next else "")
            with case.lock:
                case.waiting = {"id": item.id, "payload": kind, "node": node, "summary": value.get("summary", "")}
                if kind == "questions":
                    asked = value.get("questions") or []
                    known = {q["id"] for q in [*asked, *(value.get("removed") or [])]}
                    case.detail["questions"] = [*[q for q in case.detail["questions"] if q["id"] not in known], *asked, *(value.get("removed") or [])]
                    case.stats["questions"] += len(asked)
                elif kind == "identity":
                    case.detail["identity"] = value.get("identity")
                elif kind == "audit":
                    case.detail["canReturnToReview"] = bool(value.get("canReturn"))
            wait_text = {"identity": "같은 사람인지 확인이 필요한 장면이 있습니다.", "questions": f"판단에 필요한 사실 {len(value.get('questions') or [])}가지를 여쭤봅니다.", "review": "검토할 준비가 끝났습니다.", "audit": "감사 결과를 확인해 주세요."}[kind]
            norm.emit("activity", text=wait_text, tone="wait")
            self._status(case, STATUS_BY_INTERRUPT[kind])
            return value
        if snapshot.next and (case.meta.get("stopAfter") or (set(snapshot.next) & set(case.meta.get("stopBefore") or []))):
            # a run of part of the pipeline: it was asked to end here
            with case.lock:
                case.waiting = None
                case.detail["stoppedAfter"] = case.meta["stopShown"]
            norm.emit("activity", text="요청한 구간까지 실행하고 마쳤습니다.", tone="done")
            self._status(case, "COMPLETED")
            norm.emit("run.end", status="COMPLETED")
            case.save()
            return None
        if snapshot.next:
            self._pause(case, "user", "실행이 멈췄습니다. 계속을 누르면 이어서 진행합니다.")
            return None
        values = snapshot.values
        failed = [row["check"] for row in values.get("verification") or [] if row["verdict"] == "fail"]
        with case.lock:
            case.waiting = None
            if values.get("output"):
                case.detail["media"]["output"] = f"/api/cases/{case.id}/media/output"
                case.detail["media"]["reportHtml"] = f"/api/cases/{case.id}/report.html"
            if failed:
                case.detail["error"] = "출력 검증 실패: " + ", ".join(failed)
        norm.emit("edge", edge="report->__end__", dir="fwd")
        norm.emit("activity", text="출력 검증에 실패해 완료하지 못했습니다. 보고서에서 실패한 항목을 확인하세요." if failed else "완료되었습니다. 출력 영상과 보고서를 확인하세요.", tone="warn" if failed else "done")
        self._status(case, "FAILED" if failed else "COMPLETED")
        norm.emit("run.end", status="FAILED" if failed else "COMPLETED")
        case.save()
        return None

    def _pause(self, case: Case, reason: str, text: str) -> None:
        with case.lock:
            case.detail["pauseReason"] = reason
            case.meta["pausedFrom"] = case.status
        if case.normalizer:
            case.normalizer.emit("activity", text=text, tone="warn")
        self._status(case, "PAUSED")

    def _fail(self, case: Case, message: str) -> None:
        with case.lock:
            case.detail["error"] = message
            case.waiting = None
        if case.normalizer:
            case.normalizer.emit("activity", text=f"처리 중 오류가 발생했습니다: {message}", tone="warn")
        self._status(case, "FAILED")
        if case.normalizer:
            case.normalizer.emit("run.end", status="FAILED")
        case.save()

    # ---------- user actions ----------

    def waiting_for(self, case: Case, kind: str) -> bool:
        return case.waiting is not None and case.waiting["payload"] == kind and not case.running

    def resume(self, case: Case, kind: str, value: Any, summary: str) -> None:
        """Answer the interrupt the case is waiting on and continue the run."""
        with case.lock:
            if not self.waiting_for(case, kind):
                raise RunError(409, f"지금은 {stages.ASK_LABELS[kind]} 단계가 아닙니다.")
            waiting, case.waiting = case.waiting, None
        self._answered(case, waiting, summary)
        self._launch(case, Command(resume=value))

    def _answered(self, case: Case, waiting: dict[str, Any], summary: str, *, auto: bool = False) -> None:
        """Record the answer to what the case waited for."""
        self._normalizer(case, self._services(case)).resume(waiting["id"], summary + (" (자동 응답)" if auto else ""), auto)
        if waiting["payload"] in ("identity", "questions"):
            self._status(case, "ANALYZING" if waiting["payload"] == "identity" else "ANALYZING_LEGAL")

    def pause(self, case: Case) -> None:
        if not case.running:
            raise RunError(409, "실행 중인 작업만 멈출 수 있습니다.")
        case.stop = True

    def proceed(self, case: Case, cost_cap_usd: float | None = None) -> None:
        """Continue a paused case from its last checkpoint, optionally with a higher cost cap."""
        if case.status != "PAUSED" or case.running:
            raise RunError(409, "멈춘 작업만 계속할 수 있습니다.")
        with case.lock:
            if cost_cap_usd is not None:
                case.detail["costCapUsd"] = round(float(cost_cap_usd), 2)
            reason = case.detail.get("pauseReason")
        services = self._services(case)
        if reason == "budget" and services.cost.exceeded:
            raise RunError(409, f"비용 상한(${services.cost.cap_usd:.2f})을 지금까지 쓴 금액(${services.cost.spent:.3f})보다 높게 올려야 계속할 수 있습니다.")
        norm = self._normalizer(case, services)
        norm.emit("activity", text="이어서 진행합니다.", tone="info")
        before = case.meta.pop("pausedFrom", None)
        self._status(case, before if before in STATUS_BY_NODE.values() else "ANALYZING")
        if self.saver.get_tuple({"configurable": {"thread_id": case.id}}) is None:
            # the server stopped before the case's first step was saved (it was still queued): there is nothing
            # to continue from, so it starts from the beginning
            self._launch(case, self._first_input(case), first=not any(event["kind"] == "run.start" for event in case.events))
        else:
            self._launch(case, None)

    def _answer_itself(self, case: Case, asked: dict[str, Any]) -> Any:
        """The answer a demo run gives for the user: conservative unless the run was given answers."""
        kind = asked.get("payload", "questions")
        if kind == "identity":
            value, summary = "same", "같은 사람"
        elif kind == "questions":
            given = case.meta.get("autopilotAnswers") or {}
            value = {q["id"]: given.get(q["factKey"], "unknown") for q in asked.get("questions") or []}
            summary = " · ".join(f"{k}={v}" for k, v in value.items())
        elif kind == "review":
            value, summary = {"action": "approve", "decisions": []}, "수정 없음"
        else:
            value, summary = {"resolutions": []}, "권고 모두 적용"
        with case.lock:
            waiting, case.waiting = case.waiting, None
            case.meta["autoAnswered"] = True
        self._answered(case, waiting, summary, auto=True)
        return value

    def wait(self, case: Case, timeout: float = 600) -> str:
        """Block until the case needs the user, pauses or ends (for tests and the command line)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            settled = case.status in TERMINAL or case.status == "PAUSED" or case.waiting is not None
            if settled and not case.running:
                return case.status
            time.sleep(0.05)
        raise TimeoutError(f"case {case.id} did not settle in {timeout}s (status {case.status})")

    def state(self, case: Case) -> dict[str, Any]:
        return self.graph.get_state({"configurable": {"thread_id": case.id}}).values

    def close(self) -> None:
        self._connection.close()
