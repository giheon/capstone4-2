#!/usr/bin/env python3
"""Create a labeled procedural checkerboard/box fixture with audio and no real faces."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def create_demo_video(output: Path, seconds: float = 2, fps: int = 15, overwrite: bool = False) -> dict:
    if not math.isfinite(seconds) or not 0 < seconds <= 120 or not 1 <= fps <= 60:
        raise ValueError("Require 0 < seconds <= 120 and 1 <= fps <= 60")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("FFmpeg is required on PATH")
    if output.exists() and not overwrite:
        raise ValueError(f"Demo already exists: {output}; use --overwrite or choose another output")
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height, frame_count = 320, 240, max(1, round(seconds * fps))
    seconds = frame_count / fps
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        temporary = Path(directory) / "fixture.mp4"
        command = [ffmpeg, "-nostdin", "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "bgr24",
                   "-s:v", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-f", "lavfi", "-i",
                   f"sine=frequency=440:sample_rate=44100:duration={seconds:g}", "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-threads", "1",
                   "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(temporary)]
        rows, columns = np.indices((height, width))
        checker = (((rows // 16 + columns // 16) % 2) * 35 + 40).astype(np.uint8)
        # This helper is deliberately capped at two minutes; write individual
        # frames to a temporary raw stream to keep memory independent of duration.
        raw = Path(directory) / "frames.bgr"
        with raw.open("wb") as handle:
            for index in range(frame_count):
                frame = cv2.cvtColor(checker, cv2.COLOR_GRAY2BGR)
                cv2.rectangle(frame, (0, 0), (width, 45), (15, 15, 15), -1)
                cv2.putText(frame, "FIXTURE SYNTHETIC", (10, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)
                cv2.putText(frame, "NO ACTUAL FACES", (10, 37), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 240, 240), 1)
                x = 20 + int(index / max(1, frame_count - 1) * 100)
                cv2.rectangle(frame, (x, 80), (x + 50, 140), (80, 200, 80), 3)
                cv2.putText(frame, "BOX A", (x, 73), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 220, 80), 1)
                cv2.rectangle(frame, (220, 155), (285, 215), (220, 140, 60), 3)
                cv2.putText(frame, "BOX B", (220, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 140, 60), 1)
                handle.write(frame.tobytes())
        command[command.index("pipe:0")] = str(raw)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if completed.returncode:
            raise ValueError(f"Demo encoding failed: {completed.stderr[-3000:]}")
        temporary.replace(output)
    metadata = {"label": "FIXTURE_SYNTHETIC", "generator": "procedural-checkerboard-boxes-v1",
                "has_actual_faces": False, "face_annotations": None, "has_audio": True,
                "width": width, "height": height, "fps": fps, "frame_count": frame_count, "duration": seconds,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "opencv_version": cv2.__version__}
    output.with_suffix(".metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/demo/checkerboard.mp4")
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        metadata = create_demo_video(args.output.resolve(), args.seconds, args.fps, args.overwrite)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output), **metadata}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
