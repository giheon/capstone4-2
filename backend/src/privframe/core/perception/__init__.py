"""Local perception (design §7, FR-03): deterministic tools that turn a video into segments, tracks of faces,
people, licence plates and text, person groups, and text attached to people.

    segments = find_segments(asset, times, profile)
    tracks = detect_and_track(asset, times, segments, profile)
    text_tracks, texts = recognize_text(asset, times, segments, profile)
    persons, ambiguities = group_identities(asset, tracks, profile)
    attach_text(tracks + text_tracks, persons, texts)

Around redaction: `face_finder` (faces to blur before a frame leaves the machine) and `output_checker` (is
anything that was redacted still identifiable in the rendered output, inside the mosaics or anywhere else?).

Every function reads its model from the profile's perception.* parts and raises PerceptionError for a part
this backend cannot run. Same video and profile give the same ids and boxes. Frames never leave the machine.
"""
from .common import PerceptionCancelled, PerceptionError, Progress
from .identity import MERGE_SIMILARITY, SPLIT_SIMILARITY, apply_identity_answer, group_identities
from .segments import find_segments
from .text import OCR_INTERVAL_S, attach_text, recognize_text
from .tracking import detect_and_track
from .verify import IDENTIFIABLE_SIMILARITY, READABLE_SIMILARITY, Leak, OutputChecker, face_finder, output_checker, redetector

__all__ = [
    "IDENTIFIABLE_SIMILARITY",
    "MERGE_SIMILARITY",
    "Leak",
    "OutputChecker",
    "OCR_INTERVAL_S",
    "READABLE_SIMILARITY",
    "SPLIT_SIMILARITY",
    "PerceptionCancelled",
    "PerceptionError",
    "Progress",
    "apply_identity_answer",
    "attach_text",
    "detect_and_track",
    "face_finder",
    "find_segments",
    "group_identities",
    "recognize_text",
    "output_checker",
    "redetector",
]
