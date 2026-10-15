"""Durable node execution. Graph state contains references, never video frames.

Celery remains the sole executor; this module only controls continuation. Public
run/index/document records remain authoritative business results.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from privframe.config import fingerprint
from privframe.domain.models import ExecutionRecord, NodeRecord, now

GRAPH_VERSION = "privframe-workflows-v1"
GRAPH_NODES = {
    "analyze": ("analyze_video", "group_identities"),
    "decide": ("build_query", "retrieve", "select_evidence", "llm_decision", "validate_output"),
    "render": ("render_video", "report"),
    "extract": ("extract_document",),
    "index": ("chunk_documents", "embed_index"),
    "retrieval": ("build_query", "retrieve", "select_evidence"),
    "decision": ("llm_decision", "validate_output"),
}


class GraphState(TypedDict):
    outputs: dict[str, str]
    pause_at: str | None


class SupersededExecution(RuntimeError):
    pass


class ExecutionPaused(Exception):
    pass


class GraphRunner:
    def __init__(self, repository, artifacts):
        self.repository, self.artifacts = repository, artifacts

    def create(self, owner_id, owner_kind, graph, *, input_artifact=None, stop_after=None):
        if graph not in GRAPH_NODES or (stop_after and stop_after not in GRAPH_NODES[graph]):
            raise ValueError("Unknown graph or stop node")
        return self.repository.insert("execution", ExecutionRecord(
            id=uuid4().hex, owner_id=owner_id, owner_kind=owner_kind, graph=graph,
            graph_version=GRAPH_VERSION, input_artifact=input_artifact, stop_after=stop_after))

    def read_json(self, path):
        return json.loads(self.artifacts.resolve(path).read_text())

    def write_json(self, path, value):
        self.artifacts.put_bytes(path, json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
        return path

    def _build(self, names, operation):
        builder = StateGraph(GraphState)
        previous = START
        for name in names:
            builder.add_node(name, lambda state, node=name: operation(node, state))
            builder.add_edge(previous, name)
            # A separate gate ensures resume never repeats the external call.
            gate_name = f"after_{name}"
            def gate(state, node=name):
                if state.get("pause_at") == node:
                    interrupt({"after_node": node, "outputs": state["outputs"]})
                return {"pause_at": None}
            builder.add_node(gate_name, gate)
            builder.add_edge(name, gate_name)
            previous = gate_name
        builder.add_edge(previous, END)
        return builder

    def diagram(self, graph):
        if graph not in GRAPH_NODES:
            raise ValueError("Unknown graph")
        return self._build(GRAPH_NODES[graph], lambda name, state: state).compile().get_graph().draw_mermaid()

    def run(self, execution_id, attempt_id, operations: dict[str, Callable], inputs, guard=lambda: True):
        execution = self.repository.get("execution", execution_id, ExecutionRecord)
        if execution.graph_version != GRAPH_VERSION:
            raise ValueError("Graph version changed; create a new execution from saved inputs")
        if execution.status == "COMPLETED":
            return execution.output_artifacts
        if tuple(operations) != GRAPH_NODES[execution.graph]:
            raise ValueError("Graph operations do not match its versioned definition")

        def active():
            if not guard():
                raise SupersededExecution("A newer transport attempt owns this execution")
            current = self.repository.get("execution", execution_id, ExecutionRecord)
            if current.attempt_id != attempt_id:
                raise SupersededExecution("Execution attempt was superseded")

        def begin(value):
            value.attempt_id, value.status, value.error = attempt_id, "RUNNING", None
            value.updated_at = now()
        if not guard():
            raise SupersededExecution("Stage attempt is no longer active")
        self.repository.mutate("execution", execution_id, ExecutionRecord, begin)

        def operation(name, state):
            active()
            current = self.repository.get("execution", execution_id, ExecutionRecord)
            upstream = {key: fingerprint(self.read_json(path)) for key, path in state["outputs"].items()}
            input_fp = fingerprint({"version": GRAPH_VERSION, "node": name, "inputs": inputs, "upstream": upstream})
            cached = next((record for record in reversed(current.nodes)
                           if record.name == name and record.status == "COMPLETED"
                           and record.input_fingerprint == input_fp and record.output_artifact
                           and self.artifacts.resolve(record.output_artifact).is_file()
                           and fingerprint(self.read_json(record.output_artifact)) == record.output_fingerprint), None)
            if cached:
                path = cached.output_artifact
            else:
                started = time.monotonic()
                record = NodeRecord(name=name, attempt=1 + sum(n.name == name for n in current.nodes),
                                    status="RUNNING", input_fingerprint=input_fp)
                def start(value):
                    value.nodes.append(record)
                    value.current_node, value.updated_at = name, now()
                self.repository.mutate("execution", execution_id, ExecutionRecord, start)
                try:
                    result = operations[name](dict(state["outputs"]))
                    active()
                    path = f"executions/{execution_id}/{name}/{input_fp}.json"
                    self.write_json(path, result)
                    record.status, record.output_artifact = "COMPLETED", path
                    record.output_fingerprint = fingerprint(result)
                    record.summary = result.get("summary", {})
                    record.usage = result.get("usage", {})
                except SupersededExecution:
                    raise
                except Exception as error:
                    active()
                    record.status, record.error, record.error_type = "FAILED", str(error), type(error).__name__
                    record.retryable = not isinstance(error, (ValueError, KeyError))
                    raise
                finally:
                    record.ended_at = now()
                    record.elapsed_seconds = time.monotonic() - started
                    # A stale worker must not update public records or checkpoints.
                    active()
                    def finish(value):
                        value.nodes[-1] = record
                        value.updated_at = now()
                        if record.status == "COMPLETED":
                            value.output_artifacts[name] = record.output_artifact
                    self.repository.mutate("execution", execution_id, ExecutionRecord, finish)
            active()
            latest = self.repository.get("execution", execution_id, ExecutionRecord)
            pause = latest.pause_requested or latest.stop_after == name
            return {"outputs": {**state["outputs"], name: path}, "pause_at": name if pause else None}

        path = self.artifacts.root / "checkpoints.sqlite3"
        try:
            with sqlite3.connect(path, check_same_thread=False) as connection:
                saver = SqliteSaver(connection)
                graph = self._build(tuple(operations), operation).compile(checkpointer=saver)
                config = {"configurable": {"thread_id": execution_id}}
                saved = graph.get_state(config)
                if saved.next:
                    pending_interrupt = any(task.interrupts for task in saved.tasks)
                    payload = Command(resume=True) if pending_interrupt else None
                else:
                    payload = {"outputs": {}, "pause_at": None}
                result = graph.invoke(payload, config)
                active()
                paused = bool(result.get("__interrupt__"))
                def completed(value):
                    value.status = "PAUSED" if paused else "COMPLETED"
                    value.output_artifacts = result["outputs"]
                    value.updated_at = now()
                self.repository.mutate("execution", execution_id, ExecutionRecord, completed)
                if paused:
                    raise ExecutionPaused()
                return result["outputs"]
        except (ExecutionPaused, SupersededExecution):
            raise
        except Exception as error:
            active()
            message = str(error)
            def failed(value):
                value.status, value.error, value.updated_at = "FAILED", message, now()
            self.repository.mutate("execution", execution_id, ExecutionRecord, failed)
            raise
