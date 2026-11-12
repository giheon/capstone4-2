"""Video I/O for analysis: probe the original, build the analysis proxy, read frames (design §6.2).

The proxy keeps the original's frame count and timestamps, so proxy frame i is original frame i. It is made
upright (auto-rotated) and scaled down; nothing here writes to the original. OpenCV is never used for the time
axis (it assumes a constant frame rate); frames come from PyAV in decode order.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable, Iterator
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any

import av
import numpy as np

from .domain import Proxy, VideoAsset, VideoMeta


class VideoError(RuntimeError):
    pass


def _binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise VideoError(f"{name} 실행 파일을 찾을 수 없습니다. FFmpeg를 설치하세요 (brew install ffmpeg).")
    return found


def _run(args: list[str], timeout: float) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise VideoError(f"{Path(args[0]).name} 시간 초과 ({timeout:.0f}s)") from error
    if done.returncode != 0:
        raise VideoError(f"{Path(args[0]).name} 실패: {done.stderr.strip()[-600:]}")
    return done.stdout


def ffprobe_json(path: Path, timeout: float = 60) -> dict[str, Any]:
    out = _run([_binary("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(path)], timeout)
    return json.loads(out)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rotation_cw(stream: dict[str, Any]) -> int:
    """Degrees clockwise needed to show the coded frame upright. ffprobe reports the display matrix angle as
    counter-clockwise degrees (`rotation: -90` for a phone clip that must be turned 90° clockwise)."""
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(round(-float(side["rotation"]))) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    return int(tag) % 360 if tag else 0


def is_location_tag(key: str) -> bool:
    """Tags that record where the video was shot: `location` (©xyz), `com.apple.quicktime.location.ISO6709`,
    its accuracy, Android's `com.android.capture.location…`, anything named GPS."""
    name = key.lower()
    return "location" in name or "gps" in name


def location_tags(info: dict[str, Any]) -> list[str]:
    """Names of the location tags in ffprobe output, container first."""
    found = [key for key in ((info.get("format") or {}).get("tags") or {}) if is_location_tag(key)]
    for stream in info.get("streams") or []:
        found += [key for key in (stream.get("tags") or {}) if is_location_tag(key) and key not in found]
    return found


def read_pts(path: Path) -> tuple[list[int], Fraction]:
    """Presentation timestamps of every video frame, in presentation order, and the stream time base."""
    stat = Path(path).stat()
    pts, time_base = _demuxed_pts(str(path), stat.st_mtime_ns, stat.st_size)
    return list(pts), time_base


@lru_cache(maxsize=32)
def _demuxed_pts(path: str, mtime_ns: int, size: int) -> tuple[tuple[int, ...], Fraction]:
    """Reading the timestamps demuxes the whole file, and frames are read one at a time: once per version of a file."""
    with av.open(path) as container:
        stream = container.streams.video[0]
        pts = sorted(packet.pts for packet in container.demux(stream) if packet.pts is not None and packet.size > 0)
        return tuple(pts), Fraction(stream.time_base.numerator, stream.time_base.denominator)


def probe(path: Path, filename: str | None = None) -> tuple[VideoMeta, list[int]]:
    """Metadata of the original and its frame timestamps. Raises VideoError when there is no video stream."""
    info = ffprobe_json(path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise VideoError("영상 스트림이 없는 파일입니다.")
    try:
        pts, time_base = read_pts(path)
    except av.error.FFmpegError as error:  # ffprobe accepted the file but its packets cannot be read
        raise VideoError("영상 프레임을 읽을 수 없습니다.") from error
    if not pts:
        raise VideoError("영상 프레임을 읽을 수 없습니다.")
    rotation = rotation_cw(video)
    width, height = int(video["width"]), int(video["height"])
    upright = (height, width) if rotation in (90, 270) else (width, height)
    deltas = {b - a for a, b in zip(pts, pts[1:])}
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    audio_label = "없음"
    if audio:
        rate = f"{int(audio['sample_rate']) / 1000:g} kHz" if audio.get("sample_rate") else ""
        audio_label = " ".join(x for x in [str(audio.get("codec_name", "")).upper(), rate, f"{audio.get('channels', '')}ch" if audio.get("channels") else ""] if x)
    pix_fmt = str(video.get("pix_fmt", ""))
    bit_depth = int(video.get("bits_per_raw_sample") or (10 if "10" in pix_fmt else 12 if "12" in pix_fmt else 8))
    color = {k: str(video[k]) for k in ("color_range", "color_space", "color_transfer", "color_primaries") if video.get(k)}
    duration = float((pts[-1] - pts[0]) * time_base) + (float(min(deltas) * time_base) if deltas else 0.0)
    meta = VideoMeta(
        path=str(path),
        filename=filename or path.name,
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
        container=str((info.get("format") or {}).get("format_name", "")),
        duration_s=round(duration, 3),
        frame_count=len(pts),
        width=width,
        height=height,
        display_width=upright[0],
        display_height=upright[1],
        rotation=rotation,
        codec=str(video.get("codec_name", "")),
        profile=str(video.get("profile", "")),
        level=int(video.get("level") or 0),
        pix_fmt=pix_fmt,
        bit_depth=bit_depth,
        color=color,
        time_base=(time_base.numerator, time_base.denominator),
        avg_frame_rate=str(video.get("avg_frame_rate", "")),
        vfr=len(deltas) > 1,
        audio=audio_label,
        stream_count=len(streams),
        first_pts=pts[0],
        location_tags=location_tags(info),
    )
    return meta, pts


def frame_times(pts: list[int], time_base: tuple[int, int]) -> list[float]:
    """Seconds from the first frame for every frame index."""
    base = Fraction(*time_base)
    return [float((p - pts[0]) * base) for p in pts]


def make_proxy(meta: VideoMeta, pts: list[int], out: Path, short_side: int = 640, timeout: float = 1800) -> Proxy:
    """Scaled, upright H.264 copy with the original's timestamps (`-fps_mode passthrough -enc_time_base demux`).

    A short GOP keeps seeking fast for previews. If ffmpeg's output does not have the same number of frames
    the proxy is rebuilt frame by frame with PyAV, which cannot drop or duplicate a frame.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    landscape = meta.display_width >= meta.display_height
    short = min(short_side, min(meta.display_width, meta.display_height))
    short -= short % 2
    scale = f"-2:{short}" if landscape else f"{short}:-2"
    _run(
        [
            _binary("ffmpeg"), "-y", "-v", "error", "-i", meta.path, "-map", "0:v:0", "-vf", f"scale={scale}",
            "-fps_mode", "passthrough", "-enc_time_base", "demux", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-g", "12", "-video_track_timescale", str(meta.time_base[1]), "-movflags", "+faststart", "-an", str(out),
        ],
        timeout,
    )  # fmt: skip
    proxy_pts, proxy_base = read_pts(out)
    if len(proxy_pts) != len(pts):
        _proxy_by_decode(meta, out, short)
        proxy_pts, proxy_base = read_pts(out)
        if len(proxy_pts) != len(pts):
            raise VideoError(f"분석 프록시의 프레임 수가 원본과 다릅니다 (원본 {len(pts)} · 프록시 {len(proxy_pts)}).")
    original_base = Fraction(*meta.time_base)
    identical = all((a - pts[0]) * original_base == (b - proxy_pts[0]) * proxy_base for a, b in zip(pts, proxy_pts))
    with av.open(str(out)) as container:
        stream = container.streams.video[0]
        width, height = stream.width, stream.height
    return Proxy(path=str(out), width=width, height=height, scale=height / meta.display_height, frame_count=len(proxy_pts), pts_identical=identical)


def _proxy_by_decode(meta: VideoMeta, out: Path, short: int) -> None:
    """Fallback proxy: decode every original frame and encode it one to one."""
    ratio = short / min(meta.display_width, meta.display_height)
    width = int(round(meta.display_width * ratio / 2)) * 2
    height = int(round(meta.display_height * ratio / 2)) * 2
    with av.open(meta.path) as source, av.open(str(out), mode="w") as sink:
        in_stream = source.streams.video[0]
        stream = sink.add_stream("libx264", options={"preset": "veryfast", "crf": "20", "g": "12"})
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.time_base = in_stream.time_base
        stream.codec_context.time_base = in_stream.time_base
        for frame in source.decode(in_stream):
            image = upright(frame.to_ndarray(format="bgr24"), meta.rotation)
            small = av.VideoFrame.from_ndarray(_resize(image, width, height), format="bgr24").reformat(format="yuv420p")
            small.pts, small.time_base = frame.pts, in_stream.time_base
            for packet in stream.encode(small):
                sink.mux(packet)
        for packet in stream.encode():
            sink.mux(packet)


def _resize(image: np.ndarray, width: int, height: int) -> np.ndarray:
    import cv2

    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


def upright(image: np.ndarray, rotation: int) -> np.ndarray:
    """Turn a decoded (sensor-orientation) frame upright."""
    if rotation == 90:
        return np.ascontiguousarray(np.rot90(image, k=3))
    if rotation == 180:
        return np.ascontiguousarray(np.rot90(image, k=2))
    if rotation == 270:
        return np.ascontiguousarray(np.rot90(image, k=1))
    return image


def prepare(original: Path, workdir: Path, *, filename: str | None = None, short_side: int = 640) -> VideoAsset:
    """Probe the original and build its proxy and timestamp table under `workdir`."""
    meta, pts = probe(original, filename)
    workdir.mkdir(parents=True, exist_ok=True)
    pts_path = workdir / "pts.json"
    pts_path.write_text(json.dumps(pts), encoding="utf-8")
    proxy = make_proxy(meta, pts, workdir / "proxy.mp4", short_side)
    return VideoAsset(meta=meta, proxy=proxy, pts_path=str(pts_path))


def load_times(asset: VideoAsset) -> list[float]:
    return frame_times(json.loads(Path(asset.pts_path).read_text(encoding="utf-8")), asset.meta.time_base)


def sample_indices(times: list[float], fps: float, start: int = 0, end: int | None = None) -> list[int]:
    """Frame indices to analyse at about `fps` between frame `start` and `end` (inclusive)."""
    last = len(times) - 1 if end is None else end
    if last < start:
        return []
    step = 1.0 / max(fps, 0.1)
    out, next_t = [], times[start]
    for index in range(start, last + 1):
        if times[index] + 1e-9 >= next_t:
            out.append(index)
            # advance on the ideal grid, not from the chosen frame, so the average rate is `fps`
            # (12 fps from a 30 fps video takes every 2nd and 3rd frame in turn, not every 3rd)
            while next_t <= times[index] + 1e-9:
                next_t += step
    return out


def iter_proxy(asset: VideoAsset, indices: list[int] | None = None, should_stop: Callable[[], bool] | None = None) -> Iterator[tuple[int, np.ndarray]]:
    """Proxy frames in order as (frame index, BGR image). `indices` must be sorted when given."""
    wanted = None if indices is None else set(indices)
    last = None if indices is None else (max(indices) if indices else -1)
    with av.open(asset.proxy.path) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for index, frame in enumerate(container.decode(stream)):
            if last is not None and index > last:
                break
            if should_stop and should_stop():
                break
            if wanted is None or index in wanted:
                yield index, frame.to_ndarray(format="bgr24")


def read_frame(asset: VideoAsset, index: int, source: str = "proxy") -> np.ndarray:
    """One upright BGR frame by index, from the proxy (fast, small) or the original (full resolution)."""
    index = max(0, min(index, asset.meta.frame_count - 1))
    path = asset.proxy.path if source == "proxy" else asset.meta.path
    rotation = 0 if source == "proxy" else asset.meta.rotation
    pts = read_pts(Path(path))[0]
    with av.open(path) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        target = pts[min(index, len(pts) - 1)]
        container.seek(target, stream=stream, backward=True, any_frame=False)
        image = None
        for frame in container.decode(stream):
            image = frame
            if frame.pts is not None and frame.pts >= target:
                break
        if image is None:
            raise VideoError(f"프레임 {index}을(를) 읽지 못했습니다.")
        return upright(image.to_ndarray(format="bgr24"), rotation)


def index_at(times: list[float], t: float) -> int:
    """The frame shown at time `t` (the last frame whose timestamp is not after it)."""
    import bisect

    return max(0, min(len(times) - 1, bisect.bisect_right(times, t + 1e-9) - 1))


def encode_jpeg(image: np.ndarray, quality: int = 82, max_side: int | None = None) -> bytes:
    import cv2

    if max_side and max(image.shape[:2]) > max_side:
        ratio = max_side / max(image.shape[:2])
        image = cv2.resize(image, (round(image.shape[1] * ratio), round(image.shape[0] * ratio)), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise VideoError("JPEG 인코딩에 실패했습니다.")
    return data.tobytes()
