# 캡스톤 4-2 · PrivFrame

영상과 이용 목적·공개 범위를 입력하면 인물별 처리 제안을 만들고, 검토한 결과를 영상과 보고서로 내보냅니다. 사용자 화면은 영상 처리와 작업 내역으로 구성하고, 내부 알고리즘 실험은 개발 CLI/API로 수행합니다. 알고리즘과 모델을 교체하며 비교할 수 있도록 모듈형 모놀리스와 Ports & Adapters로 구현했습니다.

- [팀 공유용 구현 계획](docs/PrivFrame_구현계획.md)
- [파이프라인 SVG](docs/pipeline.svg) · [PNG](docs/pipeline.png) · [편집용 Mermaid](docs/pipeline.mmd)
- [검증 결과와 제한](docs/검증결과.md)
- [제품 입력·운영 인덱스·LangGraph 사용법](docs/Product_Workflows.md)
- [인물 그룹화 구현 기록](docs/Identity_Grouping.md)

위 파이프라인 이미지는 최초 베이스라인 기록입니다. 현재 노드 구성은 실행 중인 그래프에서 `privframe graph GRAPH_NAME`으로 내보낼 수 있습니다.

## Mac에서 실행

Python 3.12는 uv가 관리합니다. 검증 환경은 Node 24/npm이며, 실행 전 Node와 npm을 설치해야 합니다. 기존 `.env`가 있으면 복사하지 말고 해당 파일의 서버 API 키를 설정하세요.

```bash
brew install uv ffmpeg redis
cp -n .env.example .env
# 처음 만든 .env의 OPENAI_API_KEY를 설정한 뒤 실행합니다.
# 활성 운영 인덱스는 아래 첫 실행 흐름에서 지정합니다.
./scripts/dev.sh
```

웹: **http://localhost:5173** · API: **http://127.0.0.1:8000/docs**

스크립트는 의존성·모델 checksum을 확인하고 Redis, API, worker, Vite를 실행합니다. Ctrl-C로 이 스크립트가 시작한 프로세스를 종료합니다. 이미 떠 있는 Redis는 재사용합니다. 8000/5173 포트를 사용 중인 개발 서버가 있으면 중복 실행을 거부합니다. 로그는 `data/logs/`에 있습니다. 파일과 실행 결과는 기본 `data/`, 얼굴 모델은 `models/`에 저장합니다.

개별 프로세스를 실행하려면:

```bash
uv sync --frozen
uv run python scripts/download_model.py
redis-server --bind 127.0.0.1 --port 6379
uv run celery -A privframe.worker:celery_app worker --pool=solo --concurrency=1 --loglevel=INFO
uv run uvicorn privframe.api:app --host 127.0.0.1 --port 8000
# 다른 터미널
cd frontend
npm ci
npm run dev
```

## 첫 실행 흐름

운영자가 CLI로 근거 자료를 준비합니다. 기존 공식 문서가 있으면 재사용합니다.

```bash
uv run privframe list documents
# 문서가 없는 새 환경에서만: uv run privframe seed
uv run privframe index DOC_ID_1 DOC_ID_2 DOC_ID_3 --profile baseline
uv run privframe wait indexes INDEX_ID
uv run privframe activate-index INDEX_ID --profile baseline
uv run privframe app-config
```

1. **영상 입력:** 영상을 선택하고 이용 목적·공개 범위 두 질문에 답합니다. 모르면 모름을 선택할 수 있습니다.
2. **인물 확인:** 얼굴 그룹과 등장 구간을 확인합니다. 본인은 동의 해당 없음, 다른 사람은 동의·미취득·명시적 거부·미확인을 구분합니다. 열람 요청 목적이면 요청 당사자도 따로 확인합니다. 그룹 병합·분리와 트랙 구간 분할을 사용할 수 있습니다.
3. **처리 검토:** 인물 정보를 저장한 뒤 판단을 요청하고, 처리 제안과 인용 근거를 확인합니다. 인물 전체 또는 개별 등장 구간의 처리를 수정할 수 있습니다.
4. **출력:** 영상을 만들고 다운로드합니다. 처리만 수정해 다시 출력하면 이전 분석·근거·판단을 재사용하고 기존 결과를 보존합니다.

화면에서 문서·색인·실험 프로필을 고르지 않습니다. 키·모델·활성 근거 인덱스 등이 미준비이면 준비 안내를 표시합니다. fixture는 개발 CLI/API에서 명시했을 때만 사용하며 실제 모델 실패 시 자동 대체하지 않습니다. API 키 없이 전체 연결을 검증하려면 `uv run python scripts/test_live.py`를 사용하세요. 이 검사는 임시 저장소에서 실제 얼굴 분석·렌더와 fixture 판단을 수행하고 성공 후 정리합니다.

## Linux에서 실행

```bash
cp -n .env.example .env
docker compose up --build
```

http://localhost:8000에서 빌드된 React UI와 API를 제공합니다. API·worker는 같은 Dockerfile/코드베이스로 빌드하며 Redis가 broker 역할을 합니다. 결과는 호스트 `./data`에 유지됩니다. 다른 호스트에서 접속할 때는 SSH 터널로 8000 포트를 연결할 수 있습니다. 초기 구성은 CPU 실행입니다.

`docker compose down`은 컨테이너를 종료하고 `data/`의 실행 기록을 유지합니다. Mac 네이티브 실행과 컨테이너 실행을 **동일 data 디렉터리에서 동시에** 실행하지 마세요. worker lock은 중복 프로세스를 거부합니다. API와 worker는 동일 호스트의 로컬 디스크를 공유해야 합니다.

## 구성과 교체 방법

| 프로필 | 용도 |
|---|---|
| `baseline` | YuNet + ArcFace 인물 그룹화 + 1200/150 청킹 + OpenAI 문서 임베딩/직접 판단 |
| `haar` | Haar 얼굴 검출 비교; 랜드마크가 없어 인물 그룹화는 끔 |
| `small-chunks` | 청킹만 800/100으로 변경 |
| `fixture` | 명시적인 오프라인 통합 테스트; 실제 법률 판단 아님 |
| `fixture-yunet` | 테스트용 문서 임베딩·판단 + 실제 YuNet·ArcFace; API 키 없이 인물 검토·출력 검증 |

`configs/*.yaml`의 변경은 새 실행에만 적용됩니다. `PipelineConfig`가 지원되는 선택과 범위를 검증합니다. 지원하지 않는 설정은 조용히 무시하지 않습니다. 새 알고리즘은 domain port를 구현하고 adapters factory와 config 선택에 추가한 뒤 bootstrap에서 연결합니다.

코드 책임은 다음처럼 나뉩니다.

- `backend/src/privframe/domain`: 공통 데이터, port, 누락·근거 연결·렌더 조건 검증.
- `application`: 명령과 단계별 파이프라인, LangGraph 실행·체크포인트 조정.
- `adapters`: LangChain/Chroma/OpenAI, OpenCV/FFmpeg, 스냅샷 보고서.
- `infrastructure`: SQLAlchemy 저장소, 아티팩트 저장. `bootstrap.py`가 구체 구현을 조립합니다.
- `api.py`와 `worker.py`: HTTP와 Celery 진입점. CLI·평가는 HTTP API를 사용하며 Chroma를 직접 열지 않습니다.

raw 영상·썸네일·얼굴 임베딩은 외부 LLM에 전송하지 않습니다. 외부 API에는 문서 텍스트의 임베딩과 상황·인물 관계/동의·트랙 연결·근거 텍스트가 전달됩니다. 얼굴 특징 파일은 공개 아티팩트 API로 내려받을 수 없습니다. 키는 서버 환경에만 두고 실행 기록·프런트엔드에 포함하지 않습니다.

## 선택지와 검색 질의

사용자에게는 일반적인 표현을 보여주고, 검색에는 **선택 문구 + 법률 검색 용어 + 인물별 쟁점 요약**을 전달합니다. 예를 들어 `subject_access`는 ‘본인 영상 열람 요청에 대한 제공’과 ‘정보주체 개인영상정보 열람 요구 제3자 보호’를 함께 사용합니다. `public`은 ‘불특정 다수에게 공개’와 ‘영상 공개 제3자 개인정보 보호’를 함께 사용합니다.

매핑과 버전은 `backend/src/privframe/domain/inputs.py`에 있습니다. 인물 관계·동의·열람 요청자 정보에서 필요한 검색 쟁점을 중복 없이 추가하며 인물 ID·트랙 ID·bbox는 검색 문자열에 넣지 않습니다. 전체 구조화 사실과 인물↔트랙 연결은 별도로 LLM 판단에 전달합니다.

이 매핑은 검색어를 구성하는 베이스라인입니다. 특정 법 조항을 확정하거나 동의 상태만으로 KEEP/BLUR를 결정하는 규칙 엔진은 아직 구현하지 않았습니다. 매핑 효과를 실제 OpenAI 임베딩으로 비교하는 성능 실험도 후속 과제입니다.

## LangGraph 노드와 제어

| 그래프 | 작업 노드 |
|---|---|
| 문서 추출 `extract` | `extract_document` |
| 오프라인 색인 `index` | `chunk_documents` → `embed_index` |
| 영상 분석 `analyze` | `analyze_video` → `group_identities` |
| 검색·판단 `decide` | `build_query` → `retrieve` → `select_evidence` → `llm_decision` → `validate_output` |
| 출력 `render` | `render_video` → `report` |
| 검색 단독 `retrieval` | `build_query` → `retrieve` → `select_evidence` |
| 판단 단독 `decision` | `llm_decision` → `validate_output` |

각 작업 노드 뒤에는 `after_<노드명>` 정지 gate가 있습니다. 노드의 결과를 저장한 다음 gate에서 멈추므로 재개할 때 저장 완료한 계산을 재사용합니다. 정규화·장면 경계·검출·트래킹의 프레임 처리는 현재 `analyze_video` 내부에서 함께 실행합니다.

Celery worker가 실제 작업을 실행하고 LangGraph가 다음 노드를 관리합니다. SQLite 체크포인트는 실행 위치와 결과 참조를 저장합니다. 노드별 상태·오류 유형/내용·재시도 가능 여부·처리 시간·요약·입출력 참조·가능한 모델 사용량은 개발 CLI/API로 확인할 수 있습니다. 상세 상태 대시보드와 자동 브리핑 생성은 후속 기능입니다.

```bash
# 인물 정보 저장을 마친 작업에서 검색 노드까지만 실행:
uv run privframe decide RUN_ID --stop-after retrieve
# owner_id가 RUN_ID이고 graph가 decide인 실행 ID 확인:
uv run privframe list executions
uv run privframe execution EXECUTION_ID
uv run privframe execution EXECUTION_ID --node retrieve
uv run privframe resume EXECUTION_ID

# 실행 중 정지 요청은 현재 노드가 끝난 뒤 반영:
uv run privframe pause EXECUTION_ID
# 실제 그래프 정의 내보내기:
uv run privframe graph decide --output /tmp/privframe-decide.mmd
```

`RUN_ID`는 사용자 작업, `EXECUTION_ID`는 해당 작업의 그래프 실행을 가리킵니다. resume는 정지·실패한 실행에서 사용합니다. 프로세스 재시작 후에도 같은 저장소와 호환되는 그래프 버전으로 재개할 수 있습니다. 원격 API 호출 후 결과 저장 전에 프로세스가 끊겼다면 그 호출은 재실행될 수 있습니다. 그래프의 무조건적인 자동 재시도 정책은 추가하지 않았습니다.

## 실행 기록과 재현성

문서 원문·페이지/조문·단위 내 문자 offset·청크 hash, 인덱스 설정, 모델 ID·checksum, 코드 fingerprint·패키지 버전·lock hash를 기록합니다. 조회된 후보와 실제 전달된 원문, 판단의 raw 응답·입력 prompt·usage, 사용자 수정과 실제 렌더 파라미터를 구분해 저장합니다.

각 단계의 시도별 snapshot과 산출물 경로는 다릅니다. 재전달된 완료 작업은 다시 실행하지 않습니다. 실패·중단 작업을 재시도하면 기존 시도는 유지하고 새 시도를 추가합니다. 진행 중인 시도를 재시도하면 이전 시도를 SUPERSEDED로 표시해 늦게 도착한 결과가 현재 결과를 덮어쓰지 않도록 합니다.

LLM 재호출의 완전한 결정성을 약속하지 않습니다. **당시 실제 입력과 출력의 보존**, 버전이 고정된 로컬 알고리즘, 같은 자료를 사용한 비교가 재현성 기준입니다. 완료된 실행의 입력은 수정할 수 없으며 새 실행으로 분기합니다. 보고서 재생성은 API 호출이나 재검색을 하지 않습니다.

## CLI와 평가

```bash
uv run privframe health
uv run privframe seed
uv run privframe list documents
uv run privframe upload-document ./my-law.txt
uv run privframe index DOCUMENT_ID --profile fixture
uv run privframe upload-video ./clip.mp4
uv run privframe run --video-id VIDEO_ID --index-id INDEX_ID --purpose social_publish --audience public --profile fixture
uv run privframe wait runs RUN_ID
uv run privframe decide RUN_ID
uv run privframe wait runs RUN_ID
uv run privframe render RUN_ID
uv run privframe wait runs RUN_ID
```

검토 필요 상태에서는 `override RUN_ID --file override.json`으로 전체 트랙을 지정합니다. JSON 형태는 `{"treatments":{"track-000001":{"action":"BLUR","strength":2}},"no_faces_confirmed":false}`이며 실제 분석된 트랙 ID를 사용하세요. 얼굴 0개는 `{"treatments":{},"no_faces_confirmed":true}`입니다.

fixture 평가 자료와 실행 명령은 [evaluation/README.md](evaluation/README.md)에 있습니다. 평가 자료는 합성 자료이며 법적 정확도 점수로 해석하지 않습니다. 원문 span을 기준으로 검색 coverage를 계산해 청크 크기를 바꿔도 비교할 수 있습니다. 트랙 수·계약 유효성·검토 사유·시간·사용량을 함께 기록합니다.

## 검사와 API 타입 생성

```bash
uv run ruff check backend scripts
uv run pytest -q
uv run privframe export-openapi --output frontend/openapi.json
cd frontend
npm run generate:api
npm run typecheck
npm run build
npx playwright install chromium
npm run test:e2e
```

Python 의존성은 `uv.lock`, 프런트엔드는 `package-lock.json`에 고정되어 있습니다. 외부 OpenAI 통신은 MockTransport 요청 직렬화 검증과 구분해서 보고합니다. 공개 seed는 지정 manifest의 세 출처만 가져오며 전체 법령 크롤링과 자동 갱신을 수행하지 않습니다.

얼굴 분석은 PySceneDetect 장면 경계 + IoU 트래킹 + ArcFace 특징 + 동시 등장 제약을 적용한 그룹화입니다. 같은 영상 안의 재등장 연결을 제안하며 사용자가 확인합니다. 검출되지 않은 프레임의 얼굴 영역은 보간하지 않고, 누락 구간은 normalization 기록에 남습니다. 번호판·문서·OCR·CUDA·규칙 기반 법적 판단은 후속 확장 대상이며 객체 종류별 처리 경계는 마련했습니다.

기본 실행 스크립트는 YuNet과 ArcFace 두 모델을 내려받고 checksum을 검사합니다. ArcFace 가중치는 비상업 연구용입니다. 인물 그룹화의 설정·API·실제 A_B_A 검증과 제한은 [사용법 문서](docs/Identity_Grouping.md)를 참고하세요.

## GitHub 변경 기록

팀의 [Notion 개발 가이드](https://app.notion.com/p/3e475ad03a308156a8fed250398b93fd)를 따릅니다.

- GitHub Issue에 구현 목적·범위·완료 조건을 기록하고 Notion 작업 카드에서 연결합니다.
- `main`에 직접 push하지 않습니다. `feat/<area>-<short-name>`, `fix/<area>-<short-name>`, `docs/<short-name>` 같은 작업 브랜치를 사용합니다.
- 커밋은 `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:` 접두어를 사용합니다.
- PR에는 관련 Issue, 변경된 동작, 로컬 실행·검증 결과, 제한을 기록합니다. 최소 1명 리뷰 후 merge하고 작업 브랜치를 삭제합니다.
- 현재 구현은 저장소 문서, 기술 선택 이유는 Notion 의사결정 기록, 코드 변경과 검증 근거는 PR에서 관리합니다. 아직 구현하지 않은 규칙 엔진·OCR·사물 검출·재식별 위험 검증을 완료 기능으로 기록하지 않습니다.

`.env`, `data/`, `models/`, 의존성·빌드·테스트 출력은 Git에서 제외합니다. 공식 문서는 manifest/seed로, 얼굴 모델은 checksum을 고정한 다운로드 스크립트로 준비합니다. `A_B_A.mp4` 등 별도 테스트 원본은 팀이 승인한 공유 경로에서 준비해야 하며 저장소에는 자동 포함되지 않습니다. 원본이 없으면 해당 실제 영상 회귀 검사는 skip입니다. 공개 데모 기준 버전은 리뷰와 실행 검증 후 GitHub Release로 고정합니다.
