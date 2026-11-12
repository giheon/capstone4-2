# PrivFrame 2.0 — 실험실: 실행 메뉴 통합과 누적형 구간 실험

## 16. 실험실 보완 (2026-11-12)

M4 보완(§15) 뒤에 나온 실험실 지시 5건을 반영한 설계와 기록이다. 결정의 이유는 [ADR-0014](../adr/0014-experiment-sets-accumulate-runs-and-never-answer-for-the-user.md)에 있다.

### 16.1 요약

| # | 지시 | 한 일 |
|---|---|---|
| 1 | 상호작용이 든 구간은 자동 응답 없이, 한 번에 조합 1개 × 영상 1개 | 구간에 "사용자에게 묻는 지점"을 표시하고, 그런 구간이 범위에 들면 실행을 1건씩만 받는다. 질문 · 검토에서 멈추면 서비스 화면에서 직접 답한다. 범위 앞의 상호작용은 이미 답한 실행의 체크포인트에서 이어 간다 |
| 2 | 한 실험 안에 실행을 누적하고 비교 | 구간 실험은 "구간이 정해진 실행 묶음"이 되었다. 조합이나 영상을 바꿔 실행을 계속 더하고, 조합별로 영상 · 데이터셋에 걸쳐 평균을 낸다 |
| 3 | 실행 기록과 토폴로지를 하나로 | 메뉴 "실행" 하나. 목록에서 실행을 고르면 그 실행의 토폴로지가 열린다. 토폴로지 위의 실행 선택 목록, 체크포인트 분기 양식을 지웠다 |
| 4 | 평가는 최소 구조만 | 점수 한 줄의 형식, 실행별 저장, 조합별 집계, 평가기 등록표, 밖에서 계산한 점수를 넣는 API. 새 지표는 만들지 않았다 |
| 5 | 구현과 검증 | §16.8 |

### 16.2 구간과 상호작용

구간은 다섯 개다. "검토 · 감사"를 렌더에서 떼어 따로 두었다(전에는 렌더 구간이 검토를 자동으로 통과시켰다).

| 구간 | 시작 노드 | 끝 | 사용자에게 묻는 것 |
|---|---|---|---|
| 영상 정보 추출 `perception` | `perceive` | `perceive` 뒤 | 없음 |
| 법률 분석 `analysis` | `await_identity` 또는 `legal_analyst` | `legal_analyst` 뒤 | 같은 사람 확인(`identity`), 질문(`questions`) |
| 계획 `plan` | `plan` | `review_guide` 뒤 | 없음 |
| 검토 · 감사 `review` | `await_review` | `render_original` 앞 | 계획 검토(`review`), 감사 확인(`audit`) |
| 렌더 · 검증 `render` | `render_original` | 끝 | 없음 |

- 묻는 지점이 있는 구간이 범위에 하나라도 들면 그 실험은 **직접 응답 실험**이다.
- 직접 응답 실험의 실행은 자동 응답을 쓰지 않는다. 멈춘 실행은 실험 화면과 토폴로지의 "서비스 화면" 버튼으로 가서 답한다. 답하면 이어서 진행하고 범위 끝에서 마친다.
- 묻는 지점이 없는 범위는 `영상 정보 추출`, `계획`, `렌더 · 검증` 셋뿐이다(구간이 이어져야 하므로). 이 셋은 전처럼 조합 × 영상을 한 번에 돌린다.

### 16.3 실행 1건이 어디서 시작하는가

실행 1건은 (영상, 조합)이다. 조합의 설정은 "기준 프로필 + 바꾼 부품 · 값"이다.

1. 같은 영상(파일 해시가 같음)의 실행 가운데, 범위 앞 구간들의 부품 · 값이 이 조합과 모두 같은 것을 찾는다. 이 실험의 실행을 먼저 보고, 그다음 실험실의 다른 실행(서비스에서 올린 작업 포함)을 본다.
2. 그 실행과 조합이 처음 달라지는 구간을 `d`라 한다. 시작 구간 `f`는 `d`와 "범위 안의 첫 직접 응답 구간" 가운데 앞선 쪽이다. 범위 안에서 달라지는 것이 없으면 범위의 첫 구간이다.
3. 그 실행에 `f` 앞 체크포인트가 있으면 거기서 **이어 간다**. 앞 단계는 다시 실행하지 않고, 앞 단계에서 받은 답도 체크포인트에 들어 있어 그대로 쓴다. 후보가 여럿이면 `f`가 가장 뒤인 것, 같으면 이 실험의 최신 실행을 쓴다. 사용자가 실행을 직접 지정할 수도 있다.
4. 쓸 수 있는 실행이 없으면 **처음부터** 실행한다. 범위 앞 구간은 기준 프로필로 돌고, 영상 정보 추출의 단계는 저장된 결과를 가져온다.
5. 다만 범위 앞에 직접 응답 구간이 있는데 쓸 수 있는 실행이 없으면 시작하지 않는다. 화면이 어느 영상에 무엇이 없는지 알려 준다(예: "법률 분석까지 마친 실행이 없음").
6. 질문에 자동으로 답한 실행(데모 실행, 2026-11-12 이전 방식의 실험 실행)은 묻는 구간을 건너 이어 가지 않는다. 그 답은 사람이 한 답이 아니기 때문이다. 묻는 구간 앞(영상 정보 추출)까지는 이어 갈 수 있다.
7. 앞 구간의 부품이 기준 프로필과 다른 실행도 이어 가지 않는다. 그 차이를 비교하려면 그 구간부터 범위에 넣는다.

따라서
- 직접 응답 구간이 범위 **안**에 있으면 매 실행이 그 구간을 다시 돌고, 사용자가 매번 새로 답한다.
- 직접 응답 구간이 범위 **앞**에 있으면 이미 답한 실행의 체크포인트와 답을 다시 쓴다. 다시 묻지 않는다.

### 16.4 구간 실험 = 실행 묶음

- 구간 실험은 범위(시작 구간 ~ 끝 구간)와 기준 프로필이 정해진 묶음이다. 만들 때 첫 실행을 함께 넣고, 그 뒤로 "실행 추가"로 계속 더한다.
- 실행 추가는 부품별 후보(없으면 기준 프로필 그대로)와 영상을 고른다. 고른 후보의 곱이 조합이 된다. 이미 있는 조합이면 그 조합에 실행이 더해진다. 같은 (영상, 조합)을 다시 돌릴 수도 있다.
- 직접 응답 실험은 한 번에 실행 1건만 추가할 수 있다.
- 실행은 저마다 독립된 케이스(`exp_…`)라서 "실행" 메뉴에 따로 남고 토폴로지가 열린다.
- 실행 상태: `queued` 대기, `running` 실행 중, `waiting` 사용자 응답 대기(또는 멈춤), `completed`, `failed`, `skipped`.
- 실험 상태는 실행에서 나온다: `running`(대기 · 실행 중인 실행이 있음), `waiting`(응답을 기다리는 실행만 있음), `idle`.
- 응답 대기 중인 실행은 서버를 다시 띄워도 남는다. 사용자가 답해 케이스가 끝나면 실험이 그 결과를 가져온다.
- "멈춤"은 대기 · 실행 중인 실행을 멈춘다. 응답을 기다리는 실행은 그대로 둔다(답하거나, 실험에서 지운다).
- 영상 데이터셋에서 "자동 응답용 답"(`answers`)을 지웠다. 실험이 답을 대신하지 않으므로 쓸 곳이 없다. 메모와 정답(`expected`)은 그대로다.

**비교와 집계**

- 조합별 요약은 (조합, 영상) 칸마다 완료된 실행의 평균을 먼저 내고, 그 값을 영상에 걸쳐 평균한다. 같은 영상을 여러 번 돌려도 그 영상의 비중이 커지지 않는다.
- 데이터셋별 요약도 같은 방법으로 낸다.
- 기준 조합과의 일치도는 같은 영상의 가장 최근 기준 조합 실행과 비교한다. 기준 조합을 나중에 돌려도 채워진다.
- 조합마다 완료한 영상이 다르면 평균을 그대로 비교할 수 없다. 요약에 조합별 영상 수를 함께 준다.
- 시간은 사용자를 기다린 시간을 뺀 값이다. 기다린 시간은 `waitSec`로 따로 준다.

### 16.5 실행 메뉴

- 메뉴(다섯 개): **실행** · 실험 · "데이터셋 · 평가기" · 프로필 · 모델 후보.
- `/lab`과 `/lab/runs`는 실행 목록, `/lab/runs/{id}`는 그 실행의 토폴로지.
- 토폴로지 위의 실행 선택 목록과 "데모 실행" 버튼을 지웠다. 데모 실행은 실행 목록에서만 시작한다.
- 체크포인트 탭의 "여기서 분기" 양식과 `POST /api/lab/runs/{id}/fork`를 지웠다. 부품을 바꿔 이어 실행하는 일은 구간 실험이 한다.
- 역할: 실행 = 실행 1건의 조회와 추적, 실험 = 여러 실행의 구성 · 누적 · 비교.

### 16.6 평가의 최소 구조

- **점수 한 줄**: `{metric, value, label, unit, direction, section, evaluator, detail}`. `value`는 수, `direction`은 `higher` · `lower` · 없음.
- **저장**: 실행마다 `scores` 목록.
- **평가기 등록표**(`service/evaluation.py`): 평가기는 `id`, 이름, 보는 구간, 함수 하나다. 실행이 끝나면 범위에 맞는 평가기가 돌고 점수를 낸다. 지금 등록된 것은 전부터 있던 "데이터셋 정답과 비교"(글자 인식, 인물 · 번호판 · 장면 수) 하나다.
- **밖에서 넣기**: `PUT …/runs/{run}/scores`로 다른 도구가 계산한 점수를 넣는다. 평가기 이름 단위로 바꿔 쓴다.
- **집계**: 조합별 · 데이터셋별로 지표마다 평균과 개수.
- 팀의 평가 체계가 정해지면 평가기를 등록하거나 점수를 넣으면 된다. 화면은 지표 이름을 모르는 채로 표를 그린다.

### 16.7 API 계약

`/api/lab` 아래. 실패는 `{detail}`.

**구간** `GET /sections`

```jsonc
[{ "id": "analysis", "label": "법률 분석", "description": "…",
   "entry": "await_identity",            // 이어 가기의 시작 노드(첫 번째)
   "exit": "legal_analyst",              // 범위가 여기서 끝날 때 마지막으로 도는 노드
   "interactions": [{ "kind": "identity", "label": "같은 사람 확인" }, { "kind": "questions", "label": "질문" }],
   "slots": ["llm.analyst", …], "params": ["rag.top_k", …],
   "steps": [{ "node", "label", "slots", "params", "after", "stored" }] }]
```

**평가기** `GET /evaluators` → `[{ id, label, description, sections: [구간 id], metrics: [{ id, label, unit, direction }] }]`

**실험**

| 메서드 · 경로 | 본문 | 응답 |
|---|---|---|
| `GET /stage-experiments` | | `StageExperimentSummary[]` |
| `GET /stage-experiments/limits` | | `{ maxVariants, maxRuns, costCapUsdDefault, costCapUsdMax }` (실행 추가 1회의 한도와 묶음 전체의 비용 상한) |
| `POST /stage-experiments/preview` | `Launch` + `{ from, to?, baseProfile? }` 또는 `{ experiment: id }` | `LaunchPlan` |
| `POST /stage-experiments` | `Launch` + `{ title?, from, to?, baseProfile?, costCapUsd? }` | `StageExperiment` (201) |
| `GET /stage-experiments/{id}` | | `StageExperiment` |
| `PATCH /stage-experiments/{id}` | `{ title?, costCapUsd? }` | `StageExperiment` |
| `POST /stage-experiments/{id}/runs` | `Launch` | `StageExperiment` (201) |
| `DELETE /stage-experiments/{id}/runs/{run}` | | `StageExperiment` (끝난 실행 · 응답 대기 실행만. 케이스는 "실행"에 남는다) |
| `PUT /stage-experiments/{id}/runs/{run}/scores` | `{ evaluator, scores: Score[] }` | `StageExperiment` |
| `POST /stage-experiments/{id}/stop` | | `StageExperiment` |
| `DELETE /stage-experiments/{id}` | | `{ ok }` (실행 중이면 409) |

```jsonc
// Launch: 실행 추가 1회
{ "matrix": { "llm.planner": ["claude-sonnet-5-5-api", "claude-haiku-5-5-api"] },  // 부품 → 후보들. 없는 부품은 기준 프로필 그대로
  "paramMatrix": { "rag.top_k": [4, 8] },
  "datasets": ["sample"], "items": ["sample/sample"],   // items 가 있으면 그 영상만
  "source": "exp_1112…" }                               // 선택: 이어 갈 실행을 지정 (영상 1개일 때)

// LaunchPlan: 실제로 무엇이 돌지. 시작 요청도 같은 계산을 하고, problems 가 있으면 422
{ "from": "plan", "to": "plan", "sections": ["plan"],
  "interactive": false,
  "interactions": [],                                   // 범위 안: [{ section, kind, label }]
  "variants": [{ "key", "id": "v2" | null, "label", "base", "overrides", "params", "changed", "firstChanged", "recomputes", "framesLeaveDevice" }],
  "runs": [{ "item": "sample/sample", "itemTitle": "…", "variant": "<key>",
             "start": "fork" | "scratch",
             "source": { "caseId", "title", "startedAt" } | null,
             "startSection": "plan", "forkedAt": "plan" | null,
             "interactive": false, "asks": [],          // 이 실행이 사용자에게 물을 수 있는 것의 종류: ["identity", "questions"]
             "candidates": [{ "caseId", "title", "startedAt", "startSection" }],   // source 로 지정할 수 있는 실행 (좋은 순, source 를 지정해도 전부)
             "problem": null | "이유" }],
  "problems": ["…"] }                                    // 한도 초과, 직접 응답 실험에 2건 이상, 실행별 problem 요약

// StageExperimentSummary
{ "id", "title", "createdAt", "updatedAt", "status": "running" | "waiting" | "idle",
  "from", "to", "interactive", "baseProfile": { "id", "name", "configHash" },
  "datasets": ["sample"], "variants": 3, "items": 1,
  "progress": { "done", "total", "waiting" }, "costUsd", "costCapUsd" }

// StageExperiment = Summary 에서 variants · items 가 목록이 되고 다음이 더해진다
{ "sections": ["plan"], "interactions": [{ section, kind, label }],
  "variants": [StageVariant], "items": [StageItem], "runs": [StageRun], "summary": [VariantSummary] }

// StageVariant
{ "id": "base" | "v1"…, "key", "label", "base", "overrides", "params", "changed",
  "firstChanged": "plan" | null,                        // 범위에서 처음 달라지는 구간
  "recomputes": ["plan"],                               // 기준과 달라 다시 계산되는 범위 안 단계
  "configHash",
  "framesLeaveDevice" }

// StageRun
{ "id": "r7", "item": "sample/sample", "variant": "v1",
  "status": "queued" | "running" | "waiting" | "completed" | "failed" | "skipped",
  "createdAt", "startedAt", "finishedAt",
  "caseId", "start": "fork" | "scratch" | null, "sourceCaseId", "forkedAt": "plan" | null, "startSection": "plan" | null,
  "interactive": false, "autoAnswered": false,          // autoAnswered: 2026-11-12 이전 방식으로 자동 응답한 실행
  "waitingFor": null | "identity" | "questions" | "review" | "audit" | "paused",
  "error", "metrics": RunMetrics | null, "scores": [Score],
  "evaluationErrors": ["평가기 이름: 오류"] }            // 있을 때만: 실패한 평가기 (실행 결과는 그대로)

// RunMetrics: 전과 같고 다음이 다르다
//   + "waitSec": 사용자를 기다린 시간. elapsedSec · sectionSec · steps[].sec 은 그 시간을 뺀 값
//   + "review": { edits, findings, auditRounds, redacted: ["<target>:<scope>"] } | null   (검토 · 감사 구간)
//   - "expected" 는 없어지고 scores 로 옮겼다
//   "vsBase": 같은 영상의 최근 기준 조합 실행과의 비교 (없으면 null)

// Score
{ "metric": "expected.texts", "label": "글자 인식", "value": 0.67, "unit": "ratio", "direction": "higher",
  "section": "perception", "evaluator": "expected", "detail": "2 / 3 · 못 찾음: 김민수" }

// VariantSummary
{ "variant": "v1", "runs", "completed", "failed", "skipped", "waiting",
  "items": 2,                                           // 완료한 영상 수
  "sectionSecMean", "elapsedSecMean", "costUsdMean", "agreementMean",   // 영상에 걸친 평균 (없으면 null)
  "fullSecMean", "fullCostUsdMean",                     // 아무것도 재사용하지 않았다면 걸렸을 시간 · 비용 (쓴 것 + 아낀 것). 재사용한 양이 다른 조합끼리는 이 값으로 비교
  "costUsdTotal", "savedSecTotal", "savedCostUsdTotal", "llmCallsTotal",
  "scores": { "expected.texts": { "label", "unit", "direction", "mean", "n" } },
  "byDataset": [{ "dataset", "datasetName", "items", "completed", "sectionSecMean", "costUsdMean", "fullSecMean", "fullCostUsdMean", "agreementMean", "scores": {…} }] }
```

**실행** `GET /runs`의 각 행(`RunSummary`)에 `waitingFor`(응답 대기 종류)와 `interactive`(직접 응답 실험 실행)가 더해진다. `POST /runs/{id}/fork`는 없어졌다. 케이스 상세(`GET /api/cases/{id}`)에는 실험 실행일 때 `experimentId`가 들어 있다. 직접 응답 실험 실행은 서비스 홈의 작업 목록에 나오지 않는다(실험실에서 연다).

**영상 데이터셋** 영상 항목에서 `answers`가 없어졌다(추가 · 수정 요청에서도).

### 16.8 검증 기록 (2026-11-12)

**자동 테스트**

| 대상 | 결과 |
|---|---|
| 백엔드 `uv run --group models pytest` (legacy 제외) | 300개 통과 (전 291 + 구간 실험 8 + API 1) |
| 옛 1.0 테스트 `uv run pytest backend/tests/legacy` | 128개 통과, 1개 실패 (전과 같은 실패) |
| 화면 `npx playwright test` | 33개 통과 (모의 백엔드 27 + 고정 응답 백엔드 6) |
| `ruff check backend/` · `tsc -b` · `npm run build` | 이상 없음 |

새 백엔드 테스트(`backend/tests/test_service_experiments.py`)가 보는 것: 묻는 구간의 표시, 묻지 않는 범위의 조합 실행과 누적, 묻는 범위는 1건만 받고 사람이 답할 때까지 멈춰 있는지, 다음 실행이 다시 묻는지, 범위 뒤의 실험이 답한 실행을 이어 가며 다시 묻지 않는지(계획 → 검토 → 렌더까지), 자동으로 답한 실행을 거절하는지, 밖에서 넣은 점수의 저장과 평균, 응답 대기 실행이 관리자 재시작 뒤에도 남는지, 이전 형식 실험의 변환.

**실제 모델로 확인** (Claude 프로필, 예시 영상, 서버 `http://127.0.0.1:8000`)

| 실험 | 실행 | 시작 지점 | 묻고 답한 것 | 구간 시간 | 비용 |
|---|---|---|---|---|---|
| 법률 분석 (직접 응답) `sx_1111200147_267e` | 기준 (분석 Sonnet 5.5) | '법률 분석' 앞에서 이어 감 (영상 정보 추출은 기존 실행 것) | 질문 1개, 서비스 화면에서 답함 | 29.5초 (기다린 47.8초 제외) | $0.103 |
| | 분석 Haiku 5.5 | 같은 지점 | 질문 3개, 새로 답함 | 96.0초 (기다린 11.8초 제외) | $0.015 |
| 계획 `sx_1111200327_4643` | 기준 (계획 Opus 5.5) | '계획' 앞에서 이어 감 ← 위 기준 실행 | 묻지 않음 | 22.5초 | $0.066 |
| | 계획 Sonnet 5.5 | 같은 지점 | 묻지 않음 | 21.7초 | $0.040 |
| | 계획 Haiku 5.5 | 같은 지점 | 묻지 않음 | 7.2초 | $0.0011 |
| 검토 → 렌더 `sx_1111200433_d210` | 기준 | '검토 · 감사' 앞에서 이어 감 ← 위 계획 기준 실행 | 계획 검토, 서비스 화면에서 승인 | 12.5초 (기다린 16.2초 제외) | $0 |

- 두 분석 모델은 서로 다른 질문을 했다(Sonnet 1개, Haiku 3개). 미리 적어 둔 답으로는 맞출 수 없는 경우다. 보호 요건의 대상은 40%만 같았다.
- 계획 세 조합은 가리기로 한 대상이 같았다(기준과 일치 100%). Haiku는 요건 1건을 충족하지 못한 계획을 냈다.
- 검토 → 렌더 실행은 출력 검증 11항목에 실패가 없었다.
- 실험 3건 추가 전에는 계획 실험이 거절되었다("예시 영상에는 '법률 분석'까지 마친 실행이 없습니다"). 예시 영상의 기존 실행은 모두 자동 응답이었기 때문이다.
- 2026-11-11에 저장된 실험 3건은 새 형식으로 읽힌다. 변환 전 파일은 따로 보관했다.

**확인하지 못한 것**

- 실제 서버에서 실행 중인 실험을 멈추는 동작, 비용 상한에 걸려 건너뛰는 동작, 평가기가 실패한 경우의 표시는 화면으로 보지 않았다(백엔드 코드와 모의 백엔드에만 있다).
- 영상이 여러 개인 데이터셋에서의 집계는 모의 백엔드와 단위 테스트로만 확인했다(실제 데이터는 예시 영상 1건).
- 같은 사람 확인(`identity`)이 나오는 실제 영상으로는 돌려 보지 않았다(고정 응답 테스트에만 있다).

### 16.9 알려진 한계

- 묻는 범위의 실험은 조합마다 사람이 다시 답해야 한다. 같은 답을 해도 실행마다 따로 기록되고, 답이 달라지면 비교에 섞인다. 화면은 답의 내용을 실행 상세에서 보여 주지만 답이 같은지 검사하지는 않는다.
- 이어 갈 실행은 "범위 앞 구간의 부품이 기준 프로필과 같은" 실행뿐이다. 다른 프로필로 서비스에서 처리한 실행은 쓰지 못한다.
- "재사용 없이 걸렸을 시간 · 비용"은 실제로 쓴 값에 재사용으로 아낀 값을 더한 추정이다. 이어 간 실행이 다시 다른 실행을 이어 간 것이면 아낀 값이 일부만 잡힌다.
- 평가는 구조만 있다. 등록된 평가기는 데이터셋 정답과의 비교 하나이고, 법률 분석 · 계획의 품질 지표는 없다.
- 전체 파이프라인 평가 실험 화면(`/lab/experiments/pipeline`, 예시 데이터)은 손대지 않았다.

### 16.10 검토 수정과 정리 (2026-11-12, 푸시 전)

**검토 수정 10건.** §16.8의 수치는 이 수정 전의 것이다.

| # | 문제 | 조치 |
|---|---|---|
| 1 | `.gitignore`의 `data/`가 `backend/src/privframe/service/data/`(graph.json · datasets.json)까지 무시 | `/data/` · `/models/`로 루트에만 적용 |
| 2 | 대기열에 있던 케이스가 서버 재시작 뒤 "계속"에서 실패 | 체크포인트가 없으면 업로드부터 시작 |
| 3 | 감사에서 검토로 돌아갈 횟수를 다 쓴 뒤 또 누르면 권고가 전부 적용됨 | 돌아갈 수 있는지는 감사 확인 노드가 정해 케이스 상세(`canReturnToReview`)로 주고, 화면은 버튼을 숨기며 API는 409로 거절한다. 그래도 그 답이 오면 노드는 다시 묻는다 |
| 4 | 데모 실행의 자동 응답이 작업 스레드 종료보다 먼저 나가면 무시됨 | 자동 응답을 작업 스레드 안에서 한다(타이머 없음) |
| 5 | 이벤트 기록의 마지막 줄이 잘리면 서버가 시작되지 않음 | 잘린 줄을 버리고 파일을 다시 쓴다 |
| 6 | 계획이 다시 만들어지면 검토 화면의 수정이 다른 대상의 결정에 붙음 | 대상 · 범위가 바뀐 결정의 수정은 버린다 |
| 7 | 프로필 점검에 빈 값이 오면 500 | 저장소의 값 정리를 거친다 |
| 8 | 읽을 수 없는 영상을 올리면 500이 나고 파일이 남음 | 400으로 거절하고 케이스 폴더를 지운다 |
| 9 | 감사 화면의 요건 점검이 REQ-10 발견을 REQ-1에도 표시 | 요건 번호 전체가 일치할 때만 표시 |
| 10 | 실험이 실행을 시작하며 영상을 복사하는 동안 실험 API가 모두 멈춤 | 케이스를 만드는 일을 잠금 밖으로 옮겼다. 그 사이 실험을 멈추면 만들던 케이스를 지우고 건너뜀으로 기록한다 |

**1.0 베이스라인 삭제.** `backend/src/privframe`의 `adapters` · `application` · `domain` · `infrastructure`와 `api` · `bootstrap` · `cli` · `config` · `evaluation` · `component_evaluation` · `worker`, 옛 테스트(`backend/tests/legacy`), `scripts/`, `evaluation/`, `configs/*.yaml`, Docker 파일, 1.0 문서를 지웠다. 커밋 `c03476e`에 남아 있다.

- 1.0만 쓰던 의존성을 뺐다: sqlalchemy, celery, langchain-openai, langchain-chroma, langchain-text-splitters, 그리고 직접 쓰지 않는 python-dotenv · jinja2 · ruamel-yaml.
- 얼굴 검출 모델(YuNet)과 예시 영상의 초상은 1.0의 내려받기 스크립트와 수동 준비에 기대고 있었다. 이제 처음 쓸 때 고정한 주소에서 받아 sha256을 확인한다.
- README를 2.0 기준으로 다시 썼다.

**코드 정리.**

- 서비스 오류는 `ServiceError` 하나의 하위 클래스이고 처리기도 하나다. 시각 · ID · 파일 교체 · 내려받기는 `core/util.py`에 한 벌만 있다.
- 실험 실행은 묻지 않는 범위여도 자동 응답을 켜지 않는다. `RunManager.fork`에서 자동 응답과 옛 분기 API용 경로를 지웠다.
- 카탈로그는 파일이 바뀔 때만 다시 읽고(읽기도 약 8배 빨라짐), 영상의 프레임 시각 목록과 LangSmith 클라이언트도 한 번만 만든다.
- 지운 것: 옛 실험 파일 변환, 읽는 곳이 없던 응답 필드(조합의 `choices` · `paramValues`, 실험의 `note`), 실제 백엔드의 `/api/lab/settings`, 옛 생성 API 클라이언트의 흔적, 쓰이지 않던 내보내기와 CSS.
- 화면: 요청 계층 · 대기 상태 표 · 디바운스 · 라벨 조회의 중복을 하나로 모았다. 테스트의 공용 준비물은 `backend/tests/conftest.py`와 `frontend/tests/helpers.ts`로 옮겼다.

**검증 (정리 뒤의 코드).**

| 대상 | 결과 |
|---|---|
| 백엔드 `uv run --group models pytest` (의존성을 줄인 환경으로 다시 맞춘 뒤) | 307개 통과 |
| 화면 `npx playwright test` | 33개 통과 (모의 백엔드 27 + 고정 응답 백엔드 6) |
| `ruff check backend` · `tsc -b` · `npm run build` | 이상 없음 |
| 실제 모델(Claude 프로필): 데모 실행 1건 | 서버를 다시 띄우는 사이에 멈췄다가 새 코드에서 이어 완료. 출력 검증 11행에 실패 없음, $0.143 |
| 실제 모델: 계획 실험에 실행 1건 추가(Haiku) | 답한 실행의 체크포인트에서 이어 완료, 기준과 100% 일치, $0.0012 |

**손대지 않은 것.**

- 실행 요약 · 실험 요약의 읽히지 않는 필드 일부(`elapsedSecMean`, 합계 값들, `forkedAt`), 실험 실행의 `autoAnswered`(이전 방식 실험의 표시에 쓰인다).
- 토폴로지가 실행 중에 0.2초마다 전체를 다시 그리는 것, 케이스를 만들 때 영상을 두 번 읽고 통째로 복사하는 것.
- `runner.py`의 노드별 표(상태 · 화면에 비울 항목)를 `core/stages.py`에서 끌어오는 일, 케이스가 지금 무엇을 하는지를 한 곳에서 계산하는 일.
- D10 그림의 "POST fork" 화살표(그림을 다시 그리지 못해 본문에 주석만 달았다).
