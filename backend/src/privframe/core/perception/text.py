"""On-screen text (slot perception.ocr): read about once a second, link the same text across readings into
tracks, and attach text that sits on a person to that person (design §7.2: cues propagate to the person).
"""
from __future__ import annotations

import math
import unicodedata
from difflib import SequenceMatcher
from typing import NamedTuple

from privframe.core import video
from privframe.core.domain import Box, PersonGroup, SegmentInfo, TextItem, Track, VideoAsset
from privframe.core.pipeline import ResolvedProfile

from .common import Progress, area, box_at, centre, contains, iou, share_inside, tell
from .models import Reading, text_reader
from .tracking import Sample, fill_gaps, to_points

OCR_INTERVAL_S = 1.0  # design §7.4: OCR on about 1 fps keyframes
MIN_TEXT_SCORE = 0.5  # RapidOCR's own default acceptance score
SOLO_TEXT_SCORE = 0.8  # a text read in a single sample is kept only when the recognizer was this sure
MAX_MISSED = 2  # readings a text may be missed in and still continue its track
NEAR_SIMILARITY = 0.5  # same place: the reading may differ this much (OCR noise on small text)
MOVED_SIMILARITY = 0.8  # different place: only an almost identical reading continues the track
MOVED_DISTANCE = 0.35  # … and only within this share of the frame diagonal per reading interval
INSIDE_SHARE = 0.6  # share of a text box that must lie on a person to count as "on" that person
BODY_MARGIN = 0.2  # … where the person is their body box widened by this share of its width on each side: detector
# boxes are often tighter than the body (the sample clip's name tag is 56% outside the box of the person wearing it)
MAX_DRIFT = 0.25  # text on a person keeps its place within the body box; more drift than this is a sign behind them


class TextSample(NamedTuple):
    f: int
    t: float
    box: Box  # proxy pixels
    text: str
    score: float


def normalize(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def similarity(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _match(chain: list[TextSample], box: Box, text: str, reach: float) -> float:
    """How well a new reading continues a chain (0 = it does not). `reach` is how far a text may have moved."""
    last = chain[-1]
    alike = max(similarity(s.text, text) for s in chain[-3:])
    (ax, ay), (bx, by) = centre(last.box), centre(box)
    distance = math.hypot(ax - bx, ay - by)
    if alike >= NEAR_SIMILARITY and (iou(last.box, box) >= 0.3 or distance <= 0.6 * max(last.box[3], box[3])):
        return 1.0 + alike
    if alike >= MOVED_SIMILARITY and distance <= reach:
        return alike
    return 0.0


def link_text(samples: list[tuple[int, float, list[Reading]]], frame_size: tuple[float, float]) -> list[list[TextSample]]:
    """Chains of the same text over the OCR readings of one segment (`samples` in time order).

    A chain gets a sample at every reading where its text is found, so its first and last samples are the
    first and last readings that show it. A reading continues a chain when it is in about the same place and
    reads similarly, or reads almost identically somewhere within reach (moving camera or moving person).
    """
    diagonal = math.hypot(*frame_size)
    chains: list[list[TextSample]] = []
    last_step: list[int] = []
    for step, (f, t, readings) in enumerate(samples):
        open_chains = [n for n, seen in enumerate(last_step) if step - seen <= MAX_MISSED + 1]
        pairs = sorted(
            (
                (_match(chains[n], box, text, MOVED_DISTANCE * diagonal * (step - last_step[n])), n, i)
                for n in open_chains
                for i, (box, text, _) in enumerate(readings)
            ),
            key=lambda p: (-p[0], p[1], p[2]),
        )
        used_chains: set[int] = set()
        used: set[int] = set()
        for score, n, i in pairs:
            if score <= 0:
                break
            if n in used_chains or i in used:
                continue
            used_chains.add(n)
            used.add(i)
            chains[n].append(TextSample(f, t, *readings[i]))
            last_step[n] = step
        for i, reading in enumerate(readings):
            if i not in used:
                chains.append([TextSample(f, t, *reading)])
                last_step.append(step)
    return chains


def best_reading(chain: list[TextSample]) -> tuple[str, float]:
    """The reading with the most support (count × confidence) and its mean confidence."""
    votes: dict[str, list[TextSample]] = {}
    for sample in chain:
        votes.setdefault(normalize(sample.text), []).append(sample)
    winner = max(votes, key=lambda key: (sum(s.score for s in votes[key]), key))
    support = votes[winner]
    return max(support, key=lambda s: (s.score, s.text)).text, sum(s.score for s in support) / len(support)


def ocr_frames(times: list[float], sampled: list[int]) -> list[int]:
    """Which of a segment's analysis samples to read: the first, then one per OCR interval, and the last, so
    that a text which stays until the cut has a reading there."""
    out: list[int] = []
    for f in sampled:
        if not out or times[f] + 1e-9 >= times[out[-1]] + OCR_INTERVAL_S:
            out.append(f)
    if out and times[sampled[-1]] - times[out[-1]] > OCR_INTERVAL_S / 4:
        out.append(sampled[-1])
    return out


def recognize_text(
    asset: VideoAsset, times: list[float], segments: list[SegmentInfo], profile: ResolvedProfile, *, progress: Progress | None = None
) -> tuple[list[Track], list[TextItem]]:
    """Text tracks (ids TX1..) and one TextItem per track (text-1..).

    A track has a point with the reading's score at every OCR reading that shows the text, and interpolated
    points (score 0) at the analysis samples in between, so it is as dense as a face track.
    """
    reader = text_reader(profile)
    fps = float(profile.params["perception.analysis_fps"])
    sampled = {s.id: video.sample_indices(times, fps, s.start_frame, s.end_frame) for s in segments}
    plan = {s.id: ocr_frames(times, sampled[s.id]) for s in segments}
    order = sorted({f for frames in plan.values() for f in frames})
    readings: dict[int, list[Reading]] = {}
    for done, (f, image) in enumerate(video.iter_proxy(asset, order)):
        readings[f] = [r for r in reader.read(image) if r[2] >= MIN_TEXT_SCORE and len(normalize(r[1])) >= 2]
        tell(progress, f"글자를 읽는 중 ({times[f]:.0f}초)", done / max(1, len(order)))
    chains: list[tuple[str, list[TextSample]]] = []
    for segment in segments:
        linked = link_text([(f, times[f], readings.get(f, [])) for f in plan[segment.id]], (asset.proxy.width, asset.proxy.height))
        chains += [(segment.id, chain) for chain in linked if len(chain) >= 2 or chain[0].score >= SOLO_TEXT_SCORE]
    chains.sort(key=lambda item: (item[1][0].f, item[1][0].box))
    tracks: list[Track] = []
    items: list[TextItem] = []
    for number, (segment_id, chain) in enumerate(chains, 1):
        text, confidence = best_reading(chain)
        dense = fill_gaps([Sample(s.f, s.t, s.box, s.score) for s in chain], sampled[segment_id], times)
        quality = round(sum(s.score for s in chain) / len(chain), 3)
        tracks.append(Track(id=f"TX{number}", kind="text", segment=segment_id, points=to_points(dense, asset), text=text, quality=quality))
        items.append(TextItem(id=f"text-{number}", text=text, tracks=[f"TX{number}"], confidence=round(confidence, 3)))
    tell(progress, f"글자 {len(items)}건", 1.0)
    return tracks, items


# ---------- text on a person ----------


class _Hit(NamedTuple):
    size: float  # area of the matched body, to prefer the most specific person
    person: str
    torso: bool
    place: tuple[float, float]  # text centre relative to the body box (0..1)


def _on_person(text_box: Box, f: int, person: PersonGroup, by_id: dict[str, Track]) -> _Hit | None:
    """Whether the text lies on this person at frame f."""

    def at(ids: list[str]) -> Box | None:
        return next((box for box in (box_at(by_id[i], f) for i in ids if i in by_id) if box is not None), None)

    body, face = at(person.body_tracks), at(person.face_tracks)
    cx, cy = centre(text_box)
    if body is not None and area(body) > 0:
        reach = (body[0] - BODY_MARGIN * body[2], body[1], body[2] * (1 + 2 * BODY_MARGIN), body[3])
        if share_inside(text_box, reach) < INSIDE_SHARE:
            return None
        if face is not None:  # chest: from the chin down three and a half face heights
            torso = face[1] + face[3] <= cy <= face[1] + 4.5 * face[3]
        else:  # seen from behind or face not found: the upper-middle band of the body box
            torso = 0.15 <= (cy - body[1]) / body[3] <= 0.6
        return _Hit(area(body), person.id, torso, ((cx - body[0]) / body[2], (cy - body[1]) / body[3]))
    if face is not None and area(face) > 0:  # no body track: the area just below the face stands in for the chest
        chest = (face[0] - face[2], face[1] + face[3], 3 * face[2], 3.5 * face[3])
        if share_inside(text_box, chest) >= INSIDE_SHARE and contains(chest, (cx, cy)):
            return _Hit(area(chest), person.id, True, ((cx - chest[0]) / chest[2], (cy - chest[1]) / chest[3]))
    return None


def attach_text(tracks: list[Track], persons: list[PersonGroup], texts: list[TextItem]) -> None:
    """In place: a text that lies on one person at most of its readings gets `attached_person`; on the torso
    it becomes a `nametag`. The reading is added to that person's cues.

    Two guards against signs that merely pass behind someone: the text must keep its place within the body
    box across readings (MAX_DRIFT), and a text read only once is attached only when it is on the torso.
    """
    by_id = {t.id: t for t in tracks}
    people = {p.id: p for p in persons}
    for item in texts:
        points = [p for track_id in item.tracks if track_id in by_id for p in by_id[track_id].points if p.score > 0]
        votes: dict[str, list[_Hit]] = {}
        for point in points:
            hits = [hit for person in persons if (hit := _on_person(point.box, point.f, person, by_id))]
            if hits:
                best = min(hits)  # overlapping people: the smallest body is the most specific
                votes.setdefault(best.person, []).append(best)
        if not votes:
            continue
        person_id, hits = max(votes.items(), key=lambda kv: (len(kv[1]), kv[0]))
        on_torso = sum(h.torso for h in hits) * 2 > len(hits)
        drift = max(max(h.place[axis] for h in hits) - min(h.place[axis] for h in hits) for axis in (0, 1))
        if len(hits) * 2 <= len(points) or drift > MAX_DRIFT or (len(hits) == 1 and not on_torso):
            continue
        item.attached_person = person_id
        if on_torso:
            item.kind = "nametag"
        cue = f'명찰 글자 "{item.text}"' if on_torso else f'몸 위의 글자 "{item.text}"'
        if cue not in people[person_id].cues:
            people[person_id].cues.append(cue)
