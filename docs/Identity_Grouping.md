# 인물 그룹화와 동의 검토

2026-10-11 구현 기준. 인물 그룹은 같은 영상 안에서 얼굴 등장 구간을 연결하는 단위이며, 실제 이름이나 영상 간 신원 데이터베이스를 만들지 않습니다.

> 입력과 운영 UI는 2026-10-16에 개편했습니다. 현재 사용법은 [제품·LangGraph 안내](Product_Workflows.md)를 참고하세요. 아래는 초기 그룹화 구현 기록입니다.

## 실행과 화면

`./scripts/dev.sh`는 의존성과 YuNet·ArcFace 모델을 확인한 뒤 API·worker·UI를 시작합니다. 얼굴 분석까지 실제 모델로 확인하고 문서 검색/판단을 오프라인으로 테스트하려면 인덱스와 실행에 `fixture-yunet`을 선택하세요. 문서가 없는 실행도 가능하며, 이 경우 판단 결과는 근거 부족으로 검토 필요 상태가 됩니다.

1. 영상을 업로드하고 이용 상황을 입력합니다.
2. 분석이 끝나면 인물 카드의 얼굴과 등장 구간을 확인합니다. 후보 연결의 유사도는 확률이 아닙니다.
3. 관계와 동의를 각각 입력합니다. 본인 선택으로 동의를 추정하지 않으며 기본값은 모두 미확인입니다.
4. 다른 인물로 잘못 나뉘면 그룹을 합칩니다. 한 그룹에 다른 사람이 있으면 해당 트랙을 별도 인물로 분리합니다. 그룹 구성 변경 시 영향받는 관계·동의는 초기화됩니다.
5. 한 트랙 자체에 서로 다른 사람이 있으면 구간을 펼쳐 영상 프레임을 선택해 나눕니다. 선택한 프레임은 두 번째 구간의 시작입니다. 검출점은 빠짐없이 두 구간에 배분되며 분할 구간의 관계·동의를 다시 확인합니다.
6. ‘인물 정보 저장 후 판단’을 누릅니다. 결과에서는 인물 전체에 처리를 적용하거나 등장 구간별 처리를 수정한 뒤 출력합니다.

이용 상황이 달라진 새 실행에서는 동의를 다시 입력합니다. 완료 결과의 처리 강도만 수정하는 revise는 기존 인물 확인·근거·판단을 그대로 보존합니다. 과거 실행과 `identity_grouper: none` 프로필은 기존 트랙 검토 UI를 사용합니다.

## 분석과 설정

정규화 영상에 PySceneDetect AdaptiveDetector를 적용한 뒤 경계에서 트래커를 초기화합니다. YuNet의 5개 랜드마크로 얼굴을 112×112에 정렬하고 ONNX Runtime CPU로 `w600k_r50.onnx` 특징을 추출합니다. 크기·선명도·검출 품질과 시간 간격을 고려해 트랙당 최대 5장만 임베딩합니다.

동일 프레임에 별개 얼굴로 검출된 트랙은 자동 병합하지 않습니다. complete-link 군집화는 두 그룹의 모든 교차 트랙 쌍이 병합 기준을 충족해야 연결합니다. 트랙 내부 특징이 충돌하거나 충분한 표본이 없으면 독립 그룹으로 남기고 확인 사유를 표시합니다. 거울·화면 속 같은 얼굴처럼 동시 등장 제약의 예외는 사용자가 수정할 수 있습니다.

| 설정 | 기본값 |
|---|---|
| `identity_grouper` | `arcface`; Haar/fixture는 `none` |
| `identity_similarity_threshold` | 0.60 자동 병합 기준 |
| `identity_candidate_threshold` | 0.40 확인 후보 기준 |
| `identity_consistency_threshold` | 0.35 트랙 내부 충돌 기준 |
| `identity_min_face_size` | 48픽셀 |
| `identity_max_samples` | 최대 5장 |
| `scene_adaptive_threshold` / `scene_min_content` | 3.0 / 15.0 |
| `scene_window_width` / `scene_min_length` | 2 / 15프레임 |

설정은 새 프로필로 바꾸고 실험합니다. 군집화 임계값만 바꾸면 저장된 특징을 재사용하고, 얼굴 표본 선택·모델·검출 설정이 바뀌면 해당 단계부터 다시 계산합니다. 숫자는 보편적 동일인 판정 기준이 아니며 실제 검증 자료를 늘려가며 평가해야 합니다.

## API와 기록

`POST /api/runs/{id}/identity-review`는 현재 revision과 전체 얼굴 트랙 분할, 인물별 입력을 함께 저장합니다.

```json
{
  "revision": 0,
  "groups": [{"id": "person-0001", "track_ids": ["face-0001", "face-0006"]}],
  "persons": {"person-0001": {"relationship": "third", "consent": "unknown"}},
  "confirmed_group_ids": ["person-0001"]
}
```

실제 저장 시에는 분석된 모든 얼굴 트랙을 중복 없이 포함해야 합니다. 모르는 정보는 `unknown`으로 저장할 수 있습니다. 관계는 `self/third/unknown`, 동의는 `granted/denied/unknown`입니다. 인물 구성을 바꾼 경우 명시적으로 확인하지 않은 그룹의 입력은 초기화합니다. 오래된 revision은 HTTP 409로 거부합니다.

`POST /api/runs/{id}/split-track`의 본문은 `{"revision":1,"track_id":"face-0001","frame":150}`입니다. 분할은 인물 검토 대기 상태에서만 가능하며 완료된 실행을 변경하려면 새 실행으로 분기합니다.

CLI에서도 `privframe identity-review RUN_ID --file review.json`, `privframe split-track RUN_ID --track-id TRACK_ID --frame FRAME --revision REVISION`을 사용합니다.

보고서는 자동 그룹, 현재 사용자 그룹, 관계·동의, 변경 이력, 대표 표본, 모델 checksum, 특징/그룹 fingerprint와 처리 시간을 보존합니다. 수치 특징은 로컬 `identity/features/`에 저장하고 공개 API·LLM 입력·보고서에는 포함하지 않습니다. 실제 오류 지표는 정답 `identity_label` 주석이 있는 평가에서만 계산하며, 정답 없는 실행의 오병합·분할 오류는 미측정입니다.

## 실제 영상 검증과 제한

`data/videos/PrivFrame_ABA/A_B_A.mp4`는 1280×720, 30fps, 900프레임입니다. 실제 정규화·검출·인식 결과:

- 장면 경계: 300·600프레임, 즉 10초·20초.
- 첫 A 3개, B 2개, 마지막 A 3개로 연속 트랙 8개.
- `face-0001↔0006`, `face-0002↔0007`, `face-0003↔0008`을 연결하고 B의 두 얼굴은 별도로 유지해 인물 5개.
- 실제 API 인물 확인·fixture 판단·전체 트랙 처리·900프레임 출력·보고서·재조회까지 검증.

반복 편집 영상의 성공은 다양한 자세·조명·카메라 전환에서의 일반 성능을 뜻하지 않습니다. 추가 정답 자료를 사용해 오병합과 동일 인물 분리를 평가해야 합니다. 모델 누락·checksum 오류는 실행 실패로 남고, 완료된 검출 결과를 보존해 모델 준비 후 재시도할 수 있습니다.

`uv run pytest backend/tests/test_identity_integration.py -q`로 실제 영상 회귀 검사를 실행합니다. 참조 영상이나 모델이 없으면 이 검사는 skip이며, 군집화·API·저장소·UI 검사는 합성/모의 자료로 계속 실행됩니다. 외부 OpenAI 판단 정확도는 이 검사 범위에 포함되지 않습니다.

향후 얼굴·사람은 인물 그룹화와 동의 질문 경로로, 번호판·문서 등은 전용 검출·OCR·비식별 정책 경로로 연결합니다. 이번 구현은 `object_type`과 교체 가능한 특징 추출·그룹화 인터페이스까지만 마련했습니다.
