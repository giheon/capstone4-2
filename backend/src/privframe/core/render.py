"""Render the redacted copy straight from the original stream (design §6.2, FR-10).

One PyAV pass: the original video stream is decoded, frames that carry regions are changed in their native YUV
planes (no BGR round trip, so every pixel outside a region reaches the encoder exactly as decoded) and each frame
is encoded again with the timestamp it came with. Every other stream is copied packet by packet. Container and
stream tags, chapters, colour properties and the display rotation are written again, so the output differs from
the original only by the redactions and one generation of high-quality video encoding. Tags the plan decided
to remove (the shooting location) are the one exception: they are left out.

Regions arrive in upright display pixels; libav decodes in sensor orientation, so they are turned back with the
inverse of the display rotation before any plane is touched. A region is always a mosaic, and its block size is
fixed by the kind of target (ADR-0007).
"""
from __future__ import annotations

import struct
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np

from .domain import VideoMeta
from .geometry import Region
from .video import VideoError, read_pts

# pixel format -> (bit depth, log2 horizontal chroma subsampling, log2 vertical chroma subsampling)
PIX_FMTS: dict[str, tuple[int, int, int]] = {
    "yuv420p": (8, 1, 1), "yuvj420p": (8, 1, 1), "yuv422p": (8, 1, 0), "yuvj422p": (8, 1, 0),
    "yuv444p": (8, 0, 0), "yuvj444p": (8, 0, 0),
    "yuv420p10le": (10, 1, 1), "yuv422p10le": (10, 1, 0), "yuv444p10le": (10, 0, 0),
}  # fmt: skip

# Blocks along the region's short side, at most (fewer for a region with strength > 1). Measured in M4 (assumption
# U10) on 289 faces: at 4 to 6 blocks no redacted face is closer than 0.37 to its original by the face embedding
# (the same person unredacted: 0.82–0.99).
MOSAIC_BLOCKS = 6
VIDEOTOOLBOX_QUALITY = 75  # -q:v, 1-100
MOV_SUFFIXES = {".mp4", ".mov", ".m4v", ".3gp", ".3g2"}
MUXER_TAGS = {"major_brand", "minor_version", "compatible_brands", "encoder"}  # written by the muxer itself
X264_PROFILES = {
    "Baseline": "baseline", "Constrained Baseline": "baseline", "Main": "main", "High": "high",
    "High 10": "high10", "High 4:2:2": "high422", "High 4:4:4 Predictive": "high444",
}  # fmt: skip
X265_PROFILES = {"Main": "main", "Main 10": "main10"}
VIDEOTOOLBOX_H264_LEVELS = {30, 31, 32, 40, 41, 42, 50, 51, 52}


@dataclass(frozen=True)
class EncoderSpec:
    codec: str  # libx264 | libx265 | h264_videotoolbox | hevc_videotoolbox | ffv1
    options: dict[str, str]
    label: str


@dataclass
class RenderResult:
    path: str
    frames: int
    modified_frames: int
    codec_label: str
    seconds: float
    size_bytes: int


def encoder_for(candidate_id: str, meta: VideoMeta) -> EncoderSpec:
    """The encoder for a `render.encoder` catalog candidate, kept in the source's codec family.

    H.264 stays H.264 (libx264 also writes High 10 / 4:2:2), HEVC stays HEVC, and any other source becomes
    H.264 when it is 8-bit and HEVC when it is deeper. Profile and level follow the source where the encoder
    has a matching setting; the pixel format is always the source's and is set by `render`.
    """
    hevc = meta.codec == "hevc" or (meta.codec != "h264" and meta.bit_depth > 8)
    h264_level = "1b" if meta.level == 9 else f"{meta.level // 10}.{meta.level % 10}"
    if candidate_id in ("x264-crf16", "x264-crf14"):
        crf = candidate_id[-2:]
        options = {"crf": crf, "preset": "slow"}
        if hevc:
            if meta.codec == "hevc" and meta.profile in X265_PROFILES:
                options["profile"] = X265_PROFILES[meta.profile]
            if meta.codec == "hevc" and meta.level > 0:
                options["level"] = str(meta.level)  # general_level_idc as ffprobe reports it, 93 = 3.1
            options["x265-params"] = "log-level=error"
            return EncoderSpec("libx265", options, f"libx265 · CRF {crf} · slow")
        if meta.codec == "h264" and meta.profile in X264_PROFILES:
            options["profile"] = X264_PROFILES[meta.profile]
        if meta.codec == "h264" and meta.level > 0:
            options["level"] = h264_level
        return EncoderSpec("libx264", options, f"libx264 · CRF {crf} · slow")
    if candidate_id == "videotoolbox":
        # constant quality (-q:v) is honoured on Apple Silicon only; allow_sw keeps odd sizes working
        options = {"flags": "+qscale", "global_quality": str(VIDEOTOOLBOX_QUALITY * 118), "allow_sw": "1"}
        if hevc or meta.bit_depth > 8:  # h264_videotoolbox is 8-bit only
            options["profile"] = "main10" if meta.bit_depth > 8 else "main"
            return EncoderSpec("hevc_videotoolbox", options, f"hevc_videotoolbox · 품질 {VIDEOTOOLBOX_QUALITY}")
        if meta.profile in ("Baseline", "Constrained Baseline", "Main", "High"):
            options["profile"] = X264_PROFILES[meta.profile]
        if meta.level in VIDEOTOOLBOX_H264_LEVELS:
            options["level"] = h264_level
        return EncoderSpec("h264_videotoolbox", options, f"h264_videotoolbox · 품질 {VIDEOTOOLBOX_QUALITY}")
    if candidate_id == "ffv1":
        options = {"level": "3", "coder": "1", "context": "1", "slices": "24", "slicecrc": "1", "g": "1"}
        return EncoderSpec("ffv1", options, "FFV1 · 무손실")
    raise ValueError(f"unknown render.encoder candidate: {candidate_id}")


# ---------------------------------------------------------------------------------------------------------
# regions on native planes


def coded_box(region: Region, rotation: int, width: int, height: int) -> tuple[float, float, float, float]:
    """The region's box (x, y, w, h) in coded (sensor) pixels of a `width` × `height` coded frame.

    `rotation` is the clockwise turn that makes the coded frame upright; this is its inverse.
    """
    x, y, w, h = region.x, region.y, region.w, region.h
    if rotation == 90:
        return (y, height - x - w, h, w)
    if rotation == 180:
        return (width - x - w, height - y - h, w, h)
    if rotation == 270:
        return (width - y - h, x, h, w)
    return (x, y, w, h)


def pixel_box(box: tuple[float, float, float, float], width: int, height: int, align_x: int = 1, align_y: int = 1) -> tuple[int, int, int, int]:
    """Whole luma pixels (x0, y0, x1, y1) covering the box, grown to the chroma grid and clamped to the frame."""
    x, y, w, h = box
    x0 = max(0, int(np.floor(x)) // align_x * align_x)
    y0 = max(0, int(np.floor(y)) // align_y * align_y)
    x1 = min(width, -(-int(np.ceil(x + w)) // align_x) * align_x)
    y1 = min(height, -(-int(np.ceil(y + h)) // align_y) * align_y)
    return x0, y0, x1, y1


def _inside(region: Region, box: tuple[float, float, float, float], x0: int, y0: int, shape: tuple[int, int], sx: int, sy: int) -> np.ndarray | None:
    """Which samples of a plane crop starting at (x0, y0) the region covers; None means all of them.

    An ellipse is the one inscribed in the unclamped box; a sample is covered when its centre is inside.
    """
    if region.shape != "ellipse":
        return None
    xs = ((x0 + np.arange(shape[1]) + 0.5) * (1 << sx) - (box[0] + box[2] / 2)) / (box[2] / 2)
    ys = ((y0 + np.arange(shape[0]) + 0.5) * (1 << sy) - (box[1] + box[3] / 2)) / (box[3] / 2)
    return ys[:, None] ** 2 + xs[None, :] ** 2 <= 1.0


def _mosaic(roi: np.ndarray, block_x: int, block_y: int) -> np.ndarray:
    """Every block replaced by its mean; the grid starts at the crop's top-left corner."""
    height, width = roi.shape
    ys, xs = np.arange(0, height, block_y), np.arange(0, width, block_x)
    sums = np.add.reduceat(np.add.reduceat(roi.astype(np.float64), ys, axis=0), xs, axis=1)
    counts = np.diff(np.append(ys, height))[:, None] * np.diff(np.append(xs, width))[None, :]
    means = np.rint(sums / counts).astype(roi.dtype)
    return np.repeat(np.repeat(means, block_y, axis=0), block_x, axis=1)[:height, :width]


def redact_planes(planes: Sequence[np.ndarray], regions: Sequence[Region], pix_fmt: str, rotation: int = 0) -> None:
    """Apply the regions' mosaics to the Y, U, V planes of one coded frame, in place.

    Luma and chroma get the same blocks on the same area: the box is grown to the chroma grid and the block size
    is divided by the subsampling of each plane. Nothing outside the box changes.
    """
    _, shift_x, shift_y = PIX_FMTS[pix_fmt]
    height, width = planes[0].shape
    for region in regions:
        box = coded_box(region, rotation, width, height)
        short = min(box[2], box[3])
        x0, y0, x1, y1 = pixel_box(box, width, height, 1 << shift_x, 1 << shift_y)
        if short <= 0 or x1 <= x0 or y1 <= y0:
            continue
        block = int(np.ceil((short + 4) * region.strength / MOSAIC_BLOCKS))  # + 4: the box grown to whole chroma samples
        block += block % 2  # even, so that chroma blocks cover the same pixels as luma blocks
        for index, plane in enumerate(planes[:3]):
            sx, sy = (shift_x, shift_y) if index else (0, 0)
            px0, py0 = x0 >> sx, y0 >> sy
            roi = plane[py0 : -(-y1 >> sy), px0 : -(-x1 >> sx)]
            new = _mosaic(roi, max(1, block >> sx), max(1, block >> sy))
            inside = _inside(region, box, px0, py0, roi.shape, sx, sy)
            if inside is None:
                roi[...] = new
            else:
                roi[inside] = new[inside]


def region_mask(regions: Sequence[Region], pix_fmt: str, rotation: int, width: int, height: int) -> np.ndarray:
    """The luma pixels of a coded frame that `redact_planes` changes for these regions (uint8, 1 = changed)."""
    _, shift_x, shift_y = PIX_FMTS[pix_fmt]
    mask = np.zeros((height, width), np.uint8)
    for region in regions:
        box = coded_box(region, rotation, width, height)
        x0, y0, x1, y1 = pixel_box(box, width, height, 1 << shift_x, 1 << shift_y)
        if min(box[2], box[3]) <= 0 or x1 <= x0 or y1 <= y0:
            continue
        inside = _inside(region, box, x0, y0, (y1 - y0, x1 - x0), 0, 0)
        mask[y0:y1, x0:x1] |= 1 if inside is None else inside.astype(np.uint8)
    return mask


def plane_views(frame: av.VideoFrame, depth: int) -> list[np.ndarray]:
    """Arrays over the frame's own Y, U, V memory (line padding cut off); writing to them changes the frame."""
    dtype = np.dtype(np.uint8) if depth == 8 else np.dtype("<u2")
    return [np.frombuffer(p, dtype).reshape(p.height, -1)[:, : p.width] for p in frame.planes[:3]]


# ---------------------------------------------------------------------------------------------------------
# the pass over the original


def main_video(container: av.container.InputContainer) -> av.VideoStream:
    for stream in container.streams.video:
        if not stream.disposition & av.stream.Disposition.attached_pic:
            return stream
    raise VideoError("영상 스트림이 없는 파일입니다.")


def frame_numbers(path: str | Path) -> dict[int, int]:
    """pts -> frame index in presentation order: the numbering `video.probe` gives the case graph."""
    return {pts: index for index, pts in enumerate(read_pts(Path(path))[0])}


def _encoder_pix_fmt(codec: str, pix_fmt: str) -> str:
    """The source format, or an encoder format with the same samples when the encoder lacks it."""
    supported = {f.name for f in av.codec.Codec(codec, "w").video_formats or []}
    same_samples = {"yuvj420p": "yuv420p", "yuvj422p": "yuv422p", "yuvj444p": "yuv444p", "yuv420p10le": "p010le"}
    for candidate in (pix_fmt, same_samples.get(pix_fmt)):
        if candidate in supported:
            return candidate
    raise VideoError(f"{codec} 인코더는 원본 픽셀 포맷({pix_fmt})을 지원하지 않습니다.")


def _hdr10_params(path: str) -> str:
    """x265 options that repeat the source's HDR10 mastering-display and light-level data, or "".

    FFmpeg hands these to libx265 as global side data of the encoder, which PyAV cannot set, so they are spelled
    out. Dolby Vision has no such spelling and is not carried over.
    """
    with av.open(path) as container:
        video = main_video(container)
        next(container.decode(video), None)
        found = {kind.name: data for kind, data in video.codec_context.decoded_side_data.items()}
    parts = []
    display = found.get("MASTERING_DISPLAY_METADATA", b"")
    if len(display) >= 88:  # AVMasteringDisplayMetadata: R, G, B, white point (x, y), min and max luminance, two flags
        v = struct.unpack("<22i", display[:88])
        if v[20] and v[21] and all(v[1:20:2]):
            rx, ry, gx, gy, bx, by, wx, wy = (round(v[i] * 50000 / v[i + 1]) for i in range(0, 16, 2))
            low, high = round(v[16] * 10000 / v[17]), round(v[18] * 10000 / v[19])
            parts.append(f"master-display=G({gx},{gy})B({bx},{by})R({rx},{ry})WP({wx},{wy})L({high},{low})")
    light = found.get("CONTENT_LIGHT_LEVEL", b"")
    if len(light) >= 8:
        parts.append("max-cll=%d,%d" % struct.unpack("<2I", light[:8]))
    return ":".join(parts)


def _add_video(sink: av.container.OutputContainer, video: av.VideoStream, encoder: EncoderSpec, rotation: int, mov: bool, keep: Callable[[str], bool]) -> av.VideoStream:
    """The output video stream: the source's geometry, pixel format, time base, colour, tags and rotation."""
    source = video.codec_context
    options = dict(encoder.options)
    if encoder.codec == "libx265":
        options["x265-params"] = ":".join(x for x in (options.get("x265-params"), _hdr10_params(video.container.name)) if x)
    try:
        stream = sink.add_stream(encoder.codec, rate=video.average_rate or 30, options=options, time_base=video.time_base)
    except ValueError as error:
        raise VideoError(f"{sink.format.name} 컨테이너에 {encoder.codec} 영상을 담을 수 없습니다.") from error
    stream.width, stream.height = source.width, source.height
    stream.pix_fmt = _encoder_pix_fmt(encoder.codec, source.pix_fmt)
    stream.time_base = video.time_base
    stream.thread_type = "AUTO"  # PyAV defaults to slice threads, which makes x264 cut every frame into slices
    for name in ("color_range", "color_primaries", "color_trc", "colorspace", "chroma_sample_location"):
        setattr(stream.codec_context, name, getattr(source, name))
    if source.sample_aspect_ratio:
        stream.codec_context.sample_aspect_ratio = source.sample_aspect_ratio
    if mov and source.codec_tag in ("hvc1", "hev1") and encoder.codec in ("libx265", "hevc_videotoolbox"):
        stream.codec_context.codec_tag = source.codec_tag  # Apple players need hvc1 when the source had it
    stream.metadata.update({k: v for k, v in video.metadata.items() if keep(k)})
    stream.metadata["encoder"] = f"Lavc{'.'.join(map(str, av.library_versions['libavcodec']))} {encoder.codec}"
    stream.disposition = video.disposition
    matrix = source.coded_side_data.get("display_matrix")
    if matrix:
        stream.set_display_matrix(struct.unpack("<9i", matrix))
    elif rotation:
        stream.set_display_rotation(-rotation)
    return stream


def _open_sink(source: av.container.InputContainer, video: av.VideoStream, output: Path, encoder: EncoderSpec, rotation: int,
               skip: Sequence[av.stream.Stream], keep: Callable[[str], bool]) -> tuple[av.container.OutputContainer, av.VideoStream, dict[int, av.stream.Stream]]:  # fmt: skip
    """Open the output with the original's streams in the original's order and write the header. `keep(tag
    name)` says which container and stream tags are written again."""
    mov = output.suffix.lower() in MOV_SUFFIXES
    sink = av.open(str(output), mode="w")
    try:
        tags = {k: v for k, v in source.metadata.items() if k not in MUXER_TAGS and keep(k)}
        if mov:
            # without the track timescale the muxer rescales e.g. 1/600 to 1/19200 and the pts values change;
            # reverse-DNS keys (com.apple.quicktime.…) only survive as `mdta` tags
            flags = "+faststart+use_metadata_tags" if any("." in key for key in tags) else "+faststart"
            sink.container_options.update(video_track_timescale=str(video.time_base.denominator), movflags=flags)
        sink.metadata.update(tags)
        sink.set_chapters(source.chapters())
        out_video: av.VideoStream | None = None
        copies: dict[int, av.stream.Stream] = {}
        for stream in source.streams:
            if stream is video:
                out_video = _add_video(sink, video, encoder, rotation, mov, keep)
            elif stream not in skip:
                try:
                    copy = sink.add_stream_from_template(stream, opaque=True)
                except ValueError as error:
                    raise VideoError(f"{sink.format.name} 컨테이너에 스트림 #{stream.index}({stream.type})을 복사할 수 없습니다.") from error
                copy.metadata.update({k: v for k, v in stream.metadata.items() if keep(k)})
                copy.disposition = stream.disposition
                copies[stream.index] = copy
        assert out_video is not None
        sink.start_encoding()
    except BaseException:
        sink.close()
        raise
    return sink, out_video, copies


def render(
    meta: VideoMeta,
    output: Path,
    regions: Mapping[int, Sequence[Region]],
    encoder: EncoderSpec,
    *,
    drop_tag: Callable[[str], bool] | None = None,
    progress: Callable[[str, float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RenderResult:
    """Write the redacted copy of `meta.path` to `output` (container chosen by the suffix).

    `regions` maps original frame index (presentation order) to the regions on that frame. Container and
    stream tags for which `drop_tag(name)` is true are not written. Raises VideoError when stopped or when the
    source cannot be rendered; a partial output is removed.
    """
    started = time.perf_counter()
    output = Path(output)
    if output.resolve() == Path(meta.path).resolve():
        raise VideoError("원본 파일 위에는 렌더할 수 없습니다.")
    output.parent.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda text, fraction: None)
    say("원본 디코드", 0.0)
    try:
        frames, modified = _render(meta, output, regions, encoder, say, should_stop, (lambda name: not drop_tag(name)) if drop_tag else (lambda name: True))
    except BaseException as error:
        output.unlink(missing_ok=True)
        if isinstance(error, av.error.FFmpegError):
            raise VideoError(f"렌더 실패 ({encoder.codec}): {error}") from error
        raise
    say("완료", 1.0)
    return RenderResult(str(output), frames, modified, encoder.label, round(time.perf_counter() - started, 3), output.stat().st_size)


def _render(meta: VideoMeta, output: Path, regions: Mapping[int, Sequence[Region]], encoder: EncoderSpec,
            say: Callable[[str, float], None], should_stop: Callable[[], bool] | None, keep: Callable[[str], bool]) -> tuple[int, int]:  # fmt: skip
    numbers = frame_numbers(meta.path)
    with av.open(meta.path) as source:
        video = main_video(source)
        pix_fmt = video.codec_context.pix_fmt or ""
        if pix_fmt not in PIX_FMTS:
            raise VideoError(f"지원하지 않는 픽셀 포맷입니다: {pix_fmt or '알 수 없음'} (지원: {', '.join(PIX_FMTS)}).")
        depth = PIX_FMTS[pix_fmt][0]
        video.thread_type = "AUTO"
        # Tracks libav has no codec for (timecode, chapter text, Apple `mebx` …) cannot be copied into mp4/mov:
        # the muxer takes them and then fails at the trailer. It writes timecode and chapter tracks again from
        # the tags; the rest is left out. Other containers get one try with them and one without.
        opaque = [s for s in source.streams if s.codec_context is None]
        skip = opaque if output.suffix.lower() in MOV_SUFFIXES else []
        try:
            sink, out_video, copies = _open_sink(source, video, output, encoder, meta.rotation, skip, keep)
        except av.error.FFmpegError:
            if skip or not opaque:
                raise
            skip = opaque
            sink, out_video, copies = _open_sink(source, video, output, encoder, meta.rotation, skip, keep)
        if skip:
            say(f"코덱 없는 데이터 트랙 {len(skip)}개는 복사하지 않음", 0.0)
        with sink:
            total = max(1, meta.frame_count)
            step = max(1, total // 10)
            tail = _VideoTail(sink, video, sorted(numbers), output.suffix.lower() in MOV_SUFFIXES)
            count = modified = 0
            for packet in source.demux():
                if should_stop and should_stop():
                    raise VideoError("렌더가 중단되었습니다.")
                if packet.stream is not video:
                    if packet.size and packet.stream.index in copies:
                        packet.stream = copies[packet.stream.index]
                        sink.mux(packet)
                    continue
                tail.media_duration += packet.duration or 0
                for frame in packet.decode():  # the empty packet at the end flushes the decoder
                    wanted = regions.get(numbers.get(frame.pts, count) if frame.pts is not None else count)
                    if wanted:
                        frame.make_writable()  # the decoder may still predict from this picture
                        redact_planes(plane_views(frame, depth), wanted, pix_fmt, meta.rotation)
                        modified += 1
                    frame.pict_type = av.video.frame.PictureType.NONE  # let the encoder choose frame types
                    tail.mux(out_video.encode(frame))
                    count += 1
                    if count % step == 0:
                        say("프레임 수정 · 재인코딩", min(0.95, 0.95 * count / total))
            tail.mux(out_video.encode(None))
            say("스트림 복사 · 마무리", 0.97)
            tail.close()
    return count, modified


class _VideoTail:
    """Gives encoded packets the durations of the original and muxes them one behind.

    Encoders do not carry durations. Every packet gets its frame's display time (next pts − pts; the last frame
    ends where the original stream ends), which fixes the presentation end. mp4/mov also derive the media
    duration from the last packet in decode order, so that packet is held back and stretched until the track
    is as long as the original's packet durations add up to. Without this the stream and file durations (and
    the average frame rate ffprobe derives from them) drift by a few ticks.
    """

    def __init__(self, sink: av.container.OutputContainer, video: av.VideoStream, pts: list[int], mov: bool) -> None:
        self.sink, self.mov = sink, mov
        start = video.start_time if video.start_time is not None else pts[0]
        self.end = start + video.duration if video.duration else None
        self.shown = {a: b - a for a, b in zip(pts, pts[1:])}
        if self.end is not None and self.end > pts[-1]:
            self.shown[pts[-1]] = self.end - pts[-1]
        self.media_duration = 0  # sum of the original's video packet durations, filled while demuxing
        self.first_dts: int | None = None
        self.held: av.Packet | None = None

    def mux(self, packets: Sequence[av.Packet]) -> None:
        for packet in packets:
            if packet.pts in self.shown:
                packet.duration = self.shown[packet.pts]
            if self.held is not None:
                self.sink.mux(self.held)
            elif packet.dts is not None:
                self.first_dts = packet.dts
            self.held = packet

    def close(self) -> None:
        last, self.held = self.held, None
        if last is None:
            return
        if self.mov and self.media_duration and last.dts is not None and self.first_dts is not None:
            rest = self.media_duration - (last.dts - self.first_dts)
            if rest > 0 and (self.end is None or last.pts is None or last.pts + rest <= self.end):
                last.duration = rest
        self.sink.mux(last)
