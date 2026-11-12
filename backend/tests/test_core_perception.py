"""Local perception (M4, FR-03). The first part needs no model: trackers, text linking, grouping with made-up
feature vectors, attachment and counting are plain logic. The last part runs the real models on the team clip
and is skipped when a model package, a weight file, ffmpeg or the clip is missing."""
from conftest import PIPELINE
import math
import shutil
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from privframe.core import video
from privframe.core.domain import Decision, IdentityAmbiguity, PersonGroup, Plate, Proxy, SegmentInfo, TextItem, Track, TrackPoint, VideoAsset, VideoMeta, VideoUnderstanding
from privframe.core.geometry import Region, Timeline, visible_ranges
from privframe.core.perception import (
    MERGE_SIMILARITY,
    IDENTIFIABLE_SIMILARITY,
    READABLE_SIMILARITY,
    SPLIT_SIMILARITY,
    Leak,
    OutputChecker,
    PerceptionError,
    apply_identity_answer,
    attach_text,
    detect_and_track,
    face_finder,
    find_segments,
    group_identities,
    output_checker,
    recognize_text,
    redetector,
)
from privframe.core.perception.identity import assign_bodies, cluster_faces
from privframe.core.perception.models import MODELS_DIR
from privframe.core.perception.segments import segments_from_cuts
from privframe.core.perception.text import best_reading, link_text, ocr_frames
from privframe.core.perception.tracking import IouTracker, LibraryTracker, Sample, build_tracks, fill_gaps, keep_track
from privframe.core.perception.verify import EXPOSED_SIMILARITY, REFERENCE_FACES, TEXT_SCAN_FRAMES, count_inside, looks_like_text
from privframe.core.pipeline import load_catalog, load_profile, resolve
from privframe.core.settings import ROOT

CLIP = ROOT / "data" / "videos" / "PrivFrame_ABA" / "A_B_A.mp4"


def profile(**overrides: str):
    return resolve(load_catalog(PIPELINE), load_profile(PIPELINE, "baseline-v2"), {f"perception.{k}": v for k, v in overrides.items()})


def asset(width: int = 1000, height: int = 500, duration: float = 10.0, scale: float = 0.5) -> VideoAsset:
    meta = VideoMeta(
        path="x.mp4", filename="x.mp4", sha256="0", size_bytes=1, container="mp4", duration_s=duration, frame_count=int(duration * 10),
        width=width, height=height, display_width=width, display_height=height, codec="h264", time_base=(1, 1000),
    )  # fmt: skip
    return VideoAsset(meta=meta, proxy=Proxy(path="p.mp4", width=int(width * scale), height=int(height * scale), scale=scale, frame_count=meta.frame_count, pts_identical=True), pts_path="pts.json")


def track(track_id: str, kind: str, frames, box, segment: str = "S1", score: float = 0.9, text: str | None = None) -> Track:
    """A track at 10 fps; `box` is a fixed box or a function of the frame index."""
    points = [TrackPoint(f=f, t=f / 10, box=box(f) if callable(box) else box, score=score) for f in frames]
    return Track(id=track_id, kind=kind, segment=segment, points=points, text=text)


def unit(angle_deg: float) -> np.ndarray:
    """Unit vectors whose cosine similarity is the cosine of the angle between them."""
    return np.array([math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))])


# ---------- segments ----------


def test_segments_cover_the_whole_video_and_end_frames_are_inclusive():
    times = [i / 10 for i in range(100)]
    found = segments_from_cuts([40, 70], times, duration=10.0)
    assert [(s.id, s.start_frame, s.end_frame) for s in found] == [("S1", 0, 39), ("S2", 40, 69), ("S3", 70, 99)]
    assert [(s.start, s.end) for s in found] == [(0.0, 4.0), (4.0, 7.0), (7.0, 10.0)]
    assert all(len(s.keyframes) == 8 and s.start_frame <= s.keyframes[0] < s.keyframes[-1] <= s.end_frame for s in found)
    assert all(s.label == "" for s in found)


def test_a_video_without_cuts_is_one_segment_and_short_segments_have_fewer_keyframes():
    whole = segments_from_cuts([], [i / 10 for i in range(3)], duration=0.3)
    assert [(s.start_frame, s.end_frame, s.keyframes) for s in whole] == [(0, 2, [0, 1, 2])]
    assert segments_from_cuts([0, 500], [0.0, 0.1], duration=0.2)[0].end_frame == 1  # cuts outside the video are ignored


# ---------- tracking ----------


def test_iou_tracker_follows_two_objects_and_reconnects_after_a_short_gap():
    tracker = IouTracker(iou_threshold=0.3, max_gap=2)
    for step in range(6):
        left = ((10.0 + 2 * step, 10.0, 40.0, 40.0), 0.9)
        right = ((200.0 - 2 * step, 10.0, 40.0, 40.0), 0.8)
        tracker.feed(step, step / 10, [left] if step in (2, 3) else [right, left])  # the right object is missed twice
    tracks = tracker.result()
    assert len(tracks) == 2
    assert [s.f for s in tracks[0]] == [0, 1, 4, 5] and [s.f for s in tracks[1]] == [0, 1, 2, 3, 4, 5]
    assert all(s.box[0] > 150 for s in tracks[0]) and all(s.box[0] < 30 for s in tracks[1])


def test_iou_tracker_starts_a_new_track_after_a_long_gap_or_a_jump():
    tracker = IouTracker(max_gap=2)
    box = (10.0, 10.0, 40.0, 40.0)
    tracker.feed(0, 0.0, [(box, 0.9)])
    for step in (1, 2, 3):
        tracker.feed(step, step / 10, [])
    tracker.feed(4, 0.4, [(box, 0.9)])  # three misses: too long
    tracker.feed(5, 0.5, [((300.0, 10.0, 40.0, 40.0), 0.9)])  # no overlap
    assert [[s.f for s in t] for t in tracker.result()] == [[0], [4], [5]]


def test_library_tracker_keeps_the_first_detection_of_a_track():
    pytest.importorskip("trackers")
    tracker = LibraryTracker("bytetrack", fps=10.0, start_score=0.7)
    tracker.feed(0, 0.0, [])
    for step in range(1, 7):  # appears on the second sample, so the library numbers it only at the third
        tracker.feed(step, step / 10, [((50.0 + 3 * step, 60.0, 80.0, 80.0), 0.9)])
    (only,) = tracker.result()
    assert [s.f for s in only] == [1, 2, 3, 4, 5, 6]


def test_short_tracks_need_a_very_confident_detection():
    sure, unsure = Sample(0, 0.0, (0, 0, 10, 10), 0.95), Sample(0, 0.0, (0, 0, 10, 10), 0.8)
    weak = Sample(1, 0.1, (0, 0, 10, 10), 0.55)
    assert keep_track([sure], "face") and not keep_track([unsure], "face")
    assert keep_track([unsure, weak], "face") and not keep_track([weak, weak], "face")


def test_gaps_are_filled_with_interpolated_points_and_ids_follow_first_appearance():
    times = [i / 10 for i in range(20)]
    sampled = list(range(0, 20, 2))
    late = fill_gaps([Sample(4, 0.4, (100.0, 100.0, 20.0, 20.0), 0.9), Sample(10, 1.0, (160.0, 100.0, 20.0, 20.0), 0.8)], sampled, times)
    assert [(s.f, s.box[0], s.score) for s in late] == [(4, 100.0, 0.9), (6, 120.0, 0.0), (8, 140.0, 0.0), (10, 160.0, 0.8)]
    early = [Sample(0, 0.0, (10.0, 10.0, 50.0, 100.0), 0.9), Sample(2, 0.2, (12.0, 10.0, 50.0, 100.0), 0.7)]
    tracks = build_tracks([("S1", "face", late), ("S1", "person", early), ("S1", "face", early)], asset(scale=0.5))
    assert [(t.id, t.kind, t.points[0].f) for t in tracks] == [("T1", "face", 0), ("T2", "person", 0), ("T3", "face", 4)]
    assert tracks[0].points[0].box == (20.0, 20.0, 100.0, 200.0)  # proxy pixels → original pixels
    assert tracks[2].quality == pytest.approx(0.85) and tracks[2].segment == "S1"  # interpolated points do not count


# ---------- text ----------


def test_the_same_text_is_linked_across_readings_despite_ocr_noise_and_a_missed_reading():
    sign = (100.0, 50.0, 120.0, 30.0)
    samples = [
        (0, 0.0, [(sign, "김민지 바리스타", 0.9), ((400.0, 300.0, 80.0, 20.0), "EXIT", 0.95)]),
        (10, 1.0, [((102.0, 51.0, 120.0, 30.0), "김민지 바리스다", 0.7), ((400.0, 300.0, 80.0, 20.0), "EXIT", 0.9)]),
        (20, 2.0, [((400.0, 300.0, 80.0, 20.0), "EXIT", 0.9)]),  # the name is not read here
        (30, 3.0, [((104.0, 52.0, 120.0, 30.0), "김민지 바리스타", 0.95)]),
    ]
    chains = link_text(samples, (640.0, 360.0))
    assert [[s.f for s in chain] for chain in chains] == [[0, 10, 30], [0, 10, 20]]  # first and last reading included
    assert best_reading(chains[0]) == ("김민지 바리스타", pytest.approx(0.925))


def test_text_that_moves_is_followed_only_when_it_reads_the_same():
    samples = [
        (0, 0.0, [((50.0, 50.0, 100.0, 20.0), "NO PARKING", 0.9)]),
        (10, 1.0, [((250.0, 60.0, 100.0, 20.0), "NO PARKIN", 0.9)]),  # moved 200 px, almost the same reading
        (
            20,
            2.0,
            [
                ((250.0, 60.0, 100.0, 20.0), "DRIVEWAY", 0.9),  # same place, a different text
                ((600.0, 340.0, 30.0, 10.0), "NO PARKING", 0.9),  # same text, too far for one reading interval
            ],
        ),
    ]
    chains = link_text(samples, (640.0, 360.0))
    assert [[(s.f, s.text) for s in chain] for chain in chains] == [[(0, "NO PARKING"), (10, "NO PARKIN")], [(20, "DRIVEWAY")], [(20, "NO PARKING")]]


def test_ocr_reads_the_first_sample_one_per_second_and_the_last_sample():
    times = [i / 30 for i in range(100)]
    sampled = list(range(0, 100, 3))  # the analysis samples of one segment, 10 per second
    assert ocr_frames(times, sampled) == [0, 30, 60, 90, 99]
    assert ocr_frames(times, list(range(0, 62, 3))) == [0, 30, 60]  # the last sample is the last reading


# ---------- identities ----------


def test_face_tracks_merge_above_the_high_threshold_and_stay_apart_below_the_low_one():
    assert SPLIT_SIMILARITY < 0.363 < MERGE_SIMILARITY  # the band brackets OpenCV's SFace threshold
    features = {"a1": unit(0), "a2": unit(20), "b": unit(85), "c": unit(175)}  # cos 20° = 0.94, cos 85° = 0.09
    frames = {"a1": {0, 1}, "a2": {5, 6}, "b": {10, 11}, "c": {12}}
    groups, uncertain = cluster_faces(list(features), features, frames)
    assert groups == [["a1", "a2"], ["b"], ["c"]] and uncertain == []


def test_two_faces_in_the_same_frame_are_never_the_same_person():
    features = {"left": unit(0), "right": unit(5), "later": unit(2)}  # all three look alike
    frames = {"left": {0, 1, 2}, "right": {2, 3}, "later": {10}}
    groups, uncertain = cluster_faces(list(features), features, frames)
    assert groups == [["left", "later"], ["right"]] and uncertain == []  # cannot-link pairs are not questions


def test_the_band_between_the_thresholds_becomes_one_ambiguity_per_pair_of_groups():
    between = math.degrees(math.acos((MERGE_SIMILARITY + SPLIT_SIMILARITY) / 2))
    features = {"a1": unit(0), "a2": unit(10), "b": unit(between + 5), "none": None}
    features = {k: v for k, v in features.items() if v is not None}
    frames = {"a1": {0}, "a2": {5}, "b": {9}, "none": {20}}
    groups, uncertain = cluster_faces(["a1", "a2", "b", "none"], features, frames)
    assert groups == [["a1", "a2"], ["b"], ["none"]]  # a track without a feature stays alone and is not asked about
    ((first, second, value, track_a, track_b),) = uncertain
    assert (first, second, track_a, track_b) == (0, 1, "a2", "b") and SPLIT_SIMILARITY <= value < MERGE_SIMILARITY


def test_a_face_inside_two_overlapping_bodies_goes_to_the_body_it_heads():
    front = track("front", "person", range(10), (100.0, 100.0, 200.0, 400.0))
    behind = track("behind", "person", range(10), (220.0, 60.0, 200.0, 440.0))
    face_front = track("f1", "face", range(10), (180.0, 110.0, 40.0, 50.0))
    face_behind = track("f2", "face", range(10), (270.0, 105.0, 40.0, 50.0))  # its centre is inside both boxes
    owner = assign_bodies([["f1"], ["f2"]], {"f1": face_front, "f2": face_behind}, [front, behind])
    assert owner == {"front": 0, "behind": 1}


def test_grouping_builds_persons_ordered_by_prominence_and_keeps_people_without_a_face():
    tracks = [
        track("T1", "face", range(0, 40), (500.0, 100.0, 60.0, 80.0)),  # main person, shot 1
        track("T2", "person", range(0, 40), (400.0, 80.0, 300.0, 400.0)),
        track("T3", "face", range(60, 100), (300.0, 100.0, 60.0, 80.0), segment="S2"),  # the same person again
        track("T4", "person", range(60, 100), (200.0, 80.0, 300.0, 400.0), segment="S2"),
        track("T5", "face", range(42, 58), (800.0, 200.0, 30.0, 40.0)),  # someone who may be the same person
        track("T6", "person", range(20, 40), (50.0, 250.0, 60.0, 150.0)),  # seen from behind: no face
    ]
    doubt = math.degrees(math.acos((MERGE_SIMILARITY + SPLIT_SIMILARITY) / 2))
    vectors = {"T1": unit(0), "T3": unit(8), "T5": unit(doubt)}
    persons, ambiguities = group_identities(asset(), tracks, profile(), embed=lambda _asset, faces: {t.id: vectors[t.id] for t in faces})
    assert [(p.id, p.face_tracks, p.body_tracks) for p in persons] == [
        ("person-1", ["T1", "T3"], ["T2", "T4"]),
        ("person-2", [], ["T6"]),
        ("person-3", ["T5"], []),
    ]
    assert persons[0].prominence > persons[1].prominence > persons[2].prominence and all(0 <= p.prominence <= 1 for p in persons)
    (ambiguity,) = ambiguities
    assert (ambiguity.id, ambiguity.a, ambiguity.b) == ("identity-1", "person-1", "person-3")
    assert SPLIT_SIMILARITY <= ambiguity.similarity < MERGE_SIMILARITY
    assert 6.0 <= ambiguity.at_a <= 9.9 and 4.2 <= ambiguity.at_b <= 5.7  # a frame of the closest track of each


def understanding() -> VideoUnderstanding:
    return VideoUnderstanding(
        persons=[
            PersonGroup(id="person-1", face_tracks=["T1"], body_tracks=["T2"], cues=["안경"], attributes={"age_band": "adult"}, prominence=0.2),
            PersonGroup(id="person-2", face_tracks=["T7"], body_tracks=["T9"], cues=['명찰 글자 "김민지"', "안경"], attributes={"age_band": "minor", "role": "staff"}, prominence=0.05),
            PersonGroup(id="person-3", face_tracks=["T8"], prominence=0.01),
        ],
        texts=[TextItem(id="text-1", text="김민지", tracks=["TX1"], attached_person="person-2")],
        ambiguities=[
            IdentityAmbiguity(id="identity-1", a="person-1", b="person-2", similarity=0.41, at_a=1.0, at_b=7.0),
            IdentityAmbiguity(id="identity-2", a="person-2", b="person-3", similarity=0.35, at_a=7.0, at_b=9.0),
            IdentityAmbiguity(id="identity-3", a="person-1", b="person-3", similarity=0.33, at_a=1.0, at_b=9.0),
        ],
    )


@pytest.mark.parametrize("answer", ["same", "unknown"])
def test_same_and_unknown_merge_the_second_group_into_the_first(answer: str):
    before = understanding()
    after = apply_identity_answer(before, "identity-1", answer)
    assert [p.id for p in after.persons] == ["person-1", "person-3"]
    merged = after.persons[0]
    assert (merged.face_tracks, merged.body_tracks) == (["T1", "T7"], ["T2", "T9"])
    assert merged.cues == ["안경", '명찰 글자 "김민지"']
    assert merged.attributes == {"age_band": "adult", "role": "staff", "merge_conflicts": {"age_band": ["adult", "minor"]}}
    assert merged.prominence == pytest.approx(0.25)
    assert after.texts[0].attached_person == "person-1"
    # the two remaining questions now ask the same thing (person-1 vs person-3): only one is kept
    assert [(a.id, a.a, a.b) for a in after.ambiguities] == [("identity-2", "person-1", "person-3")]
    assert len(before.persons) == 3 and len(before.ambiguities) == 3  # the input is not changed


def test_different_keeps_both_groups_and_only_removes_the_question():
    after = apply_identity_answer(understanding(), "identity-1", "different")
    assert [p.id for p in after.persons] == ["person-1", "person-2", "person-3"]
    assert [a.id for a in after.ambiguities] == ["identity-2", "identity-3"]
    with pytest.raises(PerceptionError):
        apply_identity_answer(understanding(), "identity-9", "same")
    with pytest.raises(PerceptionError):
        apply_identity_answer(understanding(), "identity-1", "maybe")


# ---------- text on a person ----------


def walking(frame: int) -> tuple[float, float, float, float]:
    return (100.0 + 4 * frame, 100.0, 200.0, 500.0)  # a body box moving right


def test_a_name_tag_on_the_chest_is_attached_and_becomes_a_cue_of_the_person():
    tracks = [
        track("T1", "person", range(0, 31), walking),
        track("T2", "face", range(0, 31), lambda f: (170.0 + 4 * f, 110.0, 60.0, 80.0)),
        track("TX1", "text", (0, 10, 20, 30), lambda f: (150.0 + 4 * f, 260.0, 90.0, 24.0), text="김민지 바리스타"),
        track("TX2", "text", (0, 10, 20, 30), (230.0, 300.0, 90.0, 24.0), text="NO PARKING"),  # a sign the person walks past
        track("TX3", "text", (10,), (900.0, 40.0, 80.0, 20.0), text="EXIT"),
    ]
    persons = [PersonGroup(id="person-1", face_tracks=["T2"], body_tracks=["T1"]), PersonGroup(id="person-2", body_tracks=["T9"])]
    texts = [TextItem(id=f"text-{n}", text=t.text or "", tracks=[t.id]) for n, t in enumerate(tracks[2:], 1)]
    attach_text(tracks, persons, texts)
    attach_text(tracks, persons, texts)  # a second call must not repeat the cue
    assert [(t.attached_person, t.kind) for t in texts] == [("person-1", "nametag"), (None, "other"), (None, "other")]
    assert persons[0].cues == ['명찰 글자 "김민지 바리스타"'] and persons[1].cues == []


def test_text_below_a_face_is_attached_when_the_person_has_no_body_track():
    face = track("T1", "face", range(0, 21), (400.0, 100.0, 60.0, 80.0))
    tag = track("TX1", "text", (0, 10, 20), (390.0, 230.0, 70.0, 20.0), text="STAFF")
    cap = track("TX2", "text", (0, 10, 20), (400.0, 60.0, 50.0, 16.0), text="NY")  # above the face: not on the chest
    persons = [PersonGroup(id="person-1", face_tracks=["T1"])]
    texts = [TextItem(id="text-1", text="STAFF", tracks=["TX1"]), TextItem(id="text-2", text="NY", tracks=["TX2"])]
    attach_text([face, tag, cap], persons, texts)
    assert [(t.attached_person, t.kind) for t in texts] == [("person-1", "nametag"), (None, "other")]


def test_a_name_tag_that_sticks_out_of_the_detected_body_box_is_still_attached():
    # the sample clip: the detector's person box ends at x 478, the tag on the chest runs from 407 to 570
    tracks = [
        track("T1", "face", range(0, 60), (309.0, 206.0, 82.0, 104.0)),
        track("T2", "person", range(0, 60), (160.0, 162.0, 318.0, 450.0)),
        track("TX1", "text", (0, 10, 20, 30, 40, 50), (407.0, 477.0, 163.0, 35.0), text="김민지 바리스타"),
        track("TX2", "text", (0, 10, 20, 30, 40, 50), (846.0, 76.0, 227.0, 74.0), text="하늘 카페"),  # a sign far from the person
    ]
    persons = [PersonGroup(id="person-1", face_tracks=["T1"], body_tracks=["T2"])]
    texts = [TextItem(id="text-1", text="김민지 바리스타", tracks=["TX1"]), TextItem(id="text-2", text="하늘 카페", tracks=["TX2"])]
    attach_text(tracks, persons, texts)
    assert [(t.attached_person, t.kind) for t in texts] == [("person-1", "nametag"), (None, "other")]
    assert persons[0].cues == ['명찰 글자 "김민지 바리스타"']


def test_text_read_once_away_from_the_chest_is_not_attached():
    tracks = [track("T1", "person", range(0, 11), (100.0, 100.0, 200.0, 500.0)), track("TX1", "text", (5,), (120.0, 520.0, 60.0, 20.0), text="PARKINC")]
    persons, texts = [PersonGroup(id="person-1", body_tracks=["T1"])], [TextItem(id="text-1", text="PARKINC", tracks=["TX1"])]
    attach_text(tracks, persons, texts)
    assert texts[0].attached_person is None and persons[0].cues == []


# ---------- low-threshold detection ----------


def test_redetector_counts_detections_whose_centre_is_inside_a_region():
    regions = [Region(100, 100, 100, 100, "box", "d1"), Region(400, 100, 100, 200, "ellipse", "d2")]
    boxes = [
        (120.0, 120.0, 20.0, 20.0),  # inside the box
        (190.0, 190.0, 40.0, 40.0),  # centre outside the box (210, 210)
        (402.0, 102.0, 10.0, 10.0),  # in the ellipse's bounding box but outside the ellipse (corner)
        (440.0, 190.0, 20.0, 20.0),  # inside the ellipse
    ]
    assert count_inside(boxes, regions) == 2
    assert count_inside(boxes, []) == 0


def test_redetector_scales_detections_back_to_the_frame_it_was_given():
    seen: list[tuple[int, int]] = []

    def finder(image: np.ndarray):
        seen.append(image.shape[:2])
        return [(100.0, 50.0, 20.0, 20.0)]  # centre (110, 60) in the reduced image

    check = redetector(profile(), finders=[finder, lambda image: []])
    frame = np.zeros((1280, 2560, 3), dtype=np.uint8)  # reduced by half to the proxy short side of 640
    assert check(frame, 7, [Region(200, 100, 40, 40, "box")]) == 1  # centre (220, 120) in frame pixels
    assert check(frame, 7, [Region(90, 40, 40, 40, "box")]) == 0
    assert seen == [(640, 1280), (640, 1280)]
    assert check(frame, 8, []) == 0 and len(seen) == 2  # nothing redacted on the frame: no detector call


def test_face_finder_returns_boxes_in_the_pixels_of_the_image_it_was_given():
    class Fake:
        def __init__(self):
            self.calls: list[tuple[tuple[int, int], float]] = []

        def detect(self, image: np.ndarray, min_score: float):
            self.calls.append((image.shape[:2], min_score))
            return [((1.0, 2.0, 3.0, 4.0), 0.31), ((image.shape[1] - 10.0, 5.0, 10.0, 10.0), 0.9)]

    fake = Fake()
    find = face_finder(profile(), detector=fake)  # type: ignore[arg-type]
    assert find(np.zeros((90, 160, 3), dtype=np.uint8)) == [(1.0, 2.0, 3.0, 4.0), (150.0, 5.0, 10.0, 10.0)]
    assert find(np.zeros((360, 640, 3), dtype=np.uint8))[1] == (630.0, 5.0, 10.0, 10.0)
    assert [size for size, _ in fake.calls] == [(90, 160), (360, 640)]
    assert all(score < 0.5 for _, score in fake.calls)  # below the analysis threshold: recall first


# ---------- still identifiable after redaction? ----------


class FakeFaces:
    """A face detector that 'sees' what the test painted: frames are told apart by their top-left pixel."""

    def __init__(self, rows_by_marker: dict[int, list[tuple[float, float, float, float]]]):
        self.rows_by_marker = rows_by_marker

    def rows(self, image: np.ndarray, min_score: float) -> np.ndarray:
        boxes = self.rows_by_marker.get(int(image[0, 0, 0]), [])
        return np.array([[*box, *([0.0] * 10), 0.9] for box in boxes], dtype=np.float32).reshape(-1, 15)


class FakeEmbedder:
    def __init__(self, angles: dict[tuple[int, int], float | None]):
        self.angles = angles  # (frame marker, box x) → direction of the unit feature; None = cannot be embedded

    def feature(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        angle = self.angles[(int(image[0, 0, 0]), int(row[0]))]
        if angle is None:
            raise PerceptionError("no feature")
        return unit(angle)


class FakeReader:
    """Reads the text the test assigned to a crop's top-left pixel value; counts its calls."""

    def __init__(self, lines: dict[int, str], blocks: dict[int, list[str]] | None = None):
        self.lines, self.blocks, self.calls = lines, blocks or {}, 0

    def read_line(self, image: np.ndarray) -> tuple[str, float]:
        self.calls += 1
        text = self.lines.get(int(image[0, 0, 0]), "")
        return text, 0.95 if text else 0.0

    def read(self, image: np.ndarray):
        self.calls += 1
        marker = int(image[image.shape[0] // 2, image.shape[1] // 2, 0])  # the crop sits in the middle of the padded canvas
        return [((0.0, 0.0, 10.0, 10.0), text, 0.9) for text in self.blocks.get(marker, [])]


def frames(marker_out: int = 1, marker_in: int = 2) -> tuple[np.ndarray, np.ndarray]:
    output, original = np.full((360, 640, 3), 100, dtype=np.uint8), np.full((360, 640, 3), 100, dtype=np.uint8)
    output[0, 0], original[0, 0] = marker_out, marker_in
    return output, original


def checker(faces=None, embedder=None, reader=None, **known) -> OutputChecker:
    return OutputChecker(faces or FakeFaces({}), embedder or FakeEmbedder({}), reader or FakeReader({}), 640.0, **known)  # type: ignore[arg-type]


def test_a_mosaicked_face_counts_only_when_it_still_matches_the_original_face():
    output, original = frames()
    regions = [Region(90, 90, 80, 100, "ellipse", "D-1"), Region(290, 90, 80, 100, "ellipse", "D-2"), Region(490, 90, 80, 100, "ellipse", "D-3")]
    faces = FakeFaces({
        1: [(100.0, 100.0, 60.0, 80.0), (300.0, 100.0, 60.0, 80.0), (500.0, 100.0, 60.0, 80.0), (20.0, 250.0, 40.0, 50.0), (400.0, 250.0, 40.0, 50.0)],
        2: [(102.0, 101.0, 60.0, 80.0), (301.0, 99.0, 60.0, 80.0), (20.0, 250.0, 40.0, 50.0), (200.0, 250.0, 40.0, 50.0)],
    })  # fmt: skip
    still = math.degrees(math.acos(IDENTIFIABLE_SIMILARITY + 0.05))
    gone = math.degrees(math.acos(IDENTIFIABLE_SIMILARITY - 0.05))
    embedder = FakeEmbedder({(1, 100): still, (2, 102): 0.0, (1, 300): gone, (2, 301): 0.0, (1, 20): 0.0, (2, 20): 0.0})
    check = checker(faces, embedder)
    # face 1 still matches; face 2 is detected but no longer matches; face 3 has no counterpart in the original
    # (a mosaic artefact); the face at x 20 is outside every region and is never embedded
    assert check.inside(output, original, 0, regions) == 1
    assert check.inside(output, original, 0, regions[1:]) == 0
    assert check.inside(output, original, 0, []) == 0
    embedder.angles[(1, 100)] = None  # a face that cannot be embedded is not counted
    assert check.inside(output, original, 0, regions) == 0
    inside_note, scan_note = check.summary()  # the closest match seen is reported with the bound it is held against
    assert inside_note == f"얼굴 대조 최대 유사도 {IDENTIFIABLE_SIMILARITY + 0.05:.2f} (기준 {IDENTIFIABLE_SIMILARITY:.2f} 미만)"
    assert scan_note == "대조할 얼굴 · 글자가 없음" and check.leaks == []  # `inside` never reports a leak elsewhere


def test_a_redacted_text_counts_only_when_most_of_the_original_reading_comes_back():
    output, original = frames()
    tag, sign, gone, body, face = (100, 50, 160, 40), (300, 50, 120, 40), (460, 50, 120, 40), (20, 120, 150, 230), (300, 150, 60, 80)
    for marker, (x, y, w, h) in zip((50, 60, 70, 80, 90), (tag, sign, gone, body, face)):
        original[y : y + h, x : x + w] = marker
        output[y : y + h, x : x + w] = marker + 100
    reader = FakeReader({50: "김민지 바리스타", 150: "김민지 바리스", 60: "하늘 카페", 160: "", 170: "오늘의 메뉴", 80: "STAFF", 180: "STAFF", 90: "A1", 190: "A1"})
    check = checker(reader=reader)
    regions = [
        Region(*tag, "box", "D-1", 2.5),  # still readable: 6 of 7 characters
        Region(*tag, "box", "D-9", 2.5),  # the same pixels redacted by a second decision: read once
        Region(*sign, "box", "D-2", 2.5),  # original readable, output not
        Region(*gone, "box", "D-3", 2.5),  # nothing readable in the original: the output is not even read
        Region(*body, "box", "D-4"),  # a large upright box is a body, not a text
        Region(*face, "ellipse", "D-5"),  # ellipses are faces
    ]
    assert check.inside(output, original, 3, regions) == 1
    assert reader.calls == 2 + 2 + 2  # tag: original + output · sign: original + output · gone: line + block of the original only
    assert looks_like_text(regions[0], 640 * 360) and not looks_like_text(regions[4], 640 * 360) and not looks_like_text(regions[5], 640 * 360)
    assert check.summary()[0].startswith("글자 재인식 최대 일치 0.9") and f"기준 {READABLE_SIMILARITY:.2f} 미만" in check.summary()[0]


def test_a_two_line_plate_is_read_as_a_block_when_the_line_reader_finds_nothing():
    output, original = frames()
    original[200:260, 100:200], output[200:260, 100:200] = 55, 155
    reader = FakeReader({}, blocks={55: ["서울 12", "가 3456"], 155: ["가 3456"]})
    check = checker(reader=reader)
    assert check.inside(output, original, 0, [Region(100, 200, 100, 60, "box", "D-1", 2.5)]) == 1
    reader.blocks[155] = ["7"]
    assert check.inside(output, original, 0, [Region(100, 200, 100, 60, "box", "D-1", 2.5)]) == 0


# ---------- does anything redacted show anywhere else on the frame? ----------


class SceneReader:
    """Reads whole frames: the texts the test put on a frame (told apart by its top-left pixel), in its pixels."""

    def __init__(self, found: dict[int, list[tuple[tuple[float, float, float, float], str, float]]]):
        self.found, self.read_frames = found, []

    def read(self, image: np.ndarray):
        self.read_frames.append(int(image[0, 0, 0]))
        return self.found.get(int(image[0, 0, 0]), [])


def small_frame(marker: int) -> np.ndarray:
    """An output frame reduced to half size for the scan (`scale` 2.0 turns its pixels into original pixels)."""
    image = np.full((180, 320, 3), 100, dtype=np.uint8)
    image[0, 0] = marker
    return image


def never() -> tuple[np.ndarray, np.ndarray]:
    raise AssertionError("the full-size frames are only fetched when a face has to be looked at")


def test_a_redacted_persons_face_outside_every_region_is_a_leak():
    match = math.degrees(math.acos(IDENTIFIABLE_SIMILARITY + 0.05))
    other = math.degrees(math.acos(IDENTIFIABLE_SIMILARITY - 0.05))
    faces = FakeFaces({
        1: [(50.0, 50.0, 30.0, 40.0), (150.0, 50.0, 30.0, 40.0), (250.0, 50.0, 30.0, 40.0)],  # one face of three people
        2: [(50.0, 50.0, 30.0, 40.0)],
        3: [],
    })  # fmt: skip
    # features are looked up by (marker of the full-size output, x of the face in original pixels)
    embedder = FakeEmbedder({(1, 100): match, (1, 300): other, (1, 500): None, (2, 100): 180.0 - match})
    references = {"D-1": ("인물 1", [unit(270), unit(0)]), "D-4": ("인물 2", [unit(180)])}  # far apart: nobody matches both
    check = checker(faces, embedder, references=references)
    output, original = frames(marker_out=1)

    check.scan(7, small_frame(1), 2.0, lambda: (output, original), [])
    # the first face is person 1 (the closest of the reference faces decides); the second is just below the bound
    # for everyone; the third cannot be embedded
    assert check.leaks == [Leak(frame=7, ref="D-1", box=(100.0, 100.0, 60.0, 80.0), what=f"인물 1 얼굴 (유사도 {IDENTIFIABLE_SIMILARITY + 0.05:.2f})", span=(7, 7))]
    assert isinstance(check.leaks[0], Leak) and check.worst_seen == pytest.approx(IDENTIFIABLE_SIMILARITY + 0.05)

    # the same face under a mosaic of its own decision is not a leak: `inside` judges what a region covers
    covered = [Region(90, 90, 80, 100, "ellipse", "D-1")]
    check.scan(8, small_frame(1), 2.0, lambda: (output, original), covered)
    assert [leak.frame for leak in check.leaks] == [7]
    # a region somewhere else does not excuse it
    check.scan(9, small_frame(1), 2.0, lambda: (output, original), [Region(400, 200, 80, 100, "ellipse", "D-1")])
    assert [(leak.frame, leak.ref) for leak in check.leaks] == [(7, "D-1"), (9, "D-1")]

    # another frame shows person 2
    output_2, _ = frames(marker_out=2)
    check.scan(12, small_frame(2), 2.0, lambda: (output_2, original), [])
    assert [(leak.frame, leak.ref, leak.span) for leak in check.leaks][2:] == [(12, "D-4", (12, 12))] and check.leaks[2].what.startswith("인물 2 얼굴")
    check.scan(13, small_frame(3), 2.0, never, [])  # no face on the frame: nothing is fetched or compared
    assert len(check.leaks) == 3
    inside_note, scan_note = check.summary()
    assert inside_note == "" and scan_note.startswith("가린 인물 2명의 원본 얼굴과 모든 프레임의 얼굴을 대조") and "최대 유사도 0.55" in scan_note


def test_faces_are_not_compared_when_no_person_was_redacted():
    class Unused:
        def rows(self, image, min_score):
            raise AssertionError("no face reference: the detector is not run")

    check = checker(Unused())
    check.scan(0, small_frame(1), 2.0, never, [])
    assert check.leaks == [] and check.summary() == ("", "대조할 얼굴 · 글자가 없음")


def test_a_face_shown_where_the_plan_leaves_it_visible_is_not_a_leak():
    """A decision that redacts only part of the time (a requirement limited in time, a range the reviewer
    shortened) leaves the face visible on the other frames on purpose; the scan must not report those."""
    match = math.degrees(math.acos(IDENTIFIABLE_SIMILARITY + 0.05))
    faces = FakeFaces({1: [(50.0, 50.0, 30.0, 40.0)]})
    embedder = FakeEmbedder({(1, 100): match})
    check = checker(faces, embedder, references={"D-1": ("인물 1", [unit(0)])}, allowed={"D-1": {0, 1, 70}})
    output, original = frames(marker_out=1)
    for frame in (0, 70):  # visible as planned
        check.scan(frame, small_frame(1), 2.0, lambda: (output, original), [])
    assert check.leaks == []
    check.scan(25, small_frame(1), 2.0, lambda: (output, original), [])  # inside the redacted stretch, outside its region
    check.scan(90, small_frame(1), 2.0, lambda: (output, original), [])  # where the tracker never saw the face at all
    assert [(leak.frame, leak.ref) for leak in check.leaks] == [(25, "D-1"), (90, "D-1")]


def test_a_redacted_text_read_outside_the_regions_is_a_leak_until_the_neighbouring_checked_frames():
    name = "김민지 바리스타"
    assert EXPOSED_SIMILARITY > READABLE_SIMILARITY  # elsewhere on the frame only a near-exact reading is the redacted text
    reader = SceneReader({
        20: [
            ((60.0, 40.0, 80.0, 12.0), name, 0.9),  # in the clear, away from the region
            ((200.0, 40.0, 40.0, 12.0), "EXIT", 0.95),  # some other text
            ((60.0, 100.0, 80.0, 12.0), name, 0.3),  # too uncertain a reading to count
            ((205.0, 105.0, 80.0, 12.0), name, 0.9),  # its centre is inside the region: `inside` judges that one
            ((60.0, 150.0, 90.0, 12.0), "김민지 바리스다", 0.8),  # one character misread: still the name
            ((200.0, 150.0, 110.0, 12.0), "오늘의 김민지 바리스타 추천", 0.8),  # the name inside a longer reading
            ((10.0, 10.0, 30.0, 12.0), "김민", 0.9),  # a fragment is not the name
        ],
        10: [((60.0, 40.0, 80.0, 12.0), name, 0.9)],
        30: [((60.0, 40.0, 80.0, 12.0), name, 0.9)],
        21: [((60.0, 40.0, 80.0, 12.0), name, 0.9)],
    })  # fmt: skip
    check = checker(reader=reader, texts={"D-3": ('명찰 "김민지 바리스타"', name)}, text_frames=[30, 10, 20], frame_count=40)
    region = Region(400, 200, 180, 40, "box", "D-3", 2.5)  # original pixels; the frame the reader sees is half that size

    check.scan(20, small_frame(20), 2.0, never, [region])
    assert reader.read_frames == [20]
    # boxes come back in original pixels; the repair must hold from the frame after the previous checked frame
    # to the frame before the next one, because nothing in between was read
    assert check.leaks == [
        Leak(frame=20, ref="D-3", box=(120.0, 80.0, 160.0, 24.0), what='명찰 "김민지 바리스타" 판독됨', span=(11, 29)),
        Leak(frame=20, ref="D-3", box=(120.0, 300.0, 180.0, 24.0), what='명찰 "김민지 바리스타" 판독됨', span=(11, 29)),
        Leak(frame=20, ref="D-3", box=(400.0, 300.0, 220.0, 24.0), what='명찰 "김민지 바리스타" 판독됨', span=(11, 29)),
    ]
    check.scan(21, small_frame(21), 2.0, never, [])  # not one of the frames to read: OCR is not run, whatever is there
    check.scan(22, small_frame(22), 2.0, never, [])
    assert reader.read_frames == [20] and len(check.leaks) == 3
    check.scan(10, small_frame(10), 2.0, never, [])  # the first checked frame answers for everything before it …
    check.scan(30, small_frame(30), 2.0, never, [])  # … and the last for everything up to the end of the video
    assert [(leak.frame, leak.span) for leak in check.leaks[3:]] == [(10, (0, 19)), (30, (21, 39))]
    assert reader.read_frames == [20, 10, 30]
    assert check.summary() == ("", "가린 글자 1건을 3개 프레임 전체에서 판독")


def test_the_checker_is_built_from_what_the_plan_redacts(monkeypatch):
    """Faces of people whose face or body gets a mosaic, texts and plates that get one; kept targets and file
    metadata are not looked for. Redacted text is read on the frames around each range and on an even spread."""
    face_box = (400.0, 100.0, 120.0, 160.0)
    known = VideoUnderstanding(
        tracks=[
            track("T1", "face", range(0, 50), face_box),
            track("T2", "face", range(60, 100), (100.0, 100.0, 60.0, 80.0)),
            track("T3", "person", range(60, 100), (60.0, 80.0, 200.0, 400.0)),
            track("TX1", "text", (20, 30, 40), (500.0, 300.0, 160.0, 30.0), text="김민지 바리스타"),
            track("TX2", "text", (0, 10), (700.0, 40.0, 120.0, 30.0), text="하늘 카페"),
            track("TP1", "plate", range(70, 90), (800.0, 400.0, 90.0, 30.0)),
        ],
        persons=[PersonGroup(id="person-1", face_tracks=["T1"]), PersonGroup(id="person-2", face_tracks=["T2"], body_tracks=["T3"]), PersonGroup(id="person-3", body_tracks=["T9"])],
        texts=[TextItem(id="text-1", text="김민지 바리스타", tracks=["TX1"]), TextItem(id="text-2", text="하늘 카페", tracks=["TX2"])],
        plates=[Plate(id="plate-1", tracks=["TP1"], text="12가 3456"), Plate(id="plate-2", tracks=[], text=None)],
        sample_interval=0.1,
    )
    timeline = Timeline.build([i / 10 for i in range(100)], 10.0, 0.1, [(0, 99)])
    known.visible = visible_ranges(known, timeline)

    def decide(number: int, target: str, scope: str, method: str) -> Decision:
        ranges = known.visible.get(f"{target}:{scope}", [])
        shape = None if scope == "metadata" else "ellipse" if scope == "face" else "box"
        return Decision(id=f"D-{number}", target_id=target, target_label=f"대상 {target}", scope=scope, method=method, shape=shape, ranges=ranges, rationale="")

    decisions = [
        decide(1, "person-1", "face", "mosaic"),
        decide(2, "person-2", "face", "keep"),
        decide(3, "text-1", "text", "mosaic"),
        decide(4, "text-2", "text", "keep"),
        decide(5, "plate-1", "plate", "mosaic"),
        decide(6, "meta-location", "metadata", "remove"),
        decide(7, "person-3", "body", "mosaic"),  # never seen from the front: there is no face to compare
        decide(8, "plate-2", "plate", "mosaic"),  # a plate nobody could read cannot be looked for by its text
    ]
    read: list[tuple[int, str]] = []

    def read_frame(asset, index: int, source: str = "proxy") -> np.ndarray:
        read.append((index, source))
        image = np.full((500, 1000, 3), 100, dtype=np.uint8)
        image[0, 0] = 9
        return image

    monkeypatch.setattr(video, "read_frame", read_frame)
    faces = FakeFaces({9: [face_box, (100.0, 100.0, 60.0, 80.0)]})
    made = output_checker(profile(), asset(), known, decisions, timeline, faces=faces, embedder=FakeEmbedder({(9, 400): 30.0}), reader=FakeReader({}))  # type: ignore[arg-type]
    assert isinstance(made, OutputChecker) and made.frame_count == 100 and made.leaks == []
    # reference faces come from the original at full size, from moments at least half a second apart
    assert list(made.references) == ["D-1"] and made.references["D-1"][0] == "대상 person-1"
    assert len(made.references["D-1"][1]) == REFERENCE_FACES and all(np.allclose(v, unit(30.0)) for v in made.references["D-1"][1])
    assert {source for _, source in read} == {"original"} and len({index for index, _ in read}) == REFERENCE_FACES
    assert all(abs(a - b) >= 5 for i, (a, _) in enumerate(read) for b, _ in read[:i])  # 10 fps: five frames is half a second
    assert made.texts == {"D-3": ("대상 text-1", "김민지 바리스타"), "D-5": ("대상 plate-1", "12가 3456")}
    first, last = timeline.frames(known.visible["text-1:text"])[0], timeline.frames(known.visible["text-1:text"])[-1]
    assert (first, last) == (18, 42)  # samples at 2.0–4.0 s held for the pad on both sides
    assert {first - 1, first, last, last + 1} <= set(made.text_frames)  # where the name appears and disappears
    assert {0, 99} <= set(made.text_frames) and len(made.text_frames) <= TEXT_SCAN_FRAMES and made.text_frames == sorted(set(made.text_frames))
    gaps = [b - a for a, b in zip(made.text_frames, made.text_frames[1:])]
    assert max(gaps) <= 2 * 100 / (TEXT_SCAN_FRAMES - 8)  # the rest is spread over the whole video

    nothing = output_checker(profile(), asset(), known, [d for d in decisions if d.method != "mosaic"], timeline, faces=faces, embedder=FakeEmbedder({}), reader=FakeReader({}))  # type: ignore[arg-type]
    assert nothing.references == {} and nothing.texts == {} and nothing.text_frames == []


# ---------- unsupported parts ----------


@pytest.mark.parametrize(
    ("override", "run"),
    [
        ({"ocr": "gemini-3.8-flash"}, lambda p: recognize_text(asset(), [], [], p)),
        ({"face_detector": "retinaface-mnv2"}, lambda p: detect_and_track(asset(), [], [], p)),
        ({"face_detector": "scrfd-2.5g"}, lambda p: face_finder(p)),
        ({"object_detector": "yolo26n"}, lambda p: detect_and_track(asset(), [], [], p)),
        ({"object_detector": "dfine-s"}, lambda p: detect_and_track(asset(), [], [], p)),
        ({"face_embedding": "adaface-ir50"}, lambda p: group_identities(asset(), [], p)),
        ({"face_embedding": "arcface-w600k"}, lambda p: group_identities(asset(), [], p)),
    ],
)
def test_parts_this_backend_cannot_run_fail_with_a_korean_message(override, run):
    chosen = profile(**override)
    with pytest.raises(PerceptionError) as error:
        run(chosen)
    ((slot, candidate),) = ((f"perception.{k}", v) for k, v in override.items())
    assert "구현되지 않은 어댑터" in str(error.value) and slot in str(error.value) and candidate in str(error.value)


# ---------- real models on the team clip (skipped when something is missing) ----------


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    for package in ("scenedetect", "open_image_models", "trackers", "rapidocr"):
        pytest.importorskip(package)
    if not CLIP.is_file() or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("team clip or ffmpeg is not available")
    missing = [name for name in ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx") if not (MODELS_DIR / name).is_file()]
    if missing:
        pytest.skip(f"model files not in models/: {missing} (SFace is fetched on the first real run)")
    prepared = video.prepare(CLIP, tmp_path_factory.mktemp("aba"))
    return prepared, video.load_times(prepared), profile(object_detector="rfdetr-nano-onnx")  # ONNX build: no torch start-up


def test_the_clip_has_three_shots(clip):
    prepared, times, chosen = clip
    found = find_segments(prepared, times, chosen)
    assert [(s.id, s.start_frame, s.end_frame) for s in found] == [("S1", 0, 299), ("S2", 300, 599), ("S3", 600, 899)]


def test_people_are_tracked_within_shots_and_recognised_again_in_a_later_shot(clip):
    prepared, times, chosen = clip
    # one second of each shot keeps the test short: person A's group (three walkers), the café pair, the walkers again
    shots = [SegmentInfo(id=f"S{n}", start=times[a], end=times[b], start_frame=a, end_frame=b) for n, (a, b) in enumerate([(0, 29), (300, 329), (600, 629)], 1)]
    tracks = detect_and_track(prepared, times, shots, chosen)
    assert tracks == detect_and_track(prepared, times, shots, chosen)  # same input, same ids and boxes
    assert [t.id for t in tracks] == [f"T{n}" for n in range(1, len(tracks) + 1)]
    per_shot = {s.id: sorted(t.kind for t in tracks if t.segment == s.id) for s in shots}
    assert per_shot == {"S1": ["face"] * 3 + ["person"] * 3, "S2": ["face"] * 2 + ["person"] * 2, "S3": ["face"] * 3 + ["person"] * 3}
    for t in tracks:
        shot = next(s for s in shots if s.id == t.segment)
        assert all(shot.start_frame <= p.f <= shot.end_frame and p.t == times[p.f] for p in t.points)
        assert all(0 <= p.box[0] and p.box[0] + p.box[2] <= prepared.meta.display_width + 0.1 for p in t.points)
    persons, ambiguities = group_identities(prepared, tracks, chosen)
    assert len(persons) == 5 and ambiguities == []
    again = [p for p in persons if {tracks_by_id(tracks)[i].segment for i in p.face_tracks} == {"S1", "S3"}]
    assert len(again) == 3 and all(len(p.face_tracks) == 2 and len(p.body_tracks) == 2 for p in again)
    text_tracks, texts = recognize_text(prepared, times, shots, chosen)
    assert "233 Banker Street" in [t.text for t in texts]
    assert all(t.id.startswith("TX") and t.kind == "text" for t in text_tracks) and len(text_tracks) == len(texts)


def tracks_by_id(tracks: list[Track]) -> dict[str, Track]:
    return {t.id: t for t in tracks}


def test_face_finder_gives_the_same_answer_from_several_threads_and_image_sizes(clip):
    import cv2

    prepared, _, chosen = clip
    find = face_finder(chosen)
    full = video.read_frame(prepared, 100)
    images = [full, cv2.resize(full, (640, 360)), full[:, : full.shape[1] // 2], cv2.resize(full, (800, 450))] * 4
    expected = [find(image) for image in images]
    assert len(expected[0]) >= 3 and all(x + w <= full.shape[1] and y + h <= full.shape[0] for x, y, w, h in expected[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(find, images)) == expected
