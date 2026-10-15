"""Identity review rules. Pixel tracks remain separate from person membership."""

from collections import Counter

from .models import IdentityGroup, IdentityReviewInput, PersonContext, RunSnapshot, Track, now


def require_identity_edit(run: RunSnapshot) -> None:
    if run.status != "AWAITING_LABELS" or run.identity is None or run.observation is None:
        raise ValueError("Identity edits require completed analysis awaiting person review; fork to change inputs")


def validate_membership(run: RunSnapshot, groups) -> None:
    expected = {track.id for track in run.observation.tracks if track.object_type == "face"}
    ids = [group.id for group in groups]
    members = [track_id for group in groups for track_id in group.track_ids]
    if len(ids) != len(set(ids)) or any(not group.track_ids for group in groups):
        raise ValueError("Identity groups must have unique IDs and nonempty membership")
    if set(members) != expected or any(count != 1 for count in Counter(members).values()):
        raise ValueError("Identity groups must include every face track exactly once, without unknown tracks")


def apply_review(run: RunSnapshot, review: IdentityReviewInput) -> None:
    require_identity_edit(run)
    validate_membership(run, review.groups)
    identity = run.identity
    group_ids = {group.id for group in review.groups}
    if not set(review.persons).issubset(group_ids) or not set(review.confirmed_group_ids).issubset(group_ids):
        raise ValueError("Person inputs or confirmations contain an unknown group")
    before = {group.id: group for group in identity.groups}
    tracks = {track.id: track for track in run.observation.tracks}
    groups, persons = [], {}
    confirmed = set(review.confirmed_group_ids)
    for assignment in review.groups:
        previous = before.get(assignment.id)
        unchanged = previous is not None and set(previous.track_ids) == set(assignment.track_ids)
        group = IdentityGroup(
            id=assignment.id, track_ids=sorted(assignment.track_ids),
            thumbnail=next((tracks[key].thumbnail for key in assignment.track_ids if tracks[key].thumbnail), None),
            source=previous.source if unchanged else "user",
            review_reasons=list(previous.review_reasons) if unchanged else [],
        )
        supplied = review.persons.get(group.id)
        # A changed membership must be deliberately confirmed before consent can
        # be assigned. Otherwise affected facts reset, even if the ID was reused.
        facts = supplied.model_dump() if supplied and (unchanged or group.id in confirmed) else {}
        persons[group.id] = PersonContext(track_ids=group.track_ids, **facts)
        if run.scenario.use_purpose != "subject_access":
            persons[group.id].access_requester = "unknown"
        groups.append(group)
    identity.user_changes.append({
        "at": now(), "operation": "review", "revision": identity.revision,
        "before_groups": [group.model_dump(mode="json") for group in identity.groups],
        "before_persons": {key: value.model_dump(mode="json") for key, value in run.scenario.persons.items()},
        "after_groups": [group.model_dump(mode="json") for group in groups],
        "after_persons": {key: value.model_dump(mode="json") for key, value in persons.items()},
        "confirmed_group_ids": sorted(confirmed),
    })
    identity.groups = groups
    identity.reviewed = confirmed == group_ids
    identity.revision += 1
    run.scenario.persons = persons
    run.scenario.track_labels = {}  # Structured person facts are the single source for grouped runs.
    run.updated_at = now()


def split_observed_track(track: Track, frame: int, fps: float, suffix: str) -> tuple[Track, Track]:
    before = [point for point in track.points if point.frame < frame]
    after = [point for point in track.points if point.frame >= frame]
    if not before or not after:
        raise ValueError("Split frame must leave observed frames on both sides")
    children = []
    for number, points in enumerate((before, after), 1):
        children.append(Track(
            id=f"{track.id}-s{suffix}-{number}", points=points,
            start=points[0].time, end=min(track.end, (points[-1].frame + 1) / fps),
            object_type=track.object_type, source_track_id=track.source_track_id or track.id,
        ))
    return children[0], children[1]
