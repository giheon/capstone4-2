import type { components } from "./api-schema";

export type Run = components["schemas"]["RunSnapshot"];
export type Track = components["schemas"]["Track"];
export type Treatment = components["schemas"]["Treatment"];
export type Action = components["schemas"]["Action"];
export type Evidence = components["schemas"]["Evidence"];
export type DocumentRecord = components["schemas"]["DocumentRecord"];
export type IndexRecord = components["schemas"]["IndexRecord"];
export type VideoRecord = components["schemas"]["VideoRecord"];
export type ScenarioInput = components["schemas"]["ScenarioInput"];
export type Profile = { name: string; config: Record<string, unknown> };
export type Health = {
  status: string;
  api_key_configured: boolean;
  ffmpeg_available: boolean;
  redis_available: boolean;
  fixture_mode_available?: boolean;
};

export const actionNames: Record<Action, string> = {
  KEEP: "원본 유지",
  BLUR: "블러",
  MOSAIC: "모자이크",
  MASK: "단색 가림",
};
export const labelNames: Record<string, string> = {
  unknown: "관계 미확인",
  self: "본인 / 요청자",
  third: "제3자",
};
export const statusNames: Record<string, string> = {
  QUEUED: "대기 중",
  ANALYZING: "영상 분석 중",
  AWAITING_LABELS: "얼굴 확인",
  DECIDING: "근거 기반 판정 중",
  REVIEW_READY: "처리 검토",
  REVIEW_REQUIRED: "사용자 검토 필요",
  RENDERING: "영상 생성 중",
  COMPLETED: "출력 완료",
  FAILED: "실행 실패",
};
export const profileNames: Record<string, string> = {
  baseline: "기본 실험",
  haar: "얼굴 검출 비교",
  "small-chunks": "짧은 청크 비교",
  fixture: "테스트 픽스처",
};
export const busyStatuses = new Set([
  "QUEUED",
  "ANALYZING",
  "DECIDING",
  "RENDERING",
]);

export function indexMatchesProfile(
  index: Record<string, unknown>,
  profile: Record<string, unknown>,
) {
  return [
    "mode",
    "extractor",
    "chunker",
    "chunk_size",
    "chunk_overlap",
    "embedding_model",
    "embedding_dimensions",
    "retriever",
  ].every((key) => index[key] === profile[key]);
}
