// Mock of the Lab's section experiments, video datasets, evaluators and step store. The real ones are
// backend/src/privframe/service/experiments.py · datasets.py · evaluation.py and core/stages.py; the contract is
// docs/design/16-lab-runs-and-experiment-sets.md §16.7 and its shapes are in src/api/stage.ts.
// Registered from plugin.ts with one call to `stageRoutes`.
//
// An experiment is a set of runs over one range of sections; launches add runs to it. A run is a scripted
// CaseRun: it emits only the nodes of the sections it runs, marks every step taken from the step store with a
// `reuse` state event, and ends after the last section of the range — so the topology of such a run shows what
// a real one shows. A run that goes through a section which asks the user is a real mock case: it stops at the
// question and goes on when someone answers on its service screen (`/cases/{id}`), exactly as an uploaded case
// does. What a combination finds follows a few fixed rules (`perceive`, `analyse`, `planOf` below); the numbers
// are examples, not measurements.
import type { IncomingMessage, ServerResponse } from "node:http";
import type { CaseStatus } from "../src/api/contract";
import type {
  AnalysisResult,
  ArtifactStats,
  BaseComparison,
  CreateStageExperimentRequest,
  ExpectedLabels,
  Launch,
  LaunchPlan,
  PerceptionResult,
  PipelineSection,
  PlanResult,
  PlannedRun,
  PlannedVariant,
  RangeInteraction,
  RegisteredEvaluator,
  RenderResult,
  RunMetrics,
  Score,
  ScoreAggregate,
  SectionStep,
  StageExperiment,
  StageExperimentSummary,
  StageItem,
  StageLimits,
  StageRun,
  StageVariant,
  StepMetric,
  SummaryMeans,
  VariantSpec,
  VariantSummary,
  VideoItem,
  VideoSet,
} from "../src/api/stage";
import { WAITING_FOR } from "../src/lib/format";
import { CaseRun, type Hub } from "./engine";
import { auditStep, legalAnalyst, planFor, reviewStep } from "./flow";
import { HttpError, readJson, sendJson } from "./http";
import { HISTORY_RUNS } from "./lab-data";
import { FRAME_SLOTS, type PipelineStore, type ResolvedProfile } from "./pipeline";
import { type Answers, SCENES, buildAnalyses, buildRequirements, identityQuestion, initialTargets } from "./scenario";

const LIMITS: StageLimits = { maxVariants: 12, maxRuns: 60, costCapUsdDefault: 5, costCapUsdMax: 100 };
const LA = "legal_analyst";

/* ---------- sections ---------- */

// Each step says which parts and parameters its result depends on, which steps it reads, and whether the step
// store keeps its result: the table of core/stages.py, which is also what tells which steps a change runs again.
const P = "perceive/";
const stepOf = (node: string, label: string, slots: string[], params: string[], after: string[], stored = false): SectionStep => ({ node, label, slots, params, after, stored });
const ANALYST_SLOTS = ["llm.analyst", "vlm.inspect", "rag.seed", "rag.chunking", "rag.embedding", "rag.retrieval", "rag.bm25_tokenizer", "rag.reranker", "rag.store", "agent.questions"];
const ANALYST_PARAMS = ["rag.top_k", "analyst.max_tool_calls", "analyst.max_searches", "analyst.max_question_rounds", "analyst.max_questions", "analyst.max_retries"];
const SECTIONS: PipelineSection[] = [
  {
    id: "perception",
    label: "영상 정보 추출",
    description: "원본에서 장면 · 사람 · 글자 · 번호판을 찾아 추적하고, VLM으로 장면과 인물을 설명한다.",
    entry: "perceive",
    exit: "perceive",
    interactions: [],
    slots: [
      "perception.scene_cut",
      "perception.face_detector",
      "perception.object_detector",
      "perception.tracker",
      "perception.plate_detector",
      "perception.face_embedding",
      "perception.ocr",
      "vlm.input_mode",
      "vlm.scene",
      "vlm.person",
    ],
    params: ["perception.analysis_fps", "perception.proxy_short_side"],
    steps: [
      stepOf(`${P}make_proxy`, "분석 프록시", [], ["perception.proxy_short_side"], [], true),
      stepOf(
        `${P}detect_track`,
        "검출 · 추적",
        ["perception.scene_cut", "perception.face_detector", "perception.object_detector", "perception.tracker", "perception.plate_detector"],
        ["perception.analysis_fps"],
        [`${P}make_proxy`],
        true,
      ),
      stepOf(`${P}group_identities`, "인물 묶음", ["perception.face_embedding"], [], [`${P}detect_track`], true),
      stepOf(`${P}ocr`, "텍스트 인식", ["perception.ocr"], [], [`${P}detect_track`], true),
      stepOf(`${P}describe_segments`, "장면 설명", ["vlm.scene", "vlm.input_mode", "perception.face_detector"], [], [`${P}detect_track`], true),
      stepOf(`${P}describe_persons`, "인물 속성", ["vlm.person", "vlm.input_mode", "perception.face_detector"], [], [`${P}group_identities`], true),
      // put together anew in every run: nothing of it is kept
      stepOf(`${P}assemble`, "영상 이해 조립", [], [], [`${P}group_identities`, `${P}ocr`, `${P}describe_segments`, `${P}describe_persons`]),
    ],
  },
  {
    id: "analysis",
    label: "법률 분석",
    description: "같은 사람인지 불확실한 대상을 확인받고, Legal Analyst가 법령 · 판례를 검색 · 열람하며 필요한 사실을 물어 보호 요건을 도출한다.",
    entry: "await_identity",
    exit: LA,
    interactions: [
      { kind: "identity", label: "같은 사람 확인" },
      { kind: "questions", label: "질문" },
    ],
    slots: ANALYST_SLOTS,
    params: ANALYST_PARAMS,
    steps: [stepOf(LA, "Legal Analyst", ANALYST_SLOTS, ANALYST_PARAMS, [`${P}assemble`])],
  },
  {
    id: "plan",
    label: "계획",
    description: "Planner가 보호 요건을 결정(대상 · 범위 · 모자이크 또는 유지)으로 바꾸고 검증기가 대조한다.",
    entry: "plan",
    exit: "review_guide",
    interactions: [],
    slots: ["llm.planner"],
    params: ["planner.max_generations"],
    steps: [stepOf("plan", "계획 수립", ["llm.planner"], ["planner.max_generations"], [LA])],
  },
  {
    id: "review",
    label: "검토 · 감사",
    description: "사용자가 계획을 검토 · 수정하고, 수정된 계획이 모든 요건을 여전히 충족하는지 감사한다.",
    entry: "await_review",
    exit: "auditor",
    interactions: [
      { kind: "review", label: "계획 검토" },
      { kind: "audit", label: "감사 확인" },
    ],
    slots: [],
    params: [],
    steps: [stepOf("auditor", "Auditor", [], [], ["plan"])],
  },
  {
    id: "render",
    label: "렌더 · 검증",
    description: "승인된 계획대로 원본 스트림에 모자이크를 입히고 출력을 검증한다.",
    entry: "render_original",
    exit: "report",
    interactions: [],
    slots: ["render.encoder"],
    params: [],
    steps: [stepOf("render_original", "원본 스트림 렌더", ["render.encoder"], [], ["plan"]), stepOf("verify_output", "출력 검증", [], [], ["render_original"])],
  },
];
const STEPS = SECTIONS.flatMap((s) => s.steps);

/** the steps that run again when the given parts or parameters change: those that read one, and every step after such a step */
function affectedSteps(names: string[]): string[] {
  const changed = new Set(STEPS.filter((s) => [...s.slots, ...s.params].some((name) => names.includes(name))).map((s) => s.node));
  for (let grew = true; grew; ) {
    grew = false;
    for (const s of STEPS) {
      if (!changed.has(s.node) && s.after.some((node) => changed.has(node))) {
        changed.add(s.node);
        grew = true;
      }
    }
  }
  return [...changed].sort();
}

const ORDER = SECTIONS.map((s) => s.id);
const LAST_NODE = "report";
const STATUS_OF: Record<string, CaseStatus> = { perception: "ANALYZING", analysis: "ANALYZING_LEGAL", plan: "PLANNING", review: "AUDITING", render: "RENDERING" };

function span(first: string, last: string): PipelineSection[] {
  const a = ORDER.indexOf(first);
  const b = ORDER.indexOf(last);
  if (a < 0 || b < 0) throw new HttpError(422, "구간을 고르세요.");
  if (b < a) throw new HttpError(422, `구간의 끝(${last})이 시작(${first})보다 앞에 있습니다.`);
  return SECTIONS.slice(a, b + 1);
}

const asksOf = (sections: PipelineSection[]): RangeInteraction[] => sections.flatMap((s) => s.interactions.map((i) => ({ section: s.id, ...i })));
/** two configurations read the same parts and values inside a section */
const sameIn = (section: PipelineSection, a: ResolvedProfile, b: ResolvedProfile) =>
  section.slots.every((slot) => a.part[slot]?.candidate === b.part[slot]?.candidate) && section.params.every((name) => a.params[name] === b.params[name]);

/* ---------- evaluation: the least there is ---------- */

// service/evaluation.py: one registered evaluator, the comparison with what a dataset says is in a video. Which
// metrics each part of the pipeline is judged by is being designed elsewhere; nothing here anticipates it.
const EXPECTED_METRICS: RegisteredEvaluator["metrics"] = [
  { id: "expected.texts", label: "글자 인식", unit: "ratio", direction: "higher" },
  { id: "expected.people", label: "인물 수 일치", unit: "ratio", direction: "higher" },
  { id: "expected.plates", label: "번호판 수 일치", unit: "ratio", direction: "higher" },
  { id: "expected.scenes", label: "장면 수 일치", unit: "ratio", direction: "higher" },
];
const EVALUATORS: RegisteredEvaluator[] = [
  {
    id: "expected",
    label: "데이터셋 정답과 비교",
    description: "영상 정보 추출이 찾은 글자 · 인물 수 · 번호판 수 · 장면 수를 데이터셋에 적어 둔 값과 비교한다. 정답을 적어 둔 영상에만 점수가 나온다.",
    sections: ["perception"],
    metrics: EXPECTED_METRICS,
  },
];

/* ---------- videos: what is in them ---------- */

/** what a careful pipeline finds in a video; the rules below take things away from it */
interface Truth {
  texts: string[];
  /** small print only the larger OCR models read */
  fineTexts: string[];
  people: number;
  plates: number;
  scenes: number;
  sceneLabels: string[];
  placeTypes: string[];
  peopleCounts: number[];
  /** `<target>:<scope>` a careful analyst protects, most obvious first */
  protected: string[];
  kept: string[];
}

interface StoredItem extends VideoItem {
  truth: Truth;
}

interface StoredSet extends Omit<VideoSet, "items"> {
  items: StoredItem[];
}

const CAFE: Truth = {
  texts: ["김○○ 바리스타", "○○커피"],
  fineTexts: ["오늘의 원두 에티오피아"],
  people: 2,
  plates: 1,
  scenes: 4,
  sceneLabels: ["카페 전경", "인터뷰 좌석", "계산대와 직원", "창밖 주차 차량"],
  placeTypes: ["cafe", "cafe", "cafe", "street"],
  peopleCounts: [2, 1, 2, 0],
  protected: ["person-3:face", "plate-1:plate", "text-1:text", "person-3:body"],
  kept: ["person-2:face", "text-2:text"],
};
const COUNTER: Truth = {
  texts: ["주문은 여기서", "박○○ 매니저"],
  fineTexts: ["영업시간 09:00–21:00"],
  people: 3,
  plates: 0,
  scenes: 3,
  sceneLabels: ["주문대", "손님 대기 줄", "매장 안쪽 좌석"],
  placeTypes: ["cafe", "cafe", "cafe"],
  peopleCounts: [2, 3, 1],
  protected: ["person-2:face", "person-3:face", "text-2:text"],
  kept: ["person-1:face", "text-1:text"],
};
const STREET: Truth = {
  texts: ["○○부동산", "주차금지"],
  fineTexts: [],
  people: 5,
  plates: 2,
  scenes: 2,
  sceneLabels: ["골목 입구", "상가 앞 인도"],
  placeTypes: ["street", "street"],
  peopleCounts: [3, 4],
  protected: ["plate-1:plate", "plate-2:plate", "person-1:face", "person-4:face"],
  kept: ["text-1:text", "text-2:text"],
};

function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** a video the mock knows nothing about: something plausible, the same every time for the same file */
function inventTruth(seed: string): Truth {
  const h = hash(seed);
  const people = 1 + (h % 3);
  const scenes = 1 + ((h >>> 3) % 3);
  const plates = (h >>> 6) % 2;
  const pool = ["출입구 안내", "행사 현수막", "매장 간판", "이름표"];
  const texts = [pool[h % pool.length], pool[(h + 1) % pool.length]];
  return {
    texts,
    fineTexts: [],
    people,
    plates,
    scenes,
    sceneLabels: Array.from({ length: scenes }, (_, i) => `장면 ${i + 1}`),
    placeTypes: Array.from({ length: scenes }, () => "indoor"),
    peopleCounts: Array.from({ length: scenes }, (_, i) => Math.max(0, people - (i % 2))),
    protected: [...Array.from({ length: people }, (_, i) => `person-${i + 1}:face`), ...(plates ? ["plate-1:plate"] : []), "text-1:text"],
    kept: ["text-2:text"],
  };
}

const expectedOf = (t: Truth): ExpectedLabels => ({ texts: [...t.texts, ...t.fineTexts], people: t.people, plates: t.plates, scenes: t.scenes });

/* ---------- what a combination finds ---------- */

function perceive(truth: Truth, p: ResolvedProfile): PerceptionResult {
  const part = (slot: string) => p.part[slot];
  // the smallest detector loses a person in a crowded shot
  const people = part("perception.object_detector")?.candidate === "yolo26n" && truth.people >= 3 ? truth.people - 1 : truth.people;
  const plates = part("perception.plate_detector")?.adapter === "none" ? 0 : truth.plates;
  const scenes = truth.scenes + (part("perception.scene_cut")?.candidate === "content" ? 1 : 0);
  // the tiny OCR model misses small print
  const texts = part("perception.ocr")?.candidate === "rapidocr-tiny" ? [...truth.texts] : [...truth.texts, ...truth.fineTexts];
  // a small local VLM names the last scene loosely and miscounts the busiest one
  const local = part("vlm.scene")?.kind !== "api";
  const busiest = truth.peopleCounts.indexOf(Math.max(...truth.peopleCounts));
  const labels = Array.from({ length: scenes }, (_, i) => truth.sceneLabels[i] ?? `장면 ${i + 1}`);
  const counts: (number | null)[] = Array.from({ length: scenes }, (_, i) => truth.peopleCounts[i] ?? null);
  const places = Array.from({ length: scenes }, (_, i) => truth.placeTypes[i] ?? truth.placeTypes[truth.placeTypes.length - 1] ?? "indoor");
  if (local) {
    labels[labels.length - 1] = "실외 장면";
    places[places.length - 1] = "other";
    if (counts[busiest] !== null && (counts[busiest] as number) > 1) counts[busiest] = (counts[busiest] as number) - 1;
  }
  return {
    scenes,
    people,
    plates,
    faceTracks: people + 1,
    bodyTracks: people,
    texts,
    samples: 0,
    describedScenes: scenes,
    describedPersons: people,
    sceneLabels: labels,
    placeTypes: places,
    peopleCounts: counts,
    visibleSec: {},
  };
}

function searchesOf(p: ResolvedProfile): number {
  const lawirko = p.part["rag.embedding"]?.lawirko;
  let searches = 4;
  if (lawirko === undefined || lawirko < 0.75) searches += 1;
  if (p.part["rag.reranker"]?.adapter === "none") searches += 1;
  if (p.part["rag.retrieval"]?.candidate !== "hybrid-rrf") searches += 2;
  if (p.part["llm.analyst"]?.tier === "efficient") searches += 1;
  return Math.min(searches, p.params["analyst.max_searches"] ?? 12);
}

function analyse(truth: Truth, p: ResolvedProfile, answers?: Answers): AnalysisResult {
  const efficient = p.part["llm.analyst"]?.tier === "efficient";
  const asks = p.part["agent.questions"]?.candidate !== "disabled" && (p.params["analyst.max_question_rounds"] ?? 2) > 0;
  // a lighter analyst drops the least obvious requirement; a person who is said not to have agreed to publication is protected too
  const unconsented = answers && !answers.consentAssumed && !answers.consent ? truth.kept.filter((t) => t.startsWith("person-")) : [];
  const protectedTargets = [...new Set([...(efficient ? truth.protected.slice(0, -1) : truth.protected), ...unconsented])].sort();
  const searches = searchesOf(p);
  return {
    analyses: 3,
    applies: efficient ? 1 : 2,
    uncertain: (efficient ? 1 : 0) + (asks ? 0 : 1),
    notApplies: 1,
    requirements: protectedTargets.length,
    protected: protectedTargets,
    questions: asks ? 2 : 0,
    searches,
    toolCalls: searches + 4,
    citations: efficient ? 4 : 6,
    unresolved: efficient ? 1 : 0,
  };
}

function planOf(truth: Truth, analysis: AnalysisResult, p: ResolvedProfile): PlanResult {
  const efficient = p.part["llm.planner"]?.tier === "efficient";
  const generations = p.params["planner.max_generations"] ?? 3;
  // a lighter planner leaves a name tag visible that the requirements ask to cover
  const lost = efficient ? analysis.protected.find((t) => t.endsWith(":text")) : undefined;
  const redacted = analysis.protected.filter((t) => t !== lost).sort();
  return {
    decisions: analysis.protected.length + truth.kept.filter((t) => !analysis.protected.includes(t)).length,
    redacted,
    kept: [...truth.kept.filter((t) => !analysis.protected.includes(t)), ...(lost ? [lost] : [])].sort(),
    unsatisfied: lost && generations < 2 ? 1 : 0,
    attempts: efficient ? Math.min(2, generations) : 1,
  };
}

function renderOf(item: StoredItem, plan: PlanResult, p: ResolvedProfile): RenderResult {
  const encoder = p.part["render.encoder"]?.candidate;
  const rate = encoder === "x264-crf14" ? 2.4e6 : encoder === "videotoolbox" ? 1.6e6 : 1.9e6;
  return {
    rows: 8,
    passed: 8,
    failed: [],
    outputBytes: Math.round((item.durationSec * rate) / 8),
    modifiedFrames: Math.round((item.frames ?? item.durationSec * 30) * Math.min(0.9, 0.15 * plan.redacted.length)),
    repairRounds: 0,
  };
}

const plain = (text: string) => text.normalize("NFKC").toLowerCase().replace(/\s+/g, "");
const jaccard = (a: Set<string>, b: Set<string>) => {
  if (a.size === 0 && b.size === 0) return 1;
  const both = [...a].filter((x) => b.has(x)).length;
  return both / new Set([...a, ...b]).size;
};
const round = (value: number, digits = 3) => Number(value.toFixed(digits));

/** the "expected" evaluator: what perception found against what the dataset says is in the video */
function expectedScores(found: PerceptionResult | null | undefined, expected: ExpectedLabels | null): Score[] {
  if (!expected || !found) return [];
  const line = (index: number, value: number, detail: string): Score => {
    const metric = EXPECTED_METRICS[index];
    return { metric: metric.id, label: metric.label, value: round(value, 4), unit: metric.unit, direction: metric.direction, section: "perception", evaluator: "expected", detail };
  };
  const out: Score[] = [];
  const wanted = expected.texts ?? [];
  if (wanted.length) {
    const hit = wanted.filter((t) => found.texts.some((got) => plain(got) === plain(t)));
    const missing = wanted.filter((t) => !hit.includes(t));
    out.push(line(0, hit.length / wanted.length, `${hit.length} / ${wanted.length}${missing.length ? ` · 못 찾음: ${missing.join(", ")}` : ""}`));
  }
  (["people", "plates", "scenes"] as const).forEach((key, i) => {
    const want = expected[key];
    if (want !== undefined) out.push(line(i + 1, found[key] === want ? 1 : 0, `${found[key]} (정답 ${want})`));
  });
  return out;
}

/** a score as given from outside, in the stored shape (service/evaluation.py `clean`) */
function cleanScore(raw: Partial<Score>, evaluator: string): Score {
  const metric = String(raw.metric ?? "").trim();
  if (!metric) throw new HttpError(422, "점수에 지표 이름(metric)이 없습니다.");
  const value = Number(raw.value);
  if (raw.value === undefined || raw.value === null || !Number.isFinite(value)) throw new HttpError(422, `'${metric}'의 값(value)이 수가 아닙니다.`);
  const direction = raw.direction ?? null;
  if (direction !== "higher" && direction !== "lower" && direction !== null) throw new HttpError(422, `'${metric}'의 direction은 higher · lower 가운데 하나이거나 없어야 합니다.`);
  return { metric, label: String(raw.label || metric), value: round(value, 6), unit: String(raw.unit ?? ""), direction, section: raw.section || null, evaluator, detail: String(raw.detail ?? "") };
}

/** mean per metric over groups of scores, one group per video: a metric scored several times in a group counts once */
function aggregate(groups: Score[][]): Record<string, ScoreAggregate> {
  const collected = new Map<string, { label: string; unit: string; direction: Score["direction"]; values: number[] }>();
  for (const group of groups) {
    const perMetric = new Map<string, Score[]>();
    for (const score of group) perMetric.set(score.metric, [...(perMetric.get(score.metric) ?? []), score]);
    for (const [metric, scores] of perMetric) {
      const last = scores[scores.length - 1];
      const entry = collected.get(metric) ?? { label: last.label, unit: last.unit, direction: last.direction, values: [] };
      entry.values.push(scores.reduce((sum, s) => sum + s.value, 0) / scores.length);
      collected.set(metric, entry);
    }
  }
  return Object.fromEntries([...collected].map(([metric, e]) => [metric, { label: e.label, unit: e.unit, direction: e.direction, mean: round(e.values.reduce((a, b) => a + b, 0) / e.values.length, 4), n: e.values.length }]));
}

function againstBase(metrics: RunMetrics, base: RunMetrics): BaseComparison | null {
  for (const [name, field, basis] of [["plan", "redacted", "가리기로 한 대상"], ["analysis", "protected", "보호 요건의 대상"]] as const) {
    const mine = metrics[name] as Record<string, unknown> | null | undefined;
    const theirs = base[name] as Record<string, unknown> | null | undefined;
    if (mine && theirs) {
      const a = new Set(mine[field] as string[]);
      const b = new Set(theirs[field] as string[]);
      return { basis, agreement: round(jaccard(a, b)), onlyBase: [...b].filter((x) => !a.has(x)).sort(), onlyHere: [...a].filter((x) => !b.has(x)).sort() };
    }
  }
  const mine = metrics.perception;
  const theirs = base.perception;
  if (!mine || !theirs) return null;
  const a = new Set(mine.texts.map(plain));
  const b = new Set(theirs.texts.map(plain));
  const counts = [["people", "인물 수"], ["plates", "번호판 수"], ["scenes", "장면 수"]] as const;
  const same = counts.map(([key]) => mine[key] === theirs[key]);
  const different: string[] = counts.filter((_, i) => !same[i]).map(([, label]) => label);
  const parts = [jaccard(a, b), ...same.map(Number)];
  // what the VLM said of each scene: the place and how many people (free-text labels are not compared)
  if (mine.scenes === theirs.scenes && mine.scenes > 0) {
    let agree = 0;
    for (let i = 0; i < mine.scenes; i += 1) {
      const place = mine.placeTypes[i] === theirs.placeTypes[i];
      const people = mine.peopleCounts[i] === theirs.peopleCounts[i];
      if (!place) different.push(`장면 ${i + 1} 장소 (${theirs.placeTypes[i] || "없음"} → ${mine.placeTypes[i] || "없음"})`);
      if (!people) different.push(`장면 ${i + 1} 사람 수 (${theirs.peopleCounts[i]} → ${mine.peopleCounts[i]})`);
      agree += Number(place) + Number(people);
    }
    parts.push(agree / (2 * mine.scenes));
  }
  return {
    basis: "찾은 글자 · 인물 · 번호판 · 장면 수 · 장면 설명",
    agreement: round(parts.reduce((sum, v) => sum + v, 0) / parts.length),
    onlyBase: theirs.texts.filter((t) => !a.has(plain(t))).sort(),
    onlyHere: mine.texts.filter((t) => !b.has(plain(t))).sort(),
    different,
  };
}

/* ---------- runs ---------- */

type Results = Pick<RunMetrics, "perception" | "analysis" | "plan" | "review" | "render">;
type SectionId = keyof Results;

/** what the mock keeps of a run of an experiment beyond its record: enough to continue it from a checkpoint */
interface RunMemory {
  /** the video, by file */
  file: string;
  profile: ResolvedProfile;
  /** the sections it has gone through (its own, and those of the run it continued), in pipeline order */
  done: string[];
  /** the index of the section it started at (0: from the beginning) */
  started: number;
  /** made the old way: the questions inside its range were answered automatically */
  autoAnswered: boolean;
  results: Results;
  /** what it spent on each step of its range */
  steps: StepMetric[];
}

interface Video {
  key: string;
  set: StoredSet;
  item: StoredItem;
}

interface Combination {
  spec: VariantSpec;
  profile: ResolvedProfile;
  /** why the profile check refuses it */
  problem: string | null;
}

/** a run another one can continue from */
interface Source {
  run: CaseRun;
  /** a run of the experiment the launch is for */
  own: boolean;
  done: string[];
  profile: ResolvedProfile;
  /** its questions were answered automatically: not continued past a section that asks */
  auto: boolean;
  /** the index of the section it started at itself */
  started: number;
}

interface Job {
  record: StageRun;
  planned: PlannedRun;
  variant: StageVariant;
  profile: ResolvedProfile;
  video: Video;
}

/** how the examples the mock starts with are played; a launch made through the API uses none of it */
interface PlayOptions {
  /** on a virtual clock, starting at `at`: an example from the past */
  virtual?: boolean;
  at?: Date;
  /** the way runs were made before 2026-11-12: questions inside the range answered automatically */
  autoAnswer?: boolean;
  /** what the person of a recorded example answered, by interrupt kind (the scripted default otherwise) */
  answers?: Partial<Record<"identity" | "questions" | "review" | "audit", unknown>>;
  /** `<variant key>` → an example of a run that does not finish */
  failures?: Record<string, { section: string; error: string }>;
  /** an example of a launch someone stopped after this many runs */
  stopAfter?: number;
}

interface RunOutcome {
  run: CaseRun;
  metrics: RunMetrics;
  error?: string;
}

const fileOf = (item: VideoItem) => `${item.filename}|${item.durationSec}`;
const fileOfCase = (run: CaseRun) => `${run.detail.upload?.filename ?? run.detail.video.filename}|${run.detail.upload?.durationSec ?? run.detail.video.durationSec}`;

/** the sections a case or demo run has gone through, read from its events */
function doneOf(run: CaseRun): string[] {
  const ended = (node: string) => run.events.some((e) => e.kind === "node.end" && e.node === node);
  const done: string[] = [];
  if (!ended("perceive")) return done;
  done.push("perception");
  if (!run.events.some((e) => e.kind === "checkpoint" && e.next.includes("plan"))) return done;
  done.push("analysis");
  if (!ended("review_guide")) return done;
  done.push("plan");
  if (!run.events.some((e) => e.kind === "node.start" && e.node === "render_original")) return done;
  done.push("review");
  if (run.detail.status === "COMPLETED" && run.detail.report) done.push("render");
  return done;
}

/** what the review · audit section left: the person's changes, the audit's findings, and what the plan covers now */
function reviewOf(run: CaseRun, rounds = 1): NonNullable<Results["review"]> {
  const decisions = run.detail.plan?.decisions ?? [];
  return {
    edits: run.edits.length,
    findings: run.detail.findings.length,
    auditRounds: rounds,
    redacted: decisions.filter((d) => d.method !== "keep").map((d) => `${d.targetId}:${d.scope}`).sort(),
  };
}

/** how long a cloud VLM takes to describe a scene, next to the default one */
const VLM_PACE: Record<string, number> = { "gemini-3.8-flash": 0.7, "qwen3.6-27b": 0.9, "glm-4.6v": 1.1, "claude-haiku-5-5-api": 0.8, "claude-opus-5-5-api": 1.6 };

const STEP_BYTES: Record<string, number> = {
  make_proxy: 6_400_000,
  detect_track: 182_000,
  group_identities: 9_400,
  ocr: 21_000,
  describe_segments: 5_200,
  describe_persons: 3_100,
};

class StageMock {
  readonly sets: StoredSet[] = [];
  readonly experiments: StageExperiment[] = [];
  /** step results kept for later runs: key → what computing it took, and which run did */
  readonly stepStore = new Map<string, { step: string; seconds: number; costUsd: number; source: string }>();
  /** what the mock engine's run summary does not carry */
  readonly runInfo = new Map<string, { forkedAt?: string; stoppedAfter?: string; variant?: string }>();
  /** the runs of experiments, by case id */
  private readonly memory = new Map<string, RunMemory>();
  /** the last run and combination number given out in each experiment */
  private readonly numbers = new Map<string, { run: number; variant: number }>();
  readonly ready: Promise<void>;
  private readonly stopping = new Set<string>();
  private counter = 0;

  constructor(
    private readonly hub: Hub,
    private readonly store: PipelineStore,
  ) {
    this.ready = this.seed().catch((error: unknown) => {
      console.error("[mock] section-experiment examples could not be built:", error);
    });
  }

  private hex(): string {
    this.counter += 1;
    return (hash(`${Date.now()}-${this.counter}`) & 0xffff).toString(16).padStart(4, "0");
  }

  /* ----- video sets ----- */

  private publicSet(set: StoredSet): VideoSet {
    return { ...set, items: set.items.map(({ truth: _truth, ...item }) => item) };
  }

  listSets(): VideoSet[] {
    return this.sets.map((s) => this.publicSet(s));
  }

  private set(id: string): StoredSet {
    const found = this.sets.find((s) => s.id === id);
    if (!found) throw new HttpError(404, "데이터셋을 찾을 수 없습니다.");
    return found;
  }

  getSet(id: string): VideoSet {
    return this.publicSet(this.set(id));
  }

  createSet(body: { name?: string; description?: string; sendable?: boolean }): VideoSet {
    const name = String(body.name ?? "").trim();
    if (!name) throw new HttpError(422, "데이터셋 이름을 적어 주세요.");
    const slug = name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 32);
    const set: StoredSet = {
      id: `${slug || "set"}-${this.hex()}`,
      name,
      description: String(body.description ?? "").trim(),
      builtin: false,
      sendable: !!body.sendable,
      createdAt: new Date().toISOString(),
      items: [],
    };
    this.sets.push(set);
    return this.publicSet(set);
  }

  private editable(id: string): StoredSet {
    const set = this.set(id);
    if (set.builtin) throw new HttpError(409, "기본 데이터셋은 고칠 수 없습니다.");
    return set;
  }

  editSet(id: string, body: { name?: string | null; description?: string | null; sendable?: boolean | null }): VideoSet {
    const set = this.editable(id);
    if (typeof body.name === "string") {
      if (!body.name.trim()) throw new HttpError(422, "데이터셋 이름을 적어 주세요.");
      set.name = body.name.trim();
    }
    if (typeof body.description === "string") set.description = body.description.trim();
    if (typeof body.sendable === "boolean") set.sendable = body.sendable;
    return this.publicSet(set);
  }

  editItem(setId: string, itemId: string, body: { title?: string | null; memo?: string | null; expected?: ExpectedLabels | null }): VideoItem {
    const item = this.editable(setId).items.find((i) => i.id === itemId);
    if (!item) throw new HttpError(404, "그 영상은 이 데이터셋에 없습니다.");
    if (typeof body.title === "string" && body.title.trim()) item.title = body.title.trim();
    if (typeof body.memo === "string") item.memo = body.memo;
    // an empty object clears the labels
    if (body.expected) item.expected = Object.keys(body.expected).length ? body.expected : null;
    const { truth: _truth, ...shown } = item;
    return shown;
  }

  deleteSet(id: string): void {
    const set = this.set(id);
    if (set.builtin) throw new HttpError(409, "기본 데이터셋은 지울 수 없습니다.");
    this.sets.splice(this.sets.indexOf(set), 1);
  }

  private addItem(setId: string, item: Omit<StoredItem, "id" | "truth">, truth?: Truth): VideoItem {
    const set = this.set(setId);
    if (set.builtin) throw new HttpError(409, "기본 데이터셋에는 영상을 추가할 수 없습니다.");
    if (set.items.some((i) => i.filename === item.filename && i.durationSec === item.durationSec)) throw new HttpError(409, "같은 영상이 이미 이 데이터셋에 있습니다.");
    const stored: StoredItem = { ...item, id: `v${set.items.length + 1}-${this.hex()}`, truth: truth ?? inventTruth(item.filename) };
    set.items.push(stored);
    const { truth: _truth, ...shown } = stored;
    return shown;
  }

  /** the mock is told about the file instead of receiving it */
  describeItem(setId: string, body: { filename?: string; sizeBytes?: number; title?: string; memo?: string; expected?: ExpectedLabels | null }): VideoItem {
    const filename = String(body.filename ?? "").trim();
    if (!filename) throw new HttpError(400, "영상 파일이 아니거나 손상된 파일입니다.");
    const size = Number(body.sizeBytes) || 0;
    // no file to probe: a length that fits the size, the same for the same file
    const durationSec = round(Math.min(180, Math.max(4, size / 600_000 || 8 + (hash(filename) % 40))), 2);
    return this.addItem(setId, {
      title: String(body.title ?? "").trim() || filename,
      filename,
      durationSec,
      width: 1920,
      height: 1080,
      frames: Math.round(durationSec * 30),
      memo: String(body.memo ?? ""),
      expected: body.expected && Object.keys(body.expected).length ? body.expected : null,
      origin: null,
    });
  }

  itemFromCase(setId: string, body: { caseId?: string; title?: string }): VideoItem {
    const run = body.caseId ? this.hub.runs.get(body.caseId) : undefined;
    if (!run) throw new HttpError(404, "해당 작업을 찾을 수 없습니다.");
    const video = run.detail.video;
    return this.addItem(
      setId,
      {
        title: String(body.title ?? "").trim() || run.detail.title,
        filename: run.detail.upload?.filename ?? video.filename,
        durationSec: run.detail.upload?.durationSec ?? video.durationSec,
        width: run.detail.upload?.width ?? video.width,
        height: run.detail.upload?.height ?? video.height,
        frames: video.frameCount,
        memo: run.opts.memo,
        expected: null,
        origin: run.id,
      },
      // every mock case analyses the example scenario
      CAFE,
    );
  }

  removeItem(setId: string, itemId: string): void {
    const set = this.set(setId);
    if (set.builtin) throw new HttpError(409, "기본 데이터셋은 고칠 수 없습니다.");
    const index = set.items.findIndex((i) => i.id === itemId);
    if (index < 0) throw new HttpError(404, "그 영상은 이 데이터셋에 없습니다.");
    set.items.splice(index, 1);
  }

  /* ----- step store ----- */

  artifacts(): ArtifactStats {
    const steps: ArtifactStats["steps"] = {};
    for (const entry of this.stepStore.values()) {
      const name = entry.step.split("/").pop() ?? entry.step;
      const row = (steps[name] ??= { entries: 0, bytes: 0, node: entry.step, label: STEPS.find((x) => x.node === entry.step)?.label ?? name });
      row.entries += 1;
      row.bytes += STEP_BYTES[name] ?? 2_000;
    }
    return { steps: Object.fromEntries(Object.entries(steps).sort(([a], [b]) => a.localeCompare(b))) };
  }

  /* ----- experiments: what a launch would run ----- */

  private profile(baseId: string, overrides: Record<string, string> = {}, params: Record<string, number> = {}): ResolvedProfile {
    const spec = this.store.getProfile(baseId).spec;
    return this.store.resolve({ ...spec, params: { ...spec.params, ...params } }, overrides);
  }

  /** the combinations a launch names: the product of the candidates and values it lists. A part it does not list keeps the base profile's own. */
  private combinations(baseId: string, range: PipelineSection[], launch: Launch): Combination[] {
    const base = this.profile(baseId);
    const spec = this.store.getProfile(baseId).spec;
    const catalog = this.store.catalog();
    const allowedSlots = new Set(range.flatMap((s) => s.slots));
    const allowedParams = new Set(range.flatMap((s) => s.params));
    const inRange = new Set(range.flatMap((s) => s.steps.map((step) => step.node)));
    const choices: { kind: "slot" | "param"; name: string; options: (string | number)[] }[] = [];
    for (const [slotId, candidates] of Object.entries(launch.matrix ?? {})) {
      if (!allowedSlots.has(slotId)) throw new HttpError(422, `'${slotId}'은(는) 고른 구간에서 쓰는 부품이 아닙니다.`);
      const slot = this.store.slot(slotId);
      for (const id of candidates) {
        const candidate = slot.candidates.find((c) => c.id === id);
        if (!candidate || candidate.status === "planned") throw new HttpError(422, `'${slot.label}'에 '${id}' 후보가 없거나 아직 고를 수 없습니다.`);
      }
      const options = [...new Set(candidates)];
      if (options.length) choices.push({ kind: "slot", name: slotId, options });
    }
    for (const [paramId, values] of Object.entries(launch.paramMatrix ?? {})) {
      const param = catalog.params.find((x) => x.id === paramId);
      if (!param || !allowedParams.has(paramId)) throw new HttpError(422, `'${paramId}'은(는) 고른 구간의 설정값이 아닙니다.`);
      const options = [...new Set(values.map(Number))];
      if (options.some((v) => !(v >= param.min && v <= param.max))) throw new HttpError(422, `'${param.label}'은(는) ${param.min}~${param.max} 사이여야 합니다.`);
      if (options.length) choices.push({ kind: "param", name: paramId, options });
    }
    const shortOf = (slotId: string, candidateId: string) => {
      const c = this.store.slot(slotId).candidates.find((x) => x.id === candidateId);
      return c?.short ?? c?.label ?? candidateId;
    };
    let combos: (string | number)[][] = [[]];
    for (const choice of choices) combos = combos.flatMap((head) => choice.options.map((option) => [...head, option]));
    const out: Combination[] = [];
    for (const combo of combos) {
      const overrides: Record<string, string> = {};
      const params: Record<string, number> = {};
      combo.forEach((value, i) => {
        const choice = choices[i];
        // choosing the base profile's own candidate or value changes nothing
        if (choice.kind === "slot" && value !== base.part[choice.name].candidate) overrides[choice.name] = String(value);
        if (choice.kind === "param" && value !== base.params[choice.name]) params[choice.name] = Number(value);
      });
      const changed = [...Object.keys(overrides), ...Object.keys(params)];
      const key = changed.length ? [...Object.entries(overrides), ...Object.entries(params)].map(([name, value]) => `${name}=${value}`).sort().join("|") : "base";
      if (out.some((c) => c.spec.key === key)) continue;
      // a label names the part of every choice, so it reads the same whichever launch added the combination
      const label = changed.length
        ? [
            ...Object.entries(overrides).map(([slotId, id]) => `${this.store.slot(slotId).label} ${shortOf(slotId, id)}`),
            ...Object.entries(params).map(([id, value]) => `${catalog.params.find((x) => x.id === id)?.label ?? id} ${value}`),
          ].join(" · ")
        : `기준 · ${base.name}`;
      const profile = this.profile(baseId, overrides, params);
      // the Lab's profile check: a combination that cannot run would only fail halfway through
      const refused = this.store.issues({ ...spec, selections: { ...spec.selections, ...overrides }, params: { ...spec.params, ...params } }).find((i) => i.level === "error");
      const again = new Set(affectedSteps(changed));
      out.push({
        spec: {
          key,
          label,
          base: changed.length === 0,
          overrides,
          params,
          changed,
          firstChanged: range.find((sec) => changed.some((name) => sec.slots.includes(name) || sec.params.includes(name)))?.id ?? null,
          recomputes: STEPS.filter((step) => again.has(step.node) && inRange.has(step.node))
            .map((step) => step.node)
            .sort(),
          framesLeaveDevice: FRAME_SLOTS.some((slot) => profile.part[slot]?.kind === "api"),
        },
        profile: { ...profile, name: changed.length ? `${base.name} · ${label}` : profile.name },
        problem: refused ? `'${label}' 조합은 실행할 수 없습니다: ${refused.text}` : null,
      });
    }
    return out;
  }

  /** the videos a launch names: those of its datasets, or only the listed ones of them */
  private videos(launch: Launch): Video[] {
    const wanted = launch.items?.length ? new Set(launch.items) : null;
    const setIds = launch.datasets?.length ? launch.datasets : [...new Set((launch.items ?? []).map((key) => key.split("/")[0]))];
    const out: Video[] = [];
    for (const id of new Set(setIds.map(String))) {
      const set = this.set(id);
      for (const item of set.items) {
        const key = `${set.id}/${item.id}`;
        if (!wanted || wanted.has(key)) out.push({ key, set, item });
      }
    }
    return out;
  }

  /** the runs of the same file a run on this video could continue, newest first */
  private sources(experiment: StageExperiment | null, video: Video): Source[] {
    const file = fileOf(video.item);
    const own = new Set((experiment?.runs ?? []).map((r) => r.caseId));
    const out: Source[] = [];
    for (const run of this.hub.ordered()) {
      const kept = this.memory.get(run.id);
      // beside section-experiment runs only cases and demo runs: an evaluation run replays a scenario, not a file
      if (!kept && run.opts.experimentId) continue;
      const same = kept ? kept.file === file : run.id === video.item.origin || fileOfCase(run) === file;
      if (!same) continue;
      out.push({
        run,
        own: own.has(run.id),
        done: kept?.done ?? doneOf(run),
        profile: kept?.profile ?? run.profile,
        // a demo run, or an experiment run of the old way: nobody gave its answers
        auto: kept ? kept.autoAnswered : run.opts.autopilot,
        started: kept?.started ?? 0,
      });
    }
    return out;
  }

  /**
   * Where one run starts (§16.3, `_plan_run` of service/experiments.py). It continues a run of the same video
   * whose parts in front of the range are the base profile's, in front of the first section in which the two
   * differ — but never past the first section of the range that asks the user. A run whose questions were
   * answered automatically is not continued past an asking section. With none to continue the run starts from
   * the beginning, unless a section in front of the range asks the user: then it cannot start.
   */
  private planRun(experiment: StageExperiment | null, range: PipelineSection[], video: Video, combo: Combination, sourceId: string | undefined): PlannedRun {
    const first = ORDER.indexOf(range[0].id);
    const last = ORDER.indexOf(range[range.length - 1].id);
    const gate = range.find((s) => s.interactions.length > 0);
    const found: { source: Source; at: number }[] = [];
    // why the run named as `source` cannot be continued (the others are simply not offered)
    let refusal: string | null = sourceId && this.hub.runs.has(sourceId) ? "다른 영상의 실행입니다." : null;
    for (const source of this.sources(experiment, video)) {
      const wanted = source.run.id === sourceId;
      const refuse = (reason: string) => {
        if (wanted) refusal = reason;
      };
      const mismatch = SECTIONS.findIndex((section) => !sameIn(section, source.profile, combo.profile));
      const differs = mismatch < 0 ? SECTIONS.length : mismatch;
      if (differs < first) {
        refuse(`'${SECTIONS[differs].label}'의 부품이 이 실험의 기준 프로필과 다릅니다. 그 차이를 비교하려면 '${SECTIONS[differs].label}'부터 구간에 넣으세요.`);
        continue;
      }
      let at = Math.min(differs, gate ? ORDER.indexOf(gate.id) : SECTIONS.length);
      if (at > last) at = first; // nothing in the range differs: the range itself is what runs again
      if (at === 0) {
        refuse(`이 조합은 '${SECTIONS[0].label}'부터 달라 이어 갈 지점이 없습니다.`);
        continue; // the first section starts from the beginning anyway
      }
      if (source.auto && SECTIONS.slice(0, at).some((s) => s.interactions.length > 0)) {
        refuse("질문에 자동으로 답한 실행이라 그 답을 이어 쓰지 않습니다.");
        continue;
      }
      // a checkpoint stands in front of a section when everything before it is done
      if (source.done.length < at || source.run.detail.status === "FAILED") {
        refuse(`'${SECTIONS[at].label}' 앞까지 진행한 적이 없습니다.`);
        continue;
      }
      found.push({ source, at });
    }
    // the latest starting point first; then a run that worked out what lies in front of it itself (not one that
    // only continued from the same point), a run of this experiment, the newest (the order they come in)
    found.sort((a, b) => b.at - a.at || Number(b.source.started !== b.at) - Number(a.source.started !== a.at) || Number(b.source.own) - Number(a.source.own));
    const described = (c: (typeof found)[number]) => ({ caseId: c.source.run.id, title: c.source.run.detail.title, startedAt: c.source.run.createdAt });
    // every run that can be continued is offered, whether or not one is named; the named one is the one taken
    const chosen = (sourceId ? found.find((c) => c.source.run.id === sourceId) : found[0]) as (typeof found)[number] | undefined;
    let problem = combo.problem;
    if (!chosen) {
      const askedBefore = SECTIONS.slice(0, first).filter((s) => s.interactions.length > 0);
      const until = askedBefore[askedBefore.length - 1];
      if (sourceId) problem = problem ?? `지정한 실행에서는 이어 갈 수 없습니다: ${refusal ?? "실행을 찾을 수 없습니다."}`;
      else if (until) {
        problem =
          problem ??
          `'${video.item.title}'에는 '${until.label}'까지 마친 실행이 없습니다(기준 프로필과 같은 부품으로). 그 구간에서 받은 답을 이어 써야 하므로, '${until.label}'을(를) 포함한 구간 실험을 먼저 실행하거나 서비스에서 이 영상을 그 단계까지 진행하세요.`;
      }
    }
    if (!video.set.sendable && combo.spec.framesLeaveDevice) problem = problem ?? `'${video.set.name}'은(는) 외부 전송 금지 데이터셋인데, 이 조합은 프레임을 외부 모델로 보냅니다.`;
    const asks = SECTIONS.slice(chosen ? chosen.at : 0, last + 1).flatMap((s) => s.interactions.map((i) => i.kind));
    return {
      item: video.key,
      itemTitle: video.item.title,
      variant: combo.spec.key,
      start: chosen ? "fork" : "scratch",
      source: chosen ? described(chosen) : null,
      startSection: ORDER[chosen ? chosen.at : 0],
      forkedAt: chosen ? SECTIONS[chosen.at].entry : null,
      interactive: asks.length > 0,
      asks,
      candidates: found.slice(0, 8).map((c) => ({ ...described(c), startSection: ORDER[c.at] })),
      problem,
    };
  }

  /** what a launch would run, for a new experiment or for an existing one. Starting a launch computes the same. */
  private compute(target: { experiment: StageExperiment } | { from: string; to: string; baseId: string }, launch: Launch) {
    const experiment = "experiment" in target ? target.experiment : null;
    const range = "experiment" in target ? span(target.experiment.from, target.experiment.to) : span(target.from, target.to);
    const baseId = "experiment" in target ? target.experiment.baseProfile.id : target.baseId;
    try {
      this.profile(baseId);
    } catch {
      throw new HttpError(404, `프로필 '${baseId}'을(를) 찾을 수 없습니다.`);
    }
    const combos = this.combinations(baseId, range, launch);
    const videos = this.videos(launch);
    const interactions = asksOf(range);
    const runs = videos.flatMap((video) => combos.map((combo) => this.planRun(experiment, range, video, combo, launch.source)));
    const problems: string[] = [];
    if (!videos.length) problems.push("실험할 영상을 하나 이상 고르세요.");
    if (combos.length > LIMITS.maxVariants) problems.push(`조합이 ${combos.length}개입니다. 한 번에 ${LIMITS.maxVariants}개까지 추가합니다. 후보를 줄이세요.`);
    if (runs.length > LIMITS.maxRuns) problems.push(`실행이 ${runs.length}건입니다. 한 번에 ${LIMITS.maxRuns}건까지 추가합니다.`);
    if (interactions.length && runs.length > 1) {
      const names = [...new Set(interactions.map((i) => `${SECTIONS.find((s) => s.id === i.section)?.label}의 ${i.label}`))].join(" · ");
      problems.push(`이 구간은 사용자에게 묻습니다(${names}). 답을 대신하지 않으므로 실행은 한 번에 1건(조합 1개 × 영상 1개)만 추가합니다. 지금은 조합 ${combos.length}개 × 영상 ${videos.length}개입니다.`);
    }
    if (launch.source && runs.length > 1) problems.push("이어 갈 실행은 실행 1건을 추가할 때만 지정할 수 있습니다.");
    // what stops single runs, each sentence once
    problems.push(...new Set(runs.map((run) => run.problem).filter((p): p is string => !!p)));
    const variants: PlannedVariant[] = combos.map((c) => ({ ...c.spec, id: experiment?.variants.find((v) => v.key === c.spec.key)?.id ?? null }));
    const plan: LaunchPlan = {
      from: range[0].id,
      to: range[range.length - 1].id,
      sections: range.map((s) => s.id),
      interactive: interactions.length > 0,
      interactions,
      variants,
      runs,
      problems,
    };
    return { plan, combos, videos, range, baseId };
  }

  preview(body: Launch & { experiment?: string; from?: string; to?: string; baseProfile?: string }): LaunchPlan {
    if (body.experiment) return this.compute({ experiment: this.get(String(body.experiment)) }, body).plan;
    return this.compute({ from: String(body.from ?? ""), to: String(body.to || body.from || ""), baseId: String(body.baseProfile || this.store.service().default) }, body).plan;
  }

  /* ----- experiments: store ----- */

  list(): StageExperimentSummary[] {
    return [...this.experiments]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .map((e) => ({
        id: e.id,
        title: e.title,
        createdAt: e.createdAt,
        updatedAt: e.updatedAt,
        status: e.status,
        from: e.from,
        to: e.to,
        interactive: e.interactive,
        baseProfile: e.baseProfile,
        datasets: e.datasets,
        progress: e.progress,
        costUsd: e.costUsd,
        costCapUsd: e.costCapUsd,
        variants: e.variants.length,
        items: e.items.length,
      }));
  }

  get(id: string): StageExperiment {
    const found = this.experiments.find((e) => e.id === id);
    if (!found) throw new HttpError(404, "실험을 찾을 수 없습니다.");
    return found;
  }

  remove(id: string): void {
    const experiment = this.get(id);
    if (experiment.status === "running") throw new HttpError(409, "실행 중인 실험은 먼저 멈춰야 지울 수 있습니다.");
    this.experiments.splice(this.experiments.indexOf(experiment), 1); // its runs stay in the run list
  }

  stop(id: string): StageExperiment {
    const experiment = this.get(id);
    if (experiment.status !== "running") throw new HttpError(409, "실행 중인 실행이 없습니다.");
    this.stopping.add(id);
    return experiment;
  }

  edit(id: string, body: { title?: unknown; costCapUsd?: unknown }): StageExperiment {
    const experiment = this.get(id);
    if (body.title !== undefined && body.title !== null) {
      const title = String(body.title).trim();
      if (!title) throw new HttpError(422, "실험 이름을 적어 주세요.");
      experiment.title = title.slice(0, 80);
    }
    if (body.costCapUsd !== undefined && body.costCapUsd !== null) experiment.costCapUsd = this.cap(body.costCapUsd);
    this.refresh(experiment);
    return experiment;
  }

  private cap(value: unknown): number {
    const cap = Number(value);
    if (!(cap > 0 && cap <= LIMITS.costCapUsdMax)) throw new HttpError(422, `비용 상한은 0보다 크고 $${LIMITS.costCapUsdMax} 이하여야 합니다.`);
    return cap;
  }

  private run(experiment: StageExperiment, runId: string): StageRun {
    const run = experiment.runs.find((r) => r.id === runId);
    if (!run) throw new HttpError(404, "그 실행은 이 실험에 없습니다.");
    return run;
  }

  /** takes a run out of the set; its case stays in the run list */
  removeRun(id: string, runId: string): StageExperiment {
    const experiment = this.get(id);
    const run = this.run(experiment, runId);
    if (run.status === "queued" || run.status === "running") throw new HttpError(409, "실행 중이거나 차례를 기다리는 실행은 지울 수 없습니다. 실험을 멈춘 뒤 지우세요.");
    experiment.runs.splice(experiment.runs.indexOf(run), 1);
    experiment.variants = experiment.variants.filter((v) => v.base || experiment.runs.some((r) => r.variant === v.id));
    experiment.items = experiment.items.filter((i) => experiment.runs.some((r) => r.item === i.key));
    this.refresh(experiment);
    return experiment;
  }

  /** scores computed elsewhere: those stored under the same evaluator are replaced */
  putScores(id: string, runId: string, body: { evaluator?: unknown; scores?: unknown }): StageExperiment {
    const experiment = this.get(id);
    const run = this.run(experiment, runId);
    const evaluator = String(body.evaluator ?? "").trim();
    if (!evaluator) throw new HttpError(422, "점수를 낸 평가기의 이름(evaluator)을 적어 주세요.");
    const scores = (Array.isArray(body.scores) ? (body.scores as Partial<Score>[]) : []).map((raw) => cleanScore(raw, evaluator));
    if (run.status !== "completed" && run.status !== "failed") throw new HttpError(409, "끝난 실행에만 점수를 넣을 수 있습니다.");
    run.scores = [...run.scores.filter((s) => s.evaluator !== evaluator), ...scores];
    this.refresh(experiment);
    return experiment;
  }

  /** everything that follows from the runs: the comparison with the base combination, the summaries, cost, progress and status */
  private refresh(experiment: StageExperiment, at?: Date): void {
    const mean = (values: number[], digits = 3) => (values.length ? round(values.reduce((a, b) => a + b, 0) / values.length, digits) : null);
    const completed = experiment.runs.filter((r) => r.status === "completed" && r.metrics);
    const baseId = experiment.variants.find((v) => v.base)?.id;
    // against the most recent run of the base combination on the same video, whenever that run was made
    for (const run of completed) {
      const base = run.variant === baseId ? undefined : completed.filter((r) => r.variant === baseId && r.item === run.item).sort((a, b) => (b.finishedAt ?? "").localeCompare(a.finishedAt ?? ""))[0];
      (run.metrics as RunMetrics).vsBase = base ? againstBase(run.metrics as RunMetrics, base.metrics as RunMetrics) : null;
    }
    // every video counts once: first the mean of its runs, then the mean over videos
    const meansOf = (runs: StageRun[], items: StageItem[]): SummaryMeans => {
      const groups = items.map((item) => runs.filter((r) => r.item === item.key)).filter((group) => group.length > 0);
      const over = (value: (m: RunMetrics) => number | null | undefined, digits?: number) =>
        mean(
          groups
            .map((group) => mean(group.map((r) => value(r.metrics as RunMetrics)).filter((v): v is number => typeof v === "number"), 6))
            .filter((v): v is number => v !== null),
          digits,
        );
      return {
        items: groups.length,
        completed: runs.length,
        sectionSecMean: over((m) => m.sectionSec),
        costUsdMean: over((m) => m.costUsd, 5),
        // what the range would have taken and cost with nothing reused: spent + saved
        fullSecMean: over((m) => m.sectionSec + m.savedSec),
        fullCostUsdMean: over((m) => m.costUsd + m.savedCostUsd, 5),
        agreementMean: over((m) => m.vsBase?.agreement),
        scores: aggregate(groups.map((group) => group.flatMap((r) => r.scores))),
      };
    };
    const datasets = [...new Map(experiment.items.map((i) => [i.dataset, i.datasetName]))];
    experiment.summary = experiment.variants.map((variant): VariantSummary => {
      const runs = experiment.runs.filter((r) => r.variant === variant.id);
      const done = completed.filter((r) => r.variant === variant.id);
      const metrics = done.map((r) => r.metrics as RunMetrics);
      const all = meansOf(done, experiment.items);
      return {
        variant: variant.id,
        runs: runs.length,
        ...all,
        failed: runs.filter((r) => r.status === "failed").length,
        skipped: runs.filter((r) => r.status === "skipped").length,
        waiting: runs.filter((r) => r.status === "waiting").length,
        elapsedSecMean: all.items ? mean(experiment.items.map((item) => mean(done.filter((r) => r.item === item.key).map((r) => (r.metrics as RunMetrics).elapsedSec), 6)).filter((v): v is number => v !== null)) : null,
        costUsdTotal: round(metrics.reduce((sum, m) => sum + m.costUsd, 0), 5),
        savedSecTotal: round(metrics.reduce((sum, m) => sum + m.savedSec, 0), 2),
        savedCostUsdTotal: round(metrics.reduce((sum, m) => sum + m.savedCostUsd, 0), 5),
        llmCallsTotal: metrics.reduce((sum, m) => sum + m.llmCalls, 0),
        byDataset: datasets.map(([dataset, datasetName]) => ({ dataset, datasetName, ...meansOf(done, experiment.items.filter((i) => i.dataset === dataset)) })),
      };
    });
    const active = experiment.runs.some((r) => r.status === "queued" || r.status === "running");
    const waiting = experiment.runs.filter((r) => r.status === "waiting").length;
    experiment.status = active ? "running" : waiting ? "waiting" : "idle";
    if (!active) this.stopping.delete(experiment.id);
    experiment.datasets = datasets.map(([id]) => id);
    experiment.costUsd = round(experiment.runs.reduce((sum, r) => sum + (r.metrics?.costUsd ?? 0), 0), 5);
    experiment.progress = { done: experiment.runs.filter((r) => r.status === "completed" || r.status === "failed" || r.status === "skipped").length, total: experiment.runs.length, waiting };
    experiment.updatedAt = (at ?? new Date()).toISOString();
  }

  /* ----- experiments: launches ----- */

  /** a launch whose plan has problems is not started: the first reason, and how many more there are */
  private refuse(plan: LaunchPlan): void {
    if (!plan.problems.length) return;
    throw new HttpError(422, plan.problems[0] + (plan.problems.length > 1 ? ` (외 ${plan.problems.length - 1}건)` : ""));
  }

  /** a new set with its first launch. `done` resolves when the runs of that launch have ended. */
  create(request: CreateStageExperimentRequest, options: PlayOptions = {}, id?: string): { experiment: StageExperiment; done: Promise<void> } {
    const baseId = String(request.baseProfile || this.store.service().default);
    const cap = this.cap(request.costCapUsd ?? LIMITS.costCapUsdDefault);
    const computed = this.compute({ from: String(request.from ?? ""), to: String(request.to || request.from || ""), baseId }, request);
    this.refuse(computed.plan);
    const at = options.at ?? new Date();
    const base = this.profile(baseId);
    const { range, plan } = computed;
    const stamp = [at.getMonth() + 1, at.getDate(), at.getHours(), at.getMinutes(), at.getSeconds()].map((n) => String(n).padStart(2, "0")).join("");
    const rangeLabel = range[0].label + (range.length > 1 ? ` → ${range[range.length - 1].label}` : "");
    const experiment: StageExperiment = {
      id: id ?? `sx_${stamp}_${this.hex()}`,
      title: String(request.title ?? "").trim().slice(0, 80) || rangeLabel,
      createdAt: at.toISOString(),
      updatedAt: at.toISOString(),
      status: "idle",
      from: plan.from,
      to: plan.to,
      interactive: plan.interactive,
      sections: plan.sections,
      interactions: plan.interactions,
      baseProfile: { id: baseId, name: base.name, configHash: base.configHash },
      datasets: [],
      variants: [],
      items: [],
      runs: [],
      summary: [],
      costCapUsd: cap,
      costUsd: 0,
      progress: { done: 0, total: 0, waiting: 0 },
    };
    this.experiments.unshift(experiment);
    this.numbers.set(experiment.id, { run: 0, variant: 0 });
    return { experiment, done: this.enqueue(experiment, computed, options) };
  }

  /** one more launch into an existing set */
  add(id: string, launch: Launch, options: PlayOptions = {}): { experiment: StageExperiment; done: Promise<void> } {
    const experiment = this.get(id);
    const computed = this.compute({ experiment }, launch);
    this.refuse(computed.plan);
    return { experiment, done: this.enqueue(experiment, computed, options) };
  }

  /** the planned runs become records of the experiment (a combination or a video it does not have yet is added), then they run one after the other */
  private enqueue(experiment: StageExperiment, computed: ReturnType<StageMock["compute"]>, options: PlayOptions): Promise<void> {
    const numbers = this.numbers.get(experiment.id) as { run: number; variant: number };
    const at = (options.at ?? new Date()).toISOString();
    const jobs: Job[] = [];
    for (const planned of computed.plan.runs) {
      const combo = computed.combos.find((c) => c.spec.key === planned.variant) as Combination;
      const video = computed.videos.find((v) => v.key === planned.item) as Video;
      let variant = experiment.variants.find((v) => v.key === combo.spec.key);
      if (!variant) {
        variant = {
          ...combo.spec,
          id: combo.spec.base ? "base" : `v${(numbers.variant += 1)}`,
          configHash: combo.profile.configHash,
        };
        // the base combination leads, whenever it was added
        if (variant.base) experiment.variants.unshift(variant);
        else experiment.variants.push(variant);
      }
      if (!experiment.items.some((i) => i.key === video.key)) {
        const { set, item } = video;
        experiment.items.push({ key: video.key, dataset: set.id, datasetName: set.name, id: item.id, title: item.title, filename: item.filename, durationSec: item.durationSec, expected: item.expected, sendable: set.sendable });
      }
      const record: StageRun = {
        id: `r${(numbers.run += 1)}`,
        item: video.key,
        variant: variant.id,
        status: "queued",
        createdAt: at,
        startedAt: null,
        finishedAt: null,
        caseId: null,
        start: null,
        sourceCaseId: null,
        forkedAt: null,
        startSection: null,
        interactive: planned.interactive,
        autoAnswered: false,
        waitingFor: null,
        error: null,
        metrics: null,
        scores: [],
      };
      experiment.runs.push(record);
      jobs.push({ record, planned, variant, profile: combo.profile, video });
    }
    this.refresh(experiment, options.at);
    return this.execute(experiment, jobs, options);
  }

  /* ----- experiments: running ----- */

  private blocked(experiment: StageExperiment): string | null {
    if (this.stopping.has(experiment.id)) return "실험을 멈춤";
    const spent = experiment.runs.reduce((sum, r) => sum + (r.metrics?.costUsd ?? 0), 0);
    if (spent >= experiment.costCapUsd) return `실험 비용 상한 $${experiment.costCapUsd.toFixed(2)}에 도달했습니다(지금까지 $${spent.toFixed(2)}). 상한을 올리고 다시 추가하세요.`;
    return null;
  }

  private async execute(experiment: StageExperiment, jobs: Job[], options: PlayOptions): Promise<void> {
    let clock = options.at?.getTime() ?? 0;
    let finished = 0;
    const now = () => new Date(options.virtual ? clock : Date.now());
    const touch = () => this.refresh(experiment, options.virtual ? now() : undefined);
    for (const job of jobs) {
      const { record } = job;
      const reason = this.blocked(experiment);
      if (reason) {
        Object.assign(record, { status: "skipped", error: reason, finishedAt: now().toISOString() });
        touch();
        continue;
      }
      try {
        const outcome = await this.play(experiment, job, options, now());
        const metrics = outcome.metrics;
        clock += Math.round((metrics.elapsedSec + metrics.waitSec) * 1000) + 400;
        record.metrics = metrics;
        // the registered evaluators whose sections the range covers; scores put in from outside stay
        if (experiment.sections.includes("perception")) record.scores = [...record.scores.filter((s) => s.evaluator !== "expected"), ...expectedScores(metrics.perception, job.video.item.expected)];
        if (outcome.error) Object.assign(record, { status: "failed", error: outcome.error });
        // stopped while this run was in flight: the run itself did not fail
        else if (this.stopping.has(experiment.id)) Object.assign(record, { status: "skipped", error: "실험을 멈춤" });
        else record.status = "completed";
      } catch (error) {
        Object.assign(record, { status: "failed", error: error instanceof Error ? `${error.name}: ${error.message}` : String(error) });
      }
      Object.assign(record, { waitingFor: null, finishedAt: now().toISOString() });
      finished += 1;
      if (options.stopAfter === finished) this.stopping.add(experiment.id); // an example of a launch someone stopped
      touch();
    }
  }

  /** the scripted run of one combination on one video */
  private play(experiment: StageExperiment, job: Job, options: PlayOptions, startedAt: Date): Promise<RunOutcome> {
    const { record, planned, variant, profile, video } = job;
    const range = span(experiment.from, experiment.to);
    const source = planned.source ? this.hub.runs.get(planned.source.caseId) : undefined;
    const first = source ? ORDER.indexOf(planned.startSection) : 0;
    const sections = SECTIONS.slice(first, ORDER.indexOf(experiment.to) + 1);
    // a run that goes through a question waits for a person; only the examples of the old way answer themselves
    const answersItself = !planned.interactive || !!options.autoAnswer;
    const run = new CaseRun(this.hub, this.hub.nextId("exp"), {
      title: `${experiment.title} · ${video.item.title} · ${variant.label}`,
      memo: video.item.memo,
      mode: FRAME_SLOTS.some((s) => profile.part[s]?.kind === "api") ? "anonymized_upload" : "local_only",
      autopilot: answersItself,
      virtual: options.virtual,
      forkedFrom: source?.id,
      idPrefix: "exp",
      profile,
      experimentId: experiment.id,
      scenario: video.item.title,
    });
    if (options.virtual) {
      // an example from the past: it sorts where it would have run, below the runs of this session
      (run as { createdAt: string }).createdAt = startedAt.toISOString();
      run.detail.createdAt = run.createdAt;
    }
    if (options.virtual && planned.interactive && !options.autoAnswer) {
      // a recorded example in which a person answered: the answers are scripted, but they are not automatic ones
      const interrupt = run.interrupt.bind(run);
      run.interrupt = async <T,>(node: string, payload: "identity" | "questions" | "review" | "audit", summary: string, autoValue: () => T) => {
        const scripted = options.answers?.[payload];
        const reply = await interrupt<T>(node, payload, summary, scripted === undefined ? autoValue : () => scripted as T);
        return { ...reply, auto: false };
      };
    }
    this.hub.runs.set(run.id, run);
    const last = range[range.length - 1];
    const stoppedAfter = last.exit === LAST_NODE ? undefined : last.exit;
    this.runInfo.set(run.id, { forkedAt: source ? (planned.forkedAt ?? undefined) : undefined, stoppedAfter, variant: variant.label });
    // the service screen of such a case says which experiment it belongs to, and that it covered only part of the pipeline
    Object.assign(run.detail, { experimentId: experiment.id, ...(stoppedAfter ? { stoppedAfter } : {}) });
    Object.assign(record, {
      status: "running",
      caseId: run.id,
      start: source ? "fork" : "scratch",
      sourceCaseId: source?.id ?? null,
      forkedAt: source ? planned.forkedAt : null,
      startSection: ORDER[first],
      startedAt: startedAt.toISOString(),
      autoAnswered: planned.interactive && !!options.autoAnswer,
    });
    if (!options.virtual) {
      // the record follows the case: waiting while it stands at a question, running again once someone answered
      run.listeners.add((event) => {
        if (event.kind !== "run.status" || event.status === "COMPLETED" || event.status === "FAILED") return;
        const waitingFor = WAITING_FOR[event.status] ?? null;
        Object.assign(record, { status: waitingFor ? "waiting" : "running", waitingFor });
        this.refresh(experiment);
      });
    }
    this.refresh(experiment, options.virtual ? startedAt : undefined);
    return this.script(run, { job, range, sections, source, autoAnswered: planned.interactive && !!options.autoAnswer, failure: options.failures?.[variant.key] });
  }

  /** what a run starts with when it continues another: the results of the sections in front of it, and what its user answered there */
  private inherit(run: CaseRun, source: CaseRun, ahead: string[], results: Results, truth: Truth): void {
    const theirs = this.memory.get(source.id)?.results ?? {};
    for (const id of ahead as SectionId[]) {
      if (theirs[id]) Object.assign(results, { [id]: theirs[id] });
      // a case or demo run keeps no section results: what the rules say its parts find
      else if (id === "perception") results.perception = perceive(truth, source.profile);
      else if (id === "analysis") results.analysis = analyse(truth, source.profile, source.answers);
      else if (id === "plan") results.plan = planOf(truth, results.analysis ?? analyse(truth, source.profile, source.answers), source.profile);
      else if (id === "review") results.review = reviewOf(source);
    }
    const from = source.detail;
    Object.assign(run.detail, { perception: from.perception, segments: from.segments, targets: from.targets });
    if (ahead.includes("analysis")) {
      Object.assign(run.detail, { identity: from.identity, facts: from.facts, questions: from.questions, analyses: from.analyses, requirements: from.requirements });
      run.answers = source.answers ? { ...source.answers } : undefined;
    }
    if (ahead.includes("plan")) run.detail.plan = from.plan;
    if (ahead.includes("review")) {
      run.detail.findings = from.findings;
      run.edits = [...source.edits];
    }
  }

  private async script(
    run: CaseRun,
    spec: { job: Job; range: PipelineSection[]; sections: PipelineSection[]; source?: CaseRun; autoAnswered: boolean; failure?: { section: string; error: string } },
  ): Promise<RunOutcome> {
    const { job, range, sections, source } = spec;
    const { profile } = job;
    const { item, key: itemKey } = job.video;
    const inRange = new Set(range.map((s) => s.id));
    const scale = 0.55 + item.durationSec / 160; // a longer video takes longer
    const steps: StepMetric[] = [];
    const reused: string[] = [];
    const reusedBefore: string[] = [];
    let elapsedMs = 0;
    let sectionMs = 0;
    let error: string | undefined;
    const part = (slot: string) => profile.part[slot];
    const local = (slot: string) => part(slot)?.kind !== "api";
    const results: Results = {};
    const memory: RunMemory = { file: fileOf(item), profile, done: [], started: ORDER.indexOf(sections[0].id), autoAnswered: spec.autoAnswered, results, steps };
    this.memory.set(run.id, memory);
    const entry = sections[0].entry;
    if (source) {
      const ahead = ORDER.slice(0, ORDER.indexOf(sections[0].id));
      memory.done.push(...ahead);
      this.inherit(run, source, ahead, results, item.truth);
    }
    /** every mock case shows the example scene: what the service screens draw the questions on */
    const showScene = () => {
      if (!run.detail.segments.length) run.detail.segments = SCENES.map((s) => ({ id: s.id, start: s.range[0], end: s.range[1], label: s.label }));
      if (!run.detail.targets.length) run.detail.targets = initialTargets();
    };
    /** the scripted length of what `work` does, whatever the speed setting: what its waits add up to (time spent waiting for the user is not among them) */
    const timed = async (work: () => Promise<void>): Promise<number> => {
      let total = 0;
      const wait = run.wait.bind(run);
      run.wait = async (ms: number) => {
        total += ms;
        await wait(ms);
      };
      try {
        await work();
      } finally {
        run.wait = wait;
      }
      return total;
    };

    /** one listed step: computed, or taken from the step store when the same inputs were computed before */
    const keys = new Map<string, string>();
    const step = async (section: PipelineSection, node: string, ms: number, compute?: () => void, writes: string[] = []): Promise<number> => {
      const info = section.steps.find((s) => s.node === node);
      const storable = !!info?.stored;
      // a result is kept under the video, the parts and parameters the step reads, and the results it builds on
      const key = info ? [node, itemKey, ...info.slots.map((s) => `${s}=${part(s)?.candidate}`), ...info.params.map((p) => `${p}=${profile.params[p]}`), ...info.after.map((n) => keys.get(n) ?? n)].join("|") : "";
      keys.set(node, key);
      const hit = storable ? this.stepStore.get(key) : undefined;
      const before = run.detail.costUsd;
      const spent = hit ? 40 : ms;
      if (hit) {
        run.state("reuse", node, `같은 입력 · 같은 부품으로 계산한 결과를 재사용 (처음 계산 ${hit.seconds.toFixed(1)}초)`);
        await run.node(node, spent, { cached: true, writes });
        (inRange.has(section.id) ? reused : reusedBefore).push(node);
      } else {
        await run.node(node, spent, { writes, body: compute });
        if (storable) this.stepStore.set(key, { step: node, seconds: round(ms / 1000, 2), costUsd: round(run.detail.costUsd - before, 5), source: run.id });
      }
      if (inRange.has(section.id) && info) {
        steps.push({
          node,
          section: section.id,
          label: info.label,
          sec: round(spent / 1000, 2),
          costUsd: round(run.detail.costUsd - before, 5),
          reused: !!hit,
          savedSec: hit ? hit.seconds : 0,
          savedCostUsd: hit ? hit.costUsd : 0,
        });
      }
      return spent;
    };
    const advance = (section: PipelineSection, ms: number) => {
      elapsedMs += ms;
      if (inRange.has(section.id)) sectionMs += ms;
    };
    /** a section the scripted agents of the case flow go through (they ask the user): one step, timed by its waits */
    const agentStep = (section: PipelineSection, ms: number, costBefore: number) => {
      const info = section.steps[0];
      if (inRange.has(section.id)) steps.push({ node: info.node, section: section.id, label: info.label, sec: round(ms / 1000, 2), costUsd: round(run.detail.costUsd - costBefore, 5), reused: false, savedSec: 0, savedCostUsd: 0 });
      advance(section, ms);
    };

    run.emit({
      kind: "run.start",
      title: run.detail.title,
      profile: profile.name,
      profileId: profile.id,
      configHash: profile.configHash,
      models: run.models,
      components: profile.components,
      costCapUsd: run.detail.costCapUsd,
      forkedFrom: source?.id,
    });
    if (source) {
      const answered = memory.done.some((id) => SECTIONS.find((s) => s.id === id)?.interactions.length);
      run.activity(`실행 ${source.id}의 '${entry}' 직전 체크포인트에서 이어 갑니다. 그 앞 단계는 다시 실행하지 않습니다${answered ? ". 그때 받은 답도 그대로 씁니다" : ""}.`, "info");
      run.state("reuse", "__start__", `체크포인트에서 이어 감 · ${entry} 앞까지의 결과 재사용`);
      run.checkpoint([entry]);
    } else {
      run.setStatus("ANALYZING");
      run.edge("__start__", "ingest");
      await run.node("ingest", 300, { writes: ["video"], body: () => run.state("video", "ingest", `원본 보존 · ${item.width ?? 1920}×${item.height ?? 1080} · ${(item.frames ?? Math.round(item.durationSec * 30)).toLocaleString()}프레임`) });
      elapsedMs += 300;
      run.checkpoint(["perceive"]);
      run.edge("ingest", "perceive");
    }

    for (const section of sections) {
      run.setStatus(STATUS_OF[section.id]);
      const fails = spec.failure?.section === section.id;

      if (section.id === "perception") {
        const fps = (profile.params["perception.analysis_fps"] ?? 12) / 12;
        const side = (profile.params["perception.proxy_short_side"] ?? 640) / 640;
        const detector = { "rfdetr-small": 1.4, "dfine-s": 1.3, yolo26n: 0.6, "rfdetr-nano-onnx": 0.85 }[part("perception.object_detector")?.candidate ?? ""] ?? 1;
        const found = perceive(item.truth, profile);
        const task = `perceive#${run.id}`;
        run.emit({ kind: "node.start", node: "perceive", task });
        await run.node("perceive/ingest_probe", 60, { writes: ["probe"] });
        run.edge("perceive/ingest_probe", "perceive/make_proxy");
        let ms = 60;
        ms += await step(section, "perceive/make_proxy", 620 * scale * side, () => run.state("pts_map", "perceive/make_proxy", "프록시 · PTS 원본과 동일"), ["proxy", "pts_map"]);
        run.edge("perceive/make_proxy", "perceive/detect_track");
        ms += await step(
          section,
          "perceive/detect_track",
          1450 * scale * detector * fps * side,
          () => run.state("tracks", "perceive/detect_track", `장면 ${found.scenes} · 얼굴 ${found.faceTracks} · 전신 ${found.bodyTracks} · 번호판 ${found.plates} 트랙 (${part("perception.object_detector")?.short})`),
          ["tracks"],
        );
        const branch = [0, 0, 0];
        await run.parallel([
          async () => {
            run.edge("perceive/detect_track", "perceive/group_identities");
            branch[0] += await step(section, "perceive/group_identities", 380 * scale, () => run.state("person_groups", "perceive/group_identities", `인물 ${found.people} (${part("perception.face_embedding")?.short})`), ["person_groups"]);
            run.edge("perceive/group_identities", "perceive/describe_persons");
            branch[0] += await step(
              section,
              "perceive/describe_persons",
              560 * scale * (local("vlm.person") ? 2.5 : 1),
              () => {
                for (let i = 0; i < found.people; i += 1) run.llm("perceive/describe_persons", "perceive", "vlm_person", 3400, 380, 500);
              },
              ["person_attributes"],
            );
          },
          async () => {
            run.edge("perceive/detect_track", "perceive/ocr");
            branch[1] += await step(
              section,
              "perceive/ocr",
              700 * scale * (local("perception.ocr") ? 1 : 1.2),
              () => {
                if (!local("perception.ocr")) for (let i = 0; i < found.scenes; i += 1) run.llm("perceive/ocr", "perceive", "ocr", 1500, 120, 400);
                run.state("text_items", "perceive/ocr", `글자 ${found.texts.length}곳 · ${found.texts.map((t) => `"${t}"`).join(" · ")} (${part("perception.ocr")?.short})`);
              },
              ["text_items"],
            );
          },
          async () => {
            run.edge("perceive/detect_track", "perceive/describe_segments");
            branch[2] += await step(
              section,
              "perceive/describe_segments",
              1150 * scale * (local("vlm.scene") ? 2.5 : (VLM_PACE[part("vlm.scene")?.candidate ?? ""] ?? 1)),
              () => {
                for (let i = 0; i < found.scenes; i += 1) run.llm("perceive/describe_segments", "perceive", "vlm", 9200, 860, 600);
                run.state("segments", "perceive/describe_segments", `장면 ${found.scenes} 설명 · ${local("vlm.scene") ? "로컬 VLM · 전송 없음" : "얼굴을 흐리게 한 뒤 전송"} (${part("vlm.scene")?.short})`);
              },
              ["segments"],
            );
          },
        ]);
        // the section's time is the sum of its steps, as the real backend reports it: what a reused step saved is
        // then exactly what the section would have taken longer without the reuse
        ms += branch[0] + branch[1] + branch[2];
        for (const from of ["describe_persons", "ocr", "describe_segments"]) run.edge(`perceive/${from}`, "perceive/assemble");
        ms += await step(section, "perceive/assemble", 90, () => run.state("understanding", "perceive/assemble", `장면 ${found.scenes} · 인물 ${found.people} · 번호판 ${found.plates} · 글자 ${found.texts.length} (원본 시간 기준)`), ["understanding"]);
        run.detail.perception = { scenes: found.scenes, people: found.people, plates: found.plates, texts: found.texts.length, tracks: found.faceTracks + found.bodyTracks };
        run.emit({ kind: "node.end", node: "perceive", task, result: "ok", costUsd: 0, writes: ["understanding", "targets"] });
        run.activity(`사람 ${found.people}명, 번호판 ${found.plates}개, 글자 ${found.texts.length}곳, 장면 ${found.scenes}개를 찾았습니다.`, "done");
        advance(section, ms);
        results.perception = { ...found, samples: Math.round(item.durationSec * (profile.params["perception.analysis_fps"] ?? 12)) * (found.faceTracks + found.bodyTracks) };
        run.checkpoint(["await_identity"]);
      }

      if (section.id === "analysis") {
        // the case flow's own steps: the person is asked whether two tracks are the same person, then the analyst
        // works and asks what it cannot know. Each question stops the run until it is answered.
        showScene();
        const before = run.detail.costUsd;
        const ms = await timed(async () => {
          run.edge("perceive", "await_identity");
          run.detail.identity = identityQuestion();
          run.setStatus("AWAITING_IDENTITY");
          run.activity("같은 사람인지 확인이 필요한 장면이 1곳 있습니다.", "wait");
          const identity = await run.interrupt("await_identity", "identity", "묶음 불확실 1건 (T7·T9 ↔ person-3)", () => "same");
          const value = String(identity.value) as Answers["identity"];
          run.detail.identity = { ...run.detail.identity, status: "answered", answer: value };
          const label = { same: "같은 사람", different: "다른 사람", unknown: "모르겠음 → 같은 사람으로 처리" }[value] ?? value;
          run.endInterrupt("await_identity", `${label}${identity.auto ? " (자동 응답)" : ""}`, identity.auto);
          run.state("targets", "await_identity", `T7·T9 → ${value === "different" ? "새 인물(보수적으로 person-3과 같은 처리)" : "person-3에 병합"}`);
          run.checkpoint([`${LA}/model`]);
          run.edge("await_identity", `${LA}/model`);
          run.answers = { scope: "public", scopeAssumed: true, consent: false, consentAssumed: true, captions: "deferred", identity: value };
          await legalAnalyst(run, run.answers);
        });
        agentStep(section, ms, before);
        const asked = run.detail.questions.filter((q) => q.status !== "removed").length;
        results.analysis = { ...analyse(item.truth, profile, run.answers), questions: asked, searches: run.trace.searches, toolCalls: run.trace.analystTools };
      }

      if (section.id === "plan") {
        const analysis = results.analysis ?? analyse(item.truth, profile, run.answers);
        const found = planOf(item.truth, analysis, profile);
        const efficient = part("llm.planner")?.tier === "efficient";
        const once = efficient ? 620 : 980;
        const before = run.detail.costUsd;
        run.edge(`${LA}/validate`, "plan");
        const ms = once * (fails ? 3 : found.attempts);
        await run.node("plan", ms, {
          writes: ["plan"],
          body: () => {
            for (let attempt = 1; attempt <= (fails ? 3 : found.attempts); attempt += 1) {
              run.llm("plan", "planner", "planner", 5200 + attempt * 400, 1300, once);
              const ok = !fails && attempt === found.attempts;
              run.emit({ kind: "validator", node: "plan", ok, issues: ok ? [] : [`D-2가 충족 요건에 연결되지 않았습니다 (생성 ${attempt}회차)`] });
            }
            if (!fails) run.state("plan", "plan", `계획 v1 · 결정 ${found.decisions}건 · 가림 ${found.redacted.length} · 유지 ${found.kept.length} · 미충족 ${found.unsatisfied}`);
          },
        });
        steps.push({ node: "plan", section: section.id, label: section.steps[0].label, sec: round(ms / 1000, 2), costUsd: round(run.detail.costUsd - before, 5), reused: false, savedSec: 0, savedCostUsd: 0 });
        advance(section, ms);
        if (fails) {
          error = spec.failure?.error;
          break;
        }
        // the plan a person would review next, when this run (or the one it continued) has answers to build it from
        if (run.answers) run.detail.plan = planFor(run, run.answers);
        run.checkpoint(["review_guide"]);
        run.edge("plan", "review_guide");
        await run.node("review_guide", 520, { writes: ["review_guide"], body: () => void run.llm("review_guide", "review_guide", "review_guide", 4100, 1100, 500) });
        advance(section, 520);
        results.plan = found;
        run.checkpoint(["await_review"]);
      }

      if (section.id === "review") {
        // the case flow's review and audit: the person edits or accepts the plan, the auditor checks it, and the
        // person decides on what it found
        showScene();
        const answers: Answers = run.answers ?? { scope: "public", scopeAssumed: true, consent: false, consentAssumed: true, captions: "deferred", identity: "same" };
        run.answers = answers;
        if (!run.detail.requirements.length) Object.assign(run.detail, { analyses: buildAnalyses(answers), requirements: buildRequirements(answers) });
        if (!run.detail.plan) run.detail.plan = planFor(run, answers);
        const before = run.detail.costUsd;
        let rounds = 0;
        const ms = await timed(async () => {
          run.edge("review_guide", "await_review");
          for (;;) {
            rounds += 1;
            await reviewStep(run);
            if (await auditStep(run, rounds)) break;
          }
        });
        agentStep(section, ms, before);
        results.review = reviewOf(run, rounds);
        run.checkpoint(["render_original"]);
      }

      if (section.id === "render") {
        const plan = results.plan ?? planOf(item.truth, results.analysis ?? analyse(item.truth, profile, run.answers), profile);
        const found = renderOf(item, plan, profile);
        const encoder = { "x264-crf14": 1.15, videotoolbox: 0.35 }[part("render.encoder")?.candidate ?? ""] ?? 1;
        const renderMs = 2100 * scale * encoder;
        await run.node("render_original", renderMs, { writes: ["output"], body: () => run.state("output", "render_original", `원본 스트림에 모자이크 ${plan.redacted.length}곳 · ${part("render.encoder")?.short}`) });
        steps.push({ node: "render_original", section: section.id, label: section.steps[0].label, sec: round(renderMs / 1000, 2), costUsd: 0, reused: false, savedSec: 0, savedCostUsd: 0 });
        run.edge("render_original", "verify_output");
        const verifyMs = 700 * scale;
        await run.node("verify_output", verifyMs, { writes: ["verification"], body: () => run.state("verification", "verify_output", `검증 ${found.passed} / ${found.rows} 통과`) });
        steps.push({ node: "verify_output", section: section.id, label: section.steps[1].label, sec: round(verifyMs / 1000, 2), costUsd: 0, reused: false, savedSec: 0, savedCostUsd: 0 });
        run.edge("verify_output", "report");
        await run.node("report", 300, { writes: ["report"], body: () => void run.llm("report", "report", "report", 3800, 900, 300) });
        advance(section, renderMs + verifyMs + 300);
        results.render = found;
      }
      memory.done.push(section.id);
    }

    const stats = run.summary();
    if (error) {
      run.detail.error = error;
      run.activity(error, "warn");
      run.setStatus("FAILED");
      run.emit({ kind: "run.end", status: "FAILED" });
    } else {
      run.activity(range[range.length - 1].exit === LAST_NODE ? "출력 영상과 보고서를 만들었습니다." : "요청한 구간까지 실행하고 마쳤습니다.", "done");
      run.setStatus("COMPLETED");
      run.emit({ kind: "run.end", status: "COMPLETED" });
    }
    const calls = run.events.filter((e) => e.kind === "llm");
    const metrics: RunMetrics = {
      status: run.detail.status,
      elapsedSec: round(elapsedMs / 1000, 2),
      sectionSec: round(sectionMs / 1000, 2),
      waitSec: round(run.trace.humanWaitMs / 1000, 1),
      costUsd: round(run.detail.costUsd, 5),
      llmCalls: stats.llmCalls,
      toolCalls: stats.toolCalls,
      tokensIn: calls.reduce((sum, e) => sum + (e.kind === "llm" ? e.tokensIn : 0), 0),
      tokensOut: calls.reduce((sum, e) => sum + (e.kind === "llm" ? e.tokensOut : 0), 0),
      // what reuse saved inside the range: stored step results taken instead of computed
      savedSec: round(steps.reduce((sum, x) => sum + x.savedSec, 0), 2),
      savedCostUsd: round(steps.reduce((sum, x) => sum + x.savedCostUsd, 0), 5),
      steps,
      reuse: { checkpoint: source ? entry : null, steps: reused.sort(), before: reusedBefore.sort() },
      ...Object.fromEntries(range.map((s) => [s.id, results[s.id as SectionId] ?? null])),
    };
    const skippedSections = source ? range.filter((s) => ORDER.indexOf(s.id) < ORDER.indexOf(sections[0].id)).map((s) => s.id) : [];
    if (source && skippedSections.length) {
      // the sections of the range in front of the checkpoint were not run: what the source run spent on them
      // (or would have, where it took stored results itself) is what continuing saved
      const theirs = (this.memory.get(source.id)?.steps ?? []).filter((x) => skippedSections.includes(x.section));
      metrics.reuse.checkpointSavedSec = round(theirs.reduce((sum, x) => sum + x.sec + x.savedSec, 0), 2);
      metrics.reuse.checkpointSavedCostUsd = round(theirs.reduce((sum, x) => sum + x.costUsd + x.savedCostUsd, 0), 5);
      metrics.savedSec = round(metrics.savedSec + metrics.reuse.checkpointSavedSec, 2);
      metrics.savedCostUsd = round(metrics.savedCostUsd + metrics.reuse.checkpointSavedCostUsd, 5);
    }
    return { run, metrics, error };
  }

  /* ----- examples the mock starts with ----- */

  private async seed(): Promise<void> {
    const hours = (n: number) => new Date(Date.now() - n * 3_600_000);
    this.sets.push(
      {
        id: "sample",
        name: "예시 영상 (합성)",
        description: "실제 인물이 없는 합성 영상 1건. 무엇이 들어 있는지 알고 있어 결과를 정답과 비교할 수 있다.",
        builtin: true,
        sendable: true,
        createdAt: "",
        items: [
          {
            id: "sample",
            title: "카페 인터뷰 (예시 영상)",
            filename: "카페_인터뷰.mov",
            durationSec: 72,
            width: 1920,
            height: 1080,
            frames: 2158,
            memo: "유튜브에 올릴 카페 인터뷰 영상입니다. 인터뷰 대상(안경 쓴 분)은 촬영에 동의했습니다.",
            expected: expectedOf(CAFE),
            truth: CAFE,
          },
        ],
      },
      {
        id: "team-footage-7c2e",
        name: "팀 촬영본 · 매장",
        description: "팀원이 직접 찍은 매장 영상. 실제 손님이 나와 외부 모델로 보내지 않는다.",
        builtin: false,
        sendable: false,
        createdAt: hours(52).toISOString(),
        items: [
          {
            id: "v1-41d0",
            title: "주문대 앞 (오후)",
            filename: "counter_afternoon.mp4",
            durationSec: 48,
            width: 1920,
            height: 1080,
            frames: 1440,
            memo: "매장 홍보용. 직원은 동의했고 손님은 동의를 받지 않았습니다.",
            expected: expectedOf(COUNTER),
            origin: null,
            truth: COUNTER,
          },
          {
            id: "v2-9be3",
            title: "매장 앞 골목",
            filename: "street_broll.mp4",
            durationSec: 31,
            width: 1280,
            height: 720,
            frames: 930,
            memo: "배경 화면으로 쓸 골목 영상입니다.",
            expected: null,
            origin: null,
            truth: STREET,
          },
        ],
      },
    );

    // Examples of what accumulates in a set. Each is built by the same launches a person would make, played on a
    // virtual clock in the past; the last one is live and stands at its first question until someone answers it.
    const own = (profileId: string, slot: string) => this.profile(profileId).part[slot].candidate;
    const known = (slot: string, ...ids: string[]) => ids.filter((id) => this.store.slot(slot).candidates.some((c) => c.id === id && c.status !== "planned"));
    const example = async (name: string, build: () => Promise<void>) => {
      try {
        await build();
      } catch (error) {
        // a hand-edited catalog or profile folder without these parts: the page simply starts with fewer examples
        console.warn(`[mock] skipped example experiment ${name}: ${error instanceof Error ? error.message : String(error)}`);
      }
    };
    const past = (n: number): PlayOptions => ({ virtual: true, at: hours(n) });

    await example("sx_seed_vlm_3a9f", async () => {
      const scene = [own("baseline-v2", "vlm.scene"), ...known("vlm.scene", "gemini-3.8-flash", "qwen3-vl-8b-mlx")];
      await this.create({ title: "장면 설명 VLM 3종 비교", from: "perception", baseProfile: "baseline-v2", matrix: { "vlm.scene": scene }, datasets: ["sample"] }, past(27), "sx_seed_vlm_3a9f").done;
    });

    await example("sx_seed_det_b71c", async () => {
      const id = "sx_seed_det_b71c";
      const matrix = {
        "perception.object_detector": [own("local-only", "perception.object_detector"), ...known("perception.object_detector", "yolo26n")],
        "perception.ocr": [own("local-only", "perception.ocr"), ...known("perception.ocr", "rapidocr-small")],
      };
      // every combination on the videos of two datasets
      await this.create({ title: "검출기 × OCR · 두 데이터셋", from: "perception", baseProfile: "local-only", matrix, datasets: ["team-footage-7c2e", "sample"] }, past(20), id).done;
      // one combination again on one video: the cell holds two runs
      await this.add(id, { matrix: { "perception.object_detector": known("perception.object_detector", "yolo26n") }, items: ["sample/sample"] }, past(19)).done;
      // a launch someone stopped after its first run: this combination finished fewer videos than the others
      await this.add(id, { matrix: { "perception.object_detector": known("perception.object_detector", "dfine-s") }, datasets: ["team-footage-7c2e", "sample"] }, { ...past(18), stopAfter: 1 }).done;
    });

    await example("sx_seed_ask_e452", async () => {
      const id = "sx_seed_ask_e452";
      const request = { title: "분석 → 계획 · 분석 모델을 바꾸면", from: "analysis", to: "plan", baseProfile: "baseline-v2", items: ["sample/sample"], costCapUsd: 2 };
      // made the way experiments ran before 2026-11-12: the questions inside the range were answered automatically
      await this.create(request, { ...past(30), autoAnswer: true }, id).done;
      // the same combination again, answered by a person
      await this.add(id, { items: ["sample/sample"] }, past(5)).done;
      // another analyst; this time the interviewee is said to have agreed to the recording only, not to publication
      await this.add(id, { matrix: { "llm.analyst": known("llm.analyst", "gpt-5.4-mini") }, items: ["sample/sample"] }, { ...past(4), answers: { questions: { Q1: "public", Q2: "recording_only", Q3: "no" } } }).done;
    });

    await example("sx_seed_plan_7d10", async () => {
      const id = "sx_seed_plan_7d10";
      const planner = [own("baseline-v2", "llm.planner"), ...known("llm.planner", "gpt-5.4-mini")];
      // the range starts behind the questions: both runs continue the answered run of the experiment above
      await this.create({ title: "계획 모델 비교", from: "plan", baseProfile: "baseline-v2", matrix: { "llm.planner": planner }, items: ["sample/sample"] }, past(3), id).done;
      const failures = { "llm.planner=gpt-5.4-mini|planner.max_generations=1": { section: "plan", error: "계획이 검증을 3회 안에 통과하지 못했습니다 (D-2가 충족 요건에 연결되지 않음)" } };
      await this.add(id, { matrix: { "llm.planner": known("llm.planner", "gpt-5.4-mini") }, paramMatrix: { "planner.max_generations": [1] }, items: ["sample/sample"] }, { ...past(2), failures }).done;
    });

    // live: a run with another planner inside the asking range. It stops at its first question and waits there.
    await example("sx_seed_ask_e452 (waiting run)", async () => {
      void this.add("sx_seed_ask_e452", { matrix: { "llm.planner": known("llm.planner", "gpt-5.4-mini") }, items: ["sample/sample"] }).done;
    });
  }
}

/* ---------- routes ---------- */

const mocks = new WeakMap<Hub, StageMock>();

/**
 * Answers the section-experiment, evaluator, video-dataset and step-store routes under /api/lab, and adds to the
 * run list what a run of an experiment says about itself (the node it continued in front of, the node it stopped
 * after, which combination it is). Returns false for every other route.
 */
export async function stageRoutes(api: { hub: Hub; store: PipelineStore }, req: IncomingMessage, res: ServerResponse, url: URL): Promise<boolean> {
  const [, area, section, a, b, c, d] = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (area !== "lab") return false;
  let mock = mocks.get(api.hub);
  if (!mock) {
    mock = new StageMock(api.hub, api.store);
    mocks.set(api.hub, mock);
  }
  await mock.ready;
  const method = req.method ?? "GET";
  const ok = (status: number, body: unknown) => {
    sendJson(res, status, body);
    return true;
  };

  if (section === "runs" && !a && method === "GET") {
    return ok(200, [...api.hub.ordered().map((r) => ({ ...r.summary(), ...mock.runInfo.get(r.id) })), ...HISTORY_RUNS]);
  }
  if (section === "sections" && method === "GET") return ok(200, SECTIONS);
  if (section === "evaluators" && method === "GET") return ok(200, EVALUATORS);
  if (section === "artifacts" && method === "GET") return ok(200, mock.artifacts());

  if (section === "stage-experiments") {
    if (a === "limits" && !b && method === "GET") return ok(200, LIMITS);
    if (a === "preview" && !b && method === "POST") return ok(200, mock.preview(await readJson(req)));
    if (!a && method === "GET") return ok(200, mock.list());
    if (!a && method === "POST") return ok(201, mock.create(await readJson<CreateStageExperimentRequest>(req)).experiment);
    if (a && !b && method === "GET") return ok(200, mock.get(a));
    if (a && !b && method === "PATCH") return ok(200, mock.edit(a, await readJson(req)));
    if (a && b === "runs" && !c && method === "POST") return ok(201, mock.add(a, await readJson<Launch>(req)).experiment);
    if (a && b === "runs" && c && !d && method === "DELETE") return ok(200, mock.removeRun(a, c));
    if (a && b === "runs" && c && d === "scores" && method === "PUT") return ok(200, mock.putScores(a, c, await readJson(req)));
    if (a && b === "stop" && method === "POST") return ok(200, mock.stop(a));
    if (a && !b && method === "DELETE") {
      mock.remove(a);
      return ok(200, { ok: true });
    }
  }

  if (section === "video-sets") {
    if (!a && method === "GET") return ok(200, mock.listSets());
    if (!a && method === "POST") return ok(201, mock.createSet(await readJson(req)));
    if (a && !b && method === "GET") return ok(200, mock.getSet(a));
    if (a && !b && method === "PATCH") return ok(200, mock.editSet(a, await readJson(req)));
    if (a && b === "items" && c && c !== "from-case" && method === "PATCH") return ok(200, mock.editItem(a, c, await readJson(req)));
    if (a && !b && method === "DELETE") {
      mock.deleteSet(a);
      return ok(200, { ok: true });
    }
    if (a && b === "items" && !c && method === "POST") return ok(201, mock.describeItem(a, await readJson(req)));
    if (a && b === "items" && c === "from-case" && method === "POST") return ok(201, mock.itemFromCase(a, await readJson(req)));
    if (a && b === "items" && c && method === "DELETE") {
      mock.removeItem(a, c);
      return ok(200, { ok: true });
    }
  }
  return false;
}
