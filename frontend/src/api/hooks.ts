import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isNotFound } from "./client";
import type {
  AddCandidateRequest,
  AnswersRequest,
  AuditRequest,
  CaseDetail,
  CreateCaseRequest,
  FactUpdateRequest,
  GeometryDecision,
  IdentityRequest,
  ProfileSpec,
  ReviewRequest,
  ServiceTarget,
  StartExperimentRequest,
  UploadCaseRequest,
} from "./contract";
import { useRunStream } from "./events";

export const keys = {
  config: ["config"] as const,
  cases: ["cases"] as const,
  case: (id: string) => ["case", id] as const,
  graph: ["lab", "graph"] as const,
  runs: ["lab", "runs"] as const,
  experiments: ["lab", "experiments"] as const,
  experiment: (id: string) => ["lab", "experiment", id] as const,
  datasets: ["lab", "datasets"] as const,
  catalog: ["lab", "catalog"] as const,
  profiles: ["lab", "profiles"] as const,
  settings: ["lab", "settings"] as const,
};

/** which backend answers (`mock` or `real`) and what it offers; everything that differs between the two keys off this */
export const useBackendConfig = () => useQuery({ queryKey: keys.config, queryFn: api.config, staleTime: 30_000 });

/** a real backend without an example video cannot start Lab demo runs (`POST /api/lab/runs` answers 409) */
export function useNoSample(): boolean {
  const config = useBackendConfig().data;
  return config?.backend === "real" && !config.sampleAvailable;
}

/** undefined until the backend has answered, so mock-only and real-only pieces both stay hidden while loading */
export function useBackendKind(): "mock" | "real" | undefined {
  return useBackendConfig().data?.backend;
}

export const useCases = () => useQuery({ queryKey: keys.cases, queryFn: api.listCases, refetchInterval: 5000 });

export function useCase(id: string | undefined) {
  return useQuery({
    queryKey: keys.case(id ?? ""),
    queryFn: () => api.getCase(id as string),
    enabled: !!id,
    retry: (count, error) => count < 2 && !isNotFound(error),
  });
}

/** keep the case snapshot fresh: refetch whenever the run stream reports progress */
export function useCaseLive(id: string | undefined) {
  const stream = useRunStream(id);
  const client = useQueryClient();
  const lastSeen = useRef(0);
  useEffect(() => {
    if (!id) return;
    const events = stream.events;
    let relevant = false;
    for (let i = events.length - 1; i >= 0 && events[i].seq > lastSeen.current; i -= 1) {
      const kind = events[i].kind;
      if (kind === "run.status" || kind === "interrupt" || kind === "resume" || kind === "activity" || kind === "state" || kind === "run.end") {
        relevant = true;
        break;
      }
    }
    lastSeen.current = events[events.length - 1]?.seq ?? lastSeen.current;
    if (!relevant) return;
    const timer = window.setTimeout(() => {
      void client.invalidateQueries({ queryKey: keys.case(id) });
      void client.invalidateQueries({ queryKey: keys.cases });
    }, 120);
    return () => window.clearTimeout(timer);
  }, [id, stream.version, stream.events, client]);
  return stream;
}

function useCaseMutation<T>(id: string, fn: (body: T) => Promise<unknown>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.case(id) });
    },
  });
}

export const useCreateCase = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateCaseRequest) => api.createCase(body),
    onSuccess: (detail) => {
      client.setQueryData(keys.case(detail.id), detail);
      void client.invalidateQueries({ queryKey: keys.cases });
    },
  });
};

/** real backend: send the video itself. `progress` is the uploaded fraction (0–1), null when idle. */
export const useUploadCase = () => {
  const client = useQueryClient();
  const [progress, setProgress] = useState<number | null>(null);
  const mutation = useMutation({
    mutationFn: (body: UploadCaseRequest) => {
      setProgress(0);
      return api.uploadCase(body, setProgress);
    },
    onSuccess: (detail) => {
      client.setQueryData(keys.case(detail.id), detail);
      void client.invalidateQueries({ queryKey: keys.cases });
    },
    onSettled: () => setProgress(null),
  });
  return { ...mutation, progress };
};

/** what the geometry endpoint reads of each on-screen decision, in a stable order */
function geometryInput(decisions: GeometryDecision[]): GeometryDecision[] {
  return decisions
    .filter((d) => d.scope !== "metadata")
    .map(({ id, targetId, scope, method, shape, ranges }) => ({ id, targetId, scope, method, shape, ranges }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** the server places kept targets too (where a mosaic would go), so the method is not part of what the answer depends on */
const geometryKey = (decisions: GeometryDecision[]) => JSON.stringify(decisions.map(({ method: _method, ...rest }) => rest));

/** the value, once it has stopped changing for `ms` */
export function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    if (Object.is(value, settled)) return;
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, settled, ms]);
  return settled;
}

/**
 * Where the mosaic of each of `decisions` goes on every frame, worked out by the server with the renderer's rules.
 * The case's own plan is read with GET; decisions that differ from it (edits in review, or decisions made up to
 * locate a target on a still) are sent with POST once they have stopped changing. While a new answer is on its
 * way the previous one stays, so an edit never makes the preview fall back to the bare picture.
 * `settled` says the data answers exactly the decisions passed in now.
 */
export function useGeometry(c: Pick<CaseDetail, "id" | "media" | "perception" | "plan"> | undefined, decisions: GeometryDecision[]) {
  const url = c?.media?.geometry;
  const id = c?.id ?? "";
  const wanted = geometryInput(decisions);
  const sent = useSettled(JSON.stringify(wanted), 250);
  const body = useMemo(() => JSON.parse(sent) as GeometryDecision[], [sent]);
  const key = geometryKey(body);
  const saved = key === geometryKey(geometryInput(c?.plan?.decisions ?? []));
  const query = useQuery({
    queryKey: ["geometry", id, key],
    queryFn: () => api.geometry(url as string, saved ? undefined : { decisions: body }),
    enabled: !!url && !!c?.media?.ready && c.perception !== null && body.length > 0,
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === id ? previous : undefined),
    staleTime: Infinity,
    retry: 1,
  });
  return { ...query, settled: query.isSuccess && !query.isPlaceholderData && geometryKey(wanted) === key };
}

export const usePauseCase = (id: string) => useCaseMutation<void>(id, () => api.pauseCase(id));
export const useResumeCase = (id: string) =>
  useCaseMutation(id, (costCapUsd: number | undefined) => api.resumeCase(id, costCapUsd === undefined ? {} : { costCapUsd }));

export const useAnswerIdentity = (id: string) => useCaseMutation(id, (body: IdentityRequest) => api.answerIdentity(id, body));
export const useAnswerQuestions = (id: string) => useCaseMutation(id, (body: AnswersRequest) => api.answerQuestions(id, body));
export const useUpdateFact = (id: string) => useCaseMutation(id, (body: FactUpdateRequest) => api.updateFact(id, body));
export const useSubmitReview = (id: string) => useCaseMutation(id, (body: ReviewRequest) => api.submitReview(id, body));
export const useSubmitAudit = (id: string) =>
  useCaseMutation(id, (body: AuditRequest & { backToReview?: boolean }) => api.submitAudit(id, body));

export const useGraph = () => useQuery({ queryKey: keys.graph, queryFn: api.graph, staleTime: Infinity });
export const useRuns = () => useQuery({ queryKey: keys.runs, queryFn: api.runs, refetchInterval: 3000 });
export const useExperiments = () => useQuery({ queryKey: keys.experiments, queryFn: api.experiments });
export const useExperiment = (id: string | undefined) =>
  useQuery({ queryKey: keys.experiment(id ?? ""), queryFn: () => api.experiment(id as string), enabled: !!id, staleTime: Infinity });
export const useDatasets = () => useQuery({ queryKey: keys.datasets, queryFn: api.datasets });
// the catalog and profiles are files the team may edit by hand, so refetch when the window regains focus
export const useCatalog = () => useQuery({ queryKey: keys.catalog, queryFn: api.catalog, refetchOnWindowFocus: true });
export const useProfiles = () => useQuery({ queryKey: keys.profiles, queryFn: api.profiles, refetchOnWindowFocus: true });

/** the profile check as a query: one cache entry per set of selections and parameters, whichever screen asks */
export function profileCheckOptions(spec: Partial<ProfileSpec> | null) {
  const body = spec ? JSON.stringify({ selections: spec.selections, params: spec.params }) : "";
  return {
    queryKey: ["lab", "check", body] as const,
    queryFn: () => api.checkProfile(JSON.parse(body) as Partial<ProfileSpec>),
    enabled: !!spec,
    staleTime: 60_000,
  };
}

/** validation, cost estimate and config hash for an unsaved profile */
export function useProfileCheck(spec: Partial<ProfileSpec> | null) {
  return useQuery({ ...profileCheckOptions(spec), placeholderData: (previous) => previous });
}

export function useLabMutation<A, R>(fn: (arg: A) => Promise<R>, invalidate: readonly (readonly string[])[]) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      for (const key of invalidate) void client.invalidateQueries({ queryKey: key });
    },
  });
}

export const useSaveProfile = (mode: "create" | "update") =>
  useLabMutation((spec: ProfileSpec) => (mode === "create" ? api.createProfile(spec) : api.updateProfile(spec)), [keys.profiles]);
export const useDeleteProfile = () => useLabMutation((id: string) => api.deleteProfile(id), [keys.profiles, keys.catalog]);
export const usePromoteProfile = () =>
  useLabMutation(({ id, target }: { id: string; target: ServiceTarget }) => api.promoteProfile(id, target), [keys.profiles, keys.datasets]);
export const useAddCandidate = () => useLabMutation((body: AddCandidateRequest) => api.addCandidate(body), [keys.catalog]);
export const useRemoveCandidate = () =>
  useLabMutation(({ slot, id }: { slot: string; id: string }) => api.removeCandidate(slot, id), [keys.catalog]);
export const useLookupCandidate = () => useMutation({ mutationFn: (ref: string) => api.lookupCandidate(ref) });
export const useStartExperiment = () =>
  useLabMutation((body: StartExperimentRequest) => api.startExperiment(body), [keys.experiments, keys.runs, keys.profiles, keys.datasets]);
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings });

export const useSetSpeed = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (speed: number) => api.setSpeed(speed),
    onSuccess: (settings) => client.setQueryData(keys.settings, settings),
  });
};

export const useStartDemo = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (profileId?: string) => api.startDemo(profileId),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.runs }),
  });
};
