"""Original-stream renderer and output verifier, on tiny synthetic clips built here with PyAV (no network)."""
import hashlib
import shutil
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest

from privframe.core import render as R
from privframe.core import verify as V
from privframe.core.geometry import Region
from privframe.core.video import VideoError, ffprobe_json, is_location_tag, probe, upright

pytestmark = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is needed to probe clips")

TIME_BASE = Fraction(1, 90000)
DELTAS = [3000, 3003, 1500, 4500, 6000, 3000, 2970, 3030]  # irregular frame spacing: a VFR clip
WIDTH, HEIGHT, FRAMES = 192, 128, 24
CHECKS = [
    "프레임 수", "PTS 목록 (time_base 1/90000)", "오디오 framemd5", "메타·챕터·회전·색 정보", "픽셀 포맷 · 프로파일",
    "처리 영역 밖 PSNR-Y / SSIM", "VMAF (전체 프레임)", "모자이크 적용 (전체 프레임)", "처리 영역 재식별 (얼굴 대조 · 글자 재인식)",
    "모자이크한 대상의 노출 (전체 프레임 · 화면 전체)",
]  # fmt: skip
QUALITY, APPLIED, INSIDE, SCAN = 5, 7, 8, 9  # where those rows stand
LOOSE = V.Thresholds(psnr_y_min=42.0, ssim_min=0.99)  # where the test is not about the default thresholds


def picture(index: int) -> np.ndarray:
    """A checkerboard under two gradients, one of them moving, with a white marker near the top-left corner (BGR)."""
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    image = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    image[..., 0] = (xx * 255 // WIDTH + index * 3) % 256
    image[..., 1] = ((xx // 8 + yy // 8) % 2) * 180 + 40
    image[..., 2] = yy * 255 // HEIGHT
    image[8:24, 8:40] = 255
    return image


def make_clip(path: Path, *, codec: str = "libx264", pix_fmt: str = "yuv420p", rotation: int = 0, audio: bool = True, options: dict | None = None,
              tags: dict[str, str] | None = None) -> Path:  # fmt: skip
    with av.open(str(path), "w", container_options={"video_track_timescale": str(TIME_BASE.denominator)}) as out:
        out.metadata["title"] = "합성 클립"
        out.metadata["comment"] = "privframe test"
        out.metadata.update(tags or {})
        video = out.add_stream(codec, rate=30, options=options or {"crf": "18", "preset": "veryfast", "bf": "2"}, time_base=TIME_BASE)
        video.width, video.height, video.pix_fmt, video.time_base = WIDTH, HEIGHT, pix_fmt, TIME_BASE
        if rotation:
            video.set_display_rotation(-rotation)  # counter-clockwise angle, as ffprobe reports it
        sound = out.add_stream("aac", rate=48000, layout="stereo") if audio else None
        pts = 0
        for index in range(FRAMES):
            frame = av.VideoFrame.from_ndarray(picture(index), format="bgr24").reformat(format=pix_fmt)
            frame.pts, frame.time_base = pts, TIME_BASE
            pts += DELTAS[index % len(DELTAS)]
            out.mux(video.encode(frame))
        out.mux(video.encode(None))
        if sound is not None:
            total = 48000 * pts // TIME_BASE.denominator
            for start in range(0, total, 1024):
                tone = (np.sin(2 * np.pi * 440 * np.arange(start, min(start + 1024, total)) / 48000) * 0.3).astype(np.float32)
                chunk = av.AudioFrame.from_ndarray(np.stack([tone, tone]), format="fltp", layout="stereo")
                chunk.sample_rate, chunk.pts, chunk.time_base = 48000, start, Fraction(1, 48000)
                out.mux(sound.encode(chunk))
            out.mux(sound.encode(None))
    return path


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("clips")


@pytest.fixture(scope="module")
def vfr(clips):
    return probe(make_clip(clips / "vfr.mp4"))[0]


@pytest.fixture(scope="module")
def rotated(clips):
    return probe(make_clip(clips / "rotated.mp4", rotation=90, audio=False))[0]


def decoded(path: str | Path) -> tuple[list[int], Fraction, list[np.ndarray]]:
    """pts, time base and luma planes of every frame, in presentation order."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        depth = R.PIX_FMTS[stream.codec_context.pix_fmt][0]
        pts, luma = [], []
        for frame in container.decode(stream):
            pts.append(frame.pts)
            luma.append(R.plane_views(frame, depth)[0].astype(np.float64))
        return pts, Fraction(stream.time_base.numerator, stream.time_base.denominator), luma


def audio_packets(path: str | Path) -> list[tuple]:
    with av.open(str(path)) as container:
        return [(p.pts, p.dts, p.duration, p.size, hashlib.md5(bytes(p)).hexdigest()) for p in container.demux(container.streams.audio[0]) if p.size]


def psnr(a: np.ndarray, b: np.ndarray, peak: float = 255.0) -> float:
    return 10 * np.log10(peak * peak / max(np.mean((a - b) ** 2), 1e-9))


def mosaicked(luma: np.ndarray, regions, pix_fmt: str = "yuv420p", rotation: int = 0) -> np.ndarray:
    """The decoded luma plane with the regions' mosaics drawn on it: what the encoder should have been given."""
    plane = luma.copy()
    R.redact_planes([plane], regions, pix_fmt, rotation)
    return plane


class Watcher:
    """Stands in for perception.OutputChecker: records what the verifier shows it and answers as told."""

    def __init__(self, identifiable: tuple[int, ...] = (), exposed: tuple[int, ...] = ()):
        self.identifiable, self.exposed = set(identifiable), set(exposed)  # frame numbers
        self.asked: list[tuple[tuple[int, ...], tuple[int, ...], int, int]] = []
        self.scanned: list[tuple[int, tuple[int, ...], float, int]] = []
        self.leaks: list[SimpleNamespace] = []

    def inside(self, output: np.ndarray, original: np.ndarray, frame: int, regions) -> int:
        self.asked.append((output.shape, original.shape, frame, len(regions)))
        return int(frame in self.identifiable)

    def scan(self, frame: int, small: np.ndarray, scale: float, full, regions) -> None:
        self.scanned.append((frame, small.shape, scale, len(regions)))
        if frame in self.exposed:
            output, original = full()
            assert output.shape == original.shape
            self.leaks.append(SimpleNamespace(frame=frame, ref="d1", box=(0.0, 0.0, 10.0, 10.0), what="인물 1 얼굴", span=(frame, frame)))

    def summary(self) -> tuple[str, str]:
        return "안쪽 메모", "화면 전체 메모"


# ---------------------------------------------------------------------------------------------------------
# regions on planes


def planes(pix_fmt: str, seed: int = 3) -> list[np.ndarray]:
    """Y, U, V of strong per-pixel noise over the whole value range: a mosaic block's mean then differs from
    nearly every pixel it replaces, and two neighbouring blocks never get the same mean."""
    depth, sx, sy = R.PIX_FMTS[pix_fmt]
    rng = np.random.default_rng(seed)
    sizes = [(HEIGHT, WIDTH), (HEIGHT >> sy, WIDTH >> sx), (HEIGHT >> sy, WIDTH >> sx)]
    return [rng.integers(0, 1 << depth, size).astype(np.uint8 if depth == 8 else np.uint16) for size in sizes]


def blocks(before: np.ndarray, after: np.ndarray) -> tuple[list[int], list[int]]:
    """Check that `after` is a mosaic of `before` (two crops of the same area): every block is one value, the
    mean of the pixels it replaced. Returns the rows and columns at which the blocks start."""
    rows = [0, *(i for i in range(1, after.shape[0]) if (after[i] != after[i - 1]).any())]
    columns = [0, *(i for i in range(1, after.shape[1]) if (after[:, i] != after[:, i - 1]).any())]
    for y0, y1 in zip(rows, [*rows[1:], after.shape[0]]):
        for x0, x1 in zip(columns, [*columns[1:], after.shape[1]]):
            block = after[y0:y1, x0:x1]
            assert (block == block[0, 0]).all()
            assert abs(float(block[0, 0]) - before[y0:y1, x0:x1].mean()) <= 0.5
    return rows, columns


@pytest.mark.parametrize(("pix_fmt", "chroma_box"), [
    ("yuv420p", (slice(15, 31), slice(10, 31))),
    ("yuv422p", (slice(31, 62), slice(10, 31))),
    ("yuv444p", (slice(31, 62), slice(21, 62))),
    ("yuv420p10le", (slice(15, 31), slice(10, 31))),
])  # fmt: skip
def test_mosaic_covers_the_same_area_with_the_same_blocks_on_luma_and_chroma(pix_fmt, chroma_box):
    _, sx, sy = R.PIX_FMTS[pix_fmt]
    before = planes(pix_fmt)
    after = [p.copy() for p in before]
    R.redact_planes(after, [Region(21.3, 31.7, 40, 30, "box", "d1")], pix_fmt)
    luma_box = (slice(30 if sy else 31, 62), slice(20 if sx else 21, 62))  # the box grown to whole chroma samples
    grids = []
    for index, box in ((0, luma_box), (1, chroma_box), (2, chroma_box)):
        untouched = np.ones(before[index].shape, bool)
        untouched[box] = False
        assert after[index].dtype == before[index].dtype
        assert np.array_equal(after[index][untouched], before[index][untouched])  # nothing outside the box moves
        assert (after[index][box] != before[index][box]).mean() > 0.97  # and all of the box is redrawn …
        grids.append(blocks(before[index][box], after[index][box]))  # … as blocks of its own mean
    rows, columns = grids[0]
    assert 2 <= len(rows) <= R.MOSAIC_BLOCKS <= len(columns)  # the short side is the 30 px one
    for chroma_rows, chroma_columns in grids[1:]:  # a chroma block covers the pixels of a luma block
        assert [r << sy for r in chroma_rows] == rows and [c << sx for c in chroma_columns] == columns


def test_mosaic_shows_at_most_six_blocks_along_the_short_side():
    before = planes("yuv420p")
    after = [p.copy() for p in before]
    R.redact_planes(after, [Region(20.5, 30.2, 100, 48, "box", "d1")], "yuv420p")
    box = after[0][30:80, 20:122]
    rows = 1 + np.count_nonzero(np.diff(box[:, 3].astype(int)))  # block edges down one column: the short side
    columns = 1 + np.count_nonzero(np.diff(box[3, :].astype(int)))
    assert 2 <= rows <= 6 and rows < columns <= 12
    assert (box[:10, :10] == box[0, 0]).all() and not np.array_equal(after[1], before[1])
    assert np.array_equal(after[0][:30], before[0][:30]) and np.array_equal(after[0][:, 122:], before[0][:, 122:])


def test_a_stronger_region_gets_coarser_blocks_on_the_same_area():
    """Text and plates are drawn with strength 2.5 (geometry.STRENGTH): fewer, larger blocks, same pixels."""
    before = planes("yuv420p")
    face, text = [p.copy() for p in before], [p.copy() for p in before]
    R.redact_planes(face, [Region(20, 30, 100, 48, "box", "d1")], "yuv420p")
    R.redact_planes(text, [Region(20, 30, 100, 48, "box", "d1", 2.5)], "yuv420p")
    box = (slice(30, 78), slice(20, 120))
    fine, coarse = blocks(before[0][box], face[0][box]), blocks(before[0][box], text[0][box])
    assert len(coarse[0]) < len(fine[0]) <= R.MOSAIC_BLOCKS and len(coarse[1]) < len(fine[1])
    assert np.array_equal((face[0] != before[0]).any(axis=0), (text[0] != before[0]).any(axis=0))  # same columns …
    assert np.array_equal((face[0] != before[0]).any(axis=1), (text[0] != before[0]).any(axis=1))  # … and rows


def test_mosaic_flattens_detail_inside_only():
    before = planes("yuv420p")
    before[0][40:80, 60:120] = np.where((np.indices((40, 60)).sum(axis=0) // 4) % 2, 230, 110)  # fine checkerboard
    after = [p.copy() for p in before]
    R.redact_planes(after, [Region(60, 40, 60, 40, "box", "d1")], "yuv420p")
    assert after[0][40:80, 60:120].std() < before[0][40:80, 60:120].std() / 4
    outside = np.ones((HEIGHT, WIDTH), bool)
    outside[40:80, 60:120] = False
    assert np.array_equal(after[0][outside], before[0][outside])


def test_ellipse_leaves_the_corners_of_the_box():
    before = planes("yuv444p")
    box, ellipse = [p.copy() for p in before], [p.copy() for p in before]
    R.redact_planes(box, [Region(40, 20, 80, 60, "box", "d1")], "yuv444p")
    R.redact_planes(ellipse, [Region(40, 20, 80, 60, "ellipse", "d1")], "yuv444p")
    changed_box, changed_ellipse = box[0] != before[0], ellipse[0] != before[0]
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    in_box = (xx >= 40) & (xx < 120) & (yy >= 20) & (yy < 80)
    in_ellipse = ((xx + 0.5 - 80) / 40) ** 2 + ((yy + 0.5 - 50) / 30) ** 2 <= 1.0  # pixel centres in the inscribed ellipse
    assert abs(in_ellipse.sum() / (np.pi / 4 * 80 * 60) - 1) < 0.01
    # a mosaic may leave a pixel at its old value by coincidence, so: nothing outside changes, nearly all inside does
    assert not (changed_box & ~in_box).any() and changed_box[in_box].mean() > 0.98
    assert not (changed_ellipse & ~in_ellipse).any() and changed_ellipse[in_ellipse].mean() > 0.98
    assert changed_ellipse[45:55, 75:85].mean() > 0.9 and not changed_ellipse[20:26, 40:46].any() and not changed_ellipse[74:80, 114:120].any()
    assert np.array_equal(ellipse[0][in_ellipse], box[0][in_ellipse])  # the same blocks, cut to the ellipse
    for index in (1, 2):
        assert np.array_equal(ellipse[index][~in_ellipse], before[index][~in_ellipse])


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_regions_follow_the_display_rotation(rotation):
    """A region drawn around the marker of the upright picture lands on the marker of the coded frame."""
    rng = np.random.default_rng(rotation)
    size = (HEIGHT, WIDTH) if rotation in (0, 180) else (WIDTH, HEIGHT)
    shown = rng.integers(0, 120, size).astype(np.uint8)  # dark detail everywhere …
    marker = (slice(10, 30), slice(50, 110))  # … and a bright one: off-centre and not square, so every wrong turn misses it
    shown[marker] = rng.integers(180, 256, (20, 60))
    coded = np.ascontiguousarray(np.rot90(shown, k={0: 0, 90: 1, 180: 2, 270: 3}[rotation]))  # undo `upright`
    assert np.array_equal(upright(coded, rotation), shown)
    luma = coded.copy()
    region = Region(50, 10, 60, 20, "box", "d1")
    R.redact_planes([luma, luma.copy(), luma.copy()], [region], "yuv444p", rotation)
    result = upright(luma, rotation)
    elsewhere = np.ones(shown.shape, bool)
    elsewhere[marker] = False
    assert np.array_equal(result[elsewhere], shown[elsewhere])  # the detail around the marker is untouched
    rows, columns = blocks(shown[marker], result[marker])  # the marker's detail is flattened into blocks of itself
    assert len(rows) <= R.MOSAIC_BLOCKS < len(columns)
    assert result[marker].min() > 190 and result[marker].std() < shown[marker].std() / 3  # no dark pixel was mixed in
    mask = R.region_mask([region], "yuv444p", rotation, coded.shape[1], coded.shape[0])
    assert np.array_equal(upright(mask, rotation) == 1, ~elsewhere)
    assert not ((result != shown) & elsewhere).any() and (result != shown)[marker].mean() > 0.97


def test_region_mask_is_exactly_what_the_renderer_changes():
    regions = [Region(10.4, 12.9, 70, 44, "ellipse", "d1"), Region(150, 90, 80, 80, "box", "d2", 2.5), Region(-20, 100, 60, 40, "box", "d3")]
    mask = R.region_mask(regions, "yuv420p", 0, WIDTH, HEIGHT) == 1
    assert mask[30, 40] and mask[127, 191] and mask[110, 0] and not mask[13, 11] and not mask[64, 100]
    changed = np.zeros(mask.shape, bool)
    for seed in range(4):
        before = planes("yuv420p", seed)
        after = [p.copy() for p in before]
        R.redact_planes(after, regions, "yuv420p")
        here = after[0] != before[0]
        # a block mean can equal a pixel it replaces, so one picture shows almost all of the mask, never more
        assert not (here & ~mask).any() and here[mask].mean() > 0.97
        changed |= here
    assert np.array_equal(changed, mask)  # over a few different pictures every pixel of the mask has changed


# ---------------------------------------------------------------------------------------------------------
# encoder choice


def test_encoder_follows_the_source_codec_family(vfr):
    spec = R.encoder_for("x264-crf16", vfr)
    assert (spec.codec, spec.options["crf"], spec.options["preset"], spec.options["profile"]) == ("libx264", "16", "slow", "high")
    assert spec.options["level"] == f"{vfr.level // 10}.{vfr.level % 10}" and "CRF 16" in spec.label
    assert R.encoder_for("x264-crf14", vfr).options["crf"] == "14"
    assert R.encoder_for("videotoolbox", vfr).codec == "h264_videotoolbox"
    assert R.encoder_for("ffv1", vfr).codec == "ffv1"
    hevc = vfr.model_copy(update={"codec": "hevc", "profile": "Main 10", "level": 93, "pix_fmt": "yuv420p10le", "bit_depth": 10})
    spec = R.encoder_for("x264-crf14", hevc)
    assert (spec.codec, spec.options["crf"], spec.options["profile"], spec.options["level"]) == ("libx265", "14", "main10", "93")
    assert R.encoder_for("videotoolbox", hevc).codec == "hevc_videotoolbox"
    high10 = vfr.model_copy(update={"profile": "High 10", "pix_fmt": "yuv420p10le", "bit_depth": 10})
    assert R.encoder_for("x264-crf16", high10).options["profile"] == "high10"  # 10-bit H.264 stays H.264
    assert R.encoder_for("videotoolbox", high10).codec == "hevc_videotoolbox"  # VideoToolbox H.264 is 8-bit only
    with pytest.raises(ValueError):
        R.encoder_for("x264-crf99", vfr)


# ---------------------------------------------------------------------------------------------------------
# render + verify


def test_vfr_clip_keeps_frames_timestamps_audio_and_tags(vfr, tmp_path):
    regions = {i: [Region(60, 30, 70, 56, "ellipse", "d1")] for i in range(5, 15)}
    calls: list[tuple[str, float]] = []
    out = tmp_path / "out.mp4"
    result = R.render(vfr, out, regions, R.encoder_for("x264-crf16", vfr), progress=lambda text, fraction: calls.append((text, fraction)))
    assert (result.frames, result.modified_frames, result.codec_label) == (FRAMES, 10, "libx264 · CRF 16 · slow")
    assert result.size_bytes == out.stat().st_size and result.path == str(out)
    assert {"원본 디코드", "프레임 수정 · 재인코딩", "스트림 복사 · 마무리"} <= {text for text, _ in calls} and len(calls) <= 20
    assert [fraction for _, fraction in calls] == sorted(fraction for _, fraction in calls)

    before, after = decoded(vfr.path), decoded(out)
    assert vfr.vfr and before[0] == after[0] and before[1] == after[1] == TIME_BASE and len(after[0]) == FRAMES
    with av.open(str(out)) as container:  # the encoder reorders frames, so the equality above is not trivial
        assert any(frame.pict_type == av.video.frame.PictureType.B for frame in container.decode(video=0))
    assert audio_packets(vfr.path) == audio_packets(out) and len(audio_packets(out)) > 30
    meta = probe(out)[0]
    assert (meta.codec, meta.profile, meta.level, meta.pix_fmt, meta.duration_s) == (vfr.codec, vfr.profile, vfr.level, vfr.pix_fmt, vfr.duration_s)
    with av.open(str(out)) as container:
        assert container.metadata["title"] == "합성 클립" and container.metadata["comment"] == "privframe test"

    inside = (slice(40, 76), slice(75, 115))  # well within the ellipse
    outside = np.ones((HEIGHT, WIDTH), bool)
    outside[14:102, 44:146] = False  # the region and a macroblock around it
    for index, (a, b) in enumerate(zip(before[2], after[2])):
        assert psnr(a[outside], b[outside]) > 42
        if index in regions:
            assert a[inside].std() > 3 * b[inside].std()  # the checkerboard is gone …
            assert psnr(mosaicked(a, regions[index])[inside], b[inside]) > 42 > 20 > psnr(a[inside], b[inside])  # … into the mosaic of this frame
        else:
            assert psnr(a, b) > 42

    watcher = Watcher()
    rows = V.verify(vfr, out, regions, checker=watcher)
    assert [row.check for row in rows] == CHECKS
    assert [row.verdict for row in rows] == ["pass", "pass", "pass", "pass", "pass", "pass", "info", "pass", "pass", "pass"] and V.passed(rows)
    assert rows[0].original == rows[0].output == "24" and rows[1].original.startswith("가변 프레임레이트") and rows[1].output == "전부 일치"
    assert rows[2].original.endswith(rows[2].output) and "44 패킷" in rows[2].output
    assert rows[6].output == "측정 안 함 (libvmaf 없음)" or float(rows[6].output) > 50
    assert (rows[APPLIED].original, rows[APPLIED].output) == ("가려야 할 프레임 10개 (결정 1건)", "10 / 10 프레임에 모자이크")
    # the inside of the regions is asked about on the redacted frames, with both frames upright at full size
    assert rows[INSIDE].output == "0 / 10 프레임" and watcher.asked == [((HEIGHT, WIDTH, 3), (HEIGHT, WIDTH, 3), i, 1) for i in range(5, 15)]
    assert rows[INSIDE].note == "안쪽 메모"
    # the whole picture is shown for every frame, redacted or not
    assert watcher.scanned == [(i, (HEIGHT, WIDTH, 3), 1.0, int(i in regions)) for i in range(FRAMES)]
    assert (rows[SCAN].original, rows[SCAN].output, rows[SCAN].note) == ("전체 24프레임", "0 / 24 프레임에서 노출", "화면 전체 메모")

    rows = V.verify(vfr, out, regions, checker=Watcher(identifiable=(7,)), vmaf=False)
    assert rows[INSIDE].verdict == "fail" and rows[INSIDE].output == "1 / 10 프레임" and not V.passed(rows)
    assert [row.verdict for i, row in enumerate(rows) if i != INSIDE] == ["pass"] * 6 + ["info", "pass", "pass"]
    rows = V.verify(vfr, out, regions, checker=Watcher(exposed=(2, 3, 20)), vmaf=False)  # seen outside the regions, also on frames without any
    assert rows[SCAN].verdict == "fail" and rows[SCAN].output == "3 / 24 프레임에서 노출" and not V.passed(rows)
    assert rows[SCAN].note == "노출된 프레임 2–3, 20: 인물 1 얼굴 · 화면 전체 메모" and rows[INSIDE].verdict == "pass"
    strict = V.verify(vfr, out, regions, thresholds=V.Thresholds(psnr_y_min=80.0), vmaf=False)
    assert strict[QUALITY].verdict == "fail" and [row.verdict for row in strict[:5]] == ["pass"] * 5
    assert [row.verdict for row in strict[APPLIED:]] == ["pass", "info", "info"]  # without a checker nothing is claimed about identifiability


@pytest.mark.parametrize(("shape", "strength"), [("box", 1.0), ("ellipse", 1.0), ("box", 2.5), ("ellipse", 2.5)])
def test_a_mosaic_changes_the_inside_and_keeps_the_outside(vfr, tmp_path, shape, strength):
    regions = {i: [Region(60, 30, 70, 56, shape, "d1", strength)] for i in range(FRAMES)}
    out = tmp_path / "out.mp4"
    R.render(vfr, out, regions, R.encoder_for("x264-crf16", vfr))
    before, after = decoded(vfr.path), decoded(out)
    assert before[0] == after[0]
    centre, corner = (slice(44, 72), slice(78, 112)), (slice(31, 34), slice(61, 65))  # the corner is outside the ellipse
    outside = np.ones((HEIGHT, WIDTH), bool)
    outside[14:102, 44:146] = False
    for a, b in zip(before[2], after[2]):
        assert psnr(a[outside], b[outside]) > 42
        assert np.abs(a[centre] - b[centre]).mean() > 15  # the checkerboard is no longer there
        assert psnr(mosaicked(a, regions[0])[centre], b[centre]) > 42  # its place is taken by the block means
        assert a[corner].std() > 30  # a checkerboard edge runs through the corner of the box:
        if shape == "ellipse":
            assert np.abs(a[corner] - b[corner]).mean() < 3  # the ellipse leaves it as it was
        else:
            assert b[corner].std() < 3 and np.abs(a[corner] - b[corner]).mean() > 15  # the box makes it one block
    assert V.passed(V.verify(vfr, out, regions, thresholds=LOOSE, vmaf=False))


def test_lossless_encoder_shows_that_untouched_pixels_are_not_recomputed(vfr, tmp_path):
    """With FFV1 nothing but the redaction differs: frames reach the encoder exactly as decoded, all three planes."""
    regions = {i: [Region(60.5, 30.2, 70, 56, "ellipse", "d1"), Region(150, 100, 60, 40, "box", "d2", 2.5)] for i in range(5, 15)}
    out = tmp_path / "out.mp4"
    assert R.render(vfr, out, regions, R.encoder_for("ffv1", vfr)).modified_frames == 10
    changed = R.region_mask(regions[5], "yuv420p", 0, WIDTH, HEIGHT) == 1
    with av.open(vfr.path) as a, av.open(str(out)) as b:
        for index, (before, after) in enumerate(zip(a.decode(video=0), b.decode(video=0), strict=True)):
            assert before.pts == after.pts
            y0, u0, v0 = R.plane_views(before, 8)
            y1, u1, v1 = R.plane_views(after, 8)
            if index not in regions:
                assert np.array_equal(y0, y1) and np.array_equal(u0, u1) and np.array_equal(v0, v1)
                continue
            assert np.array_equal(y0[~changed], y1[~changed]) and (y0[changed] != y1[changed]).mean() > 0.9
            grown = changed.reshape(HEIGHT // 2, 2, WIDTH // 2, 2).any(axis=(1, 3))  # chroma samples under the regions
            assert np.array_equal(u0[~grown], u1[~grown]) and np.array_equal(v0[~grown], v1[~grown])
            assert not np.array_equal(u0, u1)
            wanted = [y0.copy(), u0.copy(), v0.copy()]  # and what did change is the mosaic of this very frame, to the bit
            R.redact_planes(wanted, regions[index], "yuv420p")
            assert np.array_equal(y1, wanted[0]) and np.array_equal(u1, wanted[1]) and np.array_equal(v1, wanted[2])


def test_rotated_clip_keeps_its_rotation_and_regions_land_upright(rotated, tmp_path):
    assert (rotated.rotation, rotated.display_width, rotated.display_height) == (90, HEIGHT, WIDTH)
    # upright, the marker of the coded frame (x 8-40, y 8-24) stands at the top-right: x 104-120, y 8-40
    regions = {i: [Region(100, 4, 24, 40, "box", "d1")] for i in range(FRAMES)}
    box, margin = (slice(4, 44), slice(100, 124)), (slice(6, 42), slice(100, 104))  # the region · the strip left of the marker
    out = tmp_path / "out.mp4"
    R.render(rotated, out, regions, R.encoder_for("x264-crf16", rotated))
    meta = probe(out)[0]
    assert (meta.rotation, meta.width, meta.height, meta.display_width, meta.display_height) == (90, WIDTH, HEIGHT, HEIGHT, WIDTH)
    before, after = decoded(rotated.path), decoded(out)
    assert before[0] == after[0]
    for a, b in zip(before[2], after[2]):
        shown_before, shown_after = upright(a, 90), upright(b, 90)
        assert shown_before[10:38, 106:118].mean() > 200 and shown_before[margin].mean() < 110  # the marker and its sharp edge
        assert shown_after[margin].mean() > shown_before[margin].mean() + 25  # the edge is spread into the blocks beside the marker
        wanted = upright(mosaicked(a, regions[0], rotation=90), 90)
        assert psnr(wanted[box], shown_after[box]) > 40 > 20 > psnr(shown_before[box], shown_after[box])
        assert psnr(a[:, 60:], b[:, 60:]) > 42  # coded pixels a region taken as coded coordinates would have hit
    watcher = Watcher()
    rows = V.verify(rotated, out, regions, checker=watcher, thresholds=LOOSE, vmaf=False)
    assert V.passed(rows) and rows[2].verdict == "info" and rows[2].output == "오디오 없음"
    assert "회전 90°" in rows[3].original and "회전 90°" in rows[3].output
    assert rows[APPLIED].verdict == "pass" and rows[APPLIED].output == "24 / 24 프레임에 모자이크"
    assert {(seen[0], seen[1]) for seen in watcher.asked} == {((WIDTH, HEIGHT, 3), (WIDTH, HEIGHT, 3))}  # the checker sees upright frames
    assert {seen[1] for seen in watcher.scanned} == {(WIDTH, HEIGHT, 3)} and len(watcher.scanned) == FRAMES


@pytest.mark.skipif("libx265" not in av.codecs_available, reason="libx265 is not in this PyAV build")
def test_ten_bit_hevc_stays_ten_bit_hevc(clips, tmp_path):
    source = make_clip(clips / "hevc10.mp4", codec="libx265", pix_fmt="yuv420p10le", options={"crf": "20", "preset": "ultrafast", "x265-params": "log-level=error"})
    meta = probe(source)[0]
    assert (meta.codec, meta.pix_fmt, meta.bit_depth) == ("hevc", "yuv420p10le", 10)
    regions = {i: [Region(4, 2, 44, 28, "box", "d1")] for i in range(8, 16)}
    box = (slice(2, 30), slice(4, 48))
    spec = R.encoder_for("x264-crf16", meta)
    assert spec.codec == "libx265" and spec.options["crf"] == "16"
    out = tmp_path / "out.mp4"
    R.render(meta, out, regions, spec)
    rendered = probe(out)[0]
    assert (rendered.codec, rendered.profile, rendered.pix_fmt, rendered.level) == ("hevc", "Main 10", "yuv420p10le", meta.level)
    before, after = decoded(source), decoded(out)
    assert before[0] == after[0] and audio_packets(source) == audio_packets(out)
    for index, (a, b) in enumerate(zip(before[2], after[2])):
        assert psnr(a[48:], b[48:], 1023.0) > 42
        wanted = mosaicked(a, regions[8], "yuv420p10le")  # the marker and the checkerboard around it as 10-bit block means
        assert (psnr(wanted[box], b[box], 1023.0) > 40) == (index in regions) == (psnr(a[box], b[box], 1023.0) < 25)
    rows = V.verify(meta, out, regions, thresholds=LOOSE, vmaf=False)
    assert V.passed(rows) and rows[4].output == "yuv420p10le · hevc Main 10"
    assert rows[APPLIED].verdict == "pass" and rows[APPLIED].output == "8 / 8 프레임에 모자이크"


def test_damaged_output_fails(vfr, tmp_path):
    damaged = tmp_path / "damaged.mp4"
    with av.open(vfr.path) as source, av.open(str(damaged), "w") as sink:  # a plain re-encode that loses a frame
        stream = sink.add_stream("libx264", rate=30, options={"crf": "30", "preset": "ultrafast"})
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        for index, frame in enumerate(source.decode(video=0)):
            if index != 5:
                again = av.VideoFrame.from_ndarray(frame.to_ndarray(), format="yuv420p")
                again.pts = index
                sink.mux(stream.encode(again))
        sink.mux(stream.encode(None))
    rows = V.verify(vfr, damaged, {}, vmaf=False)
    assert [row.check for row in rows] == CHECKS and not V.passed(rows)
    assert [row.verdict for row in rows[:4]] == ["fail"] * 4 and (rows[0].original, rows[0].output) == ("24", "23")
    assert "audio0: 출력에 없음" in rows[2].note and rows[3].note.startswith("다른 항목: ") and "스트림 1" in rows[3].output
    assert (rows[APPLIED].verdict, rows[APPLIED].output) == ("info", "가릴 대상 없음")  # nothing was to be redacted
    with pytest.raises(VideoError):
        V.verify(vfr, tmp_path / "missing.mp4", {})


# ---------------------------------------------------------------------------------------------------------
# is the mosaic on every frame it was decided for?


@pytest.fixture(scope="module")
def redacted(vfr, clips):
    """The VFR clip rendered with a face-like and a text-like mosaic on frames 5–14."""
    regions = {i: [Region(60, 30, 70, 56, "ellipse", "d1"), Region(20, 96, 60, 24, "box", "d2", 2.5)] for i in range(5, 15)}
    out = clips / "redacted.mp4"
    R.render(vfr, out, regions, R.encoder_for("x264-crf16", vfr))
    return out, regions


def test_a_correct_render_passes_the_applied_check(vfr, redacted):
    out, regions = redacted
    for expected in (None, {"d1": list(range(5, 15)), "d2": list(range(5, 15))}):  # the regions themselves · the plan's frames
        rows = V.verify(vfr, out, regions, expected=expected, vmaf=False)
        row = rows[APPLIED]
        assert (row.check, row.verdict) == ("모자이크 적용 (전체 프레임)", "pass") and V.passed(rows)
        assert (row.original, row.output) == ("가려야 할 프레임 10개 (결정 2건)", "10 / 10 프레임에 모자이크")


def test_a_frame_the_plan_covers_but_the_regions_do_not_fails_and_is_named(vfr, redacted):
    out, regions = redacted
    rows = V.verify(vfr, out, regions, expected={"d1": list(range(5, 17)), "d2": list(range(5, 15))}, vmaf=False)
    row = rows[APPLIED]
    assert row.verdict == "fail" and not V.passed(rows)
    assert (row.original, row.output) == ("가려야 할 프레임 12개 (결정 2건)", "10 / 12 프레임에 모자이크")
    assert row.note == "d1: 프레임 15–16에 모자이크 영역이 없음"  # the decision and the frames, and nothing about d2
    assert [r.verdict for i, r in enumerate(rows) if i != APPLIED] == ["pass"] * 6 + ["info"] * 3  # only this row sees it
    # a decision whose regions are missing altogether, and a single frame in the middle
    rows = V.verify(vfr, out, {i: items[:1] for i, items in regions.items() if i != 9}, expected={"d1": list(range(5, 15)), "d2": [5, 6]}, vmaf=False)
    assert rows[APPLIED].verdict == "fail" and "d1: 프레임 9에 모자이크 영역이 없음" in rows[APPLIED].note and "d2: 프레임 5–6에 모자이크 영역이 없음" in rows[APPLIED].note


def test_a_frame_left_as_the_original_fails_and_is_named(vfr, redacted):
    """The renderer was given frames 5–14, the plan (and the regions handed to the check) say 5–15: frame 15 of
    the output is the untouched original."""
    out, regions = redacted
    wider = {**regions, 15: regions[14]}
    rows = V.verify(vfr, out, wider, expected={"d1": list(range(5, 16)), "d2": list(range(5, 16))}, vmaf=False)
    row = rows[APPLIED]
    assert row.verdict == "fail" and not V.passed(rows)
    assert (row.original, row.output) == ("가려야 할 프레임 11개 (결정 2건)", "10 / 11 프레임에 모자이크")
    assert row.note == "프레임 15의 출력이 모자이크가 아니라 원본에 가까움"
    assert V.verify(vfr, out, wider, vmaf=False)[APPLIED].note == row.note  # the pixels decide, with or without the plan's frames
    # the original itself, checked as if it were the redacted copy: every frame is reported
    rows = V.verify(vfr, Path(vfr.path), regions, vmaf=False)
    assert rows[APPLIED].verdict == "fail" and rows[APPLIED].output == "0 / 10 프레임에 모자이크" and "프레임 5–14의 출력이" in rows[APPLIED].note


# ---------------------------------------------------------------------------------------------------------
# location tags


@pytest.fixture(scope="module")
def located(clips):
    return probe(make_clip(clips / "located.mp4", tags={"location": "+37.5665+126.9780/"}))[0]


def container_tags(path: str | Path) -> dict[str, str]:
    tags = ffprobe_json(Path(path))["format"].get("tags") or {}
    return {key: value for key, value in tags.items() if key not in R.MUXER_TAGS}


def test_location_tags_are_kept_unless_the_plan_removes_them(located, vfr, tmp_path):
    original = container_tags(located.path)
    assert original["location"] == "+37.5665+126.9780/" and "location" in located.location_tags and vfr.location_tags == []
    assert all(is_location_tag(name) for name in located.location_tags) and not is_location_tag("title")
    kept, dropped = tmp_path / "kept.mp4", tmp_path / "dropped.mp4"
    encoder = R.encoder_for("x264-crf16", located)
    R.render(located, kept, {}, encoder)
    assert container_tags(kept) == original and probe(kept)[0].location_tags == located.location_tags
    R.render(located, dropped, {}, encoder, drop_tag=is_location_tag)
    assert container_tags(dropped) == {key: value for key, value in original.items() if not is_location_tag(key)}  # that and nothing else
    assert container_tags(dropped)["title"] == "합성 클립" and probe(dropped)[0].location_tags == []
    assert decoded(dropped)[0] == decoded(located.path)[0] and audio_packets(dropped) == audio_packets(located.path)

    rows = V.verify(located, dropped, {}, dropped_tags=located.location_tags, vmaf=False)
    assert [row.check for row in rows] == [*CHECKS, "위치 메타데이터 삭제"] and V.passed(rows)
    assert (rows[-1].verdict, rows[-1].output) == ("pass", "남은 태그 없음") and all(name in rows[-1].original for name in located.location_tags)
    assert rows[3].verdict == "pass" and "계획에 따라 삭제한 태그: format.tags.location" in rows[3].note  # reported, not a difference
    # the output still carries the tag the plan removed
    rows = V.verify(located, kept, {}, dropped_tags=located.location_tags, vmaf=False)
    assert rows[-1].check == "위치 메타데이터 삭제" and rows[-1].verdict == "fail" and "location" in rows[-1].output and not V.passed(rows)
    # no removal was decided: the row is not there, a kept tag is fine and a missing one is a difference
    assert [row.check for row in V.verify(located, kept, {}, vmaf=False)] == CHECKS and V.passed(V.verify(located, kept, {}, vmaf=False))
    rows = V.verify(located, dropped, {}, vmaf=False)
    assert rows[3].verdict == "fail" and "format.tags.location" in rows[3].note


def test_stop_removes_the_partial_file_and_the_original_is_never_a_target(vfr, tmp_path):
    out = tmp_path / "out.mp4"
    polls = iter([False] * 6)
    with pytest.raises(VideoError, match="중단"):
        R.render(vfr, out, {}, R.encoder_for("x264-crf16", vfr), should_stop=lambda: next(polls, True))
    assert not out.exists()
    size = Path(vfr.path).stat().st_size
    with pytest.raises(VideoError):
        R.render(vfr, Path(vfr.path), {}, R.encoder_for("x264-crf16", vfr))
    assert Path(vfr.path).stat().st_size == size


def test_unsupported_pixel_format_is_refused(clips, tmp_path):
    meta = probe(make_clip(clips / "gray.mp4", codec="ffv1", pix_fmt="gray", audio=False, options={"level": "3"}))[0]
    assert meta.pix_fmt == "gray"
    with pytest.raises(VideoError, match="픽셀 포맷"):
        R.render(meta, tmp_path / "out.mp4", {}, R.encoder_for("x264-crf16", meta))
    assert not (tmp_path / "out.mp4").exists()
