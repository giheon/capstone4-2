# 캡스톤 4-2 · PrivFrame

영상을 올리면 사람 · 글자 · 번호판 · 위치 정보를 찾고, 개인정보 보호법과 판례를 근거로 무엇을 가릴지 정해 사용자의 검토를 거친 뒤 가린 영상과 보고서를 만드는 시스템입니다. LangGraph로 짠 멀티 에이전트 파이프라인과, 그 파이프라인의 부품을 바꿔 가며 비교하는 실험실로 이루어져 있습니다.

- 지금 동작하는 범위와 측정 결과: [M4 구축 기록](docs/design/14-m4-build.md) · [M4 보완](docs/design/15-m4-improvements.md) · [실험실 보완](docs/design/16-lab-runs-and-experiment-sets.md)
- 설계 전체와 변경 기록: [docs/design](docs/design/README.md) · 결정 기록 [docs/adr](docs/adr) · 용어 [GLOSSARY.md](GLOSSARY.md) · 기술 조사 [docs/research](docs/research/README.md)
- 법률 코퍼스: 팀 기준으로 선정 · 수집한 목록 v1이 있습니다([수집 기록](docs/design/17-m4-1-corpus-collection.md)). 무엇을 색인할지는 아직 정하지 않아, 서비스는 소량 검증 목록(`configs/corpus/smoke.yaml`)의 색인으로 동작합니다.

## 실행

준비물: [uv](https://docs.astral.sh/uv/)(Python 3.12를 관리), FFmpeg, Node 24.

```bash
brew install uv ffmpeg
cp -n .env.example .env                            # OPENROUTER_API_KEY를 채운다
uv sync --group models                             # 로컬 모델 의존성
uv run privframe-corpus fetch configs/corpus/smoke.yaml && uv run privframe-corpus parse   # 법령 · 판례 · 안내서 원문
uv run privframe-case make-sample                  # 합성 예시 영상 → data/samples/sample.mp4
uv run uvicorn privframe.service.app:app           # 백엔드 http://127.0.0.1:8000
cd frontend && npm install && PRIVFRAME_API=proxy npm run dev   # 화면 http://localhost:5180
```

- 서비스 화면은 `/`, 실험실은 `/lab`입니다.
- 처음 실행할 때 로컬 모델(얼굴 · 사물 검출, OCR, 임베딩 · 리랭커)을 내려받고 코퍼스 색인을 만듭니다.
- `frontend`에서 `npm run build`를 해 두면 백엔드가 `frontend/dist`를 함께 제공하므로 8000 포트 하나로 볼 수 있습니다.

키 없이 볼 때:

```bash
cd frontend && npm run dev                                     # 모의 백엔드로 화면만
PRIVFRAME_FIXTURE=1 uv run uvicorn privframe.service.app:app   # 실제 그래프를 고정 응답으로 (키 · 네트워크 · 모델 불필요)
```

Anthropic API로 돌릴 때: `.env`에 `ANTHROPIC_API_KEY`를 넣고 `PRIVFRAME_SERVICE_PROFILE=claude-api uv run uvicorn privframe.service.app:app`.

## 명령줄 도구

```bash
uv run privframe-case run <영상> --memo "SNS에 올릴 카페 홍보 영상"   # 화면 없이 한 건 처리 (질문은 터미널에서 답함)
uv run privframe-case run <영상> --profile claude-api --auto        # 질문에 보수적으로 자동 응답
uv run privframe-corpus search "촬영 동의와 공표" -k 5              # 코퍼스 검색 (status · show · index도 있음)
uv run privframe-corpus select configs/corpus/selection.yaml      # 법률 코퍼스 선정: 후보 탐색 → 원문 확보 → 채점 → 수집 목록 (모델 비용이 든다)
uv run privframe-smoke                                            # 도구 연결 점검 (키가 없는 대상은 건너뜀)
```

## 구조

| 위치 | 내용 |
|---|---|
| `backend/src/privframe/core` | 파이프라인: 케이스 그래프, 에이전트, 영상 정보 추출, 렌더 · 출력 검증, 코퍼스 · 검색 |
| `backend/src/privframe/service` | 웹 API, 케이스 실행 관리, 실험실(구간 실험 · 영상 데이터셋 · 평가 구조) |
| `backend/src/privframe/harness` | 명령줄 도구 (`privframe-case` · `privframe-corpus` · `privframe-smoke`) |
| `frontend/src` | 서비스 화면과 실험실 화면 |
| `frontend/mock` | 같은 API를 흉내 내는 모의 백엔드 (키 없이 화면을 볼 때, 화면 테스트) |
| `configs/pipeline` | 부품 카탈로그와 프로필 (실험실에서 고친다) |
| `configs/corpus` · `configs/knowledge` | 코퍼스 수집 목록, 쟁점 분류와 사실 정의 |

실행 기록은 `data/`(케이스 · 체크포인트 · 실험 · 코퍼스 · 색인)에, 모델 파일은 `models/`에 생기며 둘 다 git에 올리지 않습니다.

## 검사

```bash
uv run ruff check backend
uv run --group models pytest                # 고정 응답으로 그래프 전체를 돌린다 (키 불필요)
cd frontend && npm run build                # 타입 검사 + 빌드
npx playwright install chromium && npx playwright test   # 모의 백엔드 + 고정 응답으로 띄운 실제 백엔드
```

## 개인정보

- 팀이 찍은 영상은 이 컴퓨터 밖으로 내보내지 않습니다. 외부 API로 프레임을 보내는 프로필은 실험실이 "외부 전송"으로 표시하고, 외부 전송 금지 데이터셋에는 실행을 거절합니다.
- 예시 영상은 합성입니다. 얼굴은 공개 저작물인 NASA 시험 초상 하나뿐이고 이름과 번호판은 지어낸 것입니다.
- 키는 `.env`에만 둡니다.

## GitHub 변경 기록

팀의 [Notion 개발 가이드](https://app.notion.com/p/3e475ad03a308156a8fed250398b93fd)를 따릅니다.

- GitHub Issue에 구현 목적 · 범위 · 완료 조건을 기록하고 Notion 작업 카드에서 연결합니다.
- `main`에 직접 push하지 않습니다. `feat/<area>-<short-name>`, `fix/<area>-<short-name>`, `docs/<short-name>` 같은 작업 브랜치를 사용합니다.
- 커밋은 `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:` 접두어를 사용합니다.
- PR에는 관련 Issue, 변경된 동작, 로컬 실행 · 검증 결과, 제한을 기록합니다. 최소 1명 리뷰 후 merge하고 작업 브랜치를 삭제합니다.
- 현재 구현은 저장소 문서, 기술 선택 이유는 Notion 의사결정 기록과 ADR, 코드 변경과 검증 근거는 PR에서 관리합니다. 구현하지 않은 것을 완료 기능으로 기록하지 않습니다.

1.0 베이스라인(얼굴 검출 + 문서 RAG, Celery 작업 큐)은 2.0으로 교체하며 저장소에서 지웠습니다. 필요하면 커밋 `c03476e`에서 볼 수 있습니다.
