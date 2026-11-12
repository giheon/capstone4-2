# docs/research — 설계 리서치 보고서(R1~R7)

설계·PRD v2(docs/design)의 모델·도구·RAG·LLMOps·법률 코퍼스·영상 I/O 선정 근거가 된 1차 출처 리서치 원문이다. 각 파일 머리에 조사일·반영 위치·브리프가 있다. 결론 요약은 docs/design/07-models-and-tools.md §10.1~10.7에 있고, 여기 원문은 출처 추적용이다.

| ID | 파일 | 주제 | 설계 반영 |
|---|---|---|---|
| R1 | [R1-video-understanding-vlm.md](./R1-video-understanding-vlm.md) | 영상 이해·VLM 후보 조사 | 설계 §10.1 (07-models-and-tools.md), §7.3 |
| R2 | [R2-perception-models.md](./R2-perception-models.md) | 지각 모델 조사(검출·추적·분할·OCR·번호판·연령) — 종합 | 설계 §10.2 (07-models-and-tools.md) |
| R2a | [R2a-face-detection-embedding.md](./R2a-face-detection-embedding.md) | 얼굴 검출·얼굴 특징 모델 (R2 하위 조사) | 설계 §10.2 |
| R2b | [R2b-closed-set-detectors.md](./R2b-closed-set-detectors.md) | 폐집합 실시간 객체 검출기 (R2 하위 조사) | 설계 §10.2 |
| R2c | [R2c-open-vocabulary-detectors.md](./R2c-open-vocabulary-detectors.md) | 개방 어휘·텍스트 프롬프트 검출기 (R2 하위 조사) | 설계 §10.2 |
| R2d | [R2d-shape-aware-masking.md](./R2d-shape-aware-masking.md) | 형태 인식 마스킹·SAM 계열·분할 (R2 하위 조사) | 설계 §10.2, §5.6 렌더 형태 |
| R2e | [R2e-tracking-reid.md](./R2e-tracking-reid.md) | 다중 객체 추적·Re-ID (R2 하위 조사) | 설계 §10.2 |
| R2f | [R2f-korean-scene-ocr.md](./R2f-korean-scene-ocr.md) | 한국어 장면 텍스트 OCR (R2 하위 조사) | 설계 §10.2 |
| R2g | [R2g-plates-anonymization-age.md](./R2g-plates-anonymization-age.md) | 한국 번호판·익명화 툴킷·연령/미성년 추정 (R2 하위 조사) | 설계 §10.2 |
| R3 | [R3-embeddings-rerankers-chunking.md](./R3-embeddings-rerankers-chunking.md) | 임베딩·리랭커·벡터 스토어·하이브리드 검색·청킹 | 설계 §9, §10.3 |
| R4 | [R4-llm-openrouter-judges.md](./R4-llm-openrouter-judges.md) | LLM(한국어 법률 추론)·OpenRouter 연동·LLM Judge | 설계 §10.4, §12 |
| R5 | [R5-langgraph-langsmith-llmops.md](./R5-langgraph-langsmith-llmops.md) | LangGraph·LangSmith LLMOps | 설계 §5.7, §10.5, §12 |
| R6 | [R6-korean-legal-corpus.md](./R6-korean-legal-corpus.md) | 한국 법률 코퍼스·접근 경로·법적 프레임·테스트 설계 | 설계 §9, §10.6, §11 |
| R7 | [R7-video-io-exact-quality.md](./R7-video-io-exact-quality.md) | 원본 품질 보존 영상 I/O(프록시·PTS·재인코딩·검증) | 설계 §6, §10.7 |

주의: 보고서는 에이전트 산출물 원문이며 영어로 작성된 것이 많다. 설계 문서와 상충하는 수치가 있으면 설계 문서(승인본)와 이 원문의 출처 링크를 함께 확인한 뒤 설계 문서를 갱신한다.
