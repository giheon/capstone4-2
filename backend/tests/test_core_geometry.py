"""Which frames a redaction covers (core/geometry.py): the timeline's half-open, frame-aligned ranges, the
coverage of a target between and around its analysis samples, and the regions the renderer is given.

Everything here is arithmetic on made-up tracks: a 10 s clip at 30 fps (300 frames) analysed on every 5th frame,
unless a test says otherwise.
"""
import json
import random

import pytest

from privframe.core import understanding as views
from privframe.core.domain import Decision, PersonGroup, Plate, Proxy, SegmentInfo, TextItem, Track, TrackPoint, VideoAsset, VideoMeta, VideoUnderstanding, merge_ranges
from privframe.core.geometry import (
    BRIDGE_S,
    MIN_PAD_S,
    SNAP_S,
    SPATIAL_SCALE,
    STRENGTH,
    Region,
    Timeline,
    applied_ranges,
    build_regions,
    cover,
    expected_frames,
    regions_from_json,
    regions_to_json,
    seen,
    visible_ranges,
)

FPS, FRAMES = 30, 300
INTERVAL = 5 / FPS  # seconds between analysis samples
WIDTH, HEIGHT = 1920, 1080
FACE = (400.0, 200.0, 80.0, 100.0)


def clip(frames: int = FRAMES, fps: float = FPS, interval: float = INTERVAL, scenes=()) -> Timeline:
    return Timeline.build([i / fps for i in range(frames)], frames / fps, interval, scenes or [(0, frames - 1)])


def track(track_id: str, kind: str, frames, box=FACE, fps: float = FPS, text: str | None = None) -> Track:
    """A track with a sample on each of `frames`; `box` is a fixed box or a function of the frame index."""
    points = [TrackPoint(f=f, t=f / fps, box=box(f) if callable(box) else box, score=0.9) for f in frames]
    return Track(id=track_id, kind=kind, segment="S1", points=points, text=text)


def one_person(*tracks: Track, timeline: Timeline | None = None) -> VideoUnderstanding:
    """person-1 owns every track given: face tracks as its face, person tracks as its body."""
    person = PersonGroup(id="person-1", face_tracks=[t.id for t in tracks if t.kind == "face"], body_tracks=[t.id for t in tracks if t.kind == "person"])
    return known(list(tracks), persons=[person], timeline=timeline)


def known(tracks: list[Track], *, persons=(), texts=(), plates=(), timeline: Timeline | None = None) -> VideoUnderstanding:
    """An understanding with its coverage stored, as the perceive step leaves it."""
    timeline = timeline or clip()
    out = VideoUnderstanding(tracks=tracks, persons=list(persons), texts=list(texts), plates=list(plates), sample_interval=timeline.interval)
    out.visible = visible_ranges(out, timeline)
    return out


def decision(identifier: str, target: str, scope: str, ranges, method: str = "mosaic", shape: str | None = None) -> Decision:
    default = None if scope == "metadata" else "ellipse" if scope == "face" else "box"
    return Decision(id=identifier, target_id=target, target_label=target, scope=scope, method=method, shape=shape or default, ranges=ranges, rationale="")


def at(frame: int) -> float:
    """A frame's start as a stored range boundary (milliseconds)."""
    return round(frame / FPS, 3)


def frames_of(regions: dict[int, list[Region]], ref: str) -> list[int]:
    return sorted(frame for frame, items in regions.items() if any(region.ref == ref for region in items))


# ---------- timeline: half-open ranges aligned to frames ----------


def test_frames_and_ranges_are_inverse_and_ranges_are_half_open():
    timeline = clip()
    frames = [3, 4, 5, 9, 20, 21, 299]
    ranges = timeline.ranges(frames)
    assert ranges == [(0.1, 0.2), (0.3, 0.333), (0.667, 0.733), (9.967, 10.0)]  # each ends where the next frame starts
    assert timeline.frames(ranges) == frames
    assert timeline.ranges([5, 3, 4, 4]) == [(0.1, 0.2)] and timeline.ranges([]) == [] and timeline.frames([]) == []
    assert timeline.frames([(0.1, 0.2)]) == [3, 4, 5]  # the frame that starts at 0.2 is not in [0.1, 0.2)
    assert timeline.frames([(0.1, 0.2), (0.2, 0.3)]) == [3, 4, 5, 6, 7, 8]  # so two ranges that touch share no frame …
    assert timeline.ranges([3, 4, 5, 6, 7, 8]) == [(0.1, 0.3)]  # … and leave none out
    assert timeline.frames(json.loads(json.dumps(ranges))) == frames  # as stored in a plan


def test_the_last_frame_belongs_to_a_range_that_ends_at_the_duration():
    timeline = clip()
    assert timeline.count == 300 and timeline.duration == 10.0
    assert timeline.end(0) == pytest.approx(1 / 30) and timeline.end(299) == 10.0
    assert timeline.ranges([299]) == [(9.967, 10.0)] and timeline.ranges(range(300)) == [(0.0, 10.0)]
    assert timeline.frames([(0.0, 10.0)]) == list(range(300))
    assert timeline.frames([(0.0, 9.967)])[-1] == 298  # a range that ends where the last frame starts does not contain it
    assert timeline.frames([(0.0, 9.9)])[-1] == 296  # "9.9" leaves out three frames of a video that ends at 10.0
    # a file whose stated duration is not after its last timestamp still gives the last frame a moment of its own
    short = Timeline.build([i / 30 for i in range(300)], 9.9, INTERVAL)
    assert short.duration > short.times[-1] and short.frames(short.ranges([299])) == [299]
    assert short.frames(short.ranges(range(300))) == list(range(300))


@pytest.mark.parametrize("fps", [30, 60, 30000 / 1001, 24])
def test_millisecond_rounding_neither_drops_nor_adds_a_frame(fps):
    """Ranges are stored in milliseconds; at 60 fps a frame lasts 16.7 ms, so a boundary rounded the wrong way
    would lose the first or last frame of a redaction (or add one)."""
    count = 96
    timeline = clip(count, fps)
    for first in range(count):
        for last in range(first, count):
            (only,) = timeline.ranges(range(first, last + 1))
            assert only == (round(first / fps, 3), round((last + 1) / fps, 3))
            assert timeline.frames([only]) == list(range(first, last + 1))
    # the same through a plan: aligned ranges stay what they are, however often they are aligned or snapped again
    stored = timeline.ranges([*range(7, 31), *range(40, 96)])
    assert timeline.align(stored) == stored and timeline.snap(stored, stored) == stored
    assert timeline.frames(stored) == [*range(7, 31), *range(40, 96)]


def test_align_widens_a_range_to_the_whole_frames_it_touches():
    timeline = clip()
    assert timeline.align([(0.05, 0.11)]) == [(0.033, 0.133)]  # frames 1–3 are each on screen at some moment of it
    assert timeline.frames(timeline.align([(0.05, 0.11)])) == [1, 2, 3]
    assert timeline.align([(0.05, 0.06)]) == [(0.033, 0.067)]  # a moment inside one frame: that frame
    assert timeline.align([(0.1, 0.2)]) == [(0.1, 0.2)]  # already on frame boundaries: the frame starting at 0.2 stays out
    assert timeline.align([(0.0, 9.9)]) == [(0.0, 9.9)]
    assert timeline.align([(9.95, 12.0)]) == [(9.933, 10.0)]  # past the end: up to the last frame
    assert timeline.align([(0.05, 0.11), (0.12, 0.2)]) == [(0.033, 0.2)]  # ranges that widen into each other join
    assert timeline.align([(3.0, 3.001)]) == [] and timeline.align([(4.0, 3.0)]) == [] and timeline.align([]) == []
    rng = random.Random(4)
    for _ in range(200):  # widening only: every frame whose time is inside the range stays inside
        start = rng.uniform(0, 9.5)
        given = [(start, start + rng.uniform(0.01, 2.0))]
        aligned = timeline.frames(timeline.align(given))
        assert set(timeline.frames(given)) <= set(aligned) and len(aligned) <= len(timeline.frames(given)) + 2


def test_a_range_wholly_after_the_video_touches_no_frame():
    """Fails on purpose until core/geometry.py is fixed: `align` (and so `snap`, review edits and the preview)
    puts a range that starts at or after the end of the video onto the last frame, although `frames` finds no
    frame in it and no frame is on screen then."""
    timeline = clip()
    assert timeline.frames([(11.0, 12.0)]) == []
    assert timeline.align([(-3.0, -1.0)]) == []  # before the video: nothing, as it should be
    assert timeline.align([(11.0, 12.0)]) == []
    assert timeline.align([(10.0, 12.0)]) == [] and timeline.snap([(11.0, 12.0)], [(0.0, 10.0)]) == []


def test_snap_moves_a_boundary_near_an_appearance_outward_onto_it():
    timeline = clip()
    whole = [(0.0, 10.0)]
    assert timeline.snap([(0.0, 9.9)], whole) == [(0.0, 10.0)]  # "9.9" for a target that is there until the end
    assert timeline.snap([(0.4, 9.6)], whole) == [(0.0, 10.0)]  # both ends, within SNAP_S
    assert timeline.snap([(SNAP_S, 10.0 - SNAP_S)], whole) == [(0.0, 10.0)]
    assert timeline.snap([(0.6, 9.4)], whole) == [(0.6, 9.4)]  # farther away: the range means what it says
    appearances = [(2.0, 4.0), (6.0, 8.0)]
    assert timeline.snap([(2.3, 3.8)], appearances) == [(2.0, 4.0)]
    assert timeline.snap([(2.3, 7.7)], appearances) == [(2.0, 8.0)]  # the start of one appearance, the end of another
    assert timeline.snap([(2.3, 5.0)], appearances) == [(2.0, 5.0)]  # only the boundary that is near one
    assert timeline.snap([(4.6, 5.4)], appearances) == [(4.6, 5.4)]  # between two appearances, near neither end
    assert timeline.snap([(2.31, 3.79)], []) == [(2.3, 3.8)]  # nothing known about the target: aligned to frames only
    assert timeline.snap([(3.0, 3.001)], whole) == []


def test_snap_never_makes_a_range_smaller():
    timeline = clip()
    assert timeline.snap([(1.8, 4.3)], [(2.0, 4.0)]) == [(1.8, 4.3)]  # wider than the appearance: not pulled in to it
    assert timeline.snap([(1.8, 3.9)], [(2.0, 4.0)]) == [(1.8, 4.0)]
    rng = random.Random(7)
    for _ in range(300):
        visible = sorted((round(a, 3), round(a + rng.uniform(0.2, 3.0), 3)) for a in (rng.uniform(0, 9) for _ in range(rng.randint(0, 3))))
        start = rng.uniform(0, 9.5)
        given = [(round(start, 2), round(start + rng.uniform(0.05, 4.0), 2))]
        snapped, aligned = set(timeline.frames(timeline.snap(given, visible))), set(timeline.frames(timeline.align(given)))
        assert aligned <= snapped  # nothing the range said is lost
        assert snapped == set(range(min(snapped), max(snapped) + 1))  # and one range stays one range


def test_scenes_and_padding_come_from_the_analysis():
    timeline = clip(scenes=[(0, 149), (150, 299)])
    assert timeline.scenes == ((0, 149), (150, 299))
    assert timeline.scene(0) == timeline.scene(149) == (0, 149) and timeline.scene(150) == timeline.scene(299) == (150, 299)
    assert clip().scenes == ((0, 299),) and Timeline.build([0.0, 0.1], 0.2, 0.1).scenes == ((0, 1),)  # no cut known: one scene
    assert timeline.pad == pytest.approx(2 * INTERVAL)  # two analysis samples …
    assert clip(interval=1 / 30).pad == MIN_PAD_S  # … and never less than MIN_PAD_S
    assert merge_ranges([(0.1234567, 1.0000004), (0.9, 2.3456)]) == [(0.123, 2.346)]  # ranges are kept in milliseconds


# ---------- cover: every frame between and around the samples ----------


def test_a_track_whose_last_sample_is_before_the_last_frame_covers_every_frame_to_the_end():
    timeline = clip()

    def moving(frame: int) -> tuple[float, float, float, float]:
        return (100.0 + frame, 50.0, 80.0, 100.0)

    samples = range(0, FRAMES, 5)
    assert samples[-1] == 295  # the last four frames were never analysed
    seen_ = one_person(track("T1", "face", samples, moving))
    boxes = cover(seen_, "person-1", "face", timeline)
    assert sorted(boxes) == list(range(300))
    assert all(len(here) == 1 for here in boxes.values())
    assert boxes[295] == [moving(295)] and all(boxes[frame] == [moving(295)] for frame in (296, 297, 298, 299))  # held where it was last seen
    assert boxes[7][0] == pytest.approx(moving(7)) and boxes[293][0] == pytest.approx(moving(293))  # between two samples: in between
    assert seen_.visible["person-1:face"] == [(0.0, 10.0)]  # to the end of the video, not to the last sample (9.83)


def test_an_appearance_is_held_for_the_pad_on_both_sides_and_no_longer():
    appearance = track("T1", "face", range(100, 201, 5))
    assert sorted(cover(one_person(appearance), "person-1", "face", clip())) == list(range(90, 211))  # two samples = 10 frames
    dense = clip(interval=1 / FPS)  # analysed on every frame: MIN_PAD_S = 6 frames
    assert sorted(cover(one_person(appearance, timeline=dense), "person-1", "face", dense)) == list(range(94, 207))
    edge = track("T1", "face", [0, 5, 295])  # at the ends of the video the hold stops with the video
    assert sorted(cover(one_person(edge), "person-1", "face", clip())) == [*range(0, 16), *range(285, 300)]
    once = one_person(track("T1", "face", [150]))  # a single sighting is an appearance too
    assert sorted(cover(once, "person-1", "face", clip())) == list(range(140, 161))
    assert once.visible["person-1:face"] == [(at(140), at(161))]


def test_no_box_is_drawn_across_a_scene_cut():
    timeline = clip(scenes=[(0, 149), (150, 299)])
    left, right = (100.0, 50.0, 80.0, 100.0), (1500.0, 700.0, 80.0, 100.0)
    tracks = [
        track("T1", "face", range(0, 146, 5), left),  # last seen four frames before the cut
        track("T2", "face", range(152, 300, 5), right),  # first seen two frames after it
        track("T3", "face", [140, 145, 150, 155], lambda frame: left if frame < 150 else right),  # a tracker that ran over the cut
    ]
    persons = [PersonGroup(id=f"person-{n}", face_tracks=[f"T{n}"]) for n in (1, 2, 3)]
    seen_ = known(tracks, persons=persons, timeline=timeline)
    before, after, both = (cover(seen_, f"person-{n}", "face", timeline) for n in (1, 2, 3))
    assert sorted(before) == list(range(0, 150))  # held up to the cut and not one frame into the next scene
    assert sorted(after) == list(range(150, 300))  # held back to the cut and not one frame before it
    assert sorted(both) == list(range(130, 166))
    assert all(both[frame] == [left] for frame in range(130, 150)) and all(both[frame] == [right] for frame in range(150, 166))  # nothing halfway
    assert seen_.visible["person-1:face"] == [(0.0, 5.0)] and seen_.visible["person-2:face"] == [(5.0, 10.0)]
    # the same samples without the cut: one appearance, and the box travels from one place to the other
    uncut = cover(known(tracks, persons=persons), "person-3", "face", clip())
    assert uncut[147][0][0] == pytest.approx(100.0 + 1400.0 * 2 / 5)


def test_a_short_gap_inside_a_track_is_covered_and_a_long_one_is_not():
    timeline = clip()

    def moving(frame: int) -> tuple[float, float, float, float]:
        return (100.0 + 2 * frame, 50.0, 80.0, 100.0)

    # lost for 40 frames (1.33 s), then for 50 frames (1.67 s): BRIDGE_S lies between
    samples = [*range(0, 61, 5), *range(100, 121, 5), *range(170, 201, 5)]
    assert (100 - 60) / FPS < BRIDGE_S < (170 - 120) / FPS
    seen_ = one_person(track("T1", "face", samples, moving))
    boxes = cover(seen_, "person-1", "face", timeline)
    assert set(range(61, 100)) <= set(boxes) and boxes[80][0] == pytest.approx(moving(80))  # where it would have been
    assert sorted(frame for frame in boxes if 120 < frame < 170) == [*range(121, 131), *range(160, 170)]  # only the holds
    assert seen_.visible["person-1:face"] == [(0.0, at(131)), (at(160), at(211))]
    # exactly BRIDGE_S (45 frames) is still one appearance; one frame more is two
    assert sorted(cover(one_person(track("T1", "face", [0, 45])), "person-1", "face", timeline)) == list(range(0, 56))
    assert sorted(cover(one_person(track("T1", "face", [0, 46])), "person-1", "face", timeline)) == [*range(0, 11), *range(36, 57)]


def test_two_nearby_tracks_of_the_same_target_are_joined_over_a_short_gap_only():
    timeline = clip()
    first = track("T1", "face", range(0, 61, 5), FACE)
    nearby, far = (430.0, 210.0, 80.0, 100.0), (1500.0, 210.0, 80.0, 100.0)

    def frames_covered(second: Track, owner: str = "person-1") -> dict[int, list]:
        persons = [PersonGroup(id="person-1", face_tracks=["T1", *(["T2"] if owner == "person-1" else [])]), PersonGroup(id="person-2", face_tracks=["T2"] if owner == "person-2" else [])]
        return cover(known([first, second], persons=persons), "person-1", "face", timeline)

    gap = set(range(71, 90))  # after the hold of the first track and before the hold of the second
    again = frames_covered(track("T2", "face", range(100, 151, 5), nearby))  # lost for 1.33 s and numbered anew by the tracker
    assert sorted(again) == list(range(0, 161)) and again[80][0] == pytest.approx((415.0, 205.0, 80.0, 100.0))
    late = frames_covered(track("T2", "face", range(120, 151, 5), nearby))  # two seconds later: a new appearance
    assert sorted(late) == [*range(0, 71), *range(110, 161)]
    elsewhere = frames_covered(track("T2", "face", range(100, 151, 5), far))  # 1.33 s later but across the picture
    assert not gap & set(elsewhere) and 100 in elsewhere
    someone = frames_covered(track("T2", "face", range(100, 151, 5), nearby), owner="person-2")  # another person's face
    assert sorted(someone) == list(range(0, 71))


def test_redacting_the_body_also_covers_frames_where_only_the_face_was_tracked():
    timeline = clip()
    face, body = (420.0, 100.0, 60.0, 80.0), (380.0, 80.0, 160.0, 400.0)
    seen_ = one_person(track("T1", "face", range(0, 201, 5), face), track("T2", "person", range(0, 101, 5), body))
    assert {t.id for t in seen_.tracks_for("person-1", "body")} == {"T1", "T2"} and [t.id for t in seen_.tracks_for("person-1", "face")] == ["T1"]
    as_body, as_face = cover(seen_, "person-1", "body", timeline), cover(seen_, "person-1", "face", timeline)
    assert sorted(as_face) == sorted(as_body) == list(range(0, 211))
    assert sorted(as_body[50]) == sorted([face, body]) and as_face[50] == [face]
    assert as_body[150] == [face]  # the body was lost at frame 100; the person is still covered where the face is
    assert seen_.visible["person-1:body"] == seen_.visible["person-1:face"] == [(0.0, at(211))]
    regions = build_regions(seen_, [decision("D-1", "person-1", "body", seen_.visible["person-1:body"])], timeline, width=WIDTH, height=HEIGHT)
    assert frames_of(regions, "D-1") == list(range(0, 211)) and len(regions[50]) == 2 and len(regions[150]) == 1
    faceless = one_person(track("T2", "person", range(0, 101, 5), body))  # seen from behind
    assert cover(faceless, "person-1", "face", timeline) == {} and faceless.visible == {"person-1:face": [], "person-1:body": [(0.0, at(111))]}


def test_texts_and_plates_are_covered_by_their_own_tracks():
    timeline = clip()
    tracks = [track("TX1", "text", (60, 90, 120), (500.0, 600.0, 200.0, 40.0), text="김민지"), track("TP1", "plate", range(200, 251, 5), (900.0, 800.0, 120.0, 40.0))]
    seen_ = known(tracks, texts=[TextItem(id="text-1", text="김민지", tracks=["TX1"])], plates=[Plate(id="plate-1", tracks=["TP1"])])
    assert seen_.visible == {"text-1:text": [(at(50), at(131))], "plate-1:plate": [(at(190), at(261))]}  # read once a second: still one appearance
    assert seen(seen_, "text-1", "text") == [(at(50), at(131))] and seen(seen_, "text-9", "text") == [] and seen(seen_, "text-1", "face") == []
    assert cover(seen_, "nobody", "face", timeline) == {}


# ---------- regions: what the renderer draws ----------


def test_every_frame_of_a_mosaic_decisions_applied_ranges_has_a_region_for_it():
    timeline = clip()
    tracks = [
        track("T1", "face", range(0, FRAMES, 5), lambda frame: (100.0 + 4 * frame, 200.0, 80.0, 100.0)),  # there throughout
        track("T2", "person", range(30, 200, 5), (900.0, 300.0, 200.0, 500.0)),
        track("T3", "face", [*range(30, 61, 5), *range(150, 200, 5)], (960.0, 320.0, 60.0, 80.0)),  # the face is lost for three seconds
        track("TX1", "text", range(60, 241, 30), (500.0, 900.0, 240.0, 40.0), text="김민지"),
    ]
    persons = [PersonGroup(id="person-1", face_tracks=["T1"]), PersonGroup(id="person-2", face_tracks=["T3"], body_tracks=["T2"])]
    seen_ = known(tracks, persons=persons, texts=[TextItem(id="text-1", text="김민지", tracks=["TX1"])])
    decisions = [
        decision("D-1", "person-1", "face", [(0.0, 9.9)]),  # as a model or a person writes it for a 10 s video
        decision("D-2", "person-2", "face", seen_.visible["person-2:face"]),  # two appearances
        decision("D-3", "person-2", "body", [(2.0, 3.0), (4.0, 5.5)]),  # parts of what is visible, away from where it starts and ends
        decision("D-4", "text-1", "text", [(2.0, 8.0)], shape="box"),
    ]
    regions = build_regions(seen_, decisions, timeline, width=WIDTH, height=HEIGHT)
    for item in decisions:
        applied = timeline.frames(applied_ranges(item, seen_, timeline))
        assert applied and frames_of(regions, item.id) == applied  # every frame of the range, and no other
        assert set(timeline.frames(timeline.align(item.ranges))) <= set(applied)  # at least what the decision says
    assert frames_of(regions, "D-1") == list(range(300))
    assert {297, 298, 299} <= set(frames_of(regions, "D-1"))  # the frames "9.9" leaves out of a video that ends at 10.0
    assert frames_of(regions, "D-2") == [*range(20, 71), *range(140, 206)]
    assert frames_of(regions, "D-3") == [*range(60, 90), *range(120, 165)]
    assert frames_of(regions, "D-4") == list(range(50, 251))  # 2.0 and 8.0 are where its first and last reading are held from and to
    assert expected_frames(seen_, decisions, timeline) == {item.id: frames_of(regions, item.id) for item in decisions}


@pytest.mark.parametrize("written", [[(0.0, 9.9)], [(0.0, 9.93)], [(0.0, 9.967)], [(0.0, 10.0)], [(0.03, 9.97)], [(0.0, 5.0), (5.0, 9.9)]])
def test_a_target_visible_to_the_end_is_covered_on_the_last_frames_however_the_end_is_written(written):
    """The regression this module was rewritten for: decision ranges [(0.0, 9.9)] on a 10 s / 30 fps clip left
    frames 297–299 without a region, three frames of the original face at the end of the output."""
    timeline = clip()
    for samples in (range(0, FRAMES, 5), range(0, FRAMES), [*range(0, FRAMES, 5), 299]):  # last sample 295 · every frame · the last frame too
        seen_ = one_person(track("T1", "face", samples))
        assert seen_.visible["person-1:face"] == [(0.0, 10.0)]
        regions = build_regions(seen_, [decision("D-1", "person-1", "face", written)], timeline, width=WIDTH, height=HEIGHT)
        assert sorted(regions) == list(range(300)), f"frames without a region: {sorted(set(range(300)) - set(regions))}"
        assert all(regions[frame] == regions[0] for frame in (297, 298, 299))


def test_a_range_extended_beyond_the_tracked_frames_holds_the_nearest_box():
    timeline = clip()

    def moving(frame: int) -> tuple[float, float, float, float]:
        return (100.0 + 4 * frame, 300.0, 80.0, 100.0)

    seen_ = one_person(track("T1", "face", range(60, 121, 5), moving))
    assert seen_.visible["person-1:face"] == [(at(50), at(131))]
    widened = decision("D-1", "person-1", "face", [(1.0, 6.0)])  # the reviewer added more than SNAP_S on both sides
    regions = build_regions(seen_, [widened], timeline, width=WIDTH, height=HEIGHT)
    assert sorted(regions) == list(range(30, 180)) and all(len(items) == 1 for items in regions.values())
    first, last = regions[60][0], regions[120][0]
    assert (first.x, first.y, first.w, first.h) == pytest.approx((328.0, 285.0, 104.0, 130.0))  # the box at frame 60, 1.3 times as large
    assert all(regions[frame] == [first] for frame in range(30, 60))  # before it was first seen: where it was first seen
    assert all(regions[frame] == [last] for frame in range(121, 180))  # after it was last seen: where it was last seen
    assert regions[90][0].x == pytest.approx(first.x + 4 * 30) and regions[90][0] != first  # in between it follows the track
    assert expected_frames(seen_, [widened], timeline) == {"D-1": list(range(30, 180))}
    # between two appearances the nearer one in time is held
    twice = one_person(track("T1", "face", [*range(60, 121, 5), *range(240, 271, 5)], moving))
    regions = build_regions(twice, [decision("D-1", "person-1", "face", [(5.0, 7.0)])], timeline, width=WIDTH, height=HEIGHT)
    assert sorted(regions) == list(range(150, 210))
    assert all(regions[frame] == [last] for frame in range(150, 180))  # closer to frame 130, where the first appearance ends
    assert all(regions[frame][0].x == pytest.approx(100.0 + 4 * 240 - 12.0) for frame in range(181, 210))  # closer to the second


def test_keep_and_remove_decisions_draw_nothing():
    timeline = clip()
    tracks = [track("T1", "face", range(0, FRAMES, 5)), track("TX1", "text", range(0, FRAMES, 30), (500.0, 900.0, 240.0, 40.0), text="김민지")]
    seen_ = known(tracks, persons=[PersonGroup(id="person-1", face_tracks=["T1"]), PersonGroup(id="person-2", body_tracks=["T9"])], texts=[TextItem(id="text-1", text="김민지", tracks=["TX1"])])
    kept = decision("D-1", "person-1", "face", [(0.0, 10.0)], method="keep")
    removed = decision("D-2", "meta-location", "metadata", [], method="remove")
    unseen = decision("D-3", "person-2", "body", [(0.0, 10.0)])  # a mosaic for someone who was never tracked
    drawn = decision("D-4", "text-1", "text", [(0.0, 10.0)])
    assert removed.shape is None and removed.ranges == []
    assert build_regions(seen_, [kept, removed, unseen], timeline, width=WIDTH, height=HEIGHT) == {}
    assert expected_frames(seen_, [kept, removed, unseen], timeline) == {}
    regions = build_regions(seen_, [kept, removed, unseen, drawn], timeline, width=WIDTH, height=HEIGHT)
    assert {region.ref for items in regions.values() for region in items} == {"D-4"} and sorted(regions) == list(range(300))
    assert expected_frames(seen_, [kept, removed, unseen, drawn], timeline) == {"D-4": list(range(300))}


def test_a_region_is_sized_shaped_and_weighted_by_what_it_hides():
    timeline = clip()
    body, sign, plate = (800.0, 200.0, 200.0, 500.0), (500.0, 900.0, 240.0, 40.0), (1800.0, 1030.0, 120.0, 50.0)
    tracks = [
        track("T1", "face", range(0, FRAMES, 5), FACE),
        track("T2", "person", range(0, FRAMES, 5), body),
        track("TX1", "text", range(0, FRAMES, 30), sign, text="김민지"),
        track("TP1", "plate", range(0, FRAMES, 5), plate),  # at the bottom-right corner of the picture
    ]
    persons = [PersonGroup(id="person-1", face_tracks=["T1"], body_tracks=["T2"])]
    seen_ = known(tracks, persons=persons, texts=[TextItem(id="text-1", text="김민지", tracks=["TX1"])], plates=[Plate(id="plate-1", tracks=["TP1"])])
    whole = [(0.0, 10.0)]
    decisions = [
        decision("D-1", "person-1", "face", whole),
        decision("D-2", "text-1", "text", whole),
        decision("D-3", "plate-1", "plate", whole),
        decision("D-4", "person-1", "face", whole, shape="outline"),  # no segmentation yet: drawn as the bounding shape
    ]
    regions = build_regions(seen_, decisions, timeline, width=WIDTH, height=HEIGHT)[150]
    assert [region.ref for region in regions] == ["D-1", "D-2", "D-3", "D-4"]
    face_region, text_region, plate_region, outline = regions
    assert (SPATIAL_SCALE["face"], SPATIAL_SCALE["text"], STRENGTH["face"], STRENGTH["text"], STRENGTH["plate"]) == (1.3, 1.2, 1.0, 2.5, 2.5)
    assert face_region == Region(388.0, 185.0, 104.0, 130.0, "ellipse", "D-1", 1.0)  # 1.3 times the box around its centre
    assert (text_region.x, text_region.y, text_region.w, text_region.h) == pytest.approx((476.0, 896.0, 288.0, 48.0))
    assert (text_region.shape, text_region.strength) == ("box", 2.5)  # coarser blocks: a line of text survives a face's block size
    assert (plate_region.x + plate_region.w, plate_region.y + plate_region.h) == (WIDTH, HEIGHT) and plate_region.strength == 2.5  # cut at the frame
    assert (outline.shape, outline.x, outline.w) == ("ellipse", face_region.x, face_region.w)
    body_region = build_regions(seen_, [decision("D-6", "person-1", "body", whole, shape="outline")], timeline, width=WIDTH, height=HEIGHT)[150]
    assert [(region.shape, region.strength) for region in body_region] == [("box", 1.0), ("box", 1.0)]  # the body and the face inside it
    assert sorted(round(region.w, 1) for region in body_region) == [round(80.0 * 1.08, 1), round(200.0 * 1.08, 1)]


def test_regions_survive_the_json_file_between_render_and_verify():
    regions = {7: [Region(388.04, 185.0, 104.0, 130.06, "ellipse", "D-1", 1.0), Region(476.0, 896.0, 288.0, 48.0, "box", "D-2", 2.5)], 3: [Region(1.0, 2.0, 3.0, 4.0, "box")]}
    assert regions[7][0].as_list() == [388.0, 185.0, 104.0, 130.1, "ellipse", "D-1", 1.0]  # x, y, w, h, shape, decision, strength
    stored = json.loads(json.dumps(regions_to_json(regions)))
    assert list(stored) == ["3", "7"]
    back = regions_from_json(stored)
    assert back == {3: [Region(1.0, 2.0, 3.0, 4.0, "box", "", 1.0)], 7: [Region(388.0, 185.0, 104.0, 130.1, "ellipse", "D-1", 1.0), Region(476.0, 896.0, 288.0, 48.0, "box", "D-2", 2.5)]}
    assert not hasattr(back[7][0], "method")  # a region is always a mosaic


# ---------- the timeline and coverage of a real asset ----------


def test_coverage_is_stored_with_the_understanding_and_read_by_everything_else(tmp_path):
    pts = [1000 + i * 3000 for i in range(FRAMES)]  # 30 fps on a 1/90000 time base, not starting at zero
    (tmp_path / "pts.json").write_text(json.dumps(pts), encoding="utf-8")
    meta = VideoMeta(
        path="x.mp4", filename="x.mp4", sha256="0", size_bytes=1, container="mp4", duration_s=10.0, frame_count=FRAMES, width=WIDTH, height=HEIGHT,
        display_width=WIDTH, display_height=HEIGHT, codec="h264", time_base=(1, 90000), first_pts=1000,
    )  # fmt: skip
    asset = VideoAsset(meta=meta, proxy=Proxy(path="p.mp4", width=1138, height=640, scale=640 / HEIGHT, frame_count=FRAMES, pts_identical=True), pts_path=str(tmp_path / "pts.json"))
    understanding = VideoUnderstanding(
        segments=[SegmentInfo(id="S1", start=0.0, end=5.0, start_frame=0, end_frame=149), SegmentInfo(id="S2", start=5.0, end=10.0, start_frame=150, end_frame=299)],
        tracks=[track("T1", "face", range(0, FRAMES, 5)), track("TX1", "text", (60, 90, 120), (500.0, 600.0, 200.0, 40.0), text="김민지")],
        persons=[PersonGroup(id="person-1", face_tracks=["T1"])],
        texts=[TextItem(id="text-1", text="김민지", kind="nametag", tracks=["TX1"], attached_person="person-1", confidence=0.9)],
        sample_interval=INTERVAL,
    )
    assert understanding.visible == {}
    timeline = views.timeline(asset, understanding)
    assert (timeline.count, timeline.duration, timeline.interval, timeline.scenes) == (FRAMES, 10.0, INTERVAL, ((0, 149), (150, 299)))
    assert timeline.times[0] == 0.0 and timeline.times[30] == pytest.approx(1.0)  # seconds from the first frame
    assert views.with_coverage(understanding, asset) is understanding
    assert understanding.visible == visible_ranges(understanding, timeline)
    assert understanding.visible == {"person-1:face": [(0.0, 10.0)], "person-1:body": [(0.0, 10.0)], "text-1:text": [(at(50), at(131))]}
    views.save(understanding, tmp_path / "understanding.json")
    again = views.load(tmp_path / "understanding.json")
    assert again.visible == understanding.visible and again.sample_interval == INTERVAL
    targets = {target.id: target for target in views.build_targets(again)}
    assert targets["person-1"].ranges == [(0.0, 10.0)] and targets["text-1"].ranges == [(at(50), at(131))]  # a 10 s clip: 10.0, not 9.93
    assert VideoUnderstanding().sample_interval == pytest.approx(1 / 12)


# ---------- fixes after review of the first implementation ----------


def _thirty_fps(seconds: float = 10.0) -> Timeline:
    count = round(seconds * 30)
    return Timeline.build([i / 30 for i in range(count)], seconds, 1 / 12)


def test_snap_moves_a_boundary_only_onto_the_appearance_the_range_reaches_into():
    timeline = _thirty_fps()
    visible = [(2.0, 4.0), (4.2, 4.467)]
    # the end written 0.1 s short of the first appearance's end moves there, and no further
    assert timeline.snap([(2.0, 3.9)], visible) == [(2.0, 4.0)]
    # a range that stops before an appearance begins does not grow into it
    assert timeline.snap([(1.0, 1.9)], [(2.0, 4.0)]) == [(1.0, 1.9)]
    # a range reaching into the second appearance takes its end when that is close
    assert timeline.snap([(2.0, 4.3)], visible) == [(2.0, 4.467)]


def test_a_target_tracked_two_pixels_wide_still_gets_a_region_on_every_expected_frame():
    from privframe.core.domain import Decision, PersonGroup, Track, TrackPoint, VideoUnderstanding
    from privframe.core.geometry import MIN_REGION_PX, build_regions, expected_frames, visible_ranges

    timeline = _thirty_fps(2.0)
    points = [TrackPoint(f=f, t=timeline.times[f], box=(100.0, 80.0, 1.2, 1.2)) for f in range(0, timeline.count, 5)]
    understanding = VideoUnderstanding(tracks=[Track(id="T1", kind="face", points=points)], persons=[PersonGroup(id="person-1", face_tracks=["T1"])])
    understanding.visible = visible_ranges(understanding, timeline)
    decision = Decision(id="D-1", target_id="person-1", target_label="인물 1", scope="face", method="mosaic", shape="ellipse", ranges=[(0.0, 2.0)], rationale="")
    regions = build_regions(understanding, [decision], timeline, width=640, height=360)
    wanted = expected_frames(understanding, [decision], timeline)["D-1"]
    assert wanted == list(range(timeline.count)) and sorted(regions) == wanted
    assert all(region.w >= MIN_REGION_PX and region.h >= MIN_REGION_PX for items in regions.values() for region in items)
