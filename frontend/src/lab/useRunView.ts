// Live or replayed view of one run: folds the event stream up to a cursor and keeps a ticking clock.
import { useEffect, useMemo, useState } from "react";
import type { CaseStatus, GraphSpec, RunSummary, RunWaiting } from "../api/contract";
import { useRunStream } from "../api/events";
import { STATUS_LABEL, WAITING_FOR } from "../lib/format";
import { foldRun } from "../lib/runState";

type ViewMode = "live" | "replay";

/** what a waiting run waits for, in the words of the status it stands in */
export const WAITING_LABEL = Object.fromEntries(Object.entries(WAITING_FOR).map(([status, waiting]) => [waiting, STATUS_LABEL[status as CaseStatus]])) as Record<RunWaiting, string>;

/** what the service screen of a waiting run is opened for */
export const ANSWER_LABEL: Record<RunWaiting, string> = {
  identity: "서비스 화면에서 대상 확인",
  questions: "서비스 화면에서 질문 답변",
  review: "서비스 화면에서 계획 검토",
  audit: "서비스 화면에서 감사 권고 확인",
  paused: "서비스 화면에서 이어 가기",
};

export function waitingOf(status: CaseStatus | null | undefined): RunWaiting | null {
  return (status && WAITING_FOR[status]) || null;
}

/**
 * What a person has to do before the run goes on, or null. The server's `waitingFor` when it sends one, else
 * the status. A run that answers itself (autopilot) never waits for a person, and has no service screen to answer on.
 */
export function awaitsPerson(run: Pick<RunSummary, "status" | "waitingFor" | "autopilot">): RunWaiting | null {
  if (run.autopilot) return null;
  return run.waitingFor ?? waitingOf(run.status);
}

/** a graph node's label; its id while the graph is not loaded or does not have it */
export function nodeLabel(graph: GraphSpec | undefined, id: string): string {
  return graph?.nodes.find((n) => n.id === id)?.label ?? id;
}

export function useRunView(runId: string | undefined) {
  const stream = useRunStream(runId);
  const [mode, setMode] = useState<ViewMode>("live");
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [tick, setTick] = useState(0);
  const events = stream.events;
  const lastSeq = events[events.length - 1]?.seq ?? 0;

  useEffect(() => {
    setMode("live");
    setPlaying(false);
    setCursor(0);
  }, [runId]);

  const upto = mode === "live" ? Number.POSITIVE_INFINITY : cursor;
  const state = useMemo(() => foldRun(events, upto), [stream.version, upto, events]);

  // live clock: extrapolate the server clock between events so running timers and edge pulses move
  const ticking = mode === "live" && !state.ended && state.status !== "PAUSED" && stream.connected;
  useEffect(() => {
    if (!ticking) return;
    const id = window.setInterval(() => setTick((x) => x + 1), 200);
    return () => window.clearInterval(id);
  }, [ticking]);

  // replay playback: step through events, compressing long gaps (human waits) to keep it watchable
  useEffect(() => {
    if (mode !== "replay" || !playing) return;
    const index = events.findIndex((e) => e.seq > cursor);
    if (index === -1) {
      setPlaying(false);
      return;
    }
    const prev = events[index - 1];
    const gap = prev ? events[index].t - prev.t : 0;
    const delay = Math.max(16, Math.min(420, gap / 3));
    const id = window.setTimeout(() => setCursor(events[index].seq), delay);
    return () => window.clearTimeout(id);
  }, [mode, playing, cursor, events]);

  void tick;
  // a paused run is not doing anything: its clock stands at the last event
  const now =
    mode === "live" && !state.ended && state.status !== "PAUSED" && stream.receivedAt
      ? state.lastT + Math.max(0, performance.now() - stream.receivedAt)
      : state.lastT;

  const enterReplay = () => {
    setMode("replay");
    setCursor(lastSeq);
    setPlaying(false);
  };

  return {
    stream,
    state,
    now,
    mode,
    setMode: (m: ViewMode) => (m === "replay" ? enterReplay() : (setMode("live"), setPlaying(false))),
    cursor,
    setCursor,
    playing,
    setPlaying,
    lastSeq,
  };
}

export type RunView = ReturnType<typeof useRunView>;
