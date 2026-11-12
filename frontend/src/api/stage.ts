// Section experiments, video datasets, evaluators and the step store of the Lab: the types, fetch functions and
// React hooks for /api/lab/sections · stage-experiments · evaluators · video-sets · artifacts
// (docs/design/16-lab-runs-and-experiment-sets.md §16.7).
//
// A section experiment is a set of runs over one fixed range of pipeline sections. Runs are added to it launch by
// launch: each is one video with one combination of the parts read inside the range. The server decides where a
// run starts (from the beginning, or continuing an earlier run from the checkpoint in front of a section) and
// what it will ask the user; a run that asks is never answered for, it waits until a person answers in the
// service screen.
import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as http from "./client";
import type { RunSummary, RunWaiting } from "./contract";
import { keys, useBackendKind, useLabMutation, useSettled } from "./hooks";

/* ---------- sections ---------- */

export interface SectionStep {
  /** run-event node id, e.g. `perceive/detect_track` */
  node: string;
  label: string;
  /** the parts and parameters the step's result depends on: the same table the server keys its step store on */
  slots: string[];
  params: string[];
  /** the steps it reads (of this or an earlier section): when one of them runs again, so does this one */
  after: string[];
  /** the result is kept in the step store and taken again for the same inputs; false: computed in every run */
  stored: boolean;
}

/** a point at which the pipeline stops and asks the user */
export type InteractionKind = "identity" | "questions" | "review" | "audit";

export interface SectionInteraction {
  kind: InteractionKind;
  label: string;
}

/** an interaction of a range or a run, with the section it belongs to */
export interface RangeInteraction extends SectionInteraction {
  section: string;
}

export interface PipelineSection {
  id: string;
  label: string;
  description: string;
  /** the top-level graph node the section starts at (a run that continues does so from the checkpoint in front of it) */
  entry: string;
  exit: string;
  /** what the section asks the user; a range that contains one is never answered automatically */
  interactions: SectionInteraction[];
  /** catalog parts and profile parameters read inside the section */
  slots: string[];
  params: string[];
  steps: SectionStep[];
}

/* ---------- evaluation: scores and evaluators ---------- */

export type ScoreDirection = "higher" | "lower" | null;

/** one line of evaluation. The screens draw whatever metrics they find: none is known to them by name. */
export interface Score {
  metric: string;
  label: string;
  value: number;
  /** "ratio" (0–1), "count", "sec", "usd", or free text */
  unit: string;
  direction: ScoreDirection;
  section: string | null;
  /** the registered evaluator, or the outside tool, that produced it */
  evaluator: string;
  detail: string;
}

/** a metric averaged over videos (each video counts once) */
export interface ScoreAggregate {
  label: string;
  unit: string;
  direction: ScoreDirection;
  mean: number;
  /** the number of videos behind the mean */
  n: number;
}

/** `GET /api/lab/evaluators`: an evaluator registered on the server; it runs when a run whose range covers one of its sections completes */
export interface RegisteredEvaluator {
  id: string;
  label: string;
  description: string;
  sections: string[];
  metrics: { id: string; label: string; unit: string; direction: ScoreDirection }[];
}

/* ---------- experiments ---------- */

/** running: a run is queued or in progress · waiting: only runs that wait for the user are left · idle */
export type StageStatus = "running" | "waiting" | "idle";
export type StageRunStatus = "queued" | "running" | "waiting" | "completed" | "failed" | "skipped";
/** what a waiting run waits for: an answer of one kind, or to be resumed after it was paused */
export type WaitingFor = RunWaiting;

export interface StageProfileRef {
  id: string;
  name: string;
  configHash: string;
}

export interface StageExperimentSummary {
  id: string;
  title: string;
  createdAt: string;
  /** the last time a run was added, changed state or was removed */
  updatedAt: string;
  status: StageStatus;
  /** first and last section of the range */
  from: string;
  to: string;
  /** the range asks the user: runs are added one at a time and answered by a person */
  interactive: boolean;
  baseProfile: StageProfileRef;
  datasets: string[];
  /** runs that ended (completed, failed or skipped), all runs, and those waiting for the user */
  progress: { done: number; total: number; waiting: number };
  costUsd: number;
  /** runs are skipped once the set has spent this much */
  costCapUsd: number;
  /** number of combinations and of videos that have a run */
  variants: number;
  items: number;
}

/** `GET /api/lab/stage-experiments/limits`: how large one launch may be, and the cost cap of a whole set */
export interface StageLimits {
  maxVariants: number;
  maxRuns: number;
  costCapUsdDefault: number;
  costCapUsdMax: number;
}

/** a combination as a launch describes it, before or after it is part of an experiment */
export interface VariantSpec {
  /** stable across launches: the same parts and values give the same key */
  key: string;
  /** names the part of every choice ("장면 설명 Gemini 3.8 Flash"); "기준 · <profile>" for the base profile itself */
  label: string;
  /** nothing changed: the base profile itself */
  base: boolean;
  /** slot id → candidate id, where it differs from the base profile */
  overrides: Record<string, string>;
  params: Record<string, number>;
  /** slots and parameters that differ from the base profile */
  changed: string[];
  /** the first section of the range this combination changes (null: none) */
  firstChanged: string | null;
  /** the steps inside the range that are computed again because they differ from the base */
  recomputes: string[];
  framesLeaveDevice: boolean;
}

export interface StageVariant extends VariantSpec {
  /** `base`, or `v1`… in the order the combinations were added */
  id: string;
  configHash: string;
}

/** what a dataset says is in a video; the "expected" evaluator scores a perception result against it */
export interface ExpectedLabels {
  texts?: string[];
  people?: number;
  plates?: number;
  scenes?: number;
}

export interface StageItem {
  /** `<dataset id>/<video id>` */
  key: string;
  dataset: string;
  datasetName: string;
  id: string;
  title: string;
  filename: string;
  durationSec: number;
  expected: ExpectedLabels | null;
  /** false: frames of this video must not be sent to cloud models */
  sendable: boolean;
}

export interface StepMetric {
  node: string;
  section: string;
  label: string;
  /** seconds this run spent in the step (time spent waiting for the user is not in it) */
  sec: number;
  costUsd: number;
  /** the stored result of an earlier computation was taken instead of computing */
  reused: boolean;
  /** what the stored result took and cost when it was computed (0 when the step was not reused) */
  savedSec: number;
  savedCostUsd: number;
}

export interface PerceptionResult {
  scenes: number;
  people: number;
  plates: number;
  faceTracks: number;
  bodyTracks: number;
  texts: string[];
  samples: number;
  describedScenes: number;
  describedPersons: number;
  sceneLabels: string[];
  placeTypes: string[];
  peopleCounts: (number | null)[];
  visibleSec: Record<string, number>;
}

export interface AnalysisResult {
  analyses: number;
  applies: number;
  uncertain: number;
  notApplies: number;
  requirements: number;
  /** `<target>:<scope>` of every protection requirement */
  protected: string[];
  questions: number;
  searches: number;
  toolCalls: number;
  citations: number;
  unresolved: number;
}

export interface PlanResult {
  decisions: number;
  /** `<target>:<scope>` */
  redacted: string[];
  kept: string[];
  unsatisfied: number;
  attempts: number;
}

/** the review · audit section: what the person changed, what the audit found, and what the plan covers after it */
export interface ReviewResult {
  /** changes the user made in review and in the audit */
  edits: number;
  findings: number;
  auditRounds: number;
  /** `<target>:<scope>` of every decision that is not "keep" */
  redacted: string[];
}

export interface RenderResult {
  rows: number;
  passed: number;
  failed: string[];
  outputBytes: number | null;
  modifiedFrames: number | null;
  repairRounds: number;
}

export interface BaseComparison {
  /** what was compared (the last section both runs produced) */
  basis: string;
  /** 0–1 */
  agreement: number;
  onlyBase: string[];
  onlyHere: string[];
  /** values that differ (perception only): counts, and per scene what the VLM said ("장면 1 장소 (other → cafe)") */
  different?: string[];
}

export interface RunMetrics {
  status: string;
  /** seconds from start to end, without the time spent waiting for the user */
  elapsedSec: number;
  /** seconds spent inside the sections of the experiment, likewise without waiting */
  sectionSec: number;
  /** seconds the run waited for the user */
  waitSec: number;
  costUsd: number;
  llmCalls: number;
  toolCalls: number;
  tokensIn: number;
  tokensOut: number;
  /** time and cost not spent inside the range: reused steps, plus the range's sections in front of the checkpoint the run continued from */
  savedSec: number;
  savedCostUsd: number;
  steps: StepMetric[];
  reuse: {
    /** the graph node the run started in front of, when it continued an earlier run */
    checkpoint: string | null;
    /** steps of the range whose stored result was taken */
    steps: string[];
    /** steps in front of the range that were not computed either; not part of the comparison */
    before: string[];
    /** what the source run spent on the range's sections in front of the checkpoint */
    checkpointSavedSec?: number;
    checkpointSavedCostUsd?: number;
  };
  perception?: PerceptionResult | null;
  analysis?: AnalysisResult | null;
  plan?: PlanResult | null;
  review?: ReviewResult | null;
  render?: RenderResult | null;
  /** against the most recent run of the base combination on the same video (null: there is none yet) */
  vsBase?: BaseComparison | null;
}

export interface StageRun {
  /** `r1`…, unique inside the experiment */
  id: string;
  /** `StageItem.key` */
  item: string;
  /** `StageVariant.id` */
  variant: string;
  status: StageRunStatus;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  /** the Lab run (`/lab/runs/{caseId}`) and service case (`/cases/{caseId}`), once it has started */
  caseId: string | null;
  /** fork: it continued `sourceCaseId` · scratch: from the beginning · null: not started yet */
  start: "fork" | "scratch" | null;
  /** the run this one continued from */
  sourceCaseId: string | null;
  /** the graph node it started in front of (null: from the beginning) */
  forkedAt: string | null;
  /** the section it started at: the pipeline's first one for a run from the beginning (null until it has started) */
  startSection: string | null;
  /** it goes through a section that asks the user */
  interactive: boolean;
  /** made before 2026-11-12, when questions inside a range were answered automatically */
  autoAnswered: boolean;
  waitingFor: WaitingFor | null;
  /** why the run failed or was skipped */
  error: string | null;
  metrics: RunMetrics | null;
  scores: Score[];
  /** registered evaluators that failed on this run, as "<name>: <error>" (the run's result is unaffected) */
  evaluationErrors?: string[];
}

/** the columns a combination is compared on, over all videos or over those of one dataset */
export interface SummaryMeans {
  /** videos with a completed run */
  items: number;
  completed: number;
  /** means over videos: several runs of one video count once (null: nothing completed) */
  sectionSecMean: number | null;
  costUsdMean: number | null;
  /** what the range would have taken and cost with nothing reused (spent + saved): what combinations that reused different amounts are compared on */
  fullSecMean: number | null;
  fullCostUsdMean: number | null;
  agreementMean: number | null;
  /** metric id → mean */
  scores: Record<string, ScoreAggregate>;
}

export interface DatasetSummary extends SummaryMeans {
  dataset: string;
  datasetName: string;
}

export interface VariantSummary extends SummaryMeans {
  variant: string;
  runs: number;
  failed: number;
  skipped: number;
  waiting: number;
  elapsedSecMean: number | null;
  /** totals over its completed runs */
  costUsdTotal: number;
  savedSecTotal: number;
  savedCostUsdTotal: number;
  llmCallsTotal: number;
  byDataset: DatasetSummary[];
}

export interface StageExperiment extends Omit<StageExperimentSummary, "variants" | "items"> {
  sections: string[];
  /** what the range asks the user, in pipeline order */
  interactions: RangeInteraction[];
  variants: StageVariant[];
  items: StageItem[];
  runs: StageRun[];
  summary: VariantSummary[];
}

/** one launch: the runs to add. The product of the chosen candidates and values are its combinations. */
export interface Launch {
  /** slot id → candidates; a part that is not listed keeps the base profile's own */
  matrix?: Record<string, string[]>;
  paramMatrix?: Record<string, number[]>;
  datasets?: string[];
  /** `StageItem.key`s: only these videos of the datasets */
  items?: string[];
  /** the run (case id) to continue from, for a launch of one run; otherwise the server picks the best one */
  source?: string;
}

/** a new experiment: its range and base profile, and the first launch */
export interface CreateStageExperimentRequest extends Launch {
  title?: string;
  from: string;
  to?: string;
  baseProfile?: string;
  costCapUsd?: number;
}

/** what a launch is planned for: a range that has no experiment yet, or an existing experiment */
export type LaunchTarget = { from: string; to?: string; baseProfile?: string } | { experiment: string };

export interface PlannedVariant extends VariantSpec {
  /** the variant of the experiment this is, or null when the launch adds it */
  id: string | null;
}

export interface SourceRun {
  caseId: string;
  title: string;
  startedAt: string;
}

export interface PlannedRun {
  /** `StageItem.key` */
  item: string;
  itemTitle: string;
  /** `PlannedVariant.key` */
  variant: string;
  /** fork: continues `source` from the checkpoint in front of `startSection` · scratch: from the beginning */
  start: "fork" | "scratch";
  source: SourceRun | null;
  /** the section it starts at: the pipeline's first one for a run from the beginning */
  startSection: string;
  forkedAt: string | null;
  interactive: boolean;
  /** what this run will ask the user, in order (their names are in `LaunchPlan.interactions`) */
  asks: InteractionKind[];
  /** the runs `Launch.source` may name for this run, best first (all of them, whether or not one is named) */
  candidates: (SourceRun & { startSection: string })[];
  /** why this run cannot start */
  problem: string | null;
}

/** `POST /api/lab/stage-experiments/preview`: what a launch would run. Starting it does the same computation. */
export interface LaunchPlan {
  from: string;
  to: string;
  sections: string[];
  interactive: boolean;
  interactions: RangeInteraction[];
  variants: PlannedVariant[];
  runs: PlannedRun[];
  /** why the launch cannot start as it is (a launch with problems is refused with 422) */
  problems: string[];
}

/* ---------- video datasets ---------- */

export interface VideoItem {
  id: string;
  title: string;
  filename: string;
  durationSec: number;
  width?: number;
  height?: number;
  frames?: number;
  memo: string;
  expected: ExpectedLabels | null;
  /** the run this video was taken from */
  origin?: string | null;
  /** the file is gone from the server */
  missing?: boolean;
}

export interface VideoSet {
  id: string;
  name: string;
  description: string;
  builtin: boolean;
  /** false: the videos must not be sent to cloud models */
  sendable: boolean;
  createdAt: string;
  items: VideoItem[];
}

export interface CreateVideoSetRequest {
  name: string;
  description?: string;
  sendable?: boolean;
}

export interface EditVideoSetRequest {
  name?: string;
  description?: string;
  sendable?: boolean;
}

export interface EditVideoRequest {
  title?: string;
  memo?: string;
  /** `{}` clears the labels */
  expected?: ExpectedLabels;
}

export interface AddVideoRequest {
  file: File;
  title: string;
  memo: string;
  expected?: ExpectedLabels | null;
}

export interface ArtifactStats {
  /** step name (`detect_track`) → what the step store holds for it, and the graph node that produces it */
  steps: Record<string, { entries: number; bytes: number; node: string; label: string }>;
}

/* ---------- runs ---------- */

/** run summary fields of experiment runs that `contract.ts` does not list */
export type LabRun = RunSummary & {
  /** the combination of a section experiment this run is (its label) */
  variant?: string;
};

/** section experiments have ids `sx_…`; anything else is a whole-pipeline experiment of the later milestone */
export function isStageExperimentId(id: string): boolean {
  return id.startsWith("sx_");
}

export function experimentPath(id: string): string {
  return isStageExperimentId(id) ? `/lab/experiments/${encodeURIComponent(id)}` : `/lab/experiments/pipeline/${encodeURIComponent(id)}`;
}

/* ---------- fetch ---------- */

// everything here lives under /api/lab
const request = <T>(path: string, init?: RequestInit) => http.request<T>(`/lab${path}`, init);
const post = <T>(path: string, body?: unknown) => http.post<T>(`/lab${path}`, body);
const patch = <T>(path: string, body: unknown) => http.patch<T>(`/lab${path}`, body);
const del = <T>(path: string) => http.del<T>(`/lab${path}`);
const upload = <T>(path: string, form: FormData, onProgress?: (fraction: number) => void) => http.upload<T>(`/lab${path}`, form, onProgress);
const seg = http.seg;

const stageApi = {
  sections: () => request<PipelineSection[]>("/sections"),
  limits: () => request<StageLimits>("/stage-experiments/limits"),
  experiments: () => request<StageExperimentSummary[]>("/stage-experiments"),
  experiment: (id: string) => request<StageExperiment>(`/stage-experiments/${seg(id)}`),
  preview: (body: Launch & LaunchTarget, signal?: AbortSignal) => request<LaunchPlan>("/stage-experiments/preview", { method: "POST", body: JSON.stringify(body), signal }),
  create: (body: CreateStageExperimentRequest) => post<StageExperiment>("/stage-experiments", body),
  addRuns: (id: string, launch: Launch) => post<StageExperiment>(`/stage-experiments/${seg(id)}/runs`, launch),
  edit: (id: string, body: { title?: string; costCapUsd?: number }) => patch<StageExperiment>(`/stage-experiments/${seg(id)}`, body),
  removeRun: (id: string, runId: string) => del<StageExperiment>(`/stage-experiments/${seg(id)}/runs/${seg(runId)}`),
  stop: (id: string) => post<StageExperiment>(`/stage-experiments/${seg(id)}/stop`),
  remove: (id: string) => del<{ ok: boolean }>(`/stage-experiments/${seg(id)}`),
  evaluators: () => request<RegisteredEvaluator[]>("/evaluators"),
  artifacts: () => request<ArtifactStats>("/artifacts"),

  videoSets: () => request<VideoSet[]>("/video-sets"),
  createVideoSet: (body: CreateVideoSetRequest) => post<VideoSet>("/video-sets", body),
  editVideoSet: (id: string, body: EditVideoSetRequest) => patch<VideoSet>(`/video-sets/${seg(id)}`, body),
  deleteVideoSet: (id: string) => del<{ ok: boolean }>(`/video-sets/${seg(id)}`),
  editVideo: (setId: string, itemId: string, body: EditVideoRequest) => patch<VideoItem>(`/video-sets/${seg(setId)}/items/${seg(itemId)}`, body),
  /** real backend: the video itself as multipart/form-data (`expected` is a JSON string) */
  uploadVideo: (setId: string, body: AddVideoRequest, onProgress?: (fraction: number) => void) => {
    const form = new FormData();
    form.append("file", body.file, body.file.name);
    form.append("title", body.title);
    form.append("memo", body.memo);
    if (body.expected && Object.keys(body.expected).length) form.append("expected", JSON.stringify(body.expected));
    return upload<VideoItem>(`/video-sets/${seg(setId)}/items`, form, onProgress);
  },
  /** M2 mock: a JSON description of the chosen file (the mock never receives the video) */
  describeVideo: (setId: string, body: AddVideoRequest) =>
    post<VideoItem>(`/video-sets/${seg(setId)}/items`, {
      filename: body.file.name,
      sizeBytes: body.file.size,
      title: body.title,
      memo: body.memo,
      expected: body.expected ?? null,
    }),
  addVideoFromCase: (setId: string, body: { caseId: string; title?: string }) => post<VideoItem>(`/video-sets/${seg(setId)}/items/from-case`, body),
  removeVideo: (setId: string, itemId: string) => del<{ ok: boolean }>(`/video-sets/${seg(setId)}/items/${seg(itemId)}`),
};

/* ---------- hooks ---------- */

const PREVIEWS = ["lab", "stage-preview"] as const;

const stageKeys = {
  sections: ["lab", "sections"] as const,
  limits: ["lab", "stage-limits"] as const,
  experiments: ["lab", "stage-experiments"] as const,
  experiment: (id: string) => ["lab", "stage-experiment", id] as const,
  /** every launch preview, and the one of a launch */
  previews: PREVIEWS,
  preview: (body: string) => [...PREVIEWS, body] as const,
  evaluators: ["lab", "evaluators"] as const,
  videoSets: ["lab", "video-sets"] as const,
  artifacts: ["lab", "artifacts"] as const,
};

/** a run is in progress, or one waits for a person who answers in another tab */
const unsettled = (status: StageStatus | undefined) => status === "running" || status === "waiting";

export const useSections = () => useQuery({ queryKey: stageKeys.sections, queryFn: stageApi.sections, staleTime: Infinity });
export const useStageLimits = () => useQuery({ queryKey: stageKeys.limits, queryFn: stageApi.limits, staleTime: Infinity });
export const useEvaluators = () => useQuery({ queryKey: stageKeys.evaluators, queryFn: stageApi.evaluators });

/** the list follows experiments whose runs are in progress or wait for an answer */
export const useStageExperiments = () =>
  useQuery({
    queryKey: stageKeys.experiments,
    queryFn: stageApi.experiments,
    refetchInterval: (query) => (query.state.data?.some((x) => x.status === "running") ? 2000 : query.state.data?.some((x) => x.status === "waiting") ? 5000 : false),
  });

/** one experiment, polled while a run is in progress or waits for the user; the lists that depend on its runs are refreshed when its state changes */
export function useStageExperiment(id: string | undefined) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: stageKeys.experiment(id ?? ""),
    queryFn: () => stageApi.experiment(id as string),
    enabled: !!id,
    retry: (count, error) => count < 2 && !http.isNotFound(error),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : q.state.data?.status === "waiting" ? 3000 : false),
  });
  const status = query.data?.status;
  const done = query.data?.progress.done;
  useEffect(() => {
    if (!status) return;
    void client.invalidateQueries({ queryKey: stageKeys.experiments });
    void client.invalidateQueries({ queryKey: keys.runs });
    if (!unsettled(status)) void client.invalidateQueries({ queryKey: stageKeys.artifacts });
  }, [status, done, client]);
  return query;
}

/**
 * What a launch would run, asked from the server a moment after the choice stops changing. `plan` is the answer
 * to the current choice only: while a newer one is on its way `stale` is true and the previous plan stays visible.
 * `revision` is anything that changes when the runs a launch could continue from change (an experiment's `updatedAt`).
 */
export function useLaunchPlan(target: LaunchTarget | null, launch: Launch, revision = "") {
  const body = target ? JSON.stringify({ ...launch, ...target }) : "";
  const settled = useSettled(body, 300);
  const query = useQuery({
    queryKey: [...stageKeys.preview(settled), revision],
    queryFn: ({ signal }) => stageApi.preview(JSON.parse(settled) as Launch & LaunchTarget, signal),
    enabled: settled !== "",
    placeholderData: keepPreviousData,
    staleTime: 0,
    gcTime: 30_000,
    retry: false,
  });
  const stale = settled !== body || query.isPlaceholderData || (query.isFetching && !query.data);
  return { plan: query.data, stale, error: query.isError && settled === body ? query.error : null, refetch: query.refetch };
}

/** what every change to an experiment's runs touches */
function useExperimentWrite<A>(fn: (arg: A) => Promise<StageExperiment>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (experiment) => {
      client.setQueryData(stageKeys.experiment(experiment.id), experiment);
      void client.invalidateQueries({ queryKey: stageKeys.experiments });
      void client.invalidateQueries({ queryKey: stageKeys.previews });
      void client.invalidateQueries({ queryKey: keys.runs });
    },
  });
}

export const useCreateStageExperiment = () => useExperimentWrite((body: CreateStageExperimentRequest) => stageApi.create(body));
export const useAddStageRuns = (id: string) => useExperimentWrite((launch: Launch) => stageApi.addRuns(id, launch));
export const useEditStageExperiment = (id: string) => useExperimentWrite((body: { title?: string; costCapUsd?: number }) => stageApi.edit(id, body));
export const useDeleteStageRun = (id: string) => useExperimentWrite((runId: string) => stageApi.removeRun(id, runId));
export const useStopStageExperiment = (id: string) => useExperimentWrite(() => stageApi.stop(id));

export function useDeleteStageExperiment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => stageApi.remove(id),
    onSuccess: (_ok, id) => {
      client.removeQueries({ queryKey: stageKeys.experiment(id) });
      void client.invalidateQueries({ queryKey: stageKeys.experiments });
    },
  });
}

export const useArtifacts = () => useQuery({ queryKey: stageKeys.artifacts, queryFn: stageApi.artifacts });

export const useVideoSets = () => useQuery({ queryKey: stageKeys.videoSets, queryFn: stageApi.videoSets });

const useVideoSetMutation = <A, R>(fn: (arg: A) => Promise<R>) => useLabMutation(fn, [stageKeys.videoSets]);

export const useCreateVideoSet = () => useVideoSetMutation((body: CreateVideoSetRequest) => stageApi.createVideoSet(body));
export const useEditVideoSet = (id: string) => useVideoSetMutation((body: EditVideoSetRequest) => stageApi.editVideoSet(id, body));
export const useDeleteVideoSet = () => useVideoSetMutation((id: string) => stageApi.deleteVideoSet(id));
export const useEditVideo = (setId: string) =>
  useVideoSetMutation(({ itemId, ...body }: EditVideoRequest & { itemId: string }) => stageApi.editVideo(setId, itemId, body));
export const useAddVideoFromCase = (setId: string) =>
  useVideoSetMutation((body: { caseId: string; title?: string }) => stageApi.addVideoFromCase(setId, body));
export const useRemoveVideo = (setId: string) => useVideoSetMutation((itemId: string) => stageApi.removeVideo(setId, itemId));

/** add an uploaded video. `progress` is the uploaded fraction (0–1), null when idle. */
export function useAddVideo(setId: string) {
  const client = useQueryClient();
  const mock = useBackendKind() === "mock";
  const [progress, setProgress] = useState<number | null>(null);
  const mutation = useMutation({
    mutationFn: (body: AddVideoRequest) => {
      if (mock) return stageApi.describeVideo(setId, body);
      setProgress(0);
      return stageApi.uploadVideo(setId, body, setProgress);
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: stageKeys.videoSets }),
    onSettled: () => setProgress(null),
  });
  return { ...mutation, progress };
}
