// Example LLMOps records for the Lab screens. Every number here is an illustrative placeholder
// until the M5 experiments run; the UI labels them as 예시 수치. Profiles live in configs/pipeline.
import type { DatasetInfo, EvaluatorInfo, ExperimentReport, RunSummary } from "../src/api/contract";

const STRATEGIES = ["proposed", "naive_rag", "llm_only", "rule_based", "static"] as const;

export const SCENARIO_TITLES = [
  "카페 인터뷰 · 직원 명찰",
  "거리 브이로그 · 행인 다수",
  "학교 행사 · 아동 포함",
  "병원 대기실 · 민감 맥락",
  "블랙박스 · 번호판 다수",
  "사무실 회의 · 모니터 글자",
  "집회 현장 · 공익 보도",
  "가족 영상 · 비공개 보관",
  "매장 CCTV · 제공 요청",
  "배달 라이더 · 이동형 기기",
  "강의 녹화 · 수강생 얼굴",
  "증거 촬영 · 분쟁 상대방",
  "SNS 쇼츠 · 자막 실명",
];

const SCORES: number[][] = [
  [0.94, 0.78, 0.74, 0.7, 0.44],
  [0.9, 0.72, 0.69, 0.66, 0.52],
  [0.93, 0.7, 0.66, 0.74, 0.47],
  [0.88, 0.69, 0.64, 0.6, 0.38],
  [0.95, 0.84, 0.8, 0.82, 0.55],
  [0.87, 0.71, 0.73, 0.58, 0.41],
  [0.84, 0.62, 0.61, 0.5, 0.29],
  [0.97, 0.8, 0.77, 0.69, 0.18],
  [0.89, 0.76, 0.7, 0.71, 0.4],
  [0.9, 0.73, 0.68, 0.64, 0.42],
  [0.92, 0.75, 0.71, 0.69, 0.46],
  [0.86, 0.68, 0.66, 0.57, 0.35],
  [0.91, 0.64, 0.7, 0.62, 0.42],
];

export const EXPERIMENT: ExperimentReport = {
  id: "exp-1105-baseline",
  title: "베이스라인 비교",
  dataset: "scenarios-v3 · 시나리오 13개",
  repetitions: 3,
  createdAt: "2026-11-05T21:40:00+09:00",
  gate: [
    { metric: "정책 F1", threshold: "≥ 0.85", value: "0.91", passed: true },
    { metric: "누락률 (미처리 개인정보)", threshold: "≤ 3%", value: "2.1%", passed: true },
    { metric: "인용 유효율", threshold: "100%", value: "100%", passed: true },
    { metric: "케이스 비용", threshold: "≤ $0.30", value: "$0.23", passed: true },
  ],
  strategies: [
    { id: "proposed", label: "제안 구조", description: "도구 사용 에이전트 · 동적 질문 · 독립 감사", policyF1: 0.91, missRate: 0.021, overRate: 0.064, questions: 2.3, counterfactualChange: 0.85, costUsd: 0.23, latencySec: 312, proposed: true },
    { id: "naive_rag", label: "단순 RAG", description: "목적 문구 1회 검색 · 고정 질문 6개", policyF1: 0.74, missRate: 0.11, overRate: 0.12, questions: 6, counterfactualChange: 0.31, costUsd: 0.09, latencySec: 140 },
    { id: "llm_only", label: "LLM 단독", description: "검색 없이 LLM이 판단", policyF1: 0.7, missRate: 0.14, overRate: 0.09, questions: 0, counterfactualChange: 0, costUsd: 0.06, latencySec: 95 },
    { id: "rule_based", label: "규칙 기반", description: "사전 규칙 + LLM 설명", policyF1: 0.66, missRate: 0.19, overRate: 0.05, questions: 6, counterfactualChange: 0, costUsd: 0.01, latencySec: 40 },
    { id: "static", label: "전부 모자이크", description: "모든 사람·글자를 모자이크", policyF1: 0.41, missRate: 0, overRate: 0.58, questions: 0, counterfactualChange: 0, costUsd: 0, latencySec: 30 },
  ],
  scenarios: SCENARIO_TITLES.map((title, i) => ({
    id: `S${String(i + 1).padStart(2, "0")}`,
    title,
    scores: Object.fromEntries(STRATEGIES.map((s, j) => [s, SCORES[i][j]])),
  })),
  counterfactuals: [
    { id: "cf-edition", label: "안내서 판본 교체 (2024판 → 2026.3판)", expected: "change", observed: 0.62, passed: true },
    { id: "cf-remove-25-2", label: "제25조의2 제거 (이동형 기기 조항)", expected: "invariant", observed: 0.92, passed: true },
    { id: "cf-strict", label: "가상의 엄격한 조문 추가", expected: "change", observed: 0.85, passed: true },
    { id: "cf-flip-2021", label: "판례 결론 반전 (2021다219116)", expected: "change", observed: 0.77, passed: true },
    { id: "cf-captions", label: "자막 실명 유무만 바꿈", expected: "change", observed: 0.69, passed: false },
  ],
  ablations: [
    { id: "ab-questions", label: "동적 질문 끄기 → 고정 질문 6개", variable: "dynamic_questions", deltaF1: -0.06, deltaQuestions: 3.7, deltaCost: -0.01, note: "필요한 사실 재현율이 떨어지고 질문 수가 늘어남" },
    { id: "ab-auditor", label: "감사자 끄기", variable: "auditor", deltaF1: -0.05, deltaQuestions: 0, deltaCost: -0.05, note: "문신·자막 같은 잔여 식별자 누락" },
    { id: "ab-vlm-local", label: "VLM 완전 로컬 (V-3)", variable: "vlm_input", deltaF1: -0.04, deltaQuestions: 0.4, deltaCost: -0.01, note: "장면 설명 루브릭 하락 → 질문 증가" },
    { id: "ab-reranker", label: "리랭커 끄기", variable: "reranker", deltaF1: -0.02, deltaQuestions: 0, deltaCost: -0.002, note: "검색 정밀도 소폭 하락" },
    { id: "ab-seed", label: "시드 묶음 제거", variable: "seed_bundle", deltaF1: -0.01, deltaQuestions: 0.2, deltaCost: 0.03, note: "검색 호출이 늘고 정확도는 비슷" },
    { id: "ab-vlm-raw", label: "VLM 원본 전송 (V-1, 동의 영상)", variable: "vlm_input", deltaF1: 0.01, deltaQuestions: -0.1, deltaCost: 0.004, note: "품질 상한 측정용, 기본값으로 쓰지 않음" },
  ],
};

export const DATASETS: DatasetInfo[] = [
  { id: "scenarios-v3", name: "시나리오 · 정답", purpose: "법률 분석가·질문·계획 평가 (정답 요건·인용·계획)", items: 13, version: "v3", updatedAt: "2026-11-04", owner: "임지수 · 송서윤", status: "draft", fields: ["영상", "상황 메모", "필요한 사실", "정답 요건", "정답 인용", "정답 계획"] },
  { id: "perception-gt", name: "지각 정답", purpose: "검출·추적·OCR·인물 묶음 평가", items: 13, version: "v1", updatedAt: "2026-11-03", owner: "임지수", status: "draft", fields: ["박스", "트랙 ID", "텍스트", "인물 묶음"] },
  { id: "retrieval-spans", name: "검색 정답 구간", purpose: "질의별 정답 조문·판결요지 (검색 재현율·nDCG)", items: 120, version: "—", updatedAt: "—", owner: "송서윤", status: "planned", fields: ["질의", "정답 문서", "정답 구간"] },
  { id: "defect-plans", name: "결함 주입 계획", purpose: "감사자 탐지율·오탐률", items: 24, version: "—", updatedAt: "—", owner: "임기헌", status: "planned", fields: ["계획", "주입한 결함", "기대 발견"] },
  { id: "lawirko", name: "LawIRKo 부분집합", purpose: "임베딩·리랭커 비교 (공개 벤치마크)", items: 500, version: "공개판", updatedAt: "2026-10-30", owner: "외부", status: "ready", fields: ["질의", "정답 조문"] },
];

export const EVALUATORS: EvaluatorInfo[] = [
  { id: "verify_output", name: "출력 동일성", type: "deterministic", target: "렌더 · 영상 I/O", metric: "통과한 검사 수", threshold: "전부 통과", lastValue: "전부 통과", description: "프레임 수 · PTS · 오디오 framemd5 · 메타 diff · 처리 영역 밖 PSNR/SSIM · 재검출" },
  { id: "policy_f1", name: "정책 F1", type: "deterministic", target: "계획 (대상×처리×범위)", metric: "F1", threshold: "≥ 0.85", lastValue: "0.91", description: "정답 계획과 결정의 일치" },
  { id: "requirement_recall", name: "요건 재현율", type: "deterministic", target: "Legal Analyst", metric: "재현율 / 정밀도", threshold: "≥ 0.85", lastValue: "0.92 / 0.88", description: "정답 요건 대비 도출 요건" },
  { id: "citation_validity", name: "인용 유효율", type: "deterministic", target: "Legal Analyst · Auditor", metric: "유효 인용 비율", threshold: "100%", lastValue: "100%", description: "검색 결과 존재 · 표기 일치 · 시행일 유효" },
  { id: "trajectory", name: "궤적 지표", type: "trajectory", target: "에이전트 2종", metric: "도구 호출 · 질문 · 커버리지 경고 · 중복 차단", threshold: "예산 내", lastValue: "18 / 25 · 3 · 1 · 1", description: "예산 준수와 불필요한 호출 탐지" },
  { id: "question_quality", name: "질문 품질", type: "llm_panel", target: "동적 질문", metric: "루브릭 5점", threshold: "≥ 4.0", lastValue: "4.3", description: "생성 모델군을 뺀 3종 모델 패널의 다수결" },
  { id: "analysis_rubric", name: "분석 타당성", type: "llm_panel", target: "Legal Analyst", metric: "루브릭 5점", threshold: "≥ 4.0", lastValue: "4.1", description: "근거 연결 · 이익형량 · 불확실성 표시" },
  { id: "human_review", name: "사람 검토", type: "human", target: "보고서 · 계획", metric: "주석 큐 합의율", description: "교수·팀 주석 (M5)" },
];

export const HISTORY_RUNS: RunSummary[] = [
  { id: "eval_s05_r2", title: "블랙박스 · 번호판 다수", status: "COMPLETED", profile: "baseline-v2", startedAt: "2026-11-05T21:12:00+09:00", elapsedSec: 288, costUsd: 0.21, llmCalls: 27, toolCalls: 16, questions: 1, live: false, autopilot: true, source: "history", scenario: "S05", policyF1: 0.95 },
  { id: "eval_s07_r1", title: "집회 현장 · 공익 보도", status: "COMPLETED", profile: "baseline-v2", startedAt: "2026-11-05T20:58:00+09:00", elapsedSec: 351, costUsd: 0.27, llmCalls: 33, toolCalls: 22, questions: 3, live: false, autopilot: true, source: "history", scenario: "S07", policyF1: 0.84 },
  { id: "eval_s03_r3", title: "학교 행사 · 아동 포함", status: "COMPLETED", profile: "budget-saver", startedAt: "2026-11-05T20:31:00+09:00", elapsedSec: 204, costUsd: 0.08, llmCalls: 19, toolCalls: 11, questions: 2, live: false, autopilot: true, source: "history", scenario: "S03", policyF1: 0.81 },
];
