"""LangGraph stream parts → the Lab's normalized run events (ADR-0008, docs/design/10-ui.md §10.5).

Feed every `(namespace, mode, payload)` from
`graph.stream(..., stream_mode=["tasks", "checkpoints", "updates", "custom"], subgraphs=True)` to `feed`.
Node ids are slash paths (subgraph namespaces + node name), so a create_agent model turn inside the
`legal_analyst` node is `legal_analyst/model` and each tool call task is `legal_analyst/<tool name>`,
matching the topology the Lab draws.

Nodes speak to the wrapper through custom events (`privframe.core.runtime.emit`): `activity`, `budget`,
`validator`, `llm`, `state`, explicit `node.start`/`node.end`/`edge` for steps that are not graph nodes of
their own (the citation validator lives in an agent middleware), and `detail`, which is not a Lab event but a
patch of the case snapshot handed to `on_detail`.
"""
import json
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from .llm import usage

CUSTOM_KINDS = {"budget", "validator", "activity", "llm", "state", "message", "node.start", "node.end", "edge"}
AGENT_NODES = {"model", "tools"}
# a question interrupt happens inside the ask_user tool; the Lab shows the wait on its own human node
HUMAN_NODE = {"questions": "await_answers"}


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class RunEventNormalizer:
    def __init__(
        self,
        prices: dict[str, tuple[float, float]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        *,
        agents: dict[str, tuple[str, Callable[[int, int, int, int], float]]] | None = None,
        transitions: dict[str, list[tuple[str, str]]] | None = None,
        joins: set[str] | None = None,
        auto_state: bool = True,
        sink: Callable[[dict[str, Any]], None] | None = None,
        on_detail: Callable[[dict[str, Any]], None] | None = None,
        on_agent_cost: Callable[[float], None] | None = None,
        seq0: int = 0,
        t0_ms: int = 0,
    ):
        """`prices`: model name → (USD per 1M input, per 1M output), used when `agents` has no entry.
        `agents`: agent node → (model label, cost function of (input, output, cache-read, cache-write tokens)).
        `transitions`: node → [(predecessor node, edge id)] from the drawn topology; when given, the edge
        from the predecessor that finished most recently is emitted (for the nodes in `joins`, every
        predecessor that finished since the node last started, so a parallel join lights up all its edges).
        Without it the edge comes from the node that finished last in the same graph.
        `seq0`/`t0_ms` continue an earlier event log (a run resumed after a restart)."""
        self.prices = prices or {}
        self.agents = agents or {}
        self.transitions = transitions
        self.joins = joins or set()
        self.auto_state = auto_state
        self.sink = sink
        self.on_detail = on_detail
        self.on_agent_cost = on_agent_cost
        self.events: list[dict[str, Any]] = []
        self._seq = seq0
        self._clock = clock
        self._t0 = clock() - t0_ms / 1000
        self._nodes: dict[str, str] = {}  # task id → node id
        self._running: dict[str, str] = {}  # prefix → node currently running there
        self._last_done: dict[str, str] = {}  # prefix → last finished node there (edge source)
        self._done_at: dict[str, int] = {"__start__": 0}  # node → seq of its last completion
        self._started_at: dict[str, int] = {}  # node → seq of its last start
        self._interrupted: dict[str, str] = {}  # interrupt id → node
        self._seen_interrupts: set[str] = set()
        self._ended: set[str] = set()  # finished task ids
        self._cost: dict[str, float] = {}  # node → model cost since it started
        self._checkpoints: set[str] = set()
        self._replayed: set[str] = set()  # finished tasks a resume reported again
        self._tool_results: set[str] = set()  # tool call ids whose result was emitted

    # ---------- events from the run wrapper ----------

    def emit(self, kind: str, **body: Any) -> dict[str, Any]:
        self._seq += 1
        event = {"seq": self._seq, "t": round((self._clock() - self._t0) * 1000), "kind": kind, **body}
        self.events.append(event)
        if self.sink:
            self.sink(event)
        return event

    def run_start(self, title: str, profile: str, config_hash: str, models: dict[str, str], **extra: Any) -> None:
        self.emit("run.start", title=title, profile=profile, configHash=config_hash, models=models, **extra)

    def resume(self, interrupt_id: str, summary: str, auto: bool = False) -> None:
        node = self._interrupted.pop(interrupt_id, "")
        self.emit("resume", node=node, id=interrupt_id, summary=summary, auto=auto)
        if node in HUMAN_NODE.values():
            self.emit("node.end", node=node, task=f"{node}#{interrupt_id}", result="ok", costUsd=0, writes=["facts"])
            self.emit("edge", edge=f"legal_analyst/ask_user->{node}", dir="back")

    def interrupted_node(self, interrupt_id: str) -> str | None:
        return self._interrupted.get(interrupt_id)

    def pending_interrupt(self, interrupt_id: str, node: str) -> None:
        """Re-register an interrupt that was raised before this normalizer existed (after a restart)."""
        self._interrupted[interrupt_id] = node
        self._seen_interrupts.add(interrupt_id)

    # ---------- stream parts ----------

    def feed(self, namespace: tuple[str, ...], mode: str, payload: Any) -> None:
        prefix = "/".join(part.split(":")[0] for part in namespace)
        if mode == "tasks":
            self._task(prefix, payload)
        elif mode == "updates":
            self._updates(prefix, payload)
        elif mode == "checkpoints" and not namespace:
            configurable = payload.get("config", {}).get("configurable", {})
            if configurable.get("checkpoint_id", "") in self._checkpoints:
                return  # a resumed run first reports the checkpoint it continues from
            self._checkpoints.add(configurable.get("checkpoint_id", ""))
            self.emit(
                "checkpoint",
                id=configurable.get("checkpoint_id", ""),
                step=payload.get("metadata", {}).get("step", 0),
                next=list(payload.get("next") or []),
            )
        elif mode == "custom" and isinstance(payload, dict):
            kind = payload.get("kind")
            body = {k: v for k, v in payload.items() if k != "kind"}
            if kind == "detail":
                if self.on_detail:
                    self.on_detail(body.get("patch") or {})
            elif kind in CUSTOM_KINDS:
                if kind == "llm":
                    self._cost[body.get("node", "")] = self._cost.get(body.get("node", ""), 0.0) + float(body.get("costUsd") or 0)
                self.emit(kind, **body)

    def _node_id(self, prefix: str, name: str, task_input: Any) -> str:
        if name == "tools" and isinstance(task_input, list) and task_input and isinstance(task_input[0], dict):
            name = str(task_input[0].get("name", name))  # one task per tool call (create_agent sends each call)
        return f"{prefix}/{name}" if prefix else name

    def _task(self, prefix: str, task: dict[str, Any]) -> None:
        if "." in task["name"]:
            return  # agent middleware hooks (validate.before_model …) are not nodes of the drawn topology
        if "input" in task:  # task started
            if task["id"] in self._nodes:
                # after a resume the stream reports the interrupted step's tasks again: the paused one continues,
                # and the ones that had already finished are replayed from their saved results, not run again
                if task["id"] in self._ended:
                    self._replayed.add(task["id"])
                return
            node = self._node_id(prefix, task["name"], task["input"])
            self._nodes[task["id"]] = node
            self._running[prefix] = node
            agent_model = f"{prefix}/model" if prefix else "model"
            if task["name"] == "tools":
                self.emit("edge", edge=f"{agent_model}->{node}", dir="fwd")
            elif self.transitions is not None:
                started = self._started_at.get(node, -1)
                ready = [(self._done_at[source], edge) for source, edge in self.transitions.get(node, []) if self._done_at.get(source, -1) > started]
                for _, edge in sorted(ready) if node in self.joins else sorted(ready)[-1:]:
                    self.emit("edge", edge=edge, dir="fwd")
            else:
                source = self._last_done.get(prefix) or self._last_done.get(prefix.rpartition("/")[0]) or "__start__"
                if not (task["name"] == "model" and source.startswith(f"{prefix}/")):
                    self.emit("edge", edge=f"{source}->{node}", dir="fwd")
            if task["name"] == "model":
                for message in (task["input"] or {}).get("messages", [])[-1:]:
                    if isinstance(message, (HumanMessage, SystemMessage)):
                        role = "user" if isinstance(message, HumanMessage) else "system"
                        self.emit("message", agent=prefix, role=role, text=_short(message.content, 600))
            self._cost[node] = 0.0
            self._started_at[node] = self.emit("node.start", node=node, task=task["id"])["seq"]
            return
        node = self._nodes.get(task["id"], self._node_id(prefix, task["name"], None))
        if task.get("interrupts"):
            return  # paused; the node ends when the run resumes
        if task["id"] in self._replayed:
            self._replayed.discard(task["id"])
            return
        self._ended.add(task["id"])
        result = task.get("result")
        writes = sorted(result.keys()) if isinstance(result, dict) else []
        cost = round(self._cost.pop(node, 0.0), 6)
        event = self.emit("node.end", node=node, task=task["id"], result="error" if task.get("error") else "ok", costUsd=cost, writes=writes)
        self._done_at[node] = event["seq"]
        if task["name"] == "tools":
            self.emit("edge", edge=f"{(prefix + '/') if prefix else ''}model->{node}", dir="back")
        self._last_done[prefix] = node
        if self._running.get(prefix) == node:
            del self._running[prefix]

    def _updates(self, prefix: str, update: dict[str, Any]) -> None:
        for name, value in update.items():
            if name == "__interrupt__":
                for item in value:
                    if item.id in self._seen_interrupts:
                        continue  # an interrupt also surfaces in every enclosing graph; keep the innermost node
                    self._seen_interrupts.add(item.id)
                    node = self._running.get(prefix, prefix or "graph")
                    payload = item.value if isinstance(item.value, dict) else {"summary": item.value}
                    kind = payload.get("payload", "questions")
                    human = HUMAN_NODE.get(kind) if node.endswith("/ask_user") else None
                    if human:
                        self.emit("edge", edge=f"{node}->{human}", dir="fwd")
                        self.emit("node.start", node=human, task=f"{human}#{item.id}")
                        node = human
                    self._interrupted[item.id] = node
                    self.emit("interrupt", node=node, id=item.id, payload=kind, summary=_short(payload.get("summary") or payload.get("question") or payload))
                continue
            if "." in name or not isinstance(value, dict):
                continue
            messages = value.get("messages")
            if name in AGENT_NODES and isinstance(messages, list):
                for message in messages:
                    self._message(prefix, message)
                continue
            if not self.auto_state:
                continue  # nodes describe their own writes with `state` events
            writer = f"{prefix}/{name}" if prefix else name
            for channel, content in value.items():
                self.emit("state", channel=channel, writer=writer, summary=_short(content))

    def _message(self, agent: str, message: BaseMessage) -> None:
        if isinstance(message, AIMessage):
            if message.usage_metadata:
                tokens_in, tokens_out, cached, written = usage(message)
                billed = message.response_metadata.get("cost")  # OpenRouter reports what the call actually cost
                if agent in self.agents:
                    model, price = self.agents[agent]
                    cost = float(billed) if isinstance(billed, (int, float)) else price(tokens_in, tokens_out, cached, written)
                else:
                    model = message.response_metadata.get("model_name") or message.response_metadata.get("model") or ""
                    price_in, price_out = self.prices.get(model, (0.0, 0.0))
                    cost = ((tokens_in - cached) * price_in + cached * price_in * 0.1 + tokens_out * price_out) / 1_000_000
                node = f"{agent}/model"
                self._cost[node] = self._cost.get(node, 0.0) + cost
                if self.on_agent_cost:
                    self.on_agent_cost(cost)
                self.emit("llm", node=node, agent=agent, model=model, tokensIn=tokens_in, cachedIn=cached, tokensOut=tokens_out, costUsd=cost, ms=0)
            if message.text:  # a reply can be thinking and tool calls only
                self.emit("message", agent=agent, role="assistant", text=_short(message.text, 600))
            for call in message.tool_calls:
                self.emit("tool.call", agent=agent, tool=call["name"], call=call.get("id") or "", args=_short(call.get("args", {}), 300))
        elif isinstance(message, ToolMessage):
            if message.tool_call_id in self._tool_results:
                return  # reported again by a resume
            self._tool_results.add(message.tool_call_id)
            ok = getattr(message, "status", "success") != "error"
            self.emit("tool.result", agent=agent, tool=message.name or "", call=message.tool_call_id, ok=ok, summary=_short(message.content, 240))
            self.emit("message", agent=agent, role="tool", text=_short(message.content, 600), tool=message.name or "")
        elif isinstance(message, HumanMessage):  # validator feedback injected between model turns
            self.emit("message", agent=agent, role="user", text=_short(message.content, 600))
