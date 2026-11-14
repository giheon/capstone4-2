> PrivFrame 2.0 설계·PRD v2(승인본 2026-11-05)의 일부. 전체 목차는 [README.md](./README.md).

## 11. 툴 셋업과 연동 (M3, 2026-11-06 ~ 11-11)

M3의 목표는 M4 구축에 쓸 도구가 실제로 연결되고, 설계의 가정(U4 · U6 · U11)이 맞는지 미리 확인하는 것이다. 연동 코드는 M4에서 그대로 쓰도록 `privframe.core`(ADR-0005의 core 층)에 두고, 확인 절차는 `privframe.harness`에 둔다.

### 11.1 결과 요약

| 항목 | 상태 | 근거 |
|---|---|---|
| U11 LangGraph 스트림 → 실험실 이벤트 | **확인** | 사례 그래프 모양의 그래프로 정규화 이벤트 17종 생성 (§11.3) |
| 카탈로그 실재 확인 | **확인** | OpenRouter 후보 26개의 ID · 기능 · 단가가 공개 목록과 일치, Hugging Face 저장소 9개 존재 |
| 카탈로그 · 프로필 Python 로더 | **확인** | `config_hash`가 실험실 화면과 4개 프로필 모두 같음 |
| U6 CPU 지각 처리 속도 | **확인 (부분)** | CPU만으로 1분 영상당 약 62 s(분석 12 fps · 프록시 640p). 목표 180 s의 1/3. 마스크 분할 · 추적 · 인물 묶음은 아직 포함하지 않음 (§11.7) |
| 법률 코퍼스 도구 (수집 · 저장 · 분할 · 색인 · 검색) | **확인** | 실제 자료 12건으로 처음부터 끝까지 동작. 단위 503개, 청크 440개, 질의 3개 모두 기대 문서가 상위 5 안. LLM이 검색 도구를 호출해 답하고 LangSmith에 기록됨 (§11.5 · §11.9) |
| U4 OpenRouter 영상 입력 | **확인** | Qwen3.6-35B-A3B에 합성 클립(mp4 base64)을 3번 보내 3번 모두 명찰 · 번호판 글자를 정확히 읽음. 5.6~9.8 s, $0.0003~0.0007/회 (§11.8) |
| OpenRouter 구조화 출력 · 도구 호출 | **확인** | 계획 모델 GPT-5.4의 엄격한 JSON 스키마, 분석 모델 Claude Sonnet 5.5의 도구 호출 |
| LangSmith 추적 | **확인** | `privframe-dev` 프로젝트에 프로필 · config_hash 메타데이터를 단 추적을 쓰고 API로 다시 읽음 |
| 국가법령정보 API | **확인** (2026-11-11) | `LAW_OC` 등록 뒤 대상 6종(법령 · 고시 · 판례 · 헌재 · 해석례 · PIPC 결정문)의 검색과 본문 수집, 실제 응답으로 파서 확정 |
| 스킬 설치 | 사용자 실행 필요 | 자동 모드 분류기가 외부 코드 설치를 막음 (§11.6) |

### 11.2 환경과 의존성

- Python 3.12 · uv. 새로 추가: `langchain` 1.4.9 (`create_agent`), `langsmith` 0.14.17, `langchain-openrouter` 0.2.9, `av`(PyAV) 19.0.2, `kiwipiepy` 0.24.1, `bm25s` 0.3.16. LangGraph는 잠금 파일의 1.2.15를 유지한다.
- 로컬 모델용 의존성은 `models` 그룹으로 분리했다(`uv sync --group models`): `sentence-transformers` 6.2.0(torch 2.15, MPS 사용 가능), `rapidocr` 3.10.0, `rfdetr` 1.11.2, `open-image-models` 0.6.0, `trackers` 2.6.1. 하루 이틀 전에 나온 버전(trackers 2.7.0 등)은 범위에서 뺐다.
- **OpenCV 충돌**: `trackers`가 GUI판 `opencv-python`을 요구해 기존 `opencv-python-headless`와 같은 `cv2` 폴더를 덮어썼다. `[tool.uv] override-dependencies`로 GUI판을 빼고 headless 4.14만 남겼다(같은 cv2 API).
- **PyAV와 OpenCV를 한 프로세스에 올리면** macOS에서 두 패키지가 각자 넣은 libavdevice의 AVFoundation 클래스가 중복된다는 경고가 나온다. 카메라 캡처용 클래스라 우리 경로에서는 쓰이지 않지만, M4는 한 프로세스의 작업 스레드에서 실행한다. 프로세스 분리는 서비스 분리(M6) 때 한다.
- 기존 백엔드 테스트는 의존성 변경 전후 모두 128개 통과 · 1개 실패(실제 색인이 필요한 `test_product_requires_real_ready_index_and_pins_settings`, 변경 전부터 실패)로 같다.

### 11.3 연동 코드 (`backend/src/privframe/core`)

| 모듈 | 내용 |
|---|---|
| `settings.py` | `.env`의 키(`OPENROUTER_API_KEY` · `LANGSMITH_API_KEY` · `LAW_OC` · 선택 `GEMINI_API_KEY`)와 경로. 키 값은 출력하지 않고 있는지만 보고한다 |
| `pipeline.py` | `configs/pipeline`의 카탈로그 · 프로필 · 서비스 포인터를 읽고 부품을 해석한다. `config_hash`는 실험실(`frontend/mock/pipeline.ts`)과 같은 알고리즘이며, JavaScript의 숫자 표기(1.0 → 1)까지 맞췄다 |
| `llm.py` | 프로필의 API 부품을 `ChatOpenRouter`로 만든다. 공급자 선택은 요청 파라미터를 모두 지원하는 곳(`require_parameters`)과 학습에 쓰지 않는 곳(`data_collection: deny`)으로 제한하고, `PRIVFRAME_OPENROUTER_ZDR=1`이면 데이터 무보존 공급자만 쓴다 |
| `lawgokr.py` | 국가법령정보 Open API 클라이언트. 미등록 호스트의 '사용자 정보 검증 실패' 응답(HTTP 200)을 인증 오류로 바꾸고, 저장하는 출처 주소에는 OC를 넣지 않는다 |
| `events.py` | LangGraph 스트림(tasks · checkpoints · updates · custom, `subgraphs=True`)을 실험실 이벤트로 바꾼다. 노드 ID는 서브그래프 경로 + 노드 이름이고, create_agent의 도구 호출 태스크는 `<에이전트>/<도구 이름>`이 되어 토폴로지의 노드와 맞는다. interrupt는 바깥 그래프에도 다시 나타나므로 가장 안쪽 노드에서 한 번만 기록하고, 재개 때 다시 시작되는 같은 태스크는 이어서 진행으로 본다. 에이전트 밖 단계의 모델 사용량은 custom `llm` 이벤트로 보고한다 |
| `corpus.py` | 코퍼스 저장소. 원문 파일(`raw/`), 문서 등록부(`registry.jsonl`: 종류 · 인용 표기 · 권위 등급 · 출처 주소 · 받은 시각 · sha256), 표준 문서(`docs/`: 단위 목록). 같은 내용은 다시 저장하지 않는다 |
| `sources.py` | 수집기 등록표: `http_file`(직접 주소의 PDF 등), `law_html`(law.go.kr 조문 HTML, 키 불필요), `lawgokr`(API), `local_file`(직접 받아 둔 파일). 새 출처는 함수 하나를 등록한다 |
| `parsers.py` | 파서 등록표: `law_html` · `lawgokr_json`(조 단위, 그 밖은 본문 항목 단위) · `pdf`(쪽 단위) · `text`. 원문을 고쳐 쓰지 않고 문서의 구조만 되살린다 |
| `retrieval.py` | 프로필의 `rag.*` 부품으로 색인과 검색을 한다. 청킹(단위 · 고정 길이), 임베딩(sentence-transformers), Chroma, BM25(Kiwi · 공백), 하이브리드 RRF · dense · BM25, CrossEncoder 리랭커. `search`와 `get_document`는 M4의 도구가 그대로 쓴다 |

U11 확인에 쓴 그래프: `ingest` → `perceive`(서브그래프: 검출 · 장면 설명) → `legal_analyst`(create_agent: `search_law`, `ask_user` 안에서 interrupt) → `plan`. 이 그래프를 가짜 모델로 실행하고 재개까지 진행해 이벤트 69개, 실험실 계약의 17종을 모두 얻었다(테스트 `backend/tests/test_core_events.py`).

### 11.4 확인 명령 (`backend/src/privframe/harness`)

```bash
uv sync --group models
uv run privframe-smoke                 # 전부 (키가 없는 항목은 건너뜀)
uv run privframe-smoke openrouter      # 키를 넣은 뒤 U4 · 구조화 출력 · 도구 호출
uv run privframe-smoke langsmith law   # 추적 기록 확인 · API 대상 6종 검색
uv run privframe-smoke corpus adapters # 실제 자료로 수집부터 LLM 도구 호출까지 · 구현된 검색 부품 표

uv run privframe-corpus fetch configs/corpus/smoke.yaml   # 목록의 문서 수집
uv run privframe-corpus parse                              # 단위 분할
uv run privframe-corpus index [--profile baseline-v2]      # 프로필의 rag.* 부품으로 색인
uv run privframe-corpus search "촬영 동의와 공표" [-k 5] [--kind prec] [--mode bm25] [--no-rerank]
uv run privframe-corpus show pipa 제25조의2                 # 원문 열람
uv run privframe-corpus status
uv run privframe-corpus select configs/corpus/selection.yaml --version v1   # 선정 규칙으로 후보 탐색 → 원문 확보 → 채점 → 수집 목록 (M4-1, 17)
```

| 대상 | 키 | 확인 내용 |
|---|---|---|
| `env` | — | 버전, 키가 있는지(값은 표시 안 함), PyAV의 FFmpeg, torch 장치 |
| `catalog` | — | 부품 · 후보 · 프로필, config_hash |
| `models-public` | — | 카탈로그의 OpenRouter slug · 기능 · 단가와 Hugging Face 저장소를 공개 목록과 대조 |
| `stream` | — | U11 |
| `adapters` | — | 카탈로그의 `rag.*` 후보 가운데 백엔드가 지금 실행할 수 있는 것 |
| `perception` | — | U6, 팀 영상(이 컴퓨터 밖으로 보내지 않음) |
| `openrouter` | OPENROUTER_API_KEY | 계획 모델의 엄격한 JSON 출력, 분석 모델의 도구 호출, 장면 VLM의 영상 입력(U4). 영상은 사람이 없는 합성 클립만 보낸다 |
| `langsmith` | LANGSMITH_API_KEY | 프로필 · config_hash 메타데이터를 단 추적을 쓰고 다시 읽는다 |
| `law` | LAW_OC | API 대상 6종을 한 번씩 검색 |
| `corpus` | LAW_OC · OPENROUTER · LANGSMITH (없는 부분은 건너뛰고 표시) | 실제 자료로 수집 → 저장 → 분할 → 색인 → 검색 → 열람 → LLM 도구 호출 → 추적 (§11.9) |

결과는 `data/smoke/<시각>/results.json`에 남는다(data/는 git에 올리지 않음).

### 11.5 법률 코퍼스 도구 (범용, 목록과 분리)

M3에서는 코퍼스를 확정하지 않는다. 어떤 목록이 오더라도 쓸 수 있는 도구를 만들고 실제 자료 소량으로 확인했다. 최종 선정 기준과 규모는 M4-1에서 정한다([12-corpus-plan-draft.md](./12-corpus-plan-draft.md)).

| 단계 | 하는 일 | 확장 |
|---|---|---|
| 수집 | 목록 파일의 항목마다 수집기가 원문을 받는다. 실패한 항목이 있어도 나머지는 계속한다 | `sources.FETCHERS`에 함수 등록 |
| 저장 | 원문을 그대로 두고 등록부에 출처와 sha256을 적는다. 내용이 같으면 건너뛰고, 설명(인용 표기 · 권위 등급)만 바뀌면 다시 받지 않는다 | 메타데이터는 `kind` · `authority` · `dates` · `extra`의 열린 구조 |
| 분할 | 형식별 파서가 조 · 쪽 · 본문 항목 단위로 나눈다 | `parsers.PARSERS`에 함수 등록 |
| 색인 | 프로필의 청킹 · 임베딩 · 토크나이저 · 저장소 부품으로 만든다. 문서 해시와 설정의 지문이 같으면 다시 만들지 않는다 | `retrieval`의 등록표에 구현 추가 |
| 검색 | 검색 방식 · 리랭커 · k는 질의 때 고른다(색인을 다시 만들지 않음). 종류와 권위 등급으로 거를 수 있다 | 같음 |

- 목록 파일: `configs/corpus/smoke.yaml`(M3 확인용 12건), `configs/corpus/sources.yaml`(M4-1 초안 목록, 미확정, 아직 수집하지 않음).
- 항목에 `index: false`를 적으면 원문만 보관하고 색인하지 않는다. 같은 문서를 두 경로로 받은 경우에 한쪽만 색인하는 데 썼다.
- 저장 위치는 `data/corpus/`(git 제외). 직접 받은 파일은 `data/corpus/inbox/`에 두고 `local_file` 항목으로 등록한다.
- 백엔드에 아직 없는 카탈로그 후보: 문맥 문장 청킹, OpenAI 임베딩, Qdrant. 고르면 색인 때 "구현되지 않았습니다" 오류를 낸다(M4에서 채우지 않았다. 고르면 프로필 검사에 오류로 표시된다).
- 팀 시나리오와 정답은 코퍼스에 넣지 않는다(ADR-0006).

### 11.6 사용자가 할 일

1. 키 3종(OpenRouter · LangSmith · `LAW_OC`)은 모두 들어가 확인이 끝났다.
2. 스킬(선택): `npx skills add langchain-ai/langsmith-skills@langsmith-evaluator -g -y`, 같은 방식으로 `langsmith-trace`, `langsmith-dataset`, `langchain-ai/langchain-skills@langgraph-cli`, `openrouterteam/skills@openrouter-models`, `openrouterteam/skills@openrouter-video`.
3. M4-1에서 안내서를 더 넣으려면 PDF를 직접 받아 `data/corpus/inbox/`에 둔다(파일 이름과 받을 곳은 `configs/corpus/sources.yaml`). 개인정보 포털의 내려받기 주소는 시간이 지나면 만료되고, PIPC 게시판은 자동 요청을 막는다.

### 11.7 측정 세부 (2026-11-06, Apple Silicon Mac, CPU 실행)

**지각 (U6)** — 팀 영상 `A_B_A.mp4`(720p · 30 fps · 30 s, 이 컴퓨터 밖으로 보내지 않음)에서 20 s 분량을 서비스 프로필 설정(분석 12 fps · 프록시 짧은 변 640)으로 처리하고 1분 영상 기준으로 환산했다. ONNX Runtime은 macOS에서 CoreML을 먼저 고르므로 CPU 실행 경로로 고정했다.

| 단계 | 1분 영상당 | 비고 |
|---|---|---|
| 디코드(PyAV) · 축소 | 4.2 s | |
| 얼굴 검출 YuNet | 7.8 s | |
| 사람 · 물체 검출 RF-DETR Nano (ONNX) | 36.9 s | open-image-models의 ONNX판. PyTorch판(rfdetr)은 4프레임마다 재서 12.2 s → 12 fps 환산 약 49 s |
| 번호판 검출 yolo-v9-t-384 | 7.9 s | |
| OCR RapidOCR (PP-OCRv6 det tiny + 한국어 PP-OCRv5 rec, 1 fps) | 5.1 s | 설계 §7.4대로 키프레임 1 fps |
| **합계** | **61.9 s** | 목표 ≤ 180 s (VLM 제외) |

- 아직 넣지 않은 단계: 마스크 분할(RF-DETR-Seg), 추적(BoT-SORT), 인물 묶음(SFace), 장면 경계. 분할이 검출과 비슷한 비용이라고 보면 약 100 s 안팎으로 예상되며, M4에서 같은 명령으로 다시 잰다.
- **ONNX판 RF-DETR이 PyTorch판보다 빠르고 torch 없이 돈다.** 카탈로그에 `rfdetr-nano-onnx` 후보를 추가했다. 기본값을 바꿀지는 M4 지각 구현 때 정한다.

**법률 검색** — 2026-11-06에는 손으로 쓴 근거 문장 8개로 순위만 확인했다. 그 예문에는 2020다253423을 잘못 요약한 문장이 있어 폐기했고, 2026-11-11에 실제 자료로 다시 확인했다(§11.9). 당시 CPU 측정값은 임베딩 0.37 s/문장, 리랭크 0.2 s/쌍이다.

### 11.8 키로 확인한 연동과 찾은 함정 (2026-11-06)

| 확인 | 결과 |
|---|---|
| 계획 모델 `openai/gpt-5.4` · 엄격한 JSON 스키마 | 통과, 2.3 s |
| 분석 모델 `anthropic/claude-sonnet-5.5` · 도구 호출 | 통과(`search_law` 2회 호출), 3.1 s |
| 장면 VLM `qwen/qwen3.6-35b-a3b` · mp4 base64 영상 입력(U4) | 3/3 통과, 공급자 DeepInfra · Parasail · CoreWeave(요청마다 달라짐) |
| LangSmith 추적 쓰기 · 읽기 | 통과 |

연동하면서 찾은 함정. M4 구현에 그대로 반영한다.
- **`langchain-openrouter`의 `timeout`은 밀리초다.** 초 단위로 넘기면 모든 요청이 바로 시간 초과되고, SDK가 최대 300 s 동안 재시도하며 멈춘 것처럼 보인다. `chat_model(timeout_s=…)`가 밀리초로 바꿔 넘긴다(테스트 `test_core_llm.py`).
- **GPT-5.x · Claude 5.5는 OpenRouter에서 `temperature`를 받지 않는다.** `require_parameters`와 함께 보내면 고를 공급자가 없어 요청이 거절된다. 샘플링 파라미터는 지원하는 모델에만 보낸다.
- **추론형 VLM은 출력 한도가 작으면 추론에 다 쓰고 빈 답을 낸다.** 장면 설명 요청은 추론 강도 low와 넉넉한 출력 한도를 쓴다.
- **OpenRouter는 같은 모델도 요청마다 다른 공급자로 보낸다.** 실험 재현성을 위해 M4 · M5에서는 프로필에 공급자 순서(`provider.order`)를 고정하는 설정을 검토한다.
- **LangSmith는 `LANGSMITH_*` 환경 변수를 처음 읽을 때 캐시한다.** 실행 중에 환경 변수를 켜도 추적이 안 나가므로 클라이언트와 `tracing_context`를 명시한다. `list_runs`는 2027-01-31 이후 제거 예정이라 `client.runs.query()`로 옮겨야 한다. 아직 옮기지 않았다(`harness/smoke.py`).
- **Homebrew FFmpeg 9.0.2에는 `drawtext` 필터가 없다.** 합성 클립은 Pillow로 그리고 PyAV로 인코딩한다.
- **에이전트의 구조화 출력은 공급자 방식(`ProviderStrategy`)으로 한다.** 기본인 도구 방식은 `tool_choice`를 강제하는데, OpenRouter의 Claude 경로는 `require_parameters`와 함께면 이를 받지 않는다.
- **개인정보 포털(privacy.go.kr)의 첨부 내려받기 주소는 만료된다.** 2026-11-06에는 받아졌지만 11-11에는 "유효시간이 만료" 페이지가 PDF 대신 왔다. 수집기는 PDF 머리말을 확인해 이런 응답을 거절한다.
- **카탈로그 단가는 변한다.** `qwen/qwen3.6-27b`가 11-06의 $0.45 / $2.70에서 11-11에 $0.32 / $3.20으로 바뀐 것을 `models-public` 확인이 잡았고, 카탈로그를 고쳤다.
- **국가법령정보 API의 실제 응답 모양**: 법령은 `법령.조문.조문단위`(조문여부가 `전문`이면 장 제목), 고시는 `조문내용` 문자열 목록, 판례 검색 결과의 일련번호는 `판례일련번호`이고 본문에서는 `판례정보일련번호`다. 본문 항목에는 `<br/>`가 섞여 온다.

### 11.9 실제 자료 종단 확인 (2026-11-11, `privframe-smoke corpus`)

| 단계 | 결과 |
|---|---|
| 수집 | 12건: API 6건(개인정보 보호법, 표준 개인정보 보호지침, 대법원 2021다219116, 헌재 2015헌마994, 법령해석례 1, PIPC 결정문 1), 대법원 판결문 PDF 3건, 법령 HTML 1건, 직접 받아 둔 안내서 PDF 2건. 다시 실행하면 12건 모두 "바뀌지 않음" |
| 분할 | 단위 503개. 개인정보 보호법은 API와 HTML 두 경로 모두 127개 조, 표준지침 64개 조, 안내서 48쪽과 103쪽 |
| 색인 | 서비스 프로필(baseline-v2)의 부품으로 10건을 색인(중복 경로 2건 제외). 청크 440개, 1024차원, Apple MPS에서 모델 적재 11 s와 임베딩 44 s. 같은 설정이면 다시 만들지 않고 바로 연다 |
| 검색 | 3/3 통과. "촬영 동의와 공표 동의"에 2021다219116 판결요지가 1위, "폭행 장면 촬영 증거"에 2020다227455가 1위, "이동형 영상정보처리기기 촬영 사실 표시"에 표준지침 제39조의2 · 이동형 안내서 · 법 제25조의2가 상위 5 안. 첫 질의는 모델 적재로 17 s, 이후 약 1.7 s |
| 부품 교체 | 프로필에서 검색 방식을 BM25만으로, 리랭커를 없음으로 바꿔도 같은 색인으로 동작 |
| 열람 | `get_document("개인정보 보호법", "제25조의2")`가 조문 원문 775자를 돌려줌 |
| LLM | 분석 모델(Claude Sonnet 5.5, OpenRouter)이 `search_law`와 `get_document`를 합쳐 3~4회 호출해 답함. 여섯 번 실행 모두 2021다219116을 인용했고 인용한 문서는 검색 결과 안에 있음. 16~24 s, 입력 2만~7만 토큰(턴마다 대화 기록을 다시 보내 합산한 값), 카탈로그 단가로 $0.05~0.15 |
| 추적 | LangSmith `privframe-dev`에 프로필 · config_hash · 색인 지문과 함께 기록 |

- 이 12건은 도구 확인용이다. 검색 품질 수치가 아니며, 규모와 성능 최적화는 M4-1에서 한다.
- **도구 예산은 필수다.** 호출 상한 없이 돌린 한 번은 모델이 도구를 계속 불러 그래프 재귀 한도(16)에 걸렸다. 스모크는 도구 호출을 4회로 막고, 넘으면 "지금까지의 근거로 답하라"를 돌려준다. 설계의 예산과 종료 조건(§5.4)이 왜 필요한지 보여 주는 사례다.
- 입력 토큰이 큰 것은 에이전트가 턴마다 대화 기록(검색 결과와 열람 원문 포함)을 다시 보내기 때문이다. 스모크는 검색 결과를 300자, 열람 원문을 2,000자로 줄여 넣는다. M4에서 전달량과 프롬프트 캐시를 조절한다.
- 기존 판례 인용을 판결문으로 다시 읽은 결과와 정정 내용은 [12-corpus-plan-draft.md](./12-corpus-plan-draft.md) §A에 있다.
