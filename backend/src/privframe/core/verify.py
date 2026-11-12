"""Check a rendered output against its original and report the numbers (design §6.4, NFR-09).

Rows in a fixed order. The first five are exact comparisons (frame count, timestamps, packets of the copied
streams, container facts, pixel format and profile), the sixth measures how much the encoder changed the
pixels that were not redacted, and VMAF is recorded for reference. The rest are about the redaction itself:

- every frame the plan says to cover carries a mosaic, and the output's pixels there are the mosaic and not the
  original (all frames, exact: this is the check that a single uncovered frame cannot pass);
- nothing inside a redacted area can still be identified (a sample of the redacted frames);
- nothing that was to be redacted is recognisable anywhere else in any frame (all frames).

A row with verdict "fail" blocks the case. Both files are decoded once, side by side, with PyAV. Quality is
measured on the luma plane only: PSNR-Y on every frame, SSIM (11×11 Gaussian window, σ 1.5) on an even sample
because it costs about a hundred times more.
"""
from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import zip_longest
from pathlib import Path
from typing import Any

import av
import cv2
import numpy as np

from .domain import VerificationRow, VideoMeta
from .geometry import Region
from .render import PIX_FMTS, frame_numbers, main_video, plane_views, redact_planes, region_mask
from .video import VideoError, ffprobe_json, is_location_tag, location_tags, rotation_cw, upright

SSIM_WINDOW = 11
SSIM_FRAMES = 60  # at most this many redacted frames and this many untouched frames
INSIDE_FRAMES = 120  # redacted frames on which the inside of the regions is checked for identifiability
SCAN_SHORT_SIDE = 640  # every output frame is reduced to this for the whole-frame scan
# The renderer changes nothing outside a region's box, so excluding the region grown by one SSIM window keeps
# every counted pixel, and every window around it, clear of the redaction.
GUARD = SSIM_WINDOW

# differences the muxer or encoder decides and no setting of ours can remove: reported, never a failure
ALLOWED = {
    "format.tags.encoder", "format.tags.major_brand", "format.tags.minor_version", "format.tags.compatible_brands",
    "video.tags.encoder", "video.chroma_location", "video.field_order", "data_tracks",
}  # fmt: skip
VIDEO_KEYS = (
    "width", "height", "time_base", "start_pts", "duration_ts", "nb_frames", "sample_aspect_ratio",
    "display_aspect_ratio", "color_range", "color_space", "color_transfer", "color_primaries", "chroma_location",
    "field_order", "codec_tag_string",
)  # fmt: skip
COPIED_KEYS = (
    "codec_name", "profile", "codec_tag_string", "time_base", "start_pts", "duration_ts", "nb_frames", "sample_rate",
    "channels", "channel_layout", "sample_fmt", "bits_per_sample", "initial_padding", "width", "height", "pix_fmt",
    "extradata_size",
)  # fmt: skip
OPAQUE_DATA = (None, "unknown", "none", "bin_data")  # timecode, chapter text, Apple mebx …: libav cannot copy them
HDR_SIDE_DATA = {
    "MASTERING_DISPLAY_METADATA", "CONTENT_LIGHT_LEVEL", "DYNAMIC_HDR_PLUS", "DYNAMIC_HDR_VIVID", "DOVI_RPU_BUFFER",
    "DOVI_METADATA", "AMBIENT_VIEWING_ENVIRONMENT",
}  # fmt: skip


@dataclass(frozen=True)
class Thresholds:
    """Per-frame minimums outside the redacted areas (assumption U9, settled by measurement in M4).

    The check exists to catch damage outside the redacted areas (a wrong frame, shifted colours, a mask that
    leaks), which shows up far below 30 dB. It must not fail on ordinary encoder loss: measured 2026-11-11 on
    the team's two 720p clips, libx264 CRF 16 (the default encoder) has a per-frame minimum of about
    41 dB / 0.985 with or without redaction, CRF 14 about 42.6 / 0.989, CRF 10 about 45.5 / 0.993.
    The defaults leave room below the CRF 16 floor for harder footage. The design's original target
    (45 dB / 0.99) would need CRF 10 and files three to five times the source size.
    """

    psnr_y_min: float = 38.0
    ssim_min: float = 0.97


def passed(rows: list[VerificationRow]) -> bool:
    return all(row.verdict != "fail" for row in rows)


@dataclass(frozen=True)
class Leak:
    """Something that was to be redacted and can be recognised on an output frame."""

    frame: int
    ref: str  # decision id of what was recognised
    box: tuple[float, float, float, float]  # where, in upright original pixels
    what: str
    span: tuple[int, int]  # the frames a repair should cover (the checked frame, or up to the neighbouring checked frames)


class Checker(Protocol):
    """Looks at output frames for things that should no longer be identifiable (core/perception/verify.py).

    `inside` is asked about a sample of the redacted frames, `scan` sees every frame. `scan` gets the upright
    output frame reduced to SCAN_SHORT_SIDE, the factor from its pixels to original pixels, and a function that
    returns the full-size upright output and original frames, to be called only when something has to be
    looked at closely.
    """

    leaks: list[Leak]

    def inside(self, output: np.ndarray, original: np.ndarray, frame: int, regions: Sequence[Region]) -> int: ...

    def scan(self, frame: int, small: np.ndarray, scale: float, full: Callable[[], tuple[np.ndarray, np.ndarray]], regions: Sequence[Region]) -> None: ...

    def summary(self) -> tuple[str, str]: ...


def verify(
    meta: VideoMeta,
    output: Path,
    regions: Mapping[int, Sequence[Region]],
    *,
    expected: Mapping[str, Sequence[int]] | None = None,
    checker: Checker | None = None,
    dropped_tags: Sequence[str] = (),
    thresholds: Thresholds = Thresholds(),
    progress: Callable[[str, float], None] | None = None,
    vmaf: bool = True,
) -> list[VerificationRow]:
    """Compare `output` with the original described by `meta`; `regions` are the ones given to `render`.

    `expected` maps each redacting decision to the frames it must cover, worked out from the plan
    (geometry.expected_frames); without it the regions themselves are taken as the expectation.
    `checker` judges whether redacted things can still be identified. Merely detecting a face-shaped mosaic
    does not count: a detector fires on most of them, which says nothing about who it is (measured in M4, U10).
    `dropped_tags` are the tags the plan removed; they must be gone from the output. `vmaf=False` skips the
    slowest, informational measurement.
    """
    output = Path(output)
    say = progress or (lambda text, fraction: None)
    if not output.is_file():
        raise VideoError(f"검증할 출력 파일이 없습니다: {output}")
    say("프레임 비교 · PSNR · SSIM · 가림 확인", 0.02)
    try:
        frames = _compare_frames(meta, output, regions, checker, say)
        say("복사 스트림 · 메타데이터 비교", 0.72)
        rows = [_row_count(frames), _row_pts(frames), _row_packets(meta, output)]
        original, rendered = ffprobe_json(Path(meta.path)), ffprobe_json(output)
    except av.error.FFmpegError as error:
        raise VideoError(f"출력 검증 실패: {error}") from error
    rows += [_row_meta(original, rendered, frames, set(dropped_tags)), _row_format(original, rendered), _row_quality(frames, thresholds)]
    say("VMAF 측정", 0.78)
    rows.append(_row_vmaf(meta, output) if vmaf else _info("VMAF (전체 프레임)", "측정 안 함 (생략)"))
    rows.append(_row_applied(frames, regions, expected))
    inside_note, scan_note = checker.summary() if checker is not None else ("", "")
    rows.append(_row_inside(frames, checker is not None, inside_note))
    rows.append(_row_scan(frames, checker, scan_note))
    if dropped_tags:
        rows.append(_row_dropped(list(dropped_tags), rendered))
    say("검증 완료", 1.0)
    return rows


def _info(check: str, output: str, note: str | None = None) -> VerificationRow:
    return VerificationRow(check=check, original="—", output=output, verdict="info", note=note)


def _even(items: Sequence[int], limit: int) -> list[int]:
    """At most `limit` items, evenly spread and including both ends."""
    if len(items) <= limit:
        return list(items)
    return [items[i] for i in sorted({round(k * (len(items) - 1) / (limit - 1)) for k in range(limit)})]


# ---------------------------------------------------------------------------------------------------------
# one decode of both files


@dataclass
class _Frames:
    time_base: tuple[Fraction, Fraction]
    pts: tuple[list[int | None], list[int | None]]
    comparable: str | None  # None, or why the pixels cannot be compared
    psnr: list[float]
    ssim: list[float]
    ssim_split: tuple[int, int]  # sampled frames with regions, without
    modified: int
    hdr: tuple[dict[str, str], dict[str, str]]  # HDR side data of the first frame: kind -> digest
    inside_checked: int = 0
    inside_left: int = 0
    scanned: int = 0
    unapplied: list[int] = field(default_factory=list)  # frames whose regions show the original, not the mosaic


def _ssim(a: np.ndarray, b: np.ndarray, peak: int, outside: np.ndarray | None) -> float | None:
    """Mean SSIM (Wang et al.: Gaussian window 11×11, σ 1.5, K1 0.01, K2 0.03) over the pixels marked outside."""
    a, b = a.astype(np.float32), b.astype(np.float32)

    def smooth(image: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(image, (SSIM_WINDOW, SSIM_WINDOW), 1.5, borderType=cv2.BORDER_REFLECT)

    mean_a, mean_b = smooth(a), smooth(b)
    var_a, var_b = smooth(a * a) - mean_a * mean_a, smooth(b * b) - mean_b * mean_b
    covariance = smooth(a * b) - mean_a * mean_b
    c1, c2 = (0.01 * peak) ** 2, (0.03 * peak) ** 2
    values = ((2 * mean_a * mean_b + c1) * (2 * covariance + c2)) / ((mean_a * mean_a + mean_b * mean_b + c1) * (var_a + var_b + c2))
    keep = np.ones(a.shape, bool) if outside is None else outside.astype(bool)
    edge = SSIM_WINDOW // 2  # windows that would reach over the frame edge
    keep[:edge], keep[-edge:], keep[:, :edge], keep[:, -edge:] = False, False, False, False
    return float(values[keep].mean()) if keep.any() else None


def _hdr_side_data(frame: av.VideoFrame) -> dict[str, str]:
    """HDR side data on a frame. Static data is compared by content, per-frame data (Dolby Vision, HDR10+) by kind."""
    static = ("MASTERING_DISPLAY_METADATA", "CONTENT_LIGHT_LEVEL")
    return {
        kind.name: hashlib.md5(bytes(data)).hexdigest()[:8] if kind.name in static else "있음"
        for kind, data in frame.side_data.items()
        if kind.name in HDR_SIDE_DATA
    }


def _compare_frames(meta: VideoMeta, output: Path, regions: Mapping[int, Sequence[Region]], checker: Checker | None,
                    say: Callable[[str, float], None]) -> _Frames:  # fmt: skip
    numbers = frame_numbers(meta.path)
    modified = sorted(i for i, items in regions.items() if items and 0 <= i < meta.frame_count)
    touched = set(modified)
    ssim_modified = set(_even(modified, SSIM_FRAMES))
    ssim_plain = set(_even([i for i in range(meta.frame_count) if i not in touched], SSIM_FRAMES))
    inside_at = set(_even(modified, INSIDE_FRAMES)) if checker else set()
    kernel = np.ones((2 * GUARD + 1, 2 * GUARD + 1), np.uint8)
    step = max(1, meta.frame_count // 10)
    with av.open(meta.path) as source, av.open(str(output)) as rendered:
        a, b = main_video(source), main_video(rendered)
        a.thread_type = b.thread_type = "AUTO"
        pix_fmt = a.codec_context.pix_fmt or ""
        comparable = None
        if pix_fmt not in PIX_FMTS:
            comparable = f"지원하지 않는 픽셀 포맷 {pix_fmt}"
        elif (pix_fmt, a.width, a.height) != (b.codec_context.pix_fmt, b.width, b.height):
            comparable = f"픽셀 포맷 · 해상도가 다름 ({pix_fmt} {a.width}×{a.height} → {b.codec_context.pix_fmt} {b.width}×{b.height})"
        depth = PIX_FMTS.get(pix_fmt, (8, 1, 1))[0]
        peak = (1 << depth) - 1
        ratio = min(1.0, SCAN_SHORT_SIDE / max(1, min(b.width, b.height)))
        small_size = {"width": max(2, round(b.width * ratio) // 2 * 2), "height": max(2, round(b.height * ratio) // 2 * 2)}
        out = _Frames(
            time_base=(Fraction(a.time_base.numerator, a.time_base.denominator), Fraction(b.time_base.numerator, b.time_base.denominator)),
            pts=([], []), comparable=comparable, psnr=[], ssim=[], ssim_split=(len(ssim_modified), len(ssim_plain)), modified=len(modified),
            hdr=({}, {}),
        )  # fmt: skip
        for count, (fa, fb) in enumerate(zip_longest(source.decode(a), rendered.decode(b))):
            if fa is not None:
                out.pts[0].append(fa.pts)
            if fb is not None:
                out.pts[1].append(fb.pts)
            if fa is None or fb is None:
                continue
            if count == 0:
                out.hdr[0].update(_hdr_side_data(fa))
                out.hdr[1].update(_hdr_side_data(fb))
            index = numbers.get(fa.pts, count) if fa.pts is not None else count
            here = regions.get(index) or ()
            if comparable is None:
                ya, yb = plane_views(fa, depth)[0], plane_views(fb, depth)[0]
                mask = region_mask(here, pix_fmt, meta.rotation, a.width, a.height) if here else None
                outside = 1 - cv2.dilate(mask, kernel) if mask is not None else None
                pixels = ya.size if outside is None else int(outside.sum())
                if pixels:
                    mse = cv2.norm(ya, yb, cv2.NORM_L2SQR, outside) / pixels
                    out.psnr.append(float("inf") if mse == 0 else 10 * float(np.log10(peak * peak / mse)))
                    if index in ssim_modified or index in ssim_plain:
                        value = _ssim(ya, yb, peak, outside)
                        if value is not None:
                            out.ssim.append(value)
                if mask is not None and mask.any() and not _mosaic_applied(ya, yb, here, mask, pix_fmt, meta.rotation):
                    out.unapplied.append(index)

            def full(fa: av.VideoFrame = fa, fb: av.VideoFrame = fb) -> tuple[np.ndarray, np.ndarray]:
                return upright(fb.to_ndarray(format="bgr24"), meta.rotation), upright(fa.to_ndarray(format="bgr24"), meta.rotation)

            if checker is not None:
                if index in inside_at:
                    image, before = full()
                    out.inside_checked += 1
                    out.inside_left += int(checker.inside(image, before, index, here) > 0)
                checker.scan(index, upright(fb.to_ndarray(format="bgr24", **small_size), meta.rotation), b.height / small_size["height"], full, here)
                out.scanned += 1
            if count % step == 0:
                say("프레임 비교 · PSNR · SSIM · 가림 확인", 0.02 + 0.68 * min(1.0, count / max(1, meta.frame_count)))
    return out


def _mosaic_applied(original: np.ndarray, output: np.ndarray, regions: Sequence[Region], mask: np.ndarray, pix_fmt: str, rotation: int) -> bool:
    """Is the output inside the regions the mosaic of the original rather than the original itself?

    The mosaic the renderer should have drawn is computed again from the original; the output must be at least
    as close to it as to the untouched original. (Where the mosaic changes nothing — a flat surface — both
    distances are the same and there is nothing to hide.)
    """
    wanted = original.copy()
    redact_planes([wanted], regions, pix_fmt, rotation)
    return cv2.norm(wanted, output, cv2.NORM_L2SQR, mask) <= cv2.norm(original, output, cv2.NORM_L2SQR, mask)


# ---------------------------------------------------------------------------------------------------------
# rows


def _row_count(frames: _Frames) -> VerificationRow:
    a, b = len(frames.pts[0]), len(frames.pts[1])
    return VerificationRow(check="프레임 수", original=f"{a:,}", output=f"{b:,}", verdict="pass" if a == b else "fail",
                           note=None if a == b else f"디코드된 프레임이 {abs(a - b):,}개 {'적음' if b < a else '많음'}")  # fmt: skip


def _row_pts(frames: _Frames) -> VerificationRow:
    (base_a, base_b), (pts_a, pts_b) = frames.time_base, frames.pts
    deltas = {y - x for x, y in zip(pts_a, pts_a[1:]) if x is not None and y is not None}
    kind = "가변 프레임레이트" if len(deltas) > 1 else "고정 프레임레이트"

    def moment(value: int | None, base: Fraction) -> Fraction | None:
        return None if value is None else value * base

    wrong = [i for i, (x, y) in enumerate(zip(pts_a, pts_b)) if moment(x, base_a) != moment(y, base_b)]
    same = not wrong and len(pts_a) == len(pts_b)
    note = None
    if wrong:
        first = wrong[0]
        note = f"첫 불일치 프레임 {first}: {pts_a[first]} → {pts_b[first]}"
    elif len(pts_a) != len(pts_b):
        note = "프레임 수가 달라 끝부분을 비교할 수 없음"
    if base_a != base_b:
        note = " · ".join(x for x in [note, f"출력 time_base {base_b.numerator}/{base_b.denominator}"] if x)
    return VerificationRow(
        check=f"PTS 목록 (time_base {base_a.numerator}/{base_a.denominator})",
        original=f"{kind} · {len(pts_a):,}개",
        output="전부 일치" if same else f"{len(wrong) + abs(len(pts_a) - len(pts_b)):,}개 불일치",
        verdict="pass" if same else "fail",
        note=note,
    )


def _packets(path: str | Path) -> tuple[dict[str, list[tuple[Any, ...]]], int]:
    """(pts, dts, duration, size, md5) of every packet of every copied stream, and the count of opaque tracks.

    The same lines `ffmpeg -c copy -f framemd5` prints. Streams are named by type and order ("audio0"), because
    the muxer puts the tracks it writes itself (timecode, chapters) after all others.
    """
    with av.open(str(path)) as container:
        video = main_video(container)
        names: dict[int, str] = {}
        opaque = 0
        for stream in container.streams:
            if stream is video:
                continue
            if stream.codec_context is None and getattr(stream, "name", None) in OPAQUE_DATA:
                opaque += 1
                continue
            names[stream.index] = f"{stream.type}{sum(1 for name in names.values() if name.startswith(stream.type))}"
        found: dict[str, list[tuple[Any, ...]]] = {name: [] for name in names.values()}
        if names:
            for packet in container.demux([container.streams[index] for index in names]):
                if packet.size:
                    found[names[packet.stream.index]].append((packet.pts, packet.dts, packet.duration, packet.size, hashlib.md5(bytes(packet)).hexdigest()))
    return found, opaque


def _row_packets(meta: VideoMeta, output: Path) -> VerificationRow:
    check = "오디오 framemd5"
    (a, opaque), (b, _) = _packets(meta.path), _packets(output)

    def summary(streams: dict[str, list[tuple[Any, ...]]]) -> str:
        digest = hashlib.md5(repr(sorted(streams.items())).encode()).hexdigest()[:8]
        return f"{sum(len(items) for items in streams.values()):,} 패킷 · md5 {digest}"

    skipped = f"코덱 없는 데이터 트랙 {opaque}개는 비교하지 않음" if opaque else None
    if not a:
        return VerificationRow(check=check, original="오디오 없음", output="오디오 없음", verdict="info", note=skipped)
    label = meta.audio if list(a) == ["audio0"] else f"스트림 {len(a)}개 ({', '.join(a)})"
    different = [name for name in a if a[name] != b.get(name)]
    note = skipped
    if different:
        name = different[0]
        theirs = b.get(name)
        if theirs is None:
            detail = f"{name}: 출력에 없음"
        elif len(theirs) != len(a[name]):
            detail = f"{name}: 패킷 {len(a[name]):,} → {len(theirs):,}"
        else:
            detail = f"{name}: {next(i for i, (x, y) in enumerate(zip(a[name], theirs)) if x != y)}번째 패킷부터 다름"
        note = " · ".join(x for x in [detail, skipped] if x)
    return VerificationRow(check=check, original=f"{label} · {summary(a)}", output=summary({name: b.get(name, []) for name in a}),
                           verdict="fail" if different else "pass", note=note)  # fmt: skip


def _main_stream(info: dict[str, Any]) -> dict[str, Any]:
    streams = info.get("streams") or []
    return next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), {})


def _facts(info: dict[str, Any]) -> dict[str, Any]:
    """Everything that has to survive the render, flattened to comparable keys."""
    facts: dict[str, Any] = {}
    container = info.get("format") or {}
    facts["format.duration"], facts["format.start_time"] = container.get("duration"), container.get("start_time")
    for key, value in (container.get("tags") or {}).items():
        facts[f"format.tags.{key}"] = value
    for number, chapter in enumerate(info.get("chapters") or []):
        facts[f"chapter{number}"] = (chapter.get("time_base"), chapter.get("start"), chapter.get("end"), sorted((chapter.get("tags") or {}).items()))
    main = _main_stream(info)
    labels: list[str] = []
    opaque: list[str] = []
    for stream in info.get("streams") or []:
        kind = str(stream.get("codec_type", "unknown"))
        if kind == "data" and stream.get("codec_name") in OPAQUE_DATA:
            opaque.append(str(stream.get("codec_tag_string") or "?"))
            continue
        label = "video" if stream is main else f"{kind}{sum(1 for x in labels if x.startswith(kind) and x != 'video')}"
        labels.append(label)
        for key in VIDEO_KEYS if stream is main else COPIED_KEYS:
            if key in stream:
                facts[f"{label}.{key}"] = stream[key]
        for key, value in (stream.get("tags") or {}).items():
            facts[f"{label}.tags.{key}"] = value
        facts[f"{label}.disposition"] = sorted(k for k, v in (stream.get("disposition") or {}).items() if v)
        facts[f"{label}.side_data"] = sorted(str(d.get("side_data_type")) for d in stream.get("side_data_list") or [])
    facts["streams"] = labels
    facts["data_tracks"] = sorted(opaque)
    facts["video.rotation"] = rotation_cw(main)
    return facts


def _allowed(key: str) -> bool:
    # the mp4 muxer rewrites the tx3g sample description; `ffmpeg -c copy` changes it the same way
    return key in ALLOWED or (key.startswith("subtitle") and key.endswith(".extradata_size"))


def _row_meta(original: dict[str, Any], rendered: dict[str, Any], frames: _Frames, dropped: set[str]) -> VerificationRow:
    a, b = _facts(original), _facts(rendered)
    a["video.hdr_side_data"], b["video.hdr_side_data"] = frames.hdr

    def summary(facts: dict[str, Any], info: dict[str, Any]) -> str:
        main = _main_stream(info)
        colour = "/".join(str(main[k]) for k in ("color_primaries", "color_transfer", "color_range") if main.get(k)) or "색 정보 없음"
        if facts["video.hdr_side_data"]:
            colour += " · HDR 메타 " + str(len(facts["video.hdr_side_data"]))
        tags = sum(1 for key in facts if key.startswith("format.tags."))
        return f"스트림 {len(facts['streams']) + len(facts['data_tracks'])} · 태그 {tags} · 챕터 {len(info.get('chapters') or [])} · 회전 {facts['video.rotation']}° · {colour}"

    different = sorted(key for key in set(a) | set(b) if a.get(key) != b.get(key))
    removed = [key for key in different if ".tags." in key and key.split(".tags.", 1)[1] in dropped and key not in b]
    different = [key for key in different if key not in removed]
    failing = [key for key in different if not _allowed(key)]
    allowed = [key for key in different if _allowed(key)]

    def listed(keys: list[str]) -> str:
        return ", ".join(keys[:8]) + (f" 외 {len(keys) - 8}개" if len(keys) > 8 else "")

    parts = []
    if failing:
        parts.append(f"다른 항목: {listed(failing)}")
    if allowed:
        parts.append(f"허용된 차이(먹서 · 인코더가 정함): {listed(allowed)}")
    if removed:
        parts.append(f"계획에 따라 삭제한 태그: {listed(removed)}")
    if a["data_tracks"] != b["data_tracks"]:
        parts.append(f"코덱 없는 데이터 트랙 {a['data_tracks'] or '없음'} → {b['data_tracks'] or '없음'} (복사 불가)")
    return VerificationRow(check="메타·챕터·회전·색 정보", original=summary(a, original), output=summary(b, rendered),
                           verdict="fail" if failing else "pass", note=" · ".join(parts) or None)  # fmt: skip


def _level(stream: dict[str, Any]) -> str:
    level = int(stream.get("level") or 0)
    if level <= 0:
        return ""
    return f"L{level / 30:.1f}" if stream.get("codec_name") == "hevc" else f"L{level / 10:.1f}"


def _row_format(original: dict[str, Any], rendered: dict[str, Any]) -> VerificationRow:
    a, b = _main_stream(original), _main_stream(rendered)

    def summary(stream: dict[str, Any]) -> str:
        return f"{stream.get('pix_fmt', '?')} · {stream.get('codec_name', '?')} {stream.get('profile', '')}".strip()

    same = all(a.get(key) == b.get(key) for key in ("pix_fmt", "codec_name", "profile"))
    note = f"레벨 {_level(a) or '?'} → {_level(b) or '?'}" if _level(a) != _level(b) else None
    return VerificationRow(check="픽셀 포맷 · 프로파일", original=summary(a), output=summary(b), verdict="pass" if same else "fail", note=note)


def _row_quality(frames: _Frames, thresholds: Thresholds) -> VerificationRow:
    check = "처리 영역 밖 PSNR-Y / SSIM"
    target = f"기준 ≥ {thresholds.psnr_y_min:g} dB / ≥ {thresholds.ssim_min:g}"
    if frames.comparable is not None or not frames.psnr or not frames.ssim:
        return VerificationRow(check=check, original=target, output="비교 불가", verdict="fail", note=frames.comparable or "비교할 프레임이 없음")
    finite = [min(value, 100.0) for value in frames.psnr]  # identical frames count as 100 dB in the mean
    low_psnr, low_ssim = min(frames.psnr), min(frames.ssim)
    ok = low_psnr >= thresholds.psnr_y_min and low_ssim >= thresholds.ssim_min
    return VerificationRow(
        check=check,
        original=target,
        output=f"{sum(finite) / len(finite):.1f} dB (최저 {min(low_psnr, 100.0):.1f}) / {sum(frames.ssim) / len(frames.ssim):.4f} (최저 {low_ssim:.4f})",
        verdict="pass" if ok else "fail",
        note=f"PSNR-Y 전체 {len(frames.psnr):,}프레임 · SSIM 표본 {len(frames.ssim):,}프레임 (처리 {frames.ssim_split[0]} · 그 밖 {frames.ssim_split[1]}) · 영역은 {GUARD}px 넓혀 제외",
    )


@functools.lru_cache(maxsize=1)
def _has_libvmaf() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    listing = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=False).stdout
    return ffmpeg if re.search(r"\blibvmaf\b", listing) else None


def _row_vmaf(meta: VideoMeta, output: Path, timeout: float = 3600) -> VerificationRow:
    check = "VMAF (전체 프레임)"
    ffmpeg = _has_libvmaf()
    if ffmpeg is None:
        return _info(check, "측정 안 함 (libvmaf 없음)")
    model = "vmaf_4k_v0.6.1" if max(meta.width, meta.height) >= 3840 else "vmaf_v0.6.1"
    graph = f"[0:v:0]setpts=PTS-STARTPTS[d];[1:v:0]setpts=PTS-STARTPTS[r];[d][r]libvmaf=model=version={model}:n_threads={os.cpu_count() or 4}"
    args = [ffmpeg, "-hide_banner", "-nostats", "-noautorotate", "-i", str(output), "-noautorotate", "-i", meta.path,
            "-lavfi", graph, "-an", "-sn", "-dn", "-f", "null", "-"]  # fmt: skip
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return _info(check, "측정 실패", f"ffmpeg 시간 초과 ({timeout:.0f}s)")
    score = re.search(r"VMAF score: ([0-9.]+)", done.stderr)
    if done.returncode != 0 or score is None:
        return _info(check, "측정 실패", done.stderr.strip()[-200:] or None)
    return _info(check, f"{float(score.group(1)):.1f}", f"처리 영역 포함 전체 프레임 · {model} · 참고 수치")


def _spans(frames: Sequence[int], limit: int = 3) -> str:
    """Frame numbers as ranges: "298–299, 410"."""
    spans: list[list[int]] = []
    for frame in sorted(set(frames)):
        if spans and frame == spans[-1][1] + 1:
            spans[-1][1] = frame
        else:
            spans.append([frame, frame])
    text = ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in spans[:limit])
    return text + (f" 외 {len(spans) - limit}곳" if len(spans) > limit else "")


def _row_applied(frames: _Frames, regions: Mapping[int, Sequence[Region]], expected: Mapping[str, Sequence[int]] | None) -> VerificationRow:
    check = "모자이크 적용 (전체 프레임)"
    given = {(region.ref, index) for index, items in regions.items() for region in items}
    wanted = {(ref, index) for ref, indices in (expected or {}).items() for index in indices} if expected is not None else given
    if not wanted and not given:
        return _info(check, "가릴 대상 없음")
    missing: dict[str, list[int]] = {}
    for ref, index in sorted(wanted - given):
        missing.setdefault(ref, []).append(index)
    unapplied = sorted(set(frames.unapplied))
    notes = [f"{ref}: 프레임 {_spans(indices)}에 모자이크 영역이 없음" for ref, indices in missing.items()]
    if unapplied:
        notes.append(f"프레임 {_spans(unapplied)}의 출력이 모자이크가 아니라 원본에 가까움")
    if frames.comparable is not None:
        notes.append(f"픽셀 대조 불가: {frames.comparable}")
    frames_wanted = {index for _, index in wanted}
    bad = {index for indices in missing.values() for index in indices} | (set(unapplied) & frames_wanted)
    total = len(frames_wanted)
    ok = not missing and not unapplied and frames.comparable is None
    return VerificationRow(
        check=check,
        original=f"가려야 할 프레임 {total:,}개 (결정 {len({ref for ref, _ in wanted})}건)",
        output=f"{total - len(bad):,} / {total:,} 프레임에 모자이크",
        verdict="pass" if ok else "fail",
        note=" · ".join(notes) or "계획의 구간에서 계산한 프레임마다 영역이 있고, 출력의 그 자리는 원본에 모자이크를 다시 입힌 것과 같음",
    )


def _row_inside(frames: _Frames, asked: bool, measured: str = "") -> VerificationRow:
    check = "처리 영역 재식별 (얼굴 대조 · 글자 재인식)"
    if not asked:
        return _info(check, "측정 안 함")
    note = "처리 영역 없음" if not frames.modified else None
    if frames.inside_checked < frames.modified:
        note = f"처리 프레임 {frames.modified:,}개 중 {frames.inside_checked:,}개 표본"
    note = " · ".join(part for part in (note, measured) if part) or None
    return VerificationRow(check=check, original=f"처리 프레임 {frames.modified:,}개", output=f"{frames.inside_left:,} / {frames.inside_checked:,} 프레임",
                           verdict="pass" if frames.inside_left == 0 else "fail", note=note)  # fmt: skip


def _row_scan(frames: _Frames, checker: Checker | None, measured: str = "") -> VerificationRow:
    check = "모자이크한 대상의 노출 (전체 프레임 · 화면 전체)"
    if checker is None:
        return _info(check, "측정 안 함")
    leaks = list(checker.leaks)
    where = _spans([leak.frame for leak in leaks])
    note = " · ".join(part for part in (f"노출된 프레임 {where}: " + ", ".join(sorted({leak.what for leak in leaks})) if leaks else None, measured) if part) or None
    return VerificationRow(check=check, original=f"전체 {frames.scanned:,}프레임", output=f"{len({leak.frame for leak in leaks}):,} / {frames.scanned:,} 프레임에서 노출",
                           verdict="pass" if not leaks else "fail", note=note)  # fmt: skip


def _row_dropped(dropped: list[str], rendered: dict[str, Any]) -> VerificationRow:
    left = [name for name in location_tags(rendered) if is_location_tag(name)]
    return VerificationRow(check="위치 메타데이터 삭제", original=f"태그 {len(dropped)}개 ({', '.join(dropped)})", output="남은 태그 없음" if not left else f"남은 태그 {len(left)}개 ({', '.join(left)})",
                           verdict="pass" if not left else "fail", note=None)  # fmt: skip
