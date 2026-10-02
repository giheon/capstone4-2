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
    "faces": [{"frame": 0, "box": {"x": 40, "y": 50, "width": 60, "height": 70}}],
    "iou_threshold": 0.5
  }
}
```

평가 중 렌더링은 기본적으로 꺼져 있습니다. **fixture mode에서만** case에 `render: true`와 `manual_render_treatment: {"action":"MASK","strength":3}`처럼 명시적인 보호 처리를 지정할 수 있습니다. 이 처리는 검출된 모든 트랙에 수동 적용합니다. 검출 트랙이 0개인 실험 출력에는 추가로 `confirm_no_faces: true`가 필요합니다. 실제 mode의 평가는 사용자 검토 전 영상 출력을 자동 요청하지 않습니다.
