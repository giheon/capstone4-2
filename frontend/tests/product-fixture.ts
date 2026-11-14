export const appConfig = {
  ready: true,
  messages: [],
  inputs: {
    version: "minimal-input-v1",
    use_purpose: [
      { value: "social_publish", label: "SNS·인터넷 게시" },
      { value: "subject_access", label: "본인 영상 열람 요청에 대한 제공" },
      { value: "unknown", label: "아직 정하지 않음·모름" },
    ],
    audience_scope: [
      { value: "public", label: "불특정 다수에게 공개" },
      { value: "unknown", label: "아직 정하지 않음·모름" },
    ],
    relationship: [
      { value: "unknown", label: "알 수 없음" },
      { value: "self", label: "본인(영상을 업로드한 사용자)" },
      { value: "other", label: "본인이 아닌 다른 사람" },
    ],
    consent: [
      { value: "unknown", label: "동의 여부를 알 수 없음" },
      { value: "granted", label: "이번 이용·공개에 동의함" },
      { value: "not_obtained", label: "동의를 받지 않음" },
      { value: "refused", label: "동의를 명시적으로 거부함" },
      { value: "not_applicable", label: "해당 없음" },
    ],
  },
};
