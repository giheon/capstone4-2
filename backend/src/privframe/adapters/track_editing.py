"""Create a representative preview for a manually split, observed track."""

import cv2

from privframe.domain.models import Track, VideoObservation
from privframe.domain.ports import ArtifactStorage


class TrackThumbnailer:
    def __init__(self, artifacts: ArtifactStorage):
        self.artifacts = artifacts

    def __call__(self, observation: VideoObservation, track: Track, run_id: str) -> str:
        point = max(track.points, key=lambda item: item.confidence)
        capture = cv2.VideoCapture(str(self.artifacts.resolve(observation.working_artifact)))
        try:
            capture.set(cv2.CAP_PROP_POS_FRAMES, point.frame)
            success, frame = capture.read()
        finally:
            capture.release()
        if not success:
            raise ValueError("Could not read the split track preview frame")
        box = point.box
        crop = frame[box.y:box.y + box.height, box.x:box.x + box.width]
        if not crop.size:
            raise ValueError("Split track preview has no pixels")
        ratio = min(1.0, 160 / max(crop.shape[:2]))
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * ratio)), max(1, round(crop.shape[0] * ratio))))
        success, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            raise ValueError("Could not encode the split track preview")
        return self.artifacts.put_bytes(f"runs/{run_id}/identity-edits/{track.id}.jpg", encoded.tobytes())
