> PrivFrame 2.0 설계·PRD v2(승인본 2026-11-05)의 일부. 전체 목차와 다이어그램 검증 기록은 [README.md](./README.md).

## 부록 A. 채택 조합(조합 2·3·4) 아이디어별 반영
A1 간접 식별(인물 전파·요건) · A2 사건·행동 맥락(장면 설명·구간별 결정) · A3 전체 맥락 전파 · A4 목적별 보존(Planner 유용성·KEEP 근거) · A5 다중 영상(M6, Batch) · B2 유사 판례·사례(실제 사례 검색) · B3 예외·충돌 추론(Analyst 루프·권위 등급) · B7 공개 대상별 계획(케이스=범위 1개, fork 재사용) · C1 Verifier(Auditor 요건 충족 검증) · C2 검수 우선순위(Review Guide priority) · C3 불확실성 질문(동적 질문).
## 부록 B. 기존 코드 재사용 지도

> 설계 시점의 계획이다. 실제로는 2.0 코드를 새로 썼고 1.0 모듈을 import하지 않았다(14 §14.8). 1.0 코드는 2026-11-12에 저장소에서 지웠다(커밋 `c03476e`).

유지: `adapters/vision.py`의 YuNet·장면 경계·정렬·렌더 커널(마스크 적용 함수), `adapters/identity.py`·`domain/identity.py`(그룹화·검토), `adapters/rag.py`의 law.go.kr 추출기·청커 골격·Chroma 어댑터, `adapters/decision.py`의 구조화 출력 패턴, `application/graphs.py`의 실행 기록·fingerprint 캐시·supersede(데코레이터로 추출), `service.py`의 revision 409·fork/revise, `api.py` 오류 매핑, 테스트 fixture 체계. 교체: 정규화·렌더 경로, OpenCV 프레임 탐색(`track_editing.py`의 `CAP_PROP_POS_FRAMES`), IoU 트래커, QueryBuilder, DecisionPlan 계약, GraphRunner 체인, 입력 폼·UI.
## 부록 C. 추가 스킬(find-skills)
M3 설치: `langchain-ai/langsmith-skills@langsmith-evaluator`·`langsmith-trace`·`langsmith-dataset`(공식, ~5K), `langchain-ai/langchain-skills@langgraph-cli`(공식, 9.5K), `openrouterteam/skills@openrouter-models`·`openrouter-video`(공식). 2026-11-06 설치 시도는 Claude Code 자동 모드의 외부 코드 차단에 걸려 사용자가 직접 설치한다([11-tool-setup.md](./11-tool-setup.md) §11.6). M1: `softaworks/agent-toolkit@mermaid-diagrams`(5.2K). 미설치: PRD 생성·OpenCV·RAG 평가 스킬(본 문서·LangSmith 평가기로 대체).
## 부록 D. Notion 페이지 ID
허브 3df75ad03a30808695aae6866e72cb27 · 작업 관리 7ac75ad03a3082c1878e016dca54b399 · 의사결정 3e475ad03a30812f882af5555e5d8d64 · RAG 동적 활용 3fb75ad03a3080dca8a5ce87ddb1def4 · 10/30 교수 피드백 40875ad03a30809989fcf92b6a8b84d0 · 멀티에이전트 카드 40a75ad03a30808ab171d7141360918a.
