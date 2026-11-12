from privframe.core.report import build_notices, build_script, to_html

SEGMENTS = [{"id": "S1", "start": 0.0, "end": 6.0, "label": "카페"}, {"id": "S2", "start": 6.0, "end": 10.0, "label": "메뉴"}]


def decision(**fields):
    return {"id": "D-1", "targetLabel": "인물 1", "scope": "face", "method": "mosaic", "shape": "ellipse", "ranges": [[0.0, 6.0]], "satisfies": ["REQ-1"], "rationale": "이유", **fields}


def test_script_rows_name_the_scene_a_range_starts_in_and_the_requirement_behind_it():
    plan = {"decisions": [decision(), decision(id="D-2", targetLabel="글자", scope="text", method="keep", ranges=[[6.0, 10.0]], satisfies=[], rationale="개인정보가 아님")]}
    rows = build_script(plan, [{"id": "REQ-1", "statement": "얼굴을 식별할 수 없게 처리"}], SEGMENTS)
    assert [(r.start, r.end, r.scene, r.treatment, r.basis) for r in rows] == [
        (0.0, 6.0, "카페", "얼굴 모자이크 · 타원", "얼굴을 식별할 수 없게 처리"),  # half-open: ends where the next scene starts
        (6.0, 10.0, "메뉴", "유지", "개인정보가 아님"),  # starts exactly on the cut: the second scene
    ]


def test_every_redaction_on_screen_is_a_mosaic_named_by_scope_and_shape():
    plan = {"decisions": [
        decision(id="D-1", scope="body", shape="box", ranges=[[0.0, 2.0], [7.0, 8.0]]),
        decision(id="D-2", targetLabel='명찰 "김민지"', scope="text", shape="box", ranges=[[1.0, 3.0]]),
        decision(id="D-3", targetLabel="번호판 1", scope="plate", shape="outline", ranges=[[6.5, 9.0]], satisfies=["REQ-9"], rationale="번호판 근거"),
    ]}  # fmt: skip
    rows = build_script(plan, [{"id": "REQ-1", "statement": "요건 1"}], SEGMENTS)
    assert [(r.start, r.scene, r.target, r.treatment, r.basis) for r in rows] == [
        (0.0, "카페", "인물 1", "전신 모자이크 · 사각형", "요건 1"),
        (1.0, "카페", '명찰 "김민지"', "글자 모자이크 · 사각형", "요건 1"),
        (6.5, "메뉴", "번호판 1", "번호판 모자이크 · 윤곽", "번호판 근거"),  # no known requirement behind it: the decision's own reason
        (7.0, "메뉴", "인물 1", "전신 모자이크 · 사각형", "요건 1"),
    ]


def test_a_decision_about_file_metadata_is_listed_for_the_whole_file():
    location = {"id": "D-2", "targetLabel": "촬영 위치 정보", "scope": "metadata", "method": "remove", "ranges": [], "satisfies": ["REQ-2"], "rationale": "좌표 삭제"}
    requirements = [{"id": "REQ-1", "statement": "얼굴을 식별할 수 없게 처리"}, {"id": "REQ-2", "statement": "촬영 위치 좌표를 파일에서 삭제"}]
    rows = build_script({"decisions": [decision(ranges=[[2.0, 4.0]]), location]}, requirements, SEGMENTS, duration=10.0)
    assert [(r.start, r.end, r.scene, r.target, r.treatment, r.basis) for r in rows] == [
        (0.0, 10.0, "파일 전체", "촬영 위치 정보", "삭제", "촬영 위치 좌표를 파일에서 삭제"),  # it has no shape and no time range
        (2.0, 4.0, "카페", "인물 1", "얼굴 모자이크 · 타원", "얼굴을 식별할 수 없게 처리"),
    ]
    kept = {**location, "method": "keep", "satisfies": [], "rationale": "공개 범위가 제한적이라 그대로 둠"}
    (row,) = build_script({"decisions": [kept]}, requirements, SEGMENTS, duration=10.0)
    assert (row.scene, row.treatment, row.basis) == ("파일 전체", "유지", "공개 범위가 제한적이라 그대로 둠")
    html = to_html("clip.mp4", {"finishedAt": "", "elapsedSec": 1.0, "costUsd": 0.0, "output": {"filename": "o.mp4", "codec": "H264"}, "script": [r.wire() for r in rows],
                                "verification": [], "history": [], "notices": [], "citations": []}, {"decisions": [{**decision(), "satisfies": []}, location]}, [])  # fmt: skip
    assert "<td>삭제</td>" in html and "<td>모자이크</td>" in html and "파일 전체" in html


def test_notices_say_what_happened_to_the_location_tags():
    plan = {"decisions": [], "unsatisfied": []}
    plain = build_notices([], [], plan, has_audio=False)
    assert len(plain) == 1 and plain[0].startswith("이 보고서는 법률 자문이 아닙니다")
    removed = build_notices([], [], plan, has_audio=True, location_tags=["location", "location-eng"], removed_tags=["location", "location-eng"])
    assert any("음성은 처리하지 않았습니다" in n for n in removed)
    assert any("location, location-eng" in n and "삭제했습니다" in n for n in removed) and not any("그대로 있습니다" in n for n in removed)
    kept = build_notices([], [], plan, has_audio=False, location_tags=["location"], removed_tags=[])
    assert any("(location)" in n and "그대로 있습니다" in n for n in kept) and not any("삭제했습니다" in n for n in kept)
    assert kept[-1] == plain[-1]  # the disclaimer stays last
