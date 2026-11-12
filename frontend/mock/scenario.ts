// Sample scenario "카페 인터뷰" used by the M2 mock backend.
// Statute and precedent identifiers are the ones verified in docs/research/R6; the mediation case is a labelled placeholder.
// Judgment notes follow the judgment texts re-read on 2026-11-11 (docs/design/12-corpus-plan-draft.md §A).
import type {
  Analysis,
  Citation,
  Decision,
  Fact,
  Finding,
  IdentityQuestion,
  Plan,
  Question,
  Range,
  Report,
  Requirement,
  ScriptRow,
  Target,
  VerificationRow,
  VideoMeta,
} from "../src/api/contract";
import { METHOD_LABEL, SHAPE_LABEL } from "../src/lib/format";

export const SAMPLE_VIDEO: VideoMeta = {
  filename: "카페_인터뷰.mov",
  sizeBytes: 44_355_584,
  durationSec: 72,
  width: 1920,
  height: 1080,
  fps: "29.97 (가변)",
  codec: "HEVC Main 10",
  audio: "AAC 2ch 48kHz",
  frameCount: 2158,
  sample: true,
};

const C = {
  pipa2: { id: "pipa-2-1", label: "개인정보 보호법 제2조제1호", authority: 1, kind: "법률", note: "개인정보 정의 · 다른 정보와 쉽게 결합해 알아볼 수 있는 정보 포함" },
  pipa2ga: { id: "pipa-2-1-ga", label: "개인정보 보호법 제2조제1호가목", authority: 1, kind: "법률", note: "성명·영상 등으로 개인을 알아볼 수 있는 정보" },
  pipa15: { id: "pipa-15-1-1", label: "개인정보 보호법 제15조제1항제1호", authority: 1, kind: "법률", note: "정보주체의 동의를 받은 경우의 수집·이용" },
  pipa17: { id: "pipa-17-1", label: "개인정보 보호법 제17조제1항", authority: 1, kind: "법률", note: "제3자 제공의 요건" },
  stdVideo: { id: "std-2-9", label: "표준 개인정보 보호지침 제2조제9호", authority: 1, kind: "고시", note: "개인영상정보의 정의" },
  p2004: { id: "2004da16280", label: "대법원 2004다16280", authority: 2, kind: "판례", note: "초상권의 보호 범위" },
  p2020a: { id: "2020da227455", label: "대법원 2020다227455", authority: 2, kind: "판례", note: "증거 확보 목적의 촬영과 제한된 상대에게만 한 전송을 위법성 조각 사정으로 인정" },
  p2020b: { id: "2020da253423", label: "대법원 2020다253423", authority: 2, kind: "판례", note: "언론 보도의 초상권 침해: 공적 인물·공적 관심사면 위법성이 조각될 수 있음 (파기환송)" },
  p2021: { id: "2021da219116", label: "대법원 2021다219116", authority: 2, kind: "판례", note: "촬영 동의의 범위를 넘는 공표에는 별도 동의가 필요 · 증명책임은 촬영·공표자" },
  guidePseudo: { id: "pipc-pseudo-2026", label: "가명정보 처리 가이드라인 (2026.3) 비정형데이터 장", authority: 3, kind: "안내서", note: "흉터·문신 등 신체 특징은 고위험 식별 요소" },
  mediation: { id: "mediation-placeholder", label: "분쟁조정 사례 (자리 표시)", authority: 3, kind: "분쟁조정", note: "코퍼스 구축 후 실제 사례로 바뀝니다", placeholder: true },
} satisfies Record<string, Citation>;

export const SCENES: { id: string; range: Range; label: string }[] = [
  { id: "S1", range: [0, 5], label: "카페 내부, 인터뷰 준비" },
  { id: "S2", range: [5, 38], label: "인터뷰, 뒤편 카운터에서 직원 이동" },
  { id: "S3", range: [38, 49], label: "창밖으로 차량 통과" },
  { id: "S4", range: [49, 72], label: "인터뷰 계속, 직원 재등장(뒷모습)" },
];

export function initialFacts(memo: string): Fact[] {
  const declared = memo.trim().length > 0;
  return [
    {
      key: "purpose.goal",
      label: "촬영 목적",
      value: "interview_publish",
      valueLabel: "인터뷰 영상 게시",
      provenance: declared ? "declared" : "inferred",
      confidence: declared ? undefined : 0.64,
      source: declared ? "상황 메모" : "장면 설명",
      editable: false,
    },
    {
      key: "person-2.recording_consent",
      label: "person-2 촬영 동의",
      value: "yes",
      valueLabel: "예",
      provenance: declared ? "declared" : "inferred",
      confidence: declared ? undefined : 0.55,
      source: declared ? "상황 메모" : "카메라 인지 단서",
      editable: false,
    },
    {
      key: "recording.place",
      label: "장소",
      value: "public_business",
      valueLabel: "카페 영업장 (공개된 장소)",
      provenance: "inferred",
      confidence: 0.92,
      source: "장면 설명",
      editable: true,
      options: [
        { value: "public_business", label: "카페 영업장 (공개된 장소)" },
        { value: "private_space", label: "사적인 공간" },
      ],
    },
    {
      key: "people.minors_present",
      label: "아동 포함",
      value: "no",
      valueLabel: "아니오",
      provenance: "inferred",
      confidence: 0.88,
      source: "인물 속성 · 커버리지 점검",
      editable: true,
      options: [
        { value: "no", label: "아니오" },
        { value: "yes", label: "예" },
      ],
    },
    {
      key: "recording.device",
      label: "촬영 방식",
      value: "handheld",
      valueLabel: "손에 든 카메라 (이동형 기기 아님)",
      provenance: "inferred",
      confidence: 0.9,
      source: "장면 설명",
      editable: false,
    },
    {
      key: "file.location_metadata",
      label: "촬영 위치 메타데이터",
      value: "present",
      valueLabel: "파일에 촬영 위치 좌표가 기록되어 있음",
      provenance: "inferred",
      confidence: 1,
      source: "파일 메타데이터",
      editable: false,
    },
  ];
}

/** the file's shooting-location tag as a target: information in the file, with no place in the picture or on the timeline */
const LOCATION_TARGET = "meta-location";
const LOCATION_TAGS = ["com.apple.quicktime.location.ISO6709"];

export const SCOPE_OPTIONS = [
  { value: "public", label: "불특정 다수에게 공개", detail: "유튜브·SNS 공개 게시, 공개 링크" },
  { value: "limited", label: "제한된 사람에게 공개", detail: "수업·사내 공유·비공개 링크" },
  { value: "private", label: "공개하지 않음", detail: "개인 보관 · 증거 제출" },
];

export const CONSENT_OPTIONS = [
  { value: "yes", label: "예, 공개까지 동의했습니다" },
  { value: "recording_only", label: "촬영에만 동의했습니다" },
];

export const CAPTION_OPTIONS = [
  { value: "yes", label: "이름이나 소속을 표기할 예정입니다" },
  { value: "no", label: "표기하지 않습니다" },
];

export function initialTargets(): Target[] {
  return [
    {
      id: "person-2",
      kind: "person",
      label: "person-2 · 인터뷰 대상",
      detail: "안경, 연한 색 니트. 전 구간 출연",
      ranges: [[0, 72]],
      cues: ["성인 추정 0.91", "역할 단서: 인터뷰 대상", "카메라 인지", "주요도 높음"],
      tracks: ["T2"],
      personal: true,
    },
    {
      id: "person-3",
      kind: "person",
      label: "person-3 · 직원",
      detail: "앞치마, 카운터 뒤에서 이동",
      ranges: [
        [5, 38],
        [49, 58],
      ],
      cues: ["성인 추정 0.86", "역할 단서: 직원(앞치마)", "식별 단서: 명찰 글자"],
      tracks: ["T3", "T4", "T7", "T9"],
      personal: true,
    },
    {
      id: "text-1",
      kind: "text",
      label: "명찰 \"김○○ 바리스타\"",
      detail: "OCR 신뢰도 0.81 · person-3에 부착",
      ranges: [[5, 38]],
      cues: ["성명 추정"],
      attachedTo: "person-3",
      tracks: ["X1"],
      personal: true,
    },
    {
      id: "plate-1",
      kind: "plate",
      label: "창밖 차량 번호판",
      detail: "검출만 수행, 글자는 읽지 않음",
      ranges: [[40.2, 48.5]],
      cues: ["번호판 높이 24px (판독 가능 크기)"],
      tracks: ["P1"],
      personal: true,
    },
    {
      id: "text-2",
      kind: "text",
      label: "간판 \"○○커피\"",
      detail: "OCR 신뢰도 0.95 · 장소 정보",
      ranges: [[0, 72]],
      cues: ["상호"],
      tracks: ["X2"],
      personal: false,
    },
    {
      id: "text-3",
      kind: "text",
      label: "메뉴판",
      detail: "가격표 · 개인정보 아님",
      ranges: [[10, 30]],
      cues: [],
      tracks: ["X3"],
      personal: false,
    },
    {
      id: LOCATION_TARGET,
      kind: "metadata",
      label: "촬영 위치 정보",
      detail: `파일 메타데이터에 촬영 위치 좌표가 기록되어 있음 (태그 ${LOCATION_TAGS.length}개). 화면에는 보이지 않지만 파일을 받은 사람은 읽을 수 있음`,
      ranges: [],
      cues: [],
      tracks: [],
      personal: true,
    },
  ];
}

export function identityQuestion(): IdentityQuestion {
  return {
    id: "IQ-1",
    prompt: "뒷모습으로 나오는 사람(00:49–00:58)이 person-3와 같은 사람인가요?",
    detail: "얼굴이 보이지 않아 묶음 신뢰도가 0.63입니다. 복장(앞치마)은 일치합니다. 법적 판단은 여기서 묻지 않습니다.",
    candidates: [
      { label: "person-3 (정면, 명찰)", at: 12, target: "person-3", note: "트랙 T3" },
      { label: "뒷모습 인물", at: 54, target: "person-3", note: "트랙 T7·T9" },
    ],
    options: [
      { value: "same", label: "같은 사람" },
      { value: "different", label: "다른 사람" },
      { value: "unknown", label: "모르겠음", detail: "같은 사람으로 보고 같은 처리를 적용합니다" },
    ],
    status: "pending",
  };
}

export function roundOneQuestions(): Question[] {
  return [
    {
      id: "Q1",
      factKey: "purpose.publication_scope",
      round: 1,
      prompt: "이 영상을 어디에 공개할 예정인가요?",
      options: SCOPE_OPTIONS,
      basis:
        "식별 가능성은 공개 범위에 따라 달라집니다. 대법원 2020다227455는 촬영물을 제한된 상대에게만 전송한 점을 위법성 판단에서 고려했습니다. 이 사실이 없으면 직원과 번호판의 처리 여부를 정할 수 없습니다.",
      citations: [C.pipa2, C.pipa17, C.p2020a],
      impact: [
        { option: "불특정 다수", effect: "직원 얼굴·명찰, 번호판을 식별할 수 없게 처리하고 파일의 촬영 위치 좌표를 삭제" },
        { option: "제한된 사람", effect: "같은 처리, 공유 범위가 넓어지면 재검토하도록 보고서에 기록" },
        { option: "공개하지 않음", effect: "처리하지 않고 이용 범위 제한만 보고서에 기록" },
      ],
      status: "pending",
      targets: ["person-3", "text-1", "plate-1", LOCATION_TARGET],
      frameAt: 12,
    },
    {
      id: "Q2",
      factKey: "person-2.publication_consent",
      round: 1,
      prompt: "인터뷰 대상(person-2)이 영상 공개에도 동의했나요?",
      note: "상황 메모의 동의는 촬영에 대한 것입니다. 촬영 동의와 공개 동의는 별개입니다.",
      options: CONSENT_OPTIONS,
      basis:
        "대법원 2021다219116에 따르면 촬영에 동의했다고 공표에 동의한 것은 아닙니다. 공개 동의가 있으면 개인정보 보호법 제15조제1항제1호에 따라 얼굴을 그대로 둘 근거가 생깁니다.",
      citations: [C.p2021, C.pipa15],
      impact: [
        { option: "공개까지 동의", effect: "person-2 얼굴을 그대로 둠" },
        { option: "촬영에만 동의 · 모름", effect: "person-2 얼굴도 식별할 수 없게 처리" },
      ],
      status: "pending",
      targets: ["person-2"],
      frameAt: 20,
    },
    {
      id: "Q3",
      factKey: "edit.caption_names",
      round: 1,
      prompt: "공개할 때 자막이나 설명란에 출연자 이름·소속을 적을 예정인가요?",
      options: CAPTION_OPTIONS,
      basis:
        "성명은 그 자체로 개인을 알아볼 수 있는 정보입니다(개인정보 보호법 제2조제1호가목). 자막이나 설명란에 이름을 적으면 얼굴을 가려도 누구인지 알 수 있습니다.",
      citations: [C.pipa2ga, C.pipa2],
      impact: [
        { option: "적을 예정", effect: "person-2 동의 범위에 이름 표기가 포함되는지 보고서에 확인 요청" },
        { option: "적지 않음", effect: "추가 조치 없음" },
      ],
      status: "pending",
      targets: ["person-2"],
      frameAt: 30,
    },
    {
      id: "Q4",
      factKey: "recording.place",
      round: 1,
      prompt: "촬영 장소가 누구나 들어올 수 있는 영업장인가요?",
      options: [],
      basis: "공개된 장소 여부는 촬영 고지와 초상권 판단에 영향을 줍니다.",
      citations: [C.p2004],
      impact: [],
      status: "removed",
      removedReason: "장면 설명으로 이미 알고 있고(추론 0.92), 어느 답이든 결정이 같아 게이트가 묻지 않았습니다.",
      targets: [],
    },
  ];
}

export interface Answers {
  scope: "public" | "limited" | "private";
  scopeAssumed: boolean;
  consent: boolean;
  consentAssumed: boolean;
  captions: "yes" | "no" | "deferred";
  identity: "same" | "different" | "unknown";
}

export function buildAnalyses(a: Answers): Analysis[] {
  const conservative = a.scope !== "private";
  return [
    {
      id: "A-1",
      question: "동의하지 않은 제3자(직원)의 얼굴을 공개해도 되는가",
      applicability: conservative ? "applies" : "not_applies",
      reasoning: conservative
        ? "공개 범위가 넓고 직원의 동의가 없으므로 식별할 수 없게 처리해야 한다."
        : "공개하지 않으므로 제3자 제공에 해당하지 않는다.",
      citations: [C.pipa17, C.pipa2, C.p2004],
      dependsOn: ["purpose.publication_scope"],
      targets: ["person-3"],
    },
    {
      id: "A-2",
      question: "얼굴을 가려도 명찰의 이름으로 직원을 알아볼 수 있는가",
      applicability: conservative ? "applies" : "not_applies",
      reasoning: "명찰에 성명으로 보이는 글자가 있어 얼굴 처리만으로는 식별을 막을 수 없다.",
      citations: [C.pipa2ga, C.pipa2],
      dependsOn: ["purpose.publication_scope"],
      targets: ["text-1", "person-3"],
    },
    {
      id: "A-3",
      question: "창밖 차량의 번호판이 개인정보인가",
      applicability: conservative ? "applies" : "not_applies",
      reasoning: "차량등록번호는 다른 정보와 쉽게 결합해 소유자를 알아볼 수 있는 정보다.",
      citations: [C.pipa2, C.guidePseudo],
      dependsOn: ["purpose.publication_scope"],
      targets: ["plate-1"],
    },
    {
      id: "A-4",
      question: "인터뷰 대상의 얼굴을 그대로 둘 근거가 있는가",
      applicability: a.consent ? "not_applies" : "applies",
      reasoning: a.consent
        ? "공개까지 동의했으므로 얼굴을 그대로 둘 수 있다."
        : "촬영 동의만으로는 공표 동의가 있다고 볼 수 없다.",
      citations: [C.pipa15, C.p2021],
      dependsOn: ["person-2.publication_consent"],
      targets: ["person-2"],
    },
    {
      id: "A-5",
      question: "간판의 상호가 직원 식별에 기여하는가",
      applicability: "uncertain",
      reasoning: "공개된 영업장이고 결합 용이성을 판단할 근거가 부족하다. 보수적 가정은 '유지하고 보고서에 고지'.",
      citations: [C.pipa2],
      dependsOn: ["recording.place"],
      targets: ["text-2"],
    },
    {
      id: "A-6",
      question: "파일에 기록된 촬영 위치 좌표를 함께 공개해도 되는가",
      applicability: conservative ? "applies" : "not_applies",
      reasoning: conservative
        ? "좌표는 화면에 보이지 않아도 파일을 받은 사람이 읽을 수 있고, 영상과 결합하면 출연자가 일하는 곳을 알아볼 수 있게 한다."
        : "공개하지 않으므로 파일의 좌표가 다른 사람에게 전달되지 않는다.",
      citations: [C.pipa2],
      dependsOn: ["purpose.publication_scope"],
      targets: [LOCATION_TARGET],
    },
  ];
}

export function buildRequirements(a: Answers): Requirement[] {
  if (a.scope === "private") return [];
  const reqs: Requirement[] = [
    {
      id: "REQ-1",
      statement: "공개 범위가 넓으므로 동의하지 않은 직원의 얼굴을 식별할 수 없게 처리",
      targets: ["person-3"],
      ranges: [
        [5, 38],
        [49, 58],
      ],
      derivedFrom: ["A-1"],
      citations: [C.pipa17, C.pipa2, C.p2004],
      scope: "face",
    },
    {
      id: "REQ-2",
      statement: "얼굴을 가려도 이름으로 식별되므로 명찰 글자를 읽을 수 없게 처리",
      targets: ["text-1"],
      ranges: [[5, 38]],
      derivedFrom: ["A-2"],
      citations: [C.pipa2ga, C.pipa2],
      scope: "text",
    },
    {
      id: "REQ-3",
      statement: "차량 번호판을 읽을 수 없게 처리 (번호판이 보이는 전 구간)",
      targets: ["plate-1"],
      ranges: [[40.2, 48.5]],
      derivedFrom: ["A-3"],
      citations: [C.pipa2, C.guidePseudo],
      scope: "plate",
    },
    {
      id: "REQ-5",
      statement: "공개하는 파일에서 촬영 위치 좌표를 삭제",
      targets: [LOCATION_TARGET],
      ranges: [],
      derivedFrom: ["A-6"],
      citations: [C.pipa2],
      scope: "metadata",
    },
  ];
  if (!a.consent) {
    reqs.push({
      id: "REQ-4",
      statement: "공개 동의가 확인되지 않은 인터뷰 대상의 얼굴을 식별할 수 없게 처리",
      targets: ["person-2"],
      ranges: [[0, 72]],
      derivedFrom: ["A-4"],
      citations: [C.pipa15, C.p2021],
      scope: "face",
    });
  }
  return reqs;
}

export function buildPlan(a: Answers): Plan {
  const keepAll = a.scope === "private";
  const person3 = a.identity === "different" ? "person-3 · 직원 (뒷모습 인물 포함)" : "person-3 · 직원";
  const decisions: Decision[] = [
    {
      id: "D-1",
      targetId: "person-3",
      targetLabel: `${person3} 얼굴`,
      scope: "face",
      method: keepAll ? "keep" : "mosaic",
      shape: "ellipse",
      ranges: [
        [5, 38],
        [49, 58],
      ],
      satisfies: keepAll ? [] : ["REQ-1"],
      analysisId: "A-1",
      rationale: keepAll ? "공개하지 않으므로 처리하지 않습니다." : "동의하지 않은 제3자이고 공개 범위가 넓습니다.",
      citations: [C.pipa17, C.pipa2, C.p2004],
      similarCases: [C.mediation],
      riskIfChanged: "그대로 두면 동의 없는 제3자의 얼굴이 공개되어 제17조 위반 소지가 있습니다.",
      risk: keepAll ? "low" : "high",
      source: "planner",
      edited: false,
    },
    {
      id: "D-2",
      targetId: "text-1",
      targetLabel: "person-3 명찰 글자",
      scope: "text",
      method: keepAll ? "keep" : "mosaic",
      shape: "box",
      ranges: [[5, 38]],
      satisfies: keepAll ? [] : ["REQ-2"],
      analysisId: "A-2",
      rationale: keepAll ? "공개하지 않으므로 처리하지 않습니다." : "얼굴을 가려도 이름으로 식별됩니다. 글자는 얼굴보다 굵은 모자이크로 덮습니다.",
      citations: [C.pipa2ga, C.pipa2],
      similarCases: [],
      riskIfChanged: "그대로 두면 이름이 노출되어 얼굴 처리(D-1)가 무력화됩니다.",
      risk: keepAll ? "low" : "high",
      source: "planner",
      edited: false,
    },
    {
      id: "D-3",
      targetId: "plate-1",
      targetLabel: "창밖 차량 번호판",
      scope: "plate",
      method: keepAll ? "keep" : "mosaic",
      shape: "box",
      ranges: [[41, 48.5]],
      satisfies: keepAll ? [] : ["REQ-3"],
      analysisId: "A-3",
      rationale: keepAll ? "공개하지 않으므로 처리하지 않습니다." : "차량 소유자를 알아볼 수 있는 정보입니다.",
      citations: [C.pipa2, C.guidePseudo],
      similarCases: [],
      riskIfChanged: "그대로 두면 차량 소유자를 알아볼 수 있습니다. 구간을 줄이면 진입 프레임이 노출됩니다.",
      risk: keepAll ? "low" : "medium",
      source: "planner",
      edited: false,
    },
    {
      id: "D-4",
      targetId: "person-2",
      targetLabel: "person-2 · 인터뷰 대상 얼굴",
      scope: "face",
      method: a.consent || keepAll ? "keep" : "mosaic",
      shape: "ellipse",
      ranges: [[0, 72]],
      satisfies: a.consent || keepAll ? [] : ["REQ-4"],
      analysisId: "A-4",
      rationale: a.consent
        ? "공개까지 동의했다는 답변이 있어 그대로 둡니다."
        : keepAll
          ? "공개하지 않으므로 처리하지 않습니다."
          : "공개 동의가 확인되지 않아 보수적으로 처리합니다.",
      citations: [C.pipa15, C.p2021],
      similarCases: [],
      riskIfChanged: a.consent
        ? "동의가 철회되면 다시 렌더해야 합니다. 동의 범위에 공개 플랫폼이 포함되는지 확인을 권합니다."
        : "그대로 두면 공표 동의 없이 얼굴이 공개됩니다.",
      risk: a.consent || keepAll ? "low" : "high",
      source: "planner",
      edited: false,
    },
    {
      id: "D-5",
      targetId: "text-2",
      targetLabel: "간판 \"○○커피\"",
      scope: "region",
      method: "keep",
      shape: "box",
      ranges: [[0, 72]],
      satisfies: [],
      analysisId: "A-5",
      rationale: "판단 근거가 부족해 불확실로 표시했습니다. 보수적 가정은 '유지하고 보고서에 고지'입니다.",
      citations: [C.pipa2],
      similarCases: [],
      riskIfChanged: "가리면 장소 정보가 사라지지만 법적 위험은 줄어듭니다.",
      risk: "uncertain",
      source: "planner",
      edited: false,
    },
    {
      id: "D-6",
      targetId: LOCATION_TARGET,
      targetLabel: "촬영 위치 정보",
      scope: "metadata",
      method: keepAll ? "keep" : "remove",
      ranges: [],
      satisfies: keepAll ? [] : ["REQ-5"],
      analysisId: "A-6",
      rationale: keepAll ? "공개하지 않으므로 파일의 좌표를 그대로 둡니다." : "파일을 받은 사람이 촬영 위치 좌표를 읽을 수 있습니다.",
      citations: [C.pipa2],
      similarCases: [],
      riskIfChanged: "그대로 두면 공개한 파일에서 촬영 위치 좌표를 읽을 수 있습니다.",
      risk: keepAll ? "low" : "medium",
      source: "planner",
      edited: false,
    },
  ];
  return { version: 1, decisions, unsatisfied: [] };
}

const covers = (d: Decision, range: Range) =>
  d.method !== "keep" && d.ranges.some(([s, e]) => s <= range[0] + 0.05 && e >= range[1] - 0.05);

interface AuditOptions {
  /** a frontier auditor re-inspects people and looks for identifiers the plan left visible */
  deep: boolean;
  /** the profile has a plate detector, so a plate can be tracked and covered */
  plateTracked: boolean;
  /** the profile has a segmentation model, so outline masks are available */
  outline: boolean;
}

export function auditPlan(plan: Plan, requirements: Requirement[], opts: AuditOptions = { deep: true, plateTracked: true, outline: true }): Finding[] {
  const findings: Finding[] = [];
  const byTarget = (id: string) => plan.decisions.filter((d) => d.targetId === id);
  for (const req of requirements) {
    for (const target of req.targets) {
      const ds = byTarget(target);
      const keep = ds.length > 0 && ds.every((d) => d.method === "keep");
      // file metadata has no ranges to cover: the requirement is met when the information is removed
      const uncovered = req.scope === "metadata" ? [] : req.ranges.filter((range) => !ds.some((d) => covers(d, range)));
      if (req.scope === "metadata" ? !keep : uncovered.length === 0) continue;
      if (target === "plate-1" && !keep) {
        findings.push({
          id: "F-2",
          type: "requirement_unmet",
          severity: "medium",
          title: "번호판이 결정 구간보다 먼저 보입니다",
          detail:
            "번호판 트랙은 00:40.2부터 보이는데 D-3은 00:41.0부터 시작합니다. 진입 0.8초 동안 판독 가능한 크기(높이 24px)로 노출됩니다.",
          targetId: "plate-1",
          targetLabel: "창밖 차량 번호판 · D-3",
          ranges: [[40.2, 41]],
          citations: [C.pipa2],
          recommendation: "D-3 구간을 00:40.0–00:48.7로 넓힙니다 (트랙 전체 + 시간 여유).",
          status: "open",
        });
        continue;
      }
      findings.push({
        id: `F-${req.id}`,
        type: "requirement_unmet",
        severity: "high",
        title: `${req.id}가 충족되지 않습니다`,
        detail: keep
          ? `검토에서 '${ds[0]?.targetLabel ?? target}'을(를) 유지로 바꿔 요건 "${req.statement}"을 만족하지 않습니다.`
          : "요건 구간 일부가 결정 구간 밖에 있습니다.",
        targetId: target,
        targetLabel: ds[0]?.targetLabel ?? target,
        ranges: uncovered,
        citations: req.citations,
        recommendation: req.scope === "metadata" ? "파일에서 이 정보를 삭제합니다." : "계획자가 제안한 처리로 되돌립니다.",
        status: "open",
      });
    }
  }
  if (opts.deep) {
    const tag = plan.decisions.find((d) => d.targetId === "text-1");
    if (tag && tag.method === "keep" && !findings.some((f) => f.targetId === "text-1")) {
      findings.push({
        id: "F-4",
        type: "residual_identifier",
        severity: "high",
        title: "명찰 글자로 직원 이름을 읽을 수 있습니다",
        detail: "얼굴을 모자이크해도 명찰의 \"김○○ 바리스타\"가 그대로 보입니다. 성명은 그 자체로 개인을 알아볼 수 있는 정보입니다(개인정보 보호법 제2조제1호가목).",
        targetId: "text-1",
        targetLabel: "명찰 \"김○○ 바리스타\" · D-2",
        ranges: [[5, 38]],
        citations: [C.pipa2ga, C.pipa2],
        recommendation: "명찰 글자를 모자이크(사각형)로 처리합니다, 00:05–00:38.",
        status: "open",
      });
    }
    const plate = plan.decisions.find((d) => d.targetId === "plate-1");
    if (!opts.plateTracked) {
      findings.push({
        id: "F-6",
        type: "residual_identifier",
        severity: "medium",
        title: "번호판이 보이지만 검출 트랙이 없어 처리할 수 없습니다",
        detail: "00:40–00:48 창밖 차량의 번호판이 판독 가능한 크기로 보입니다. 이 프로필은 번호판 검출기를 쓰지 않아 가릴 영역이 없습니다.",
        targetId: "plate-1",
        targetLabel: "창밖 차량 번호판",
        ranges: [[40.2, 48.5]],
        citations: [C.pipa2],
        recommendation: "번호판 검출기를 켠 프로필로 다시 실행하세요. 이 실행에서는 적용할 수 없습니다.",
        status: "open",
      });
    } else if (plate && plate.method === "keep" && !findings.some((f) => f.targetId === "plate-1")) {
      findings.push({
        id: "F-5",
        type: "residual_identifier",
        severity: "medium",
        title: "번호판이 처리되지 않았습니다",
        detail: "번호판 요건이 도출되지 않아 D-3이 유지로 남았습니다. 차량등록번호는 소유자를 알아볼 수 있는 정보입니다.",
        targetId: "plate-1",
        targetLabel: "창밖 차량 번호판 · D-3",
        ranges: [[40.2, 48.5]],
        citations: [C.pipa2, C.guidePseudo],
        recommendation: "D-3을 모자이크(사각형)로 바꿉니다, 00:40.0–00:48.7.",
        status: "open",
      });
    }
  }
  const d1 = plan.decisions.find((d) => d.id === "D-1");
  if (d1 && d1.method !== "keep") {
    if (opts.deep) findings.unshift({
      id: "F-1",
      type: "residual_identifier",
      severity: "high",
      title: "얼굴과 명찰을 가린 뒤에도 문신으로 직원을 알아볼 수 있습니다",
      detail:
        "inspect_visual 재점검 결과 00:52–00:58에 person-3의 왼팔 안쪽 문자 문신이 보입니다(0.77). 신체 특징은 고위험 식별 요소이며 REQ-1의 '식별할 수 없게'에 미달합니다.",
      targetId: "person-3-tattoo",
      targetLabel: "person-3 · 왼팔 문신",
      ranges: [[52, 58]],
      citations: [C.guidePseudo, C.pipa2],
      recommendation: "문신 영역에 모자이크(사각형)를 추가합니다, 00:51.7–00:58.3.",
      status: "open",
    });
    const d4 = plan.decisions.find((d) => d.id === "D-4");
    if (d1.shape === "ellipse" && d4?.method === "keep") {
      findings.push({
        id: "F-3",
        type: "over_redaction",
        severity: "low",
        title: "직원 얼굴 모자이크가 인터뷰 대상의 손짓까지 덮습니다",
        detail:
          "00:30–00:34에 D-1의 타원이 person-2의 손 제스처와 겹칩니다. 요건 충족에는 문제가 없지만 영상의 쓸모가 줄어듭니다.",
        targetId: "person-3",
        targetLabel: "person-3 얼굴 · D-1",
        ranges: [[30, 34]],
        citations: [],
        recommendation: opts.outline
          ? "D-1의 형태를 타원에서 윤곽(얼굴 분할 마스크)으로 바꿉니다."
          : "윤곽 마스크 모델이 없는 프로필이라 타원을 유지합니다. 과잉 처리로 기록합니다.",
        status: "open",
      });
    }
  }
  return findings;
}

export function applyFinding(plan: Plan, finding: Finding, opts: { outline: boolean } = { outline: true }): Plan {
  const decisions = plan.decisions.map((d) => ({ ...d }));
  if (finding.id === "F-1") {
    decisions.push({
      id: "D-7",
      targetId: "person-3-tattoo",
      targetLabel: "person-3 왼팔 문신",
      scope: "body",
      method: "mosaic",
      shape: "box",
      ranges: [[51.7, 58.3]],
      satisfies: ["REQ-1"],
      rationale: "감사 권고 F-1을 적용했습니다.",
      citations: [C.guidePseudo, C.pipa2],
      similarCases: [],
      riskIfChanged: "제거하면 문신으로 직원을 알아볼 수 있습니다.",
      risk: "high",
      source: "auditor",
      edited: false,
    });
  } else if (finding.id === "F-2") {
    const d3 = decisions.find((d) => d.id === "D-3");
    if (d3) {
      d3.ranges = [[40, 48.7]];
      d3.source = "auditor";
    }
  } else if (finding.id === "F-3") {
    const d1 = decisions.find((d) => d.id === "D-1");
    if (d1 && opts.outline) {
      d1.shape = "outline";
      d1.source = "auditor";
    }
  } else if (finding.id === "F-4") {
    const d2 = decisions.find((d) => d.id === "D-2");
    if (d2) Object.assign(d2, { method: "mosaic", shape: "box", ranges: [[5, 38]], source: "auditor", rationale: "감사 권고 F-4를 적용했습니다. 이름이 보이면 얼굴 처리가 무력화됩니다.", risk: "high" });
  } else if (finding.id === "F-5") {
    const d3 = decisions.find((d) => d.id === "D-3");
    if (d3) Object.assign(d3, { method: "mosaic", shape: "box", ranges: [[40, 48.7]], source: "auditor", rationale: "감사 권고 F-5를 적용했습니다.", risk: "medium" });
  } else if (finding.id.startsWith("F-REQ")) {
    const fresh = buildPlanFallback(finding);
    for (const d of decisions) {
      if (fresh.has(d.id)) Object.assign(d, fresh.get(d.id), { source: "auditor", edited: false });
    }
  }
  return { ...plan, version: plan.version + 1, decisions };
}

function buildPlanFallback(finding: Finding): Map<string, Partial<Decision>> {
  const restore: Record<string, Partial<Decision>> = {
    "REQ-1": { method: "mosaic", shape: "ellipse", ranges: [[5, 38], [49, 58]] },
    "REQ-2": { method: "mosaic", shape: "box", ranges: [[5, 38]] },
    "REQ-3": { method: "mosaic", shape: "box", ranges: [[40, 48.7]] },
    "REQ-4": { method: "mosaic", shape: "ellipse", ranges: [[0, 72]] },
    "REQ-5": { method: "remove" },
  };
  const req = finding.id.replace("F-", "");
  const map = new Map<string, Partial<Decision>>();
  const id = { "REQ-1": "D-1", "REQ-2": "D-2", "REQ-3": "D-3", "REQ-4": "D-4", "REQ-5": "D-6" }[req];
  if (id && restore[req]) map.set(id, restore[req]);
  return map;
}

/** the report's script: one row per time range of every decision; a decision about file metadata is one row for the whole file */
function buildScript(plan: Plan, requirements: Requirement[], findings: Finding[]): ScriptRow[] {
  const rows: ScriptRow[] = [];
  for (const d of plan.decisions) {
    const reqs = d.satisfies.map((id) => requirements.find((r) => r.id === id)?.id ?? id);
    const finding = findings.find(
      (f) =>
        (f.id === "F-1" && d.id === "D-7") ||
        ((f.id === "F-2" || f.id === "F-5") && d.id === "D-3") ||
        (f.id === "F-3" && d.id === "D-1") ||
        (f.id === "F-4" && d.id === "D-2") ||
        (f.id === "F-REQ-5" && d.id === "D-6"),
    );
    const basis = [
      reqs.length ? reqs.join(", ") : d.method === "keep" ? d.rationale : "",
      d.citations[0]?.label ?? "",
      finding ? `감사 ${finding.id} ${finding.status === "applied" ? "적용" : "거부 · 위험 수용"}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    if (d.scope === "metadata") {
      rows.push({ start: 0, end: SAMPLE_VIDEO.durationSec, scene: "파일 전체", target: d.targetLabel, treatment: METHOD_LABEL[d.method], basis });
      continue;
    }
    for (const [s, e] of d.ranges) {
      const scene = SCENES.find((sc) => s >= sc.range[0] && s < sc.range[1]) ?? SCENES[0];
      rows.push({
        start: s,
        end: e,
        scene: scene.label,
        target: d.targetLabel,
        treatment: d.method === "keep" || !d.shape ? METHOD_LABEL[d.method] : `${METHOD_LABEL[d.method]} (${SHAPE_LABEL[d.shape]})`,
        basis,
      });
    }
  }
  return rows.sort((a, b) => a.start - b.start || a.end - b.end);
}

/** did the plan remove the file's location tag */
export const removesLocation = (plan: Plan) => plan.decisions.some((d) => d.targetId === LOCATION_TARGET && d.method === "remove");

/** the output checks of the mock run; one more when the plan removed the location tag */
export function verificationRows(locationRemoved: boolean): VerificationRow[] {
  const rows: VerificationRow[] = [
    { check: "프레임 수", original: "2,158", output: "2,158", verdict: "pass" },
    { check: "PTS 목록 (time_base 1/30000)", original: "가변 프레임레이트", output: "전부 동일", verdict: "pass" },
    { check: "오디오 framemd5", original: "6161b8b1…", output: "6161b8b1…", verdict: "pass", note: "바이트 단위 동일" },
    { check: "메타·챕터·회전·색 정보", original: "BT.709 · 회전 0°", output: locationRemoved ? "동일 (encoder 태그와 삭제한 위치 태그만 다름)" : "동일 (encoder 태그만 다름)", verdict: "pass" },
    { check: "픽셀 포맷 · 프로파일", original: "yuv420p10le · Main 10", output: "yuv420p10le · Main 10", verdict: "pass" },
    { check: "처리 영역 밖 PSNR-Y / SSIM", original: "—", output: "47.8 dB (최저 45.9) / 0.994", verdict: "pass", note: "기준 ≥ 45 dB · ≥ 0.99 (가정 U9, M4에서 확정)" },
    { check: "VMAF (전체 프레임)", original: "—", output: "96.1", verdict: "info", note: "참고 수치" },
    { check: "처리 영역 재검출 (얼굴·번호판·OCR)", original: "—", output: "0 / 2,158 프레임", verdict: "pass" },
  ];
  if (locationRemoved) rows.push({ check: "위치 메타데이터 삭제", original: `태그 ${LOCATION_TAGS.length}개 (${LOCATION_TAGS.join(", ")})`, output: "남은 태그 없음", verdict: "pass" });
  return rows;
}

export function buildReport(args: {
  plan: Plan;
  requirements: Requirement[];
  findings: Finding[];
  questions: Question[];
  answers: Answers;
  costUsd: number;
  elapsedSec: number;
  edits: string[];
}): Report {
  const { plan, requirements, findings, questions, answers } = args;
  const history: string[] = [];
  for (const q of questions.filter((q) => q.status !== "removed")) {
    const chosen = q.options.find((o) => o.value === q.answer)?.label;
    history.push(
      q.status === "answered"
        ? `${q.id} ${q.prompt} → ${chosen ?? q.answer}`
        : q.status === "deferred"
          ? `${q.id} ${q.prompt} → 미답 (보수적 가정 적용)`
          : `${q.id} ${q.prompt} → ${q.answer === "unknown" ? "모름 (보수적 가정 적용)" : "대기"}`,
    );
  }
  history.push(...args.edits);
  for (const f of findings) {
    history.push(
      f.status === "applied"
        ? `감사 ${f.id} 적용 — ${f.recommendation}`
        : f.status === "rejected"
          ? `감사 ${f.id} 거부 · 위험 수용 — 사유: ${f.rejectReason ?? "기록 없음"}`
          : `감사 ${f.id} 미처리`,
    );
  }
  const notices = [
    "간판 \"○○커피\"는 장소 정보로 유지했습니다. 직원 식별과 결합될 수 있어 불확실로 기록합니다.",
    "음성은 처리하지 않았습니다. 대화에 이름이나 장소가 들릴 수 있습니다.",
    "이 보고서는 법률 자문이 아니며, 근거는 조사일 기준 현행 법령과 판례입니다.",
  ];
  if (answers.consent) notices.splice(1, 0, "person-2의 공개 동의가 철회되면 다시 렌더해야 합니다.");
  if (answers.captions === "yes") notices.push("자막에 이름을 적을 예정이므로 person-2 동의 범위에 이름 표기가 포함되는지 확인하세요.");
  if (answers.captions === "deferred") notices.push("자막 이름 표기 질문에 답하지 않아 '표기하지 않음'으로 가정했습니다.");
  if (answers.scope === "limited") notices.push("제한된 공개를 전제로 했습니다. 공유 범위가 넓어지면 다시 검토하세요.");
  notices.push(
    removesLocation(plan)
      ? `원본 파일의 촬영 위치 메타데이터(${LOCATION_TAGS.join(", ")})는 계획에 따라 출력 파일에서 삭제했습니다.`
      : `원본 파일의 촬영 위치 메타데이터(${LOCATION_TAGS.join(", ")})는 출력 파일에 그대로 있습니다. 스크립트의 '촬영 위치 정보' 행에 이유가 있습니다.`,
  );
  const cites = new Map<string, Citation>();
  for (const d of plan.decisions) for (const c of [...d.citations, ...d.similarCases]) cites.set(c.id, c);
  return {
    finishedAt: new Date().toISOString(),
    elapsedSec: args.elapsedSec,
    costUsd: args.costUsd,
    output: { filename: "카페_인터뷰.privframe.mov", sizeBytes: 43_934_720, codec: "HEVC Main 10 · CRF 16" },
    verification: verificationRows(removesLocation(plan)),
    script: buildScript(plan, requirements, findings),
    history,
    notices,
    citations: [...cites.values()].sort((a, b) => a.authority - b.authority),
  };
}
