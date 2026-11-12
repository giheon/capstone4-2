"""Person grouping (slot perception.face_embedding): which face tracks are the same person, which body
tracks belong to them, and which pairs are too close to call (design §5.7 await_identity).

Face feature vectors may be biometric data. They live in local variables of `group_identities` only: they are
never written to disk, logged, or returned.
"""
from __future__ import annotations

import math
from collections.abc import Callable

import cv2
import numpy as np

from privframe.core import video
from privframe.core.domain import Box, IdentityAmbiguity, PersonGroup, Track, TrackPoint, VideoAsset, VideoUnderstanding, merge_ranges
from privframe.core.pipeline import ResolvedProfile

from .common import PerceptionError, Progress, area, box_at, centre, contains, iou, tell
from .models import face_detector, face_embedder

# OpenCV's SFace demo calls two single images the same person at cosine ≥ 0.363 (its LFW operating point).
# Around that value the answer is unreliable, so only clearly higher track-level scores merge automatically,
# clearly lower ones stay apart, and the band in between is put to the user. Measured on the team clip A_B_A
# (thirds of each face track compared, 2026-11-11): same person 0.82–0.99, different people −0.17–0.25. One clip
# shot in one session does not tune the band; re-measure when labelled videos with harder cases exist.
MERGE_SIMILARITY = 0.50
SPLIT_SIMILARITY = 0.30
SAMPLES_PER_TRACK = 5  # face crops embedded per track, spread over its duration
SMALL_FACE_PX = 80  # proxy faces narrower than this are cropped from the original when it has more pixels
CROP_SIDE = 448  # face crops are scaled down to this before landmark detection (SFace aligns to 112 px)

Embed = Callable[[VideoAsset, list[Track]], dict[str, np.ndarray]]


# ---------- features ----------


def _quality(point: TrackPoint) -> float:
    return point.score * math.sqrt(area(point.box))


def best_point(track: Track) -> TrackPoint:
    return max(track.points, key=lambda p: (_quality(p), -p.f))


def _pick(track: Track, count: int = SAMPLES_PER_TRACK) -> list[TrackPoint]:
    """The best detected point of each of `count` equal stretches of the track."""
    detected = [p for p in track.points if p.score > 0]
    if not detected:
        return []
    parts = np.array_split(np.arange(len(detected)), min(count, len(detected)))
    return [max((detected[i] for i in part), key=lambda p: (_quality(p), -p.f)) for part in parts]


def _feature(image: np.ndarray, box: Box, profile: ResolvedProfile) -> np.ndarray | None:
    """Feature of the face at `box` (pixels of `image`): crop with context, find landmarks again, align, embed."""
    x, y, w, h = box
    x0, y0 = max(0, int(x - w / 2)), max(0, int(y - h / 2))
    x1, y1 = min(image.shape[1], int(x + 1.5 * w)), min(image.shape[0], int(y + 1.5 * h))
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    ratio = min(1.0, CROP_SIDE / max(crop.shape[:2]))
    if ratio < 1:
        crop = cv2.resize(crop, (round(crop.shape[1] * ratio), round(crop.shape[0] * ratio)), interpolation=cv2.INTER_AREA)
    expected = ((x - x0) * ratio, (y - y0) * ratio, w * ratio, h * ratio)
    rows = face_detector(profile).rows(crop, 0.5)
    row = max(rows, key=lambda r: iou(tuple(r[:4]), expected), default=None)
    if row is None or iou(tuple(row[:4]), expected) < 0.3:
        return None
    return face_embedder(profile).feature(crop, row)


def embed_faces(asset: VideoAsset, faces: list[Track], profile: ResolvedProfile, progress: Progress | None = None) -> dict[str, np.ndarray]:
    """One unit vector per face track that has at least one usable crop."""
    face_embedder(profile)  # an unsupported candidate fails before any frame is read
    scale = asset.proxy.scale
    wanted: dict[str, dict[int, list[tuple[str, Box]]]] = {"proxy": {}, "original": {}}
    for track in faces:
        for point in _pick(track):
            small = point.box[2] * scale < SMALL_FACE_PX and scale < 0.95
            wanted["original" if small else "proxy"].setdefault(point.f, []).append((track.id, point.box))
    total = max(1, sum(len(frames) for frames in wanted.values()))
    collected: dict[str, list[np.ndarray]] = {}
    done: list[int] = []

    def take(image: np.ndarray, frame: int, source: str, factor: float) -> None:
        for track_id, box in wanted[source][frame]:
            vector = _feature(image, (box[0] * factor, box[1] * factor, box[2] * factor, box[3] * factor), profile)
            if vector is not None:
                collected.setdefault(track_id, []).append(vector)
        done.append(frame)
        tell(progress, "같은 사람의 얼굴을 묶는 중", len(done) / total)

    for frame, image in video.iter_proxy(asset, sorted(wanted["proxy"])):
        take(image, frame, "proxy", scale)
    for frame in sorted(wanted["original"]):
        take(video.read_frame(asset, frame, source="original"), frame, "original", 1.0)
    out: dict[str, np.ndarray] = {}
    for track_id, vectors in collected.items():
        mean = np.mean(vectors, axis=0)
        out[track_id] = mean / max(float(np.linalg.norm(mean)), 1e-9)
    return out


# ---------- grouping ----------


def cluster_faces(
    ids: list[str], features: dict[str, np.ndarray], frames: dict[str, set[int]]
) -> tuple[list[list[str]], list[tuple[int, int, float, str, str]]]:
    """Constrained average-link agglomeration.

    Two tracks seen in the same frame are different people (cannot-link), and so are any groups containing
    such a pair. Groups merge, most similar first, while their mean pairwise cosine similarity is at least
    MERGE_SIMILARITY. Returns the groups and, for every pair of final groups whose similarity is in
    [SPLIT_SIMILARITY, MERGE_SIMILARITY), `(group a, group b, similarity, closest track of a, of b)`.
    Tracks without a feature stay alone.
    """
    n = len(ids)
    similarity = np.full((n, n), np.nan)
    blocked = np.zeros((n, n), dtype=bool)  # cannot-link, and later also "merged away"
    for i in range(n):
        for j in range(i + 1, n):
            if frames[ids[i]] & frames[ids[j]]:
                blocked[i, j] = blocked[j, i] = True
            elif ids[i] in features and ids[j] in features:
                similarity[i, j] = similarity[j, i] = float(np.clip(features[ids[i]] @ features[ids[j]], -1.0, 1.0))
    total, count = np.nan_to_num(similarity), (~np.isnan(similarity)).astype(float)
    members = [[i] for i in range(n)]

    def links() -> np.ndarray:
        """Mean similarity of every pair of live groups (row < column), -inf where they cannot be linked."""
        value = np.full((n, n), -np.inf)
        usable = (count > 0) & ~blocked
        value[usable] = total[usable] / count[usable]
        value[np.tril_indices(n)] = -np.inf
        return value

    while n > 1:
        value = links()
        i, j = divmod(int(np.argmax(value)), n)  # first maximum in row order: ties go to the earliest tracks
        if value[i, j] < MERGE_SIMILARITY:
            break
        for table in (total, count):
            table[i] += table[j]
            table[:, i] += table[:, j]
        blocked[i] |= blocked[j]
        blocked[:, i] |= blocked[:, j]
        blocked[j] = blocked[:, j] = True
        members[i], members[j] = sorted(members[i] + members[j]), []
    live = [i for i in range(n) if members[i]]
    value = links() if n > 1 else np.zeros((n, n))
    uncertain = []
    for a, i in enumerate(live):
        for b, j in enumerate(live[a + 1 :], a + 1):
            if value[i, j] >= SPLIT_SIMILARITY:
                known = [(similarity[x, y], -x, -y) for x in members[i] for y in members[j] if not np.isnan(similarity[x, y])]
                _, x, y = max(known)  # the most similar pair of tracks, earliest on ties
                uncertain.append((a, b, float(value[i, j]), ids[-x], ids[-y]))
    return [[ids[m] for m in members[i]] for i in live], uncertain


def assign_bodies(groups: list[list[str]], faces: dict[str, Track], bodies: list[Track]) -> dict[str, int]:
    """body track id → index of the face group it belongs to.

    At every face sample the face votes for the one body box it sits in best (centre inside the box, closest
    to the box's top centre), so a face inside two overlapping people counts for one of them. A body goes to
    the group that wins more than half of the samples they share, strongest claims first, and a group never
    gets two bodies that are on screen in the same frame.
    """
    votes: dict[tuple[str, int], int] = {}
    shared: dict[tuple[str, int], int] = {}
    for group, ids in enumerate(groups):
        for face_id in ids:
            face = faces[face_id]
            nearby = [b for b in bodies if b.segment == face.segment and b.points[0].f <= face.points[-1].f and b.points[-1].f >= face.points[0].f]
            for point in face.points:
                cx, cy = centre(point.box)
                best: tuple[float, str] | None = None
                for body in nearby:
                    box = box_at(body, point.f)
                    if box is None:
                        continue
                    shared[(body.id, group)] = shared.get((body.id, group), 0) + 1
                    if contains(box, (cx, cy)) and box[2] > 0 and box[3] > 0:
                        offset = math.hypot((cx - box[0] - box[2] / 2) / box[2], (cy - box[1]) / box[3])
                        if best is None or (offset, body.id) < best:
                            best = (offset, body.id)
                if best is not None:
                    votes[(best[1], group)] = votes.get((best[1], group), 0) + 1
    frames = {b.id: {p.f for p in b.points} for b in bodies}
    owner: dict[str, int] = {}
    for (body_id, group), count in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])):
        if body_id in owner or count * 2 <= shared[(body_id, group)]:
            continue
        if any(frames[body_id] & frames[other] for other, g in owner.items() if g == group):
            continue
        owner[body_id] = group
    return owner


def prominence(face_tracks: list[Track], body_tracks: list[Track], frame_area: float, duration: float, step: float) -> float:
    """Share of the video the person is on screen × mean share of the frame their box covers (0..1)."""
    members = face_tracks + body_tracks
    seconds = sum(b - a for a, b in merge_ranges([(t.start, t.end + step) for t in members]))
    shares = [area(p.box) / frame_area for t in (body_tracks or face_tracks) for p in t.points]
    if not shares or duration <= 0:
        return 0.0
    return round(min(1.0, seconds / duration) * min(1.0, sum(shares) / len(shares)), 4)


def group_identities(
    asset: VideoAsset,
    tracks: list[Track],
    profile: ResolvedProfile,
    *,
    progress: Progress | None = None,
    embed: Embed | None = None,
) -> tuple[list[PersonGroup], list[IdentityAmbiguity]]:
    """`embed` replaces the face-feature step (tests); by default the profile's embedding model is used."""
    faces = [t for t in tracks if t.kind == "face"]
    bodies = [t for t in tracks if t.kind == "person"]
    by_id = {t.id: t for t in tracks}
    features = embed(asset, faces) if embed else embed_faces(asset, faces, profile, progress)
    seen = {t.id: {p.f for p in t.points if p.score > 0} for t in faces}
    groups, uncertain = cluster_faces([t.id for t in faces], features, seen)
    del features  # nothing below needs the vectors
    owner = assign_bodies(groups, {t.id: t for t in faces}, bodies)
    members = [(ids, [b.id for b in bodies if owner.get(b.id) == index]) for index, ids in enumerate(groups)]
    members += [([], [b.id]) for b in bodies if b.id not in owner]  # people with no visible face

    frame_area = float(asset.meta.display_width * asset.meta.display_height)
    step = 1.0 / max(float(profile.params.get("perception.analysis_fps", 12)), 0.1)
    ranked = []
    for index, (face_ids, body_ids) in enumerate(members):
        value = prominence([by_id[i] for i in face_ids], [by_id[i] for i in body_ids], frame_area, asset.meta.duration_s, step)
        ranked.append((-value, min(by_id[i].points[0].f for i in face_ids + body_ids), index))
    ranked.sort()  # most prominent first, then first on screen
    name = {index: f"person-{rank}" for rank, (_, _, index) in enumerate(ranked, 1)}
    persons = [PersonGroup(id=name[index], face_tracks=members[index][0], body_tracks=members[index][1], prominence=abs(value)) for value, _, index in ranked]
    ambiguities = []
    for i, j, value, track_a, track_b in sorted(uncertain, key=lambda item: (-item[2], item[0], item[1])):
        if int(name[j].split("-")[1]) < int(name[i].split("-")[1]):  # `a` is the more prominent person
            i, j, track_a, track_b = j, i, track_b, track_a
        ambiguities.append(
            IdentityAmbiguity(
                id=f"identity-{len(ambiguities) + 1}", a=name[i], b=name[j], similarity=round(value, 3),
                at_a=best_point(by_id[track_a]).t, at_b=best_point(by_id[track_b]).t,
            )
        )  # fmt: skip
    tell(progress, f"인물 {len(persons)}명 · 확인 필요 {len(ambiguities)}건", 1.0)
    return persons, ambiguities


def apply_identity_answer(understanding: VideoUnderstanding, ambiguity_id: str, answer: str) -> VideoUnderstanding:
    """The user's answer to one ambiguity. `same` merges group b into a; `unknown` does the same, because
    treating two possibly-same people as one never leaves one of them unredacted; `different` keeps both."""
    if answer not in ("same", "different", "unknown"):
        raise PerceptionError(f"알 수 없는 답변입니다: '{answer}' (same · different · unknown 중 하나)")
    result = understanding.model_copy(deep=True)
    asked = next((a for a in result.ambiguities if a.id == ambiguity_id), None)
    if asked is None:
        raise PerceptionError(f"인물 묶음 확인 항목 '{ambiguity_id}'을(를) 찾을 수 없습니다.")
    result.ambiguities = [a for a in result.ambiguities if a.id != ambiguity_id]
    keep = next((p for p in result.persons if p.id == asked.a), None)
    gone = next((p for p in result.persons if p.id == asked.b), None)
    if answer == "different" or keep is None or gone is None:
        return result
    keep.face_tracks += [t for t in gone.face_tracks if t not in keep.face_tracks]
    keep.body_tracks += [t for t in gone.body_tracks if t not in keep.body_tracks]
    keep.cues += [c for c in gone.cues if c not in keep.cues]
    clashes = {k: [keep.attributes[k], v] for k, v in gone.attributes.items() if k in keep.attributes and keep.attributes[k] != v}
    keep.attributes = {**gone.attributes, **keep.attributes}  # group a's description wins; what b said differently is kept beside it
    if clashes:
        keep.attributes["merge_conflicts"] = {**keep.attributes.get("merge_conflicts", {}), **clashes}
    keep.prominence = round(min(1.0, keep.prominence + gone.prominence), 4)  # exact when the two never overlap in time
    result.persons = [p for p in result.persons if p.id != gone.id]
    for text in result.texts:
        if text.attached_person == gone.id:
            text.attached_person = keep.id
    remaining: list[IdentityAmbiguity] = []
    for other in result.ambiguities:
        a, b = (keep.id if x == gone.id else x for x in (other.a, other.b))
        if a != b and not any({a, b} == {r.a, r.b} for r in remaining):
            remaining.append(other.model_copy(update={"a": a, "b": b}))
    result.ambiguities = remaining
    return result
