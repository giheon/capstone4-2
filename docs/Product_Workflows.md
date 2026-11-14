# 제품 입력과 관찰 가능한 실행 구조

2026-10-16 구현 기준. [RAG Input 결정](https://app.notion.com/p/3f475ad03a3080e09ae4d69559d22854)의 ‘임기헌’ 아래 1~3절을 적용했다. 일반 화면은 영상 처리와 작업 내역만 제공하고, 자료 구성과 부품별 실험은 운영 CLI/API로 수행한다.

## 사용자 입력

업로드 시 이용 목적 11개, 공개·제공 범위 6개 중 각각 하나를 선택한다. `unknown`도 유효한 명시적 선택이다. 자유서술과 영상 전체 동의를 추가로 요구하지 않는다. 코드·문구·검색 용어는 `domain/inputs.py`에 버전과 함께 관리하며 `/api/app-config`가 화면에 선택지를 제공한다.

분석 후에는 기존 Person Group마다 관계 `self/other/unknown`을 받는다. 본인의 동의는 `not_applicable`이며 질문을 생략한다. 다른 사람·관계 미확인은 `granted/not_obtained/refused/unknown`을 구분한다. 동의는 자동 KEEP/BLUR 명령이 아니다. `subject_access`일 때 열람 요청 당사자 `yes/no/unknown`을 별도로 확인한다. 업로더와 열람 요청자는 다를 수 있고 여러 요청자를 지정할 수도 있다.

그룹 구성·목적·공개 범위가 바뀌면 관련 확인을 초기화한다. 과거 `third`는 `other`, 모호한 `denied`는 `unknown`으로 읽고 원래 코드를 `legacy_values`에 보존한다. 기존 자유서술 실행도 계속 조회할 수 있다. 처리 강도만 바꾸는 revise는 기존 판단을 재사용하지만 새 실행·체크포인트를 사용한다.

Query Builder는 목적·공개 범위의 문구와 검색 용어, 중복 제거한 관계·동의·열람 쟁점을 조립한다. 인물·트랙 ID와 bbox는 검색에 포함하지 않는다. LLM 입력에는 전체 구조화 사실과 인물↔트랙 연결, 영상 관측, 실제 전달 근거를 보낸다. 얼굴 픽셀·특징 벡터는 외부에 전송하지 않는다. HTML 보고서는 이용 정보·인물 확인·처리·인용 근거를 보여주고, JSON에는 설정·모델·전체 검색·사용자 수정·실행 참조를 보존한다.

## 운영 자료 준비

서버를 실행하고 `.env`에 실제 `OPENAI_API_KEY`를 설정한 뒤 아래 순서로 진행한다. 문서 관리 UI는 없다.

```bash
uv run privframe list documents
# 새 저장소에서 공식 자료가 없다면:
uv run privframe seed
uv run privframe list documents
# 사용할 READY 문서 ID들을 지정:
uv run privframe index DOC_ID_1 DOC_ID_2 DOC_ID_3 --profile baseline
uv run privframe wait indexes INDEX_ID
uv run privframe activate-index INDEX_ID --profile baseline
uv run privframe app-config
```

기존 공식 자료 3종은 이번 정리에서 보존했다. 별도 PDF/TXT는 `upload-document PATH`와 manifest로 추가한다. READY이면서 실사용 프로필과 호환되는 실사용 인덱스만 활성화할 수 있다. 최신 인덱스를 자동 선택하지 않는다. 생성된 작업은 당시 설정과 인덱스를 고정하며 인덱스 교체가 기존 작업을 바꾸지 않는다.

키·모델·영상 처리 도구·활성 인덱스·큐 서비스가 준비되지 않았으면 화면에 준비 안내를 표시하고 일반 작업 생성을 제한한다. fixture는 운영 프로필로 활성화할 수 없으며, 개발 CLI/API에서 명시했을 때만 사용한다. 이 구성은 현재 한 호스트·solo worker 기준이다.

## 그래프와 노드 기록

FastAPI → Celery/Redis → LangGraph → 기존 port/adapter 순서로 실행한다. 그래프는 다음 경계로 구성한다.

| 그래프 | 노드 |
|---|---|
| `extract` | `extract_document` |
| `index` | `chunk_documents` → `embed_index` |
| `analyze` | `analyze_video` → `group_identities` |
| `decide` | `build_query` → `retrieve` → `select_evidence` → `llm_decision` → `validate_output` |
| `render` | `render_video` → `report` |
| `retrieval` | 질의·검색·근거 선택만 독립 실행 |
| `decision` | 저장한 영상 관측·입력·근거로 LLM·검증만 독립 실행 |

`analyze_video` 안에서는 기존 영상 정규화·장면 경계·프레임 검출·트래킹을 유지한다. 검색기의 후보 탐색과 근거 예산 처리는 기존 retriever 계약을 유지하며 `select_evidence`에서 교체 가능한 reranker를 적용한다. 인물 입력과 처리 검토는 도메인의 사용자 대기 상태로 유지한다.

노드마다 상태·시도·입출력 fingerprint·결과 artifact·요약·오류 유형·재시도 가능 여부·시간·사용 가능한 모델 usage를 기록한다. 이는 후속 노드 상태 화면과 상황 브리핑의 입력이다. 상세 대시보드나 추가 LLM 브리핑 호출은 포함하지 않는다.

LangGraph의 `checkpoints.sqlite3`에는 결과 참조와 진행 위치를 저장하고, 큰 관측·근거는 `executions/`의 노드 결과에 저장한다. 인물 특징 파일은 계속 비공개이다. 일반 artifact API는 execution 저장소를 노출하지 않으며 개발용 실행 출력 API로 필요한 노드 JSON을 조회한다.

실행 ID는 재시도·재개 중 유지하고 Celery/단계 시도 ID와 구분한다. 완료 결과를 저장한 후 별도의 gate에서 interrupt한다. worker 재시작 후에도 저장된 결과를 재사용한다. 그래프 버전 불일치는 재개를 거부하며 저장된 입력으로 새 실행을 만든다. 재개는 실행 중 네트워크 호출의 원격 완료 여부까지 보장하는 기능은 아니다. 저장 전에 프로세스가 끊긴 외부 호출은 재호출될 수 있다. SDK 재시도 위에 무조건적인 그래프 자동 재시도는 추가하지 않았다.

```bash
uv run privframe list executions
uv run privframe execution EXECUTION_ID
uv run privframe execution EXECUTION_ID --node retrieve
uv run privframe pause EXECUTION_ID
uv run privframe resume EXECUTION_ID
uv run privframe graph decide --output /tmp/privframe-decide.mmd
uv run privframe run --video-id VIDEO_ID --profile fixture-yunet \
  --purpose social_publish --audience public --stop-after analyze_video
uv run privframe decide RUN_ID --stop-after retrieve
uv run privframe index DOC_ID --profile fixture --stop-after chunk_documents
```

pause는 현재 노드를 마친 경계에서 적용한다. 바로 종료시키거나 프레임 중간부터 재개하지 않는다. 실패한 실행은 출력과 오류를 조회한 뒤 같은 실행을 재개할 수 있다. 정상적인 근거 부족·추가 확인은 검토 상태이고, provider 실패는 실패 노드로 기록한다. 늦게 도착한 이전 시도가 최신 결과를 덮어쓰지 못하도록 기존 attempt 검사를 유지한다.

## 독립 실행·평가

`component --file INPUT.json`은 `POST /api/executions`에 다음 형태를 전달한다.

```json
{
  "graph": "retrieval",
  "profile": "fixture",
  "index_id": "READY_INDEX_ID",
  "scenario": {"use_purpose": "social_publish", "audience_scope": "public"},
  "stop_after": "retrieve"
}
```

LLM만 실행하려면 `graph: decision`으로 하고 저장된 snapshot의 `scenario`, `observation`, `evidence`를 전달한다. 프로필 설정도 실행 생성 시 고정한다. 실행 조회·출력·정지·재개는 `/api/executions/{id}` 아래의 GET, `outputs/{node}`, `pause`, `resume`로 제공한다.

`evaluate --target index|retrieval|vision|decision`은 각 부품을 별도로 평가한다. 공통 manifest는 `{"cases":[...]}`이며 target별 사례 입력은 다음과 같다.

- index: `id`, `documents: [{path, title?}]`. 문서 추출·색인만 실행한다.
- retrieval: `id`, `scenario`, `index_id`, 선택적 `expected_source_spans`. 기존 고정 인덱스에서만 검색한다.
- vision: `id`, `video`, 선택적 `scenario`, `manual_face_expectation`. 영상·그룹화만 수행하며, 정답 주석이 있으면 기존 검출·그룹화 지표를 계산한다.
- decision: `id`, `snapshot`. 저장된 전체 실행 JSON에서 관측·입력·근거를 읽어 판단 계약을 평가한다.

경로는 manifest 디렉터리를 기준으로 해석한다. 기존 전체 평가 형식은 `--target end-to-end`로 유지한다. 정답이 없는 지표는 실제 정확도로 간주하지 않는다.

## 데이터 정리와 재검증

기존 fixture 실행 9개·인덱스 3개·astronaut 중복 업로드 6개, Chroma/얼굴 특징 캐시, QA·Docker 검증 산출물, 로그와 중복 모델을 제거했다. 테스트 영상 A/B/A_B_A·astronaut·checkerboard 각 원본과 출처, 공식 자료 3종, 실제 모델을 보존했다. 초기 정리 전후 13개 보존 파일의 SHA-256이 일치했다. DB 레코드와 파일을 함께 정리했고 전체 Redis를 초기화하지 않았다.

```bash
uv run ruff check backend scripts
uv run pytest -q
npm --prefix frontend run build
npm --prefix frontend run test:e2e
uv run python scripts/test_live.py
```

live 검사는 별도 임시 data 디렉터리·Redis 포트·API 포트를 사용하며 성공 후 삭제한다. 실제 YuNet·ArcFace·FFmpeg와 API·worker·브라우저를 사용하고 텍스트 임베딩·판단은 명시적 fixture다. 일반 저장소에 테스트 이력을 남기지 않는다. 실패 시 진단 경로를 출력하고 임시 로그를 남긴다. 브라우저 캡처는 기본 `/tmp/privframe-playwright-results` 또는 지정한 `PRIVFRAME_TEST_OUTPUT`에 저장한다.
