// The scripted case run. Each step mirrors a node of the approved case graph (docs/design/02, D4).
import type { CaseRun } from "./engine";
import type { Decision, Fact, Finding, Plan, Question } from "../src/api/contract";
import { METHOD_LABEL, SHAPE_LABEL } from "../src/lib/format";
import {
  type Answers,
  applyFinding,
  auditPlan,
  buildAnalyses,
  buildPlan,
  buildReport,
  buildRequirements,
  CAPTION_OPTIONS,
  CONSENT_OPTIONS,
  identityQuestion,
  initialFacts,
  SCENES,
  initialTargets,
  removesLocation,
  roundOneQuestions,
  SCOPE_OPTIONS,
  verificationRows,
} from "./scenario";

const A = "legal_analyst";
const AU = "auditor";

/** what the chosen profile changes in this scripted run (M2 mock only; M4 runs the real parts) */
interface Traits {
  analystEfficient: boolean;
  plannerEfficient: boolean;
  auditor: "none" | "frontier" | "efficient";
  questions: boolean;
  seed: boolean;
  /** extra searches caused by weaker retrieval (low LawIRKo, no reranker, single-mode search) */
  extraSearches: string[];
  plateTracked: boolean;
  outline: boolean;
  vlmLocal: boolean;
  personLocal: boolean;
  ocrApi: boolean;
  inputMode: string;
  perceptionFactor: number;
  render: { label: string; factor: number };
  maxTools: number;
  maxSearches: number;
  maxRounds: number;
  maxQuestions: number;
  maxRetries: number;
  maxGenerations: number;
  auditorMaxTools: number;
}

const DETECTOR_FACTOR: Record<string, number> = { "rfdetr-small": 1.4, "dfine-s": 1.3, yolo26n: 0.8 };
const RENDER: Record<string, { label: string; factor: number }> = {
  "x264-crf16": { label: "libx265 · CRF 16 (HEVC Main 10 원본이라 자동 전환)", factor: 1 },
  "x264-crf14": { label: "libx265 · CRF 14 (HEVC Main 10 원본이라 자동 전환)", factor: 1.15 },
  videotoolbox: { label: "hevc_videotoolbox (Apple 하드웨어)", factor: 0.35 },
};

function traitsOf(run: CaseRun): Traits {
  const p = run.profile.part;
  const n = (id: string, fallback: number) => run.profile.params[id] ?? fallback;
  const extra: string[] = [];
  const lawirko = p["rag.embedding"]?.lawirko;
  if (lawirko === undefined || lawirko < 0.75) extra.push(`임베딩(${p["rag.embedding"]?.short}) 조문 검색 정확도가 낮아 다시 검색`);
  if (p["rag.reranker"]?.adapter === "none") extra.push("리랭커가 없어 상위 결과에 판례가 빠져 다시 검색");
  if (p["rag.retrieval"]?.candidate !== "hybrid-rrf") extra.push(`${p["rag.retrieval"]?.short}만 써서 조문 번호 질의를 따로 검색`);
  let factor = DETECTOR_FACTOR[p["perception.object_detector"]?.candidate ?? ""] ?? 1;
  if (p["perception.segmenter"]?.candidate === "efficienttam-s") factor *= 1.3;
  if (p["perception.segmenter"]?.adapter === "none") factor *= 0.85;
  return {
    analystEfficient: p["llm.analyst"]?.tier === "efficient",
    plannerEfficient: p["llm.planner"]?.tier === "efficient",
    auditor: p["llm.auditor"]?.adapter === "none" ? "none" : p["llm.auditor"]?.tier === "efficient" ? "efficient" : "frontier",
    questions: p["agent.questions"]?.candidate !== "disabled" && n("analyst.max_question_rounds", 2) > 0,
    seed: p["rag.seed"]?.candidate !== "disabled",
    extraSearches: extra,
    plateTracked: p["perception.plate_detector"]?.adapter !== "none",
    outline: p["perception.segmenter"]?.adapter !== "none",
    vlmLocal: p["vlm.scene"]?.kind !== "api",
    personLocal: p["vlm.person"]?.kind !== "api",
    ocrApi: p["perception.ocr"]?.kind === "api",
    inputMode: p["vlm.input_mode"]?.candidate ?? "v2",
    perceptionFactor: factor,
    render: RENDER[p["render.encoder"]?.candidate ?? ""] ?? { label: p["render.encoder"]?.label ?? "인코더", factor: 1 },
    maxTools: n("analyst.max_tool_calls", 25),
    maxSearches: n("analyst.max_searches", 12),
    maxRounds: n("analyst.max_question_rounds", 2),
    maxQuestions: n("analyst.max_questions", 6),
    maxRetries: n("analyst.max_retries", 2),
    maxGenerations: n("planner.max_generations", 3),
    auditorMaxTools: n("auditor.max_tool_calls", 10),
  };
}

/** the same rules in words, shown next to experiment results */
export const MOCK_RULES = [
  "Legal Analyst가 경량 모델이면 참고 패턴 확인과 아동 확인을 건너뛰고, 명찰을 이름 표기로 보지 않아 명찰 요건(REQ-2)을 놓칩니다. 검증기의 커버리지 경고는 아동 확인만 되돌립니다.",
  "상위 모델 Auditor는 문신(F-1), 번호판 구간(F-2), 유지로 남은 명찰·번호판(F-4·F-5)을 찾습니다. 경량 Auditor는 요건 대조와 번호판 구간만 봅니다. '감사 생략'이면 검토 뒤 바로 렌더합니다.",
  "임베딩의 LawIRKo가 0.75 미만이거나, 리랭커가 없거나, 하이브리드 검색이 아니면 그만큼 재검색을 한 번씩 더 합니다. 시드 묶음을 끄면 핵심 조문을 먼저 두 번 검색합니다.",
  "동적 질문을 끄거나 질문 라운드 상한이 0이면 묻지 않고 공개 범위는 불특정 다수, 공개 동의는 없음으로 가정합니다. 라운드당 질문 상한을 넘는 질문은 버립니다.",
  "도구 호출·검색 예산이 바닥나면 남은 단계를 건너뛰고 제출합니다. 번호판 근거를 찾지 못하면 번호판 요건(REQ-3)이 빠집니다.",
  "인용 검증 재시도 상한이 0이면 첫 검증 실패(인용 표기)를 고치지 못하고 미해결로 남깁니다. 경량 Planner는 계획 검증에 한 번 실패해 다시 생성합니다.",
  "번호판 검출기를 끄면 번호판 트랙이 없어 어떤 단계도 번호판을 가릴 수 없습니다. 윤곽 마스크를 끄면 과잉 처리 권고(F-3)를 적용할 수 없습니다.",
  "로컬 VLM은 비용이 0이지만 장면·인물 설명과 시각 재점검이 느립니다. 검출기·인코더 선택은 단계 소요 시간만 바꿉니다.",
  "같은 실험의 반복 실행은 시드에 따라 지연 ±8%, 토큰 ±5~10%가 달라지고 결정은 같습니다.",
];

/** what the analyst established in this run; requirements and decisions follow from it */
const knowledge = new WeakMap<CaseRun, Set<string>>();
const REQUIREMENT_NEEDS: Record<string, string> = { "REQ-1": "third_party", "REQ-2": "nametag", "REQ-3": "plate" };
const ANALYSIS_NEEDS: Record<string, string> = { "A-1": "third_party", "A-2": "nametag", "A-3": "plate" };

function knows(run: CaseRun): Set<string> {
  return knowledge.get(run) ?? new Set(["third_party", "nametag", "plate"]);
}

function analysesFor(run: CaseRun, answers: Answers) {
  const k = knows(run);
  return buildAnalyses(answers).filter((a) => !ANALYSIS_NEEDS[a.id] || k.has(ANALYSIS_NEEDS[a.id]));
}

function requirementsFor(run: CaseRun, answers: Answers) {
  const k = knows(run);
  return buildRequirements(answers).filter((r) => !REQUIREMENT_NEEDS[r.id] || k.has(REQUIREMENT_NEEDS[r.id]));
}

/** decisions whose requirement was never derived stay "keep"; an untracked plate has no decision at all */
export function planFor(run: CaseRun, answers: Answers): Plan {
  const plan = buildPlan(answers);
  const reqs = new Set(requirementsFor(run, answers).map((r) => r.id));
  const plateTracked = traitsOf(run).plateTracked;
  const decisions = plan.decisions
    .filter((d) => plateTracked || d.targetId !== "plate-1")
    .map((d) => {
      const lost = d.satisfies.filter((id) => !reqs.has(id));
      if (!lost.length || d.method === "keep") return d;
      return { ...d, method: "keep" as const, satisfies: d.satisfies.filter((id) => reqs.has(id)), rationale: `요건 ${lost.join(", ")}이(가) 도출되지 않아 처리하지 않습니다.`, risk: "high" as const };
    });
  return { ...plan, decisions };
}

type QuestionAnswers = Record<string, string>;

export async function runCase(run: CaseRun): Promise<void> {
  const t = traitsOf(run);
  run.emit({
    kind: "run.start",
    title: run.detail.title,
    profile: run.profile.name,
    profileId: run.profile.id,
    configHash: run.profile.configHash,
    models: run.models,
    components: run.profile.components,
    costCapUsd: run.detail.costCapUsd,
    forkedFrom: run.opts.forkedFrom,
  });

  /* ---------- ingest + perceive ---------- */
  run.setStatus("ANALYZING");
  run.edge("__start__", "ingest");
  await run.node("ingest", 900, {
    writes: ["video"],
    body: () =>
      run.state("video", "ingest", "원본 보존 · 1920×1080 · 2,158프레임 · 가변 프레임레이트 · HEVC Main 10 · 오디오 AAC"),
  });
  run.activity("원본을 수정하지 않고 보존했고, 메타데이터를 기록했습니다.", "info");
  run.checkpoint(["perceive"]);
  run.edge("ingest", "perceive");

  const perceiveTask = "perceive#sub";
  run.emit({ kind: "node.start", node: "perceive", task: perceiveTask });
  await run.node("perceive/ingest_probe", 700, { writes: ["probe"] });
  run.edge("perceive/ingest_probe", "perceive/make_proxy");
  await run.node("perceive/make_proxy", 1600, {
    writes: ["proxy", "pts_map"],
    body: () => run.state("pts_map", "perceive/make_proxy", "프록시 640p · 2,158프레임 · PTS 목록 원본과 동일 (assert 통과)"),
  });
  run.activity("분석용 축소 사본을 만들었습니다. 프레임 수와 타임스탬프가 원본과 같습니다.", "info");
  run.edge("perceive/make_proxy", "perceive/detect_track");
  const part = (slot: string) => run.part(slot)?.short ?? "";
  await run.node("perceive/detect_track", 3200 * t.perceptionFactor, {
    writes: ["tracks"],
    body: () =>
      run.state(
        "tracks",
        "perceive/detect_track",
        `얼굴 9 · 전신 6 · ${t.plateTracked ? "번호판 1" : "번호판 검출 안 함"} · 텍스트 4 트랙 (${[part("perception.face_detector"), part("perception.object_detector"), part("perception.tracker")].join(" · ")})`,
      ),
  });
  await run.parallel([
    async () => {
      run.edge("perceive/detect_track", "perceive/group_identities");
      await run.node("perceive/group_identities", 1800, {
        writes: ["person_groups"],
        body: () => run.state("person_groups", "perceive/group_identities", `인물 2 · 불확실 묶음 1 (T7·T9, 유사도 0.63 · ${part("perception.face_embedding")})`),
      });
      run.edge("perceive/group_identities", "perceive/describe_persons");
      await run.node("perceive/describe_persons", 1800 * (t.personLocal ? 3 : 1), {
        writes: ["person_attributes"],
        body: () => {
          run.llm("perceive/describe_persons", "perceive", "vlm_person", 3600, 420, 900);
          run.llm("perceive/describe_persons", "perceive", "vlm_person", 3400, 380, 860);
        },
      });
    },
    async () => {
      run.edge("perceive/detect_track", "perceive/ocr");
      await run.node("perceive/ocr", 2400 * (t.ocrApi ? 1.2 : 1), {
        writes: ["text_items"],
        body: () => {
          if (t.ocrApi) for (let i = 0; i < 4; i += 1) run.llm("perceive/ocr", "perceive", "ocr", 1500, 120, 500);
          run.state("text_items", "perceive/ocr", `명찰 \"김○○ 바리스타\" 0.81 · 간판 \"○○커피\" 0.95 · 메뉴판 0.88 (${part("perception.ocr")})`);
        },
      });
    },
    async () => {
      run.edge("perceive/detect_track", "perceive/describe_segments");
      await run.node("perceive/describe_segments", 3000 * (t.vlmLocal ? 3 : 1), {
        writes: ["segments"],
        body: () => {
          for (let i = 0; i < 4; i += 1) run.llm("perceive/describe_segments", "perceive", "vlm", 9200, 860, 700);
          const sent = t.vlmLocal ? "로컬 VLM · 전송 없음" : t.inputMode === "v1" ? "원본 프레임 전송 (ZDR)" : "얼굴 익명화 후 전송";
          run.state("segments", "perceive/describe_segments", `장면 4 · 카페 영업장 · 인터뷰 · 아동 없음 · ${sent} (${part("vlm.scene")})`);
        },
      });
    },
  ]);
  for (const from of ["describe_persons", "ocr", "describe_segments"]) run.edge(`perceive/${from}`, "perceive/assemble");
  await run.node("perceive/assemble", 400, {
    writes: ["understanding"],
    body: () => {
      run.detail.perception = { scenes: 4, people: 2, plates: 1, texts: 3, tracks: 9 };
      run.detail.segments = SCENES.map((s) => ({ id: s.id, start: s.range[0], end: s.range[1], label: s.label }));
      run.detail.targets = initialTargets().filter((x) => t.plateTracked || x.id !== "plate-1");
      run.state("understanding", "perceive/assemble", "VideoUnderstanding: 장면 4 · 인물 2 · 번호판 1 · 텍스트 3 (원본 PTS 기준)");
    },
  });
  run.emit({ kind: "node.end", node: "perceive", task: perceiveTask, result: "ok", costUsd: 0, writes: ["understanding", "targets"] });
  run.activity(
    `사람 2명, ${t.plateTracked ? "번호판 1개, " : ""}글자 3곳, 장면 4개를 찾았습니다. ${t.vlmLocal && t.personLocal ? "장면 설명은 이 컴퓨터의 모델로 했습니다." : t.inputMode === "v1" ? "원본 프레임을 외부 모델에 보냈습니다." : "얼굴은 익명화한 뒤 외부 모델에 보냈습니다."}`,
    "done",
  );

  /* ---------- identity confirmation ---------- */
  run.edge("perceive", "await_identity");
  run.detail.identity = identityQuestion();
  run.setStatus("AWAITING_IDENTITY");
  run.activity("같은 사람인지 확인이 필요한 장면이 1곳 있습니다.", "wait");
  const identity = await run.interrupt("await_identity", "identity", "묶음 불확실 1건 (T7·T9 ↔ person-3)", () => "same");
  const identityValue = String(identity.value) as Answers["identity"];
  run.detail.identity = { ...run.detail.identity, status: "answered", answer: identityValue };
  const identityLabel = { same: "같은 사람", different: "다른 사람", unknown: "모르겠음 → 같은 사람으로 처리" }[identityValue] ?? identityValue;
  run.endInterrupt("await_identity", `${identityLabel}${identity.auto ? " (자동 응답)" : ""}`, identity.auto);
  run.state("targets", "await_identity", `T7·T9 → ${identityValue === "different" ? "새 인물(보수적으로 person-3과 같은 처리)" : "person-3에 병합"}`);
  run.checkpoint([`${A}/model`]);
  run.edge("await_identity", `${A}/model`);

  /* ---------- legal analyst ---------- */
  const answers: Answers = { scope: "public", scopeAssumed: true, consent: false, consentAssumed: true, captions: "deferred", identity: identityValue };
  run.answers = answers;
  await legalAnalyst(run, answers);

  /* ---------- plan + review guide ---------- */
  run.edge(`${A}/validate`, "plan");
  run.setStatus("PLANNING");
  run.activity(`보호 요건 ${run.detail.requirements.length}건을 처리 계획으로 바꾸고 있습니다.`, "info");
  await run.node("plan", 1800, {
    writes: ["plan"],
    body: async () => {
      run.llm("plan", "planner", "planner", 5200, 1400, 1700);
      let plan = planFor(run, answers);
      if (t.plannerEfficient) {
        run.emit({ kind: "validator", node: "plan", ok: false, issues: ["D-2가 충족 요건에 연결되지 않았습니다 (생성 1회차)"] });
        if (t.maxGenerations >= 2) {
          await run.wait(900);
          run.llm("plan", "planner", "planner", 5900, 1300, 1500);
        } else {
          const unmet = requirementsFor(run, answers).filter((r) => r.id === "REQ-2");
          plan = { ...plan, unsatisfied: unmet.map((r) => ({ requirementId: r.id, reason: "계획 생성 상한이 1회라 D-2와의 연결 오류를 고치지 못함" })) };
          run.activity("계획 생성 상한이 1회라 요건 연결 오류를 고치지 못했습니다.", "warn");
        }
      }
      run.detail.plan = plan;
      run.emit({ kind: "validator", node: "plan", ok: plan.unsatisfied.length === 0, issues: plan.unsatisfied.map((u) => `${u.requirementId} 미충족`) });
      run.state("plan", "plan", `계획 v1 · 결정 ${plan.decisions.length}건 · 미충족 ${plan.unsatisfied.length}`);
    },
  });
  run.checkpoint(["review_guide"]);
  run.edge("plan", "review_guide");
  await run.node("review_guide", 1600, {
    writes: ["review_guide"],
    body: () => {
      run.llm("review_guide", "review_guide", "review_guide", 4100, 1100, 1500);
      run.state("review_guide", "review_guide", "결정별 근거 · 유사 사례 · 수정 시 위험 · 우선순위");
    },
  });
  run.edge("review_guide", "await_review");

  /* ---------- review → audit loop (direct edits allowed once) ---------- */
  let rounds = 0;
  for (;;) {
    rounds += 1;
    await reviewStep(run);
    const passed = await auditStep(run, rounds);
    if (passed) break;
  }

  /* ---------- render, verify, report ---------- */
  run.setStatus("RENDERING");
  run.activity("원본 스트림에 처리 결과를 입히고 있습니다.", "info");
  const steps = [
    { label: "원본 디코드 (PyAV, 센서 방향 · PTS 기록)", ms: 1100 },
    { label: "결정 구간의 프레임만 YUV 평면에서 수정", ms: 1700 },
    { label: `영상 재인코딩 (${t.render.label} · 원본 time_base)`, ms: 1900 * t.render.factor },
    { label: "오디오 · 자막 · 메타데이터 스트림 바이트 복사", ms: 600 },
  ];
  run.detail.renderSteps = [...steps.map((s) => ({ label: s.label, state: "pending" as const })), { label: "출력 검증", state: "pending" as const }];
  await run.node("render_original", 0, {
    writes: ["output"],
    body: async () => {
      for (const [i, step] of steps.entries()) {
        run.detail.renderSteps[i] = { ...run.detail.renderSteps[i], state: "running" };
        run.state("render_progress", "render_original", step.label);
        await run.wait(step.ms);
        run.detail.renderSteps[i] = { ...run.detail.renderSteps[i], state: "done" };
      }
    },
  });
  run.edge("render_original", "verify_output");
  const verifyIndex = steps.length;
  run.detail.renderSteps[verifyIndex] = { ...run.detail.renderSteps[verifyIndex], state: "running" };
  await run.node("verify_output", 2200, {
    writes: ["verification"],
    body: () => run.state("verification", "verify_output", "프레임 수 · PTS · 오디오 framemd5 · 메타 diff · 처리 영역 밖 PSNR/SSIM · VMAF · 재검출"),
  });
  // how many checks there are depends on the plan (removing the location tag adds one), so the count is read, not written down
  const checks = verificationRows(removesLocation(run.detail.plan as Plan));
  const passed = checks.filter((row) => row.verdict !== "fail").length;
  run.detail.renderSteps[verifyIndex] = { ...run.detail.renderSteps[verifyIndex], state: "done", detail: `${passed} / ${checks.length} 통과` };
  run.activity(`출력 검증 ${checks.length}항목을 모두 통과했습니다.`, "done");
  run.edge("verify_output", "report");
  await run.node("report", 1500, {
    writes: ["report"],
    body: () => {
      run.llm("report", "report", "report", 6100, 2300, 1400);
    },
  });
  run.detail.report = buildReport({
    plan: run.detail.plan as Plan,
    requirements: run.detail.requirements,
    findings: run.detail.findings,
    questions: run.detail.questions,
    answers,
    costUsd: run.detail.costUsd,
    elapsedSec: Math.round(run.now() / 1000),
    edits: run.edits,
  });
  run.detail.report.output.codec = `HEVC Main 10 · ${t.render.label.split(" (")[0]}`;
  run.state("report", "report", `초 단위 스크립트 ${run.detail.report.script.length}행 · 인용 ${run.detail.report.citations.length}건`);
  run.edge("report", "__end__");
  run.setStatus("COMPLETED");
  run.activity("완료되었습니다. 출력 영상과 보고서를 확인하세요.", "done");
  run.emit({ kind: "run.end", status: "COMPLETED" });
}

export async function legalAnalyst(run: CaseRun, answers: Answers): Promise<void> {
  const t = traitsOf(run);
  const known = new Set<string>();
  knowledge.set(run, known);
  const task = `${A}#agent`;
  let tools = 0;
  let searches = 0;
  let skipped = false;
  // one call is always kept for finalize, so the loop can end with a structured result
  const canTool = () => tools < t.maxTools - 1;
  const canSearch = () => canTool() && searches < t.maxSearches;
  const exhausted = (what: string) => {
    if (skipped) return;
    skipped = true;
    run.activity(`${what} 예산이 바닥나 남은 단계를 건너뛰고 제출합니다.`, "warn");
    run.message(A, "user", `예산 소진: ${what} — 남은 단계를 건너뛰고 finalize 하라.`);
  };
  const useTool = async (
    tool: string,
    args: string,
    ms: number,
    result: string | (() => string),
    opts: { search?: boolean; ok?: boolean; writes?: string[]; during?: () => Promise<void> } = {},
  ) => {
    tools += 1;
    if (opts.search) {
      searches += 1;
      run.trace.searches += 1;
    }
    run.budget(A, "tool_calls", tools, t.maxTools);
    if (opts.search) run.budget(A, "searches", searches, t.maxSearches);
    return run.tool(A, tool, args, ms, result, opts);
  };
  const search = async (query: string, kinds: string, ms: number, result: string) => {
    if (!canSearch()) {
      exhausted(canTool() ? "검색" : "도구 호출");
      return false;
    }
    await useTool("search_law", `query="${query}" kinds=[${kinds}] as_of=2026-11-05 k=${run.profile.params["rag.top_k"] ?? 8}`, ms, result, { search: true });
    return true;
  };

  run.setStatus("ANALYZING_LEGAL");
  run.emit({ kind: "node.start", node: A, task });
  run.detail.facts = initialFacts(run.opts.memo);
  run.state("facts", A, `알려진 사실 ${run.detail.facts.length}건 (선언 ${run.detail.facts.filter((f) => f.provenance === "declared").length} · 추론 ${run.detail.facts.filter((f) => f.provenance === "inferred").length})`);
  if (t.seed) {
    run.state("seed_bundle", A, "핵심 문서 9건 사전 로딩: 제2조 · 제15조 · 제17조 · 제25조 · 제25조의2 · 제58조 · 제58조의2 · 표준지침 영상 조항 · 가명 가이드라인 영상 절");
  }
  run.budget(A, "tool_calls", 0, t.maxTools);
  run.budget(A, "searches", 0, t.maxSearches);
  run.budget(A, "question_rounds", 0, t.maxRounds);
  run.budget(A, "retries", 0, t.maxRetries);
  run.message(
    A,
    "system",
    "한국 개인정보 보호법 분석가. 관찰·사실·검색한 근거만으로 판단하고, 근거가 없으면 uncertain으로 표시한다. 질문은 사실이 부족할 때만, 영향이 있는 것만 만든다. (prompt analyst@c41e)",
  );
  run.message(
    A,
    "user",
    `관찰: 장면 4 · person-2 인터뷰 대상 · person-3 직원(명찰) · ${t.plateTracked ? "창밖 번호판 1 · " : ""}간판 · 메뉴판. 사실: 촬영 목적=인터뷰 영상 게시, person-2 촬영 동의=예, 장소=카페 영업장(추론 0.92), 아동=아니오(추론 0.88).`,
  );
  run.activity("법령과 판례를 찾고 있습니다: 동의하지 않은 사람의 얼굴을 공개할 때의 요건", "info");

  if (!t.seed) {
    await run.think(A, "사전 로딩된 조문이 없다. 개인정보의 정의와 영상 공개 요건 조문부터 찾는다.", 5600, 140, 1100);
    await search("개인정보 정의 제2조 제1호", "법령", 700, "규범 2 — 제2조제1호, 제2조제1호가목");
    await search("영상 공개 제3자 제공 요건 조문", "법령, 고시", 700, "규범 3 — 제17조제1항, 제15조제1항, 표준지침 제2조제9호");
  }
  await run.think(A, "직원(person-3)은 동의 정보가 없는 제3자다. 영상 공개 시 제3자 얼굴을 처리해야 하는 요건부터 확인한다.", 6400, 310, 1500);
  if (await search("제3자 얼굴 영상 공개 동의 요건", "법령, 판례", 900, "규범 3 · 해석 2 — 제17조제1항, 제15조제1항, 제2조제1호, 대법원 2004다16280, 2020다227455")) known.add("third_party");
  for (const reason of t.extraSearches) {
    await run.think(A, `${reason}한다.`, 6800, 120, 800);
    await search("제3자 얼굴 공개 판례 초상권 이익형량", "판례", 800, "해석 2 — 대법원 2004다16280, 2020다227455");
  }
  if (t.analystEfficient) {
    run.message(A, "assistant", "명찰 글자는 OCR 결과로 확인했다. 직원 얼굴을 처리하면 충분하다고 본다.");
    run.causes.set("text-1", "경량 분석 모델이 명찰을 이름 표기로 보지 않아 명찰 요건(REQ-2)을 만들지 않음");
  } else if (canTool()) {
    await run.think(A, "person-3 가슴의 글자가 이름이 적힌 명찰인지 다시 본다.", 7100, 160, 1100);
    await useTool("inspect_visual", "target=person-3 at=00:12.4 question=\"가슴의 텍스트가 이름이 적힌 명찰인가\"", 1200, "예 — \"김○○ 바리스타\" (OCR 0.81, 성명 추정)");
    if (canTool()) {
      await run.think(A, "명찰의 성명이 그 자체로 개인정보인지 정의 조문의 원문을 확인한다.", 7400, 120, 900);
      await useTool("get_document", "citation=\"개인정보 보호법 제2조제1호\" span=가목·나목", 600, "가목: 성명, 주민등록번호 및 영상 등을 통하여 개인을 알아볼 수 있는 정보 · 나목: 다른 정보와 쉽게 결합하여 알아볼 수 있는 정보");
      known.add("nametag");
      run.activity("명찰 글자를 다시 확인했습니다. 이름이 적힌 명찰로 보입니다.", "info");
    }
  }
  if (!known.has("nametag") && !run.causes.has("text-1")) run.causes.set("text-1", "도구 호출 예산이 바닥나 명찰의 성명이 개인정보인지 조문을 확인하지 못함");
  if (t.plateTracked) {
    await run.think(A, "창밖 차량 번호판이 개인정보인지 확인한다.", 7800, 110, 900);
    if (await search("차량등록번호 개인정보 해당 여부", "법령, 안내서", 800, "규범 2 · 참고 1 — 제2조제1호, 가명정보 처리 가이드라인(2026.3) 비정형데이터 장")) known.add("plate");
    else run.causes.set("plate-1", "검색 예산이 바닥나 번호판 근거를 찾지 못해 번호판 요건(REQ-3)이 빠짐");
  } else {
    run.causes.set("plate-1", "번호판 검출기를 끈 프로필이라 번호판을 관찰하지 못함");
  }
  if (!t.analystEfficient && canTool()) {
    await run.think(A, "참고 패턴과 관찰을 대조해 놓친 쟁점이 있는지 본다.", 8200, 90, 700);
    await useTool("checklist", "observations=[아동?, 텍스트, 번호판, 장소]", 300, "참고 패턴 17건 · 관찰 기반 힌트 3건 (아동 가능성, 텍스트·자막, 번호판)");
    if (canTool()) {
      await run.think(A, "아동이 포함됐는지 시각적으로 확인한다.", 8400, 90, 800);
      await useTool("inspect_visual", "range=00:00–01:12 question=\"미성년자로 보이는 인물이 있는가\"", 1100, "아니오 (0.88)");
    }
  }
  await run.think(A, "인터뷰 대상의 동의는 촬영에 대한 것으로 보인다. 촬영 동의와 공표 동의의 관계를 확인한다.", 8800, 140, 1000);
  await search("촬영 동의 공표 동의 구별", "판례", 800, "해석 2 — 대법원 2021다219116, 2020다227455");

  // ask_user: question gate → interrupt → answers
  const questions = roundOneQuestions();
  const generated = questions.filter((q) => q.status === "pending");
  const allowed = t.questions && canTool() ? generated.slice(0, t.maxQuestions) : [];
  const dropReason = !t.questions
    ? "이 프로필은 동적 질문을 쓰지 않아 보수적 가정으로 진행합니다."
    : !canTool()
      ? "도구 호출 예산이 바닥나 묻지 못했습니다."
      : `라운드당 질문 상한 ${t.maxQuestions}개를 넘어 묻지 않았습니다.`;
  run.detail.questions = questions.map((q) =>
    q.status === "pending" && !allowed.includes(q) ? { ...q, status: "removed" as const, removedReason: dropReason } : q,
  );
  if (allowed.length === 0) {
    run.message(A, "assistant", `질문하지 않는다: ${dropReason} 공개 범위는 불특정 다수, 공개 동의는 없음으로 가정한다.`);
    run.state("questions", "질문 게이트", `생성 ${questions.length} → 질문 0 (${dropReason})`);
    run.activity(dropReason, "warn");
  } else {
    await run.think(
      A,
      `영상과 메모로 알 수 없는데 판단을 바꾸는 사실이 ${allowed.length}가지 있다: ${allowed.map((q) => q.prompt.replace(/\?$/, "")).join(" / ")}. 장소 질문은 이미 아는 사실이라 게이트가 거를 것이다.`,
      9300,
      640,
      1600,
    );
    run.countQuestion(allowed.length);
    run.budget(A, "question_rounds", 1, t.maxRounds);
    await useTool(
      "ask_user",
      `questions=[${questions.map((q) => q.factKey).join(", ")}]`,
      300,
      () => `답변: ${allowed.map((q) => `${q.factKey}=${run.detail.questions.find((x) => x.id === q.id)?.answer ?? "나중에"}`).join(", ")}`,
      {
        writes: ["questions", "facts"],
        during: async () => {
          run.state("questions", "질문 게이트", `생성 ${questions.length} → 제거 ${questions.length - allowed.length}(이미 아는 사실 · 영향 없음 · 상한) → 질문 ${allowed.length}`);
          run.edge(`${A}/ask_user`, "await_answers");
          run.setStatus("AWAITING_ANSWERS");
          run.activity(`판단에 필요한 사실 ${allowed.length}가지를 여쭤봅니다.`, "wait");
          const auto: QuestionAnswers = Object.fromEntries(allowed.map((q) => [q.id, ({ Q1: "public", Q2: "yes", Q3: "later" } as QuestionAnswers)[q.id] ?? "later"]));
          const reply = await run.interrupt<QuestionAnswers>("await_answers", "questions", `질문 ${allowed.length}건 (라운드 1/${t.maxRounds})`, () => auto);
          applyAnswers(run, answers, reply.value);
          const summary = allowed.map((q) => `${q.id}=${labelOf(q, reply.value[q.id])}`).join(" · ");
          run.endInterrupt("await_answers", `${summary}${reply.auto ? " (자동 응답)" : ""}`, reply.auto);
          run.edge(`${A}/ask_user`, "await_answers", "back");
          run.setStatus("ANALYZING_LEGAL");
        },
      },
    );
    run.activity(`답변을 반영해 공개 범위·동의에 의존하는 판단을 다시 평가합니다.`, "info");
    await run.think(A, "답변을 반영해 공개 범위와 동의에 의존하는 분석 A-1~A-4만 다시 평가한다.", 9900, 220, 1200);
  }
  if (!allowed.some((q) => q.id === "Q2")) run.causes.set("person-2", `공개 동의를 묻지 못해 보수적으로 처리함 (${dropReason})`);
  // dedupe guard
  if (canTool()) {
    await useTool("search_law", "query=\"제3자 얼굴 영상 공개 동의 요건\" kinds=[법령, 판례]", 120, "중복 질의 차단 — 이전 검색 결과를 재사용합니다", { ok: false });
    run.trace.dedupeBlocks += 1;
  }
  if (!t.analystEfficient) {
    await run.think(A, "간판의 상호가 직원 식별에 기여하는지 근거를 찾는다.", 10200, 100, 800);
    await search("상호 간판 결합 용이성 개인정보", "안내서, 결정례", 700, "참고 1 · 판단 근거 부족");
  }

  const analyses = analysesFor(run, answers);
  const requirements = requirementsFor(run, answers);
  run.trace.citations = analyses.reduce((n, a) => n + a.citations.length, 0);
  await run.think(A, `분석 ${analyses.length}건, 요건 ${requirements.length}건, 미해결 1건을 정리해 제출한다.`, 10800, 1450, 2200);
  await useTool("finalize", `analyses=${analyses.length} requirements=${requirements.length} unresolved=1`, 300, "제출됨 → 인용 검증기");

  // validator: first attempt fails on a citation format
  run.edge(`${A}/finalize`, `${A}/validate`);
  await run.node(`${A}/validate`, 400, {
    writes: ["validation"],
    body: () => {
      run.emit({ kind: "validator", node: `${A}/validate`, ok: false, issues: ["인용 표기 불일치: \"제15조 제1항 1호\" → 원문 표기 \"제15조제1항제1호\""] });
    },
  });
  if (t.maxRetries < 1) {
    run.trace.unresolvedCitations += 1;
    run.activity("인용 검증 재시도 상한이 0이라 인용 표기 1건을 고치지 못하고 미해결로 남겼습니다.", "warn");
  } else {
    run.budget(A, "retries", 1, t.maxRetries);
    run.edge(`${A}/validate`, `${A}/model`);
    run.activity("인용 표기 1건이 원문과 달라 고쳤습니다.", "warn");
    if (t.analystEfficient) {
      run.trace.coverageWarnings += 1;
      run.emit({ kind: "validator", node: `${A}/validate`, ok: false, issues: ["커버리지 경고: 관찰에 있는 '아동 가능성' 패턴이 검토되지 않았습니다 (질문 형태로 1회 반환)"] });
      await run.think(A, "커버리지 경고에 따라 아동 포함 가능성을 확인한다.", 11200, 90, 700);
      await useTool("inspect_visual", "range=00:00–01:12 question=\"미성년자로 보이는 인물이 있는가\"", 1100, "아니오 (0.88)");
    }
    await run.think(A, "검증기 지적에 따라 인용 표기를 원문과 같게 고쳐 다시 제출한다.", 11400, 380, 900);
    await useTool("finalize", `analyses=${analyses.length} requirements=${requirements.length} unresolved=1`, 300, "제출됨 → 인용 검증기");
    run.edge(`${A}/finalize`, `${A}/validate`);
  }
  await run.node(`${A}/validate`, 400, {
    writes: ["validation"],
    body: () => {
      const unresolved = run.trace.unresolvedCitations > 0;
      run.emit({ kind: "validator", node: `${A}/validate`, ok: !unresolved, issues: unresolved ? ["인용 표기 1건 미해결 (재시도 상한 0) → 보고서에 기록"] : [] });
      run.detail.analyses = analyses;
      run.detail.requirements = requirements;
      run.state("analyses", A, `분석 ${analyses.length}건 · 적용 ${analyses.filter((x) => x.applicability === "applies").length} · 불확실 ${analyses.filter((x) => x.applicability === "uncertain").length}`);
      run.state("requirements", A, requirements.map((r) => r.id).join(" · ") || "요건 없음 (공개하지 않음)");
    },
  });
  run.emit({ kind: "node.end", node: A, task, result: "ok", costUsd: 0, writes: ["analyses", "requirements", "facts", "questions"] });
  run.activity(`보호 요건 ${requirements.length}건을 정리했습니다.`, "done");
  run.checkpoint(["plan"]);
}

function labelOf(q: Question, value: string | undefined): string {
  if (!value) return "?";
  if (value === "unknown") return "모름";
  if (value === "later") return "나중에";
  return q.options.find((o) => o.value === value)?.label ?? value;
}

export function applyAnswers(run: CaseRun, answers: Answers, values: QuestionAnswers): void {
  const questions = run.detail.questions.map((q) => {
    if (q.status === "removed" || !(q.id in values)) return q;
    const value = values[q.id];
    return value === "later" ? { ...q, status: "deferred" as const, answer: undefined } : { ...q, status: "answered" as const, answer: value };
  });
  run.detail.questions = questions;
  const v = (id: string) => questions.find((q) => q.id === id);
  const q1 = v("Q1");
  const q2 = v("Q2");
  const q3 = v("Q3");
  const scope = q1?.status === "answered" && q1.answer && q1.answer !== "unknown" ? (q1.answer as Answers["scope"]) : "public";
  answers.scope = scope;
  answers.scopeAssumed = !(q1?.status === "answered" && q1.answer !== "unknown");
  answers.consent = q2?.status === "answered" && q2.answer === "yes";
  answers.consentAssumed = !(q2?.status === "answered" && q2.answer !== "unknown");
  answers.captions = q3?.status === "answered" && (q3.answer === "yes" || q3.answer === "no") ? q3.answer : "deferred";

  const answered = (key: string, label: string, value: string, valueLabel: string, options: { value: string; label: string }[]): Fact => ({
    key,
    label,
    value,
    valueLabel,
    provenance: "answered",
    source: "질문 답변",
    editable: true,
    options,
  });
  const keep = run.detail.facts.filter((f) => !["purpose.publication_scope", "person-2.publication_consent", "edit.caption_names"].includes(f.key));
  const added: Fact[] = [];
  if (q1 && q1.status !== "deferred" && q1.status !== "pending") {
    const opt = SCOPE_OPTIONS.find((o) => o.value === q1.answer);
    added.push(answered("purpose.publication_scope", "공개 범위", q1.answer ?? "unknown", opt?.label ?? "모름 (불특정 다수로 가정)", SCOPE_OPTIONS));
  }
  if (q2 && q2.status !== "deferred" && q2.status !== "pending") {
    const opt = CONSENT_OPTIONS.find((o) => o.value === q2.answer);
    added.push(answered("person-2.publication_consent", "person-2 공개 동의", q2.answer ?? "unknown", opt?.label ?? "모름 (동의 없음으로 가정)", CONSENT_OPTIONS));
  }
  if (q3 && q3.status !== "deferred" && q3.status !== "pending") {
    const opt = CAPTION_OPTIONS.find((o) => o.value === q3.answer);
    added.push(answered("edit.caption_names", "자막 이름 표기", q3.answer ?? "unknown", opt?.label ?? "모름", CAPTION_OPTIONS));
  }
  run.detail.facts = [...keep, ...added];
  run.state("facts", "await_answers", added.map((f) => `${f.key}=${f.value}`).join(" · ") || "새 사실 없음 (모두 나중에)");
}

/** user edits or late answers during review: re-evaluate only what depends on the changed facts */
export async function reevaluate(run: CaseRun, answers: Answers, reason: string): Promise<{ changed: string[] }> {
  const before = run.detail.plan;
  run.emit({ kind: "node.start", node: `${A}/model`, task: `${A}/model#reeval-${run.events.length}` });
  run.message(A, "user", `사실 변경: ${reason}`);
  await run.wait(900);
  run.llm(`${A}/model`, A, "analyst", 6400, 520, 900, 5400);
  run.detail.analyses = analysesFor(run, answers);
  run.detail.requirements = requirementsFor(run, answers);
  const next = planFor(run, answers);
  const changed: string[] = [];
  if (before) {
    for (const d of next.decisions) {
      const old = before.decisions.find((x) => x.id === d.id);
      if (!old || old.method !== d.method) changed.push(d.id);
    }
    next.version = before.version + 1;
  }
  run.detail.plan = next;
  run.message(A, "assistant", changed.length ? `의존 분석을 재평가했다. 바뀐 결정: ${changed.join(", ")}` : "의존 분석을 재평가했다. 결정 변경 없음.");
  run.emit({ kind: "node.end", node: `${A}/model`, task: `${A}/model#reeval`, result: "ok", costUsd: 0, writes: ["analyses", "requirements", "plan"] });
  run.state("plan", A, `계획 v${next.version} · 재평가 사유: ${reason} · 변경 ${changed.length}건`);
  run.activity(changed.length ? `답변을 반영해 결정 ${changed.length}건이 바뀌었습니다 (${changed.join(", ")}).` : "답변을 반영해 다시 평가했습니다. 결정은 그대로입니다.", changed.length ? "warn" : "info");
  return { changed };
}

export async function reviewStep(run: CaseRun): Promise<void> {
  run.setStatus("AWAITING_REVIEW");
  const deferred = run.detail.questions.filter((q) => q.status === "deferred").length;
  run.activity(`검토할 준비가 끝났습니다. 결정 ${run.detail.plan?.decisions.length ?? 0}건${deferred ? ` · 미답 질문 ${deferred}건` : ""}`, "wait");
  const reply = await run.interrupt<{ decisions: Decision[] }>(
    "await_review",
    "review",
    `결정 ${run.detail.plan?.decisions.length ?? 0}건 · 미답 질문 ${deferred}건`,
    () => ({ decisions: run.detail.plan?.decisions ?? [] }),
  );
  const plan = run.detail.plan as Plan;
  const incoming = reply.value.decisions;
  const edits: string[] = [];
  const merged = plan.decisions.map((d) => {
    const next = incoming.find((x) => x.id === d.id);
    if (!next) return d;
    const methodChanged = next.method !== d.method;
    const shapeChanged = next.shape !== d.shape;
    const rangeChanged = JSON.stringify(next.ranges) !== JSON.stringify(d.ranges);
    if (!methodChanged && !shapeChanged && !rangeChanged) return d;
    const parts = [
      methodChanged ? `처리 ${METHOD_LABEL[d.method]}→${METHOD_LABEL[next.method]}` : "",
      shapeChanged && d.shape && next.shape ? `형태 ${SHAPE_LABEL[d.shape]}→${SHAPE_LABEL[next.shape]}` : "",
      rangeChanged ? "구간 수정" : "",
    ].filter(Boolean);
    edits.push(`검토 변경 ${d.id} ${d.targetLabel}: ${parts.join(", ")}`);
    return { ...d, method: next.method, shape: next.shape, ranges: next.ranges, source: "user" as const, edited: true };
  });
  run.detail.plan = { ...plan, version: plan.version + (edits.length ? 1 : 0), decisions: merged };
  run.edits.push(...edits);
  run.endInterrupt("await_review", `${edits.length ? `수정 ${edits.length}건` : "수정 없음"}${reply.auto ? " (자동 응답)" : ""}`, reply.auto);
  if (edits.length) run.state("plan", "await_review", `계획 v${run.detail.plan.version} · 사용자 수정 ${edits.length}건`);
}

export async function auditStep(run: CaseRun, round: number): Promise<boolean> {
  const t = traitsOf(run);
  const outline = { outline: t.outline };
  if (t.auditor === "none") {
    run.activity("이 프로필은 감사를 생략합니다. 검토가 끝난 계획으로 바로 렌더합니다.", "warn");
    run.state("findings", "await_review", "감사 생략 (프로필 설정)");
    run.causes.set("person-3-tattoo", "감사를 생략한 프로필이라 문신을 찾지 못함");
    return true;
  }
  run.edge("await_review", `${AU}/model`);
  run.setStatus("AUDITING");
  run.activity("다른 모델이 수정된 계획을 다시 검증하고 있습니다.", "info");
  const task = `${AU}#agent-${round}`;
  run.emit({ kind: "node.start", node: AU, task });
  let tools = 0;
  const useTool = async (tool: string, args: string, ms: number, result: string) => {
    if (tools >= t.auditorMaxTools) return false;
    tools += 1;
    run.budget(AU, "tool_calls", tools, t.auditorMaxTools);
    await run.tool(AU, tool, args, ms, result);
    return true;
  };
  if (round === 1) {
    run.message(AU, "system", "독립 감사자. 계획이 요건을 충족하는지, 잔여 식별자·과잉 처리·불일치가 있는지 검증한다. 분석가와 다른 모델군. (prompt auditor@19d7)");
  }
  run.message(AU, "user", `계획 v${run.detail.plan?.version} · 요건 ${run.detail.requirements.length}건 · 사용자 수정 ${run.edits.length}건 · 관찰 요약 첨부`);
  run.budget(AU, "tool_calls", 0, t.auditorMaxTools);
  let deep = false;
  if (t.auditor === "frontier") {
    await run.think(AU, "요건별로 결정 구간을 대조하고, 처리 뒤에도 남는 식별 단서가 있는지 재등장 구간부터 본다.", 7300, 240, 1400);
    deep = await useTool("inspect_visual", "target=person-3 range=00:49–00:58 question=\"얼굴·명찰 외에 남은 식별 단서가 있는가\"", 1200, "왼팔 안쪽 문자 문신 (0.77)");
    if (deep) {
      await run.think(AU, "신체 특징의 식별 위험에 관한 기준을 찾는다.", 7900, 120, 800);
      await useTool("search_law", "query=\"문신 신체 특징 식별 비정형 데이터\" kinds=[안내서]", 700, "참고 1 — 가명정보 처리 가이드라인(2026.3) 비정형데이터 장");
      await useTool("get_document", "doc=\"가명정보 처리 가이드라인 2026.3\" span=비정형데이터 · 신체 특징", 600, "흉터·문신 등 신체 특징은 고위험 식별 요소로 본다.");
    } else {
      run.causes.set("person-3-tattoo", "감사자 도구 호출 예산이 바닥나 재등장 구간을 다시 보지 못함");
    }
  } else {
    await run.think(AU, "요건과 결정 구간이 맞는지만 대조한다.", 7300, 160, 1000);
    run.causes.set("person-3-tattoo", "경량 감사 모델이 요건 대조만 하고 재등장 구간의 신체 특징을 다시 보지 않음");
  }
  if (t.plateTracked) {
    await run.think(AU, "번호판 트랙과 결정 D-3의 구간을 비교한다.", 8400, 100, 700);
    if (!(await useTool("inspect_visual", "target=plate-1 compare=track_vs_D-3", 900, "트랙 시작 00:40.2 · 결정 시작 00:41.0 · 0.8초 노출"))) {
      run.causes.set("plate-1", "감사자 도구 호출 예산이 바닥나 번호판 구간을 비교하지 못함");
    }
  }
  const planned = auditPlan(run.detail.plan as Plan, run.detail.requirements, { deep, plateTracked: t.plateTracked, outline: t.outline });
  // a check the auditor could not run (budget) cannot produce its finding
  const findings = planned.filter((f) => f.id !== "F-2" || !run.causes.get("plate-1")?.startsWith("감사자"));
  await run.think(AU, `발견 ${findings.length}건을 정리한다.`, 9100, 900, 1300);
  run.detail.findings = findings;
  run.state("findings", AU, findings.map((f) => `${f.id}(${f.severity})`).join(" · ") || "발견 없음");
  run.emit({ kind: "node.end", node: AU, task, result: "ok", costUsd: 0, writes: ["findings"] });

  if (findings.length === 0) {
    run.edge(AU, "render_original");
    return true;
  }
  run.edge(`${AU}/model`, "await_audit_confirm");
  run.detail.canReturnToReview = round <= (run.profile.params["auditor.max_rounds"] ?? 1);
  run.setStatus("AWAITING_AUDIT_CONFIRM");
  run.activity(`감사에서 확인할 사항 ${findings.length}건이 나왔습니다.`, "wait");
  const reply = await run.interrupt<{ resolutions: { findingId: string; action: "apply" | "reject"; reason?: string }[]; backToReview?: boolean }>(
    "await_audit_confirm",
    "audit",
    `발견 ${findings.length}건`,
    () => ({ resolutions: findings.map((f) => ({ findingId: f.id, action: "apply" as const })) }),
  );
  if (reply.value.backToReview && round <= (run.profile.params["auditor.max_rounds"] ?? 1)) {
    run.endInterrupt("await_audit_confirm", "계획으로 돌아가 직접 수정", reply.auto);
    run.edge("await_audit_confirm", "await_review");
    return false;
  }
  let plan = run.detail.plan as Plan;
  const resolved: Finding[] = findings.map((f) => {
    const r = reply.value.resolutions.find((x) => x.findingId === f.id);
    if (r?.action === "reject") return { ...f, status: "rejected" as const, rejectReason: r.reason ?? "" };
    plan = applyFinding(plan, f, outline);
    return { ...f, status: "applied" as const };
  });
  run.detail.plan = plan;
  run.detail.findings = resolved;
  const applied = resolved.filter((f) => f.status === "applied").length;
  const rejected = resolved.length - applied;
  run.endInterrupt("await_audit_confirm", `적용 ${applied} · 위험 수용 ${rejected}${reply.auto ? " (자동 응답)" : ""}`, reply.auto);
  run.state("plan", "await_audit_confirm", `계획 v${plan.version} · 감사 권고 적용 ${applied} · 위험 수용 ${rejected}`);
  if (rejected) run.state("risk_acceptance", "await_audit_confirm", resolved.filter((f) => f.status === "rejected").map((f) => `${f.id}: ${f.rejectReason}`).join(" · "));
  run.edge("await_audit_confirm", "render_original");
  return true;
}
