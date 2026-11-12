// One EventSource per run, shared by every component that watches it.
// Bursts (the backlog sent on connect) are flushed to React once per animation frame.
import { useSyncExternalStore } from "react";
import type { RunEvent } from "./contract";

export interface RunStream {
  events: RunEvent[];
  version: number;
  connected: boolean;
  ended: boolean;
  /** wall-clock ms when the newest event arrived; used to extrapolate the server clock while live */
  receivedAt: number;
}

interface Entry {
  events: RunEvent[];
  seqs: Set<number>;
  listeners: Set<() => void>;
  source: EventSource | null;
  snapshot: RunStream;
  scheduled: boolean;
  connected: boolean;
  receivedAt: number;
  closeTimer: number | null;
}

const EMPTY: RunStream = { events: [], version: 0, connected: false, ended: false, receivedAt: 0 };
const entries = new Map<string, Entry>();

function flush(entry: Entry): void {
  entry.scheduled = false;
  const last = entry.events[entry.events.length - 1];
  entry.snapshot = {
    events: entry.events,
    version: entry.snapshot.version + 1,
    connected: entry.connected,
    ended: last?.kind === "run.end",
    receivedAt: entry.receivedAt,
  };
  for (const listener of entry.listeners) listener();
}

function schedule(entry: Entry): void {
  if (entry.scheduled) return;
  entry.scheduled = true;
  requestAnimationFrame(() => flush(entry));
}

function open(id: string, entry: Entry): void {
  const source = new EventSource(`/api/runs/${encodeURIComponent(id)}/events`);
  entry.source = source;
  source.onopen = () => {
    entry.connected = true;
    schedule(entry);
  };
  source.onerror = () => {
    entry.connected = false;
    schedule(entry);
  };
  source.onmessage = (message) => {
    const event = JSON.parse(message.data as string) as RunEvent;
    if (entry.seqs.has(event.seq)) return;
    entry.seqs.add(event.seq);
    entry.events.push(event);
    entry.receivedAt = performance.now();
    if (event.kind === "run.end") {
      // keep the stream open briefly so late listeners still get the final snapshot, then stop reconnecting
      source.close();
      entry.connected = false;
    }
    schedule(entry);
  };
}

function entryFor(id: string): Entry {
  let entry = entries.get(id);
  if (!entry) {
    entry = {
      events: [],
      seqs: new Set(),
      listeners: new Set(),
      source: null,
      snapshot: EMPTY,
      scheduled: false,
      connected: false,
      receivedAt: 0,
      closeTimer: null,
    };
    entries.set(id, entry);
  }
  return entry;
}

function subscribe(id: string, listener: () => void): () => void {
  const entry = entryFor(id);
  entry.listeners.add(listener);
  if (entry.closeTimer !== null) {
    window.clearTimeout(entry.closeTimer);
    entry.closeTimer = null;
  }
  const ended = entry.events[entry.events.length - 1]?.kind === "run.end";
  if (!entry.source && !ended) open(id, entry);
  return () => {
    entry.listeners.delete(listener);
    if (entry.listeners.size === 0) {
      entry.closeTimer = window.setTimeout(() => {
        entry.source?.close();
        entry.source = null;
        entry.connected = false;
      }, 4000);
    }
  };
}

export function useRunStream(id: string | undefined): RunStream {
  return useSyncExternalStore(
    (listener) => (id ? subscribe(id, listener) : () => undefined),
    () => (id ? entryFor(id).snapshot : EMPTY),
    () => EMPTY,
  );
}
