# PrivFrame 평가 자료

`fixture-manifest.json`과 `corpus/`는 **FIXTURE_SYNTHETIC** 자료입니다. 실제 법령, 법률 정답, 실제 인물이 없는 자료로 업로드 → 추출 → 색인 → 탐지 → 검색 → 구조화된 제안의 연결을 확인합니다.

```bash
uv run python scripts/create_demo_video.py
uv run privframe evaluate --manifest evaluation/fixture-manifest.json --profile fixture --output data/evaluation/fixture.json
```

API와 worker가 실행 중이어야 합니다. 명령은 CLI와 UI가 사용하는 같은 HTTP API를 호출합니다. `--profile`은 호출당 하나이며, manifest의 `mode`와 실제 서버 profile의 `mode`가 일치해야 합니다. 실패 결과도 문서·색인·실행 ID와 함께 JSON에 기록됩니다. manifest의 모든 상대 경로는 manifest가 있는 디렉터리를 기준으로 해석합니다.

`expected_source_spans`는 문서의 manifest ID와 원문 `text`, 선택적 `article`/`page`를 정답 위치로 사용합니다. 원문 문자 위치를 검색된 청크의 offset 범위와 대조하므로 청크 크기, 겹침, 생성 ID가 달라도 비교할 수 있습니다. 원문에서 찾을 수 없는 정답 표기는 `invalid_reference_count`에 기록하며 recall을 계산하지 않습니다. `retrieval`은 검색 후보 전체, `provided_source_span_recall`은 실제 LLM 입력에 들어간 근거를 측정합니다.

실제 검색/모델 실험에서는 별도 manifest에 `mode: "real"`, `label: "REFERENCE_EVALUATION"`을 지정하고 사람이 확인한 공식 PDF/TXT 경로와 정답 원문을 넣으세요. 같은 manifest를 각 profile로 한 번씩 실행하고 JSON의 입력 해시, 색인 설정, 모델, source-span recall, track-map 검증 결과, usage와 stage timing을 비교할 수 있습니다. 이 도구는 법적 판단 정확도를 자동 채점하지 않습니다.

얼굴 정답 자료가 없다면 `manual_face_expectation`을 생략하거나 `annotations: null`로 두세요. 영상별 예상 트랙 수는 별도 비교값이며 탐지 recall을 대신하지 않습니다. 실제 얼굴 박스 평가는 아래처럼 사람이 **정규화된 영상의 픽셀 좌표와 0부터 시작하는 CFR 프레임**을 표시한 경우만 가능합니다. `exhaustive: false`이면 명시적으로 표시한 프레임만 평가합니다.

```json
{
  "has_actual_faces": true,
  "expected_track_count": 1,
  "annotations": {
    "frame_basis": "normalized_cfr_zero_based",
    "width": 640,
    "height": 480,
    "fps": 30,
    "exhaustive": false,
    "annotated_frames": [0, 1],
    "faces": [{"frame": 0, "box": {"x": 40, "y": 50, "width": 60, "height": 70}, "identity_label": "person-A"}],
    "iou_threshold": 0.5
  }
}
```

`identity_label`은 선택 항목입니다. 같은 인물이 재등장하는 다른 프레임의 얼굴에도 같은 값을 표시하면 `identity_metrics`가 자동 제안(`automatic`)과 현재 그룹(`current`)을 각각 평가합니다. 실명 대신 `person-A` 같은 영상 안에서만 쓰는 정답 ID를 사용하면 됩니다. 동의 여부나 그룹 확인을 뜻하는 값은 아닙니다.

- 정답 박스와 실제 관측 박스를 같은 프레임에서 IoU 기준으로 일대일 매칭한 뒤 트랙별 정답 인물을 구합니다.
- `false_merged_track_pairs`는 서로 다른 인물의 트랙이 같은 그룹에 묶인 쌍의 수이고, `split_same_person_track_pairs`는 같은 인물의 트랙이 다른 그룹으로 나뉜 쌍의 수입니다. 실제 검출·매칭된 정답이 한 인물로 일관된 트랙끼리 비교합니다.
- 한 트랙에서 서로 다른 정답 인물이 발견되면 `mixed_identity_track_ids`로 보고하고 쌍 계산에서는 제외합니다. `unmatched_identity_annotations`와 `unlabeled_track_count`도 함께 확인해야 하며, 일부 프레임의 정답만으로 영상 전체의 정확도를 주장할 수는 없습니다.
- `unresolved_track_count`는 그룹 소속이 누락·중복되거나 그룹에 `review_reasons`가 남은 트랙의 수입니다. `simultaneous_track_pair_count`는 같은 그룹 안의 서로 다른 트랙이 실제 같은 프레임에 관측된 경우를 셉니다. 등장 구간의 시작·끝만 겹치는 경우는 포함하지 않습니다.
- 인물 정답이 없으면 `identity_metrics`는 `null`입니다. 정답은 있지만 비교 가능한 트랙 쌍이 없으면 오병합·분할 오류도 `null`입니다. 이 경우 오류가 0이라는 뜻이 아닙니다. 얼굴 검출 결과의 누락은 기존 `face_metrics`로 별도 평가합니다.

새 실행에 확인되지 않은 인물 그룹이 있으면 평가기는 분석·그룹화 지표를 먼저 저장하고 `status: "AWAITING_LABELS"`, case의 `review_pending: true`로 종료합니다. `/decide`나 그룹 확인 API를 자동 호출하지 않습니다. `summary.review_pending_case_count`로 해당 실행 수를 확인할 수 있으며, 이 상태는 평가 실패가 아닙니다. 실행 ID를 이용해 화면에서 그룹과 인물별 입력을 확인한 뒤 판정을 진행하세요. 그룹화가 꺼진 기존 fixture 평가는 계속 검색·판정까지 진행합니다.

평가 중 렌더링은 기본적으로 꺼져 있습니다. **fixture mode에서만** case에 `render: true`와 `manual_render_treatment: {"action":"MASK","strength":3}`처럼 명시적인 보호 처리를 지정할 수 있습니다. 이 처리는 검출된 모든 트랙에 수동 적용합니다. 검출 트랙이 0개인 실험 출력에는 추가로 `confirm_no_faces: true`가 필요합니다. 실제 mode의 평가는 사용자 검토 전 영상 출력을 자동 요청하지 않습니다.

## 부품별 독립 평가

`privframe evaluate --target index|retrieval|vision|decision --manifest FILE --profile PROFILE --output /tmp/privframe-evaluation.json`으로 관련 부품만 실행할 수 있습니다. 입력 형식과 중간 정지·재개는 [제품·LangGraph 안내](../docs/Product_Workflows.md)의 독립 실행·평가 절을 참고하세요. 실제 서버를 사용하는 검사는 일반 `data/`와 분리한 임시 저장소에서 실행하세요.
