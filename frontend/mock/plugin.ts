// Vite plugin that serves the PrivFrame API contract from the in-memory mock engine.
// Enabled by default; set PRIVFRAME_API=proxy to send /api to a real backend instead.
// Profiles and the model catalog come from configs/pipeline (PRIVFRAME_PIPELINE_DIR overrides the folder;
// PRIVFRAME_PIPELINE_SANDBOX=1 works on a temporary copy so tests never touch the repository files).
import fs from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import os from "node:os";
import path from "node:path";
import type { Connect, Plugin } from "vite";
import type {
  AddCandidateRequest,
  AnswersRequest,
  AuditRequest,
  CreateCaseRequest,
  DatasetInfo,
  EvaluatorInfo,
  FactUpdateRequest,
  IdentityRequest,
  ProfileCheck,
  ProfileSpec,
  ProfileSummary,
  ReviewRequest,
  RunEvent,
  ServiceTarget,
  StartExperimentRequest,
  StartRunRequest,
} from "../src/api/contract";
import { type CaseRun, Hub } from "./engine";
import { DATASET_ID, DEMO_MEMO, ExperimentStore } from "./experiments";
import { applyAnswers, reevaluate } from "./flow";
import { GRAPH } from "./graph";
import { HttpError, readJson, sendJson } from "./http";
import { DATASETS, EVALUATORS, HISTORY_RUNS } from "./lab-data";
import { lookupOpenRouter } from "./openrouter";
import { FRAME_SLOTS, PipelineStore, type ResolvedProfile } from "./pipeline";
import { verificationRows } from "./scenario";
import { stageRoutes } from "./stage";

const FACT_TO_QUESTION: Record<string, string> = {
  "purpose.publication_scope": "Q1",
  "person-2.publication_consent": "Q2",
  "edit.caption_names": "Q3",
};

interface MockApi {
  hub: Hub;
  store: PipelineStore;
  experiments: ExperimentStore;
  estimates: Map<string, Promise<number>>;
}

function streamEvents(req: IncomingMessage, res: ServerResponse, run: CaseRun, since: number): void {
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  res.write("retry: 1500\n\n");
  const send = (event: RunEvent) => res.write(`id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`);
  for (const event of run.events) if (event.seq > since) send(event);
  run.listeners.add(send);
  const ping = setInterval(() => res.write(": ping\n\n"), 15000);
  req.on("close", () => {
    clearInterval(ping);
    run.listeners.delete(send);
  });
}

function requireRun(hub: Hub, id: string | undefined): CaseRun {
  const run = id ? hub.runs.get(id) : undefined;
  if (!run) throw new HttpError(404, "해당 작업을 찾을 수 없습니다.");
  return run;
}

function requireWaiting(run: CaseRun, payload: string, label: string): void {
  if (!run.isWaiting(payload)) throw new HttpError(409, `지금은 ${label} 단계가 아닙니다.`);
}

function servicedProfile(api: MockApi, target: ServiceTarget): ResolvedProfile {
  const id = api.store.service()[target];
  return api.store.resolve(api.store.getProfile(id).spec);
}

/** cost of the sample case for a configuration, from a silent run on the virtual clock */
function estimateCost(api: MockApi, profile: ResolvedProfile): Promise<number> {
  const hit = api.estimates.get(profile.configHash);
  if (hit) return hit;
  const run = api.hub.create({ title: "비용 추정", memo: DEMO_MEMO, mode: "anonymized_upload", autopilot: true, virtual: true, register: false, profile });
  const cost = run.done.then(() => run.detail.costUsd);
  api.estimates.set(profile.configHash, cost);
  return cost;
}

async function checkProfile(api: MockApi, spec: ProfileSpec): Promise<ProfileCheck> {
  const resolved = api.store.resolve(spec);
  return {
    configHash: resolved.configHash,
    issues: api.store.issues(spec),
    components: resolved.components,
    estimate: {
      costUsd: await estimateCost(api, resolved),
      framesLeaveDevice: FRAME_SLOTS.some((s) => resolved.part[s]?.kind === "api"),
      apiParts: resolved.components.filter((c) => c.kind === "api").length,
      localParts: resolved.components.filter((c) => c.kind === "local").length,
    },
  };
}

async function profileSummary(api: MockApi, id: string): Promise<ProfileSummary> {
  const stored = api.store.getProfile(id);
  const check = await checkProfile(api, stored.spec);
  const service = api.store.service();
  const latest = api.experiments.latest(stored.spec.id, check.configHash);
  return {
    ...stored.spec,
    ...check,
    path: stored.path,
    source: stored.source,
    updatedAt: stored.updatedAt,
    service: (Object.keys(service) as ServiceTarget[]).filter((k) => service[k] === stored.spec.id),
    gate: latest ? (latest.metrics.passed ? "passed" : "failed") : "not_run",
    lastExperiment: latest
      ? { id: latest.id, createdAt: latest.createdAt, policyF1: latest.metrics.policyF1, costUsd: latest.metrics.costUsd, passed: latest.metrics.passed }
      : undefined,
  };
}

/** evaluator values from the latest experiment of the service profile */
function evaluatorValues(api: MockApi): { evaluators: EvaluatorInfo[]; source?: string } {
  const service = servicedProfile(api, "default");
  const latest = api.experiments.latest(service.id, service.configHash);
  if (!latest) return { evaluators: EVALUATORS.map((e) => ({ ...e, lastValue: undefined })) };
  const m = latest.metrics;
  // the scenario's plan removes the location tag, which adds a check: the count is read from the rows
  const checks = verificationRows(true);
  const value: Record<string, string> = {
    verify_output: `${checks.filter((row) => row.verdict !== "fail").length} / ${checks.length}`,
    policy_f1: m.policyF1.toFixed(2),
    requirement_recall: `${m.requirementRecall.toFixed(2)} / ${m.requirementPrecision.toFixed(2)}`,
    citation_validity: `${Math.round(m.citationValidity * 100)}%`,
    trajectory: `${Math.round(m.toolCalls)} / ${service.params["analyst.max_tool_calls"]} · ${m.questions.toFixed(0)} · ${m.coverageWarnings.toFixed(0)} · 1`,
    question_quality: "M5 (LLM 패널)",
    analysis_rubric: "M5 (LLM 패널)",
  };
  return { evaluators: EVALUATORS.map((e) => ({ ...e, lastValue: value[e.id] })), source: `${latest.id} · ${service.name}` };
}

async function route(api: MockApi, req: IncomingMessage, res: ServerResponse, url: URL): Promise<void> {
  const { hub, store, experiments } = api;
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  const method = req.method ?? "GET";
  const [, area, id, action] = parts;

  // the mock has an example scenario built in and no real media; the screens key their mock-only labels off this
  if (area === "config" && method === "GET") return sendJson(res, 200, { backend: "mock", sampleAvailable: true });

  if (area === "cases") {
    if (!id) {
      // experiment runs the user answers are opened from the Lab, not listed among the uploader's own work
      if (method === "GET") return sendJson(res, 200, hub.ordered().filter((r) => !r.opts.autopilot && !r.opts.experimentId).map((r) => r.summary()));
      if (method === "POST") {
        const body = await readJson<CreateCaseRequest>(req);
        const mode = body.mode === "local_only" ? "local_only" : "anonymized_upload";
        const run = hub.create({
          title: body.sample ? "카페 인터뷰 (예시 영상)" : body.filename || "제목 없는 영상",
          memo: body.memo ?? "",
          mode,
          upload: body.sample
            ? undefined
            : { filename: body.filename, sizeBytes: body.sizeBytes, durationSec: body.durationSec, width: body.width, height: body.height },
          autopilot: false,
          profile: servicedProfile(api, mode === "local_only" ? "local_only" : "default"),
        });
        return sendJson(res, 201, run.detail);
      }
    }
    const run = requireRun(hub, id);
    if (!action && method === "GET") return sendJson(res, 200, run.detail);
    if (action === "report.json" && method === "GET") {
      if (!run.detail.report) throw new HttpError(409, "보고서가 아직 없습니다.");
      return sendJson(
        res,
        200,
        {
          case: { id: run.id, title: run.detail.title, video: run.detail.video, upload: run.detail.upload, mock: true },
          profile: { id: run.profile.id, name: run.profile.name, configHash: run.profile.configHash },
          facts: run.detail.facts,
          questions: run.detail.questions,
          analyses: run.detail.analyses,
          requirements: run.detail.requirements,
          plan: run.detail.plan,
          findings: run.detail.findings,
          report: run.detail.report,
        },
        { "Content-Disposition": `attachment; filename="${run.id}-report.json"` },
      );
    }
    if (method !== "POST") throw new HttpError(405, "허용되지 않는 메서드입니다.");
    if (action === "identity") {
      requireWaiting(run, "identity", "대상 확인");
      const body = await readJson<IdentityRequest>(req);
      run.resolve("identity", body.value);
      return sendJson(res, 202, { ok: true });
    }
    if (action === "answers") {
      const body = await readJson<AnswersRequest>(req);
      const values = Object.fromEntries((body.answers ?? []).map((a) => [a.questionId, a.value]));
      if (run.isWaiting("questions")) {
        run.resolve("questions", values);
        return sendJson(res, 202, { ok: true });
      }
      if (run.detail.status === "AWAITING_REVIEW" && run.answers) {
        applyAnswers(run, run.answers, values);
        void reevaluate(run, run.answers, `늦은 답변 ${Object.keys(values).join(", ")}`);
        return sendJson(res, 202, { ok: true });
      }
      throw new HttpError(409, "지금은 답변을 받을 수 없습니다.");
    }
    if (action === "facts") {
      if (run.detail.status !== "AWAITING_REVIEW" || !run.answers) throw new HttpError(409, "사실은 검토 단계에서만 고칠 수 있습니다.");
      const body = await readJson<FactUpdateRequest>(req);
      const questionId = FACT_TO_QUESTION[body.key];
      if (questionId) {
        applyAnswers(run, run.answers, { [questionId]: body.value });
      } else {
        run.detail.facts = run.detail.facts.map((f) =>
          f.key === body.key
            ? { ...f, value: body.value, valueLabel: f.options?.find((o) => o.value === body.value)?.label ?? body.value, provenance: "answered", source: "사용자 수정", confidence: undefined }
            : f,
        );
        run.state("facts", "await_review", `${body.key}=${body.value} (사용자 수정)`);
      }
      void reevaluate(run, run.answers, `사실 수정 ${body.key}`);
      return sendJson(res, 202, { ok: true });
    }
    if (action === "review") {
      requireWaiting(run, "review", "검토");
      const body = await readJson<ReviewRequest>(req);
      run.resolve("review", { decisions: body.decisions ?? [] });
      return sendJson(res, 202, { ok: true });
    }
    if (action === "audit") {
      requireWaiting(run, "audit", "감사 확인");
      const body = await readJson<AuditRequest & { backToReview?: boolean }>(req);
      // as the real backend: past the allowed rounds the flow would take the reply as "apply everything" and render
      if (body.backToReview && run.detail.canReturnToReview === false) {
        throw new HttpError(409, "계획으로 돌아가 다시 고칠 수 있는 횟수를 모두 썼습니다. 권고마다 적용하거나 거부해 주세요.");
      }
      run.resolve("audit", { resolutions: body.resolutions ?? [], backToReview: body.backToReview });
      return sendJson(res, 202, { ok: true });
    }
    throw new HttpError(404, "알 수 없는 요청입니다.");
  }

  if (area === "runs" && action === "events" && method === "GET") {
    const run = requireRun(hub, id);
    const since = Number(req.headers["last-event-id"] ?? url.searchParams.get("since") ?? 0) || 0;
    return streamEvents(req, res, run, since);
  }

  if (area !== "lab") throw new HttpError(404, "알 수 없는 API 경로입니다.");
  const [, , section, a, b, c] = parts;

  // section experiments, video datasets, the step store (mock/stage.ts)
  if (await stageRoutes(api, req, res, url)) return;

  if (section === "graph") return sendJson(res, 200, GRAPH);

  if (section === "catalog") {
    if (!a && method === "GET") return sendJson(res, 200, store.catalog());
    if (a === "lookup" && method === "GET") return sendJson(res, 200, await lookupOpenRouter(url.searchParams.get("ref") ?? ""));
    if (a === "candidates" && !b && method === "POST") {
      const body = await readJson<AddCandidateRequest>(req);
      return sendJson(res, 201, store.addCandidate(body));
    }
    if (a === "candidates" && b && c && method === "DELETE") {
      store.removeCandidate(b, c);
      return sendJson(res, 200, { ok: true });
    }
  }

  if (section === "profiles") {
    if (!a && method === "GET") {
      const list = await Promise.all(store.listProfiles().map((p) => profileSummary(api, p.spec.id)));
      return sendJson(res, 200, list);
    }
    if (!a && method === "POST") {
      const body = await readJson<ProfileSpec>(req);
      const saved = store.saveProfile(body, "create");
      return sendJson(res, 201, await profileSummary(api, saved.spec.id));
    }
    if (a === "check" && method === "POST") {
      const body = await readJson<ProfileSpec>(req);
      // a draft from the editor: parts it does not mention take the catalog default
      const defaults = Object.fromEntries(store.slots().map((s) => [s.id, s.default]));
      return sendJson(res, 200, await checkProfile(api, { ...body, id: body.id || "draft", selections: { ...defaults, ...body.selections }, params: body.params ?? {} }));
    }
    if (a && !b && method === "GET") return sendJson(res, 200, await profileSummary(api, a));
    if (a && !b && method === "PUT") {
      const body = await readJson<ProfileSpec>(req);
      store.saveProfile({ ...body, id: a }, "update");
      return sendJson(res, 200, await profileSummary(api, a));
    }
    if (a && !b && method === "DELETE") {
      store.deleteProfile(a);
      return sendJson(res, 200, { ok: true });
    }
    if (a && b === "promote" && method === "POST") {
      const body = await readJson<{ target?: ServiceTarget }>(req);
      const target: ServiceTarget = body.target === "local_only" ? "local_only" : "default";
      const summary = await profileSummary(api, a);
      if (summary.issues.some((i) => i.level === "error")) throw new HttpError(409, "오류가 있는 프로필은 승격할 수 없습니다.");
      if (summary.gate !== "passed") throw new HttpError(409, "현재 설정으로 회귀 게이트를 통과한 실험이 있어야 승격할 수 있습니다.");
      if (target === "local_only" && summary.estimate.framesLeaveDevice) throw new HttpError(409, "프레임을 외부로 보내는 프로필은 '이 컴퓨터에서만 처리'에 쓸 수 없습니다.");
      store.promote(a, target);
      return sendJson(res, 200, await profileSummary(api, a));
    }
  }

  if (section === "experiments") {
    if (!a && method === "GET") return sendJson(res, 200, experiments.list());
    if (!a && method === "POST") {
      const body = await readJson<StartExperimentRequest>(req);
      return sendJson(res, 201, await experiments.start({ ...body, dataset: body.dataset || DATASET_ID }));
    }
    if (a && method === "GET") return sendJson(res, 200, experiments.get(a));
  }

  if (section === "datasets") {
    const { evaluators, source } = evaluatorValues(api);
    const datasets: DatasetInfo[] = DATASETS;
    return sendJson(res, 200, { datasets, evaluators, evaluatorSource: source });
  }

  if (section === "settings") {
    if (method === "POST") {
      const body = await readJson<{ speed?: number }>(req);
      const speed = Number(body.speed);
      if (Number.isFinite(speed)) hub.settings.speed = Math.min(16, Math.max(0.5, speed));
    }
    return sendJson(res, 200, hub.settings);
  }

  if (section === "runs") {
    if (!a && method === "GET") return sendJson(res, 200, [...hub.ordered().map((r) => r.summary()), ...HISTORY_RUNS]);
    if (!a && method === "POST") {
      const body = await readJson<StartRunRequest>(req);
      const profile = body.profileId ? store.resolve(store.getProfile(body.profileId).spec) : servicedProfile(api, "default");
      const errors = body.profileId ? store.issues(store.getProfile(body.profileId).spec).filter((i) => i.level === "error") : [];
      if (errors.length) throw new HttpError(400, `프로필에 오류가 있어 실행할 수 없습니다: ${errors[0].text}`);
      const run = hub.create({ title: `카페 인터뷰 (데모 · ${profile.name})`, memo: DEMO_MEMO, mode: "anonymized_upload", autopilot: true, idPrefix: "demo", profile });
      return sendJson(res, 201, run.summary());
    }
  }
  throw new HttpError(404, "알 수 없는 API 경로입니다.");
}

function pipelineDir(root: string): string {
  const dir = path.resolve(process.env.PRIVFRAME_PIPELINE_DIR ?? path.join(root, "..", "configs", "pipeline"));
  if (process.env.PRIVFRAME_PIPELINE_SANDBOX !== "1") return dir;
  const copy = fs.mkdtempSync(path.join(os.tmpdir(), "privframe-pipeline-"));
  fs.cpSync(dir, copy, { recursive: true });
  return copy;
}

function createApi(root: string): MockApi {
  const dir = pipelineDir(root);
  const repo = path.resolve(root, "..");
  const display = dir.startsWith(repo) ? path.relative(repo, dir) : `${dir} (테스트 사본)`;
  const hub = new Hub();
  const store = new PipelineStore(dir, display);
  const api: MockApi = { hub, store, experiments: new ExperimentStore(hub, store), estimates: new Map() };
  const baseline = servicedProfile(api, "default");
  hub.create({ title: "카페 인터뷰 (기록된 데모)", memo: DEMO_MEMO, mode: "anonymized_upload", autopilot: true, virtual: true, idPrefix: "demo", profile: baseline });
  // the service profile starts with a measured gate, like a deployment that was promoted through an experiment
  void api.experiments.start({ profiles: [baseline.id], baseline: baseline.id, dataset: DATASET_ID, repetitions: 1, title: "서비스 프로필 기준 실험 (서버 시작 시 자동)" });
  return api;
}

export function privframeMockApi(): Plugin {
  let api: MockApi | undefined;
  const handler: Connect.NextHandleFunction = (req, res, next) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (!url.pathname.startsWith("/api/")) return next();
    if (!api) return sendJson(res, 503, { detail: "모의 서버가 아직 준비되지 않았습니다." });
    route(api, req, res, url).catch((error: unknown) => {
      if (res.headersSent) return;
      if (error instanceof HttpError) sendJson(res, error.status, { detail: error.message });
      else sendJson(res, 500, { detail: error instanceof Error ? error.message : String(error) });
    });
  };
  return {
    name: "privframe-mock-api",
    configResolved(config) {
      api = createApi(config.root);
    },
    configureServer(server) {
      server.middlewares.use(handler);
    },
    configurePreviewServer(server) {
      server.middlewares.use(handler);
    },
  };
}
