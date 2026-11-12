# R7 — 원본 품질 보존 영상 I/O(프록시·PTS·재인코딩·검증)

- 조사일: 2026-11-05 · 방식: 백그라운드 리서치 에이전트가 1차 출처(공식 문서·소스·모델 카드·논문·가격 페이지)만 인용해 작성한 보고서 원문 · 반영 위치: 설계 §6, §10.7
- 상태: 설계·PRD v2 승인(2026-11-05) 시점의 스냅샷. 수치·가격·버전은 조사일 기준이며, 재검증 없이는 갱신되지 않는다. `UNVERIFIED` 표시는 1차 출처로 확인하지 못한 항목이다.

<details><summary>리서치 브리프(에이전트에 전달한 요청 전문)</summary>

```text
You are a research agent. Today is 2026-11-05. Do NOT write any files; return findings in your final message as Markdown with tables and a URL for every claim. Use WebSearch (mode "extended" when needed) and WebFetch on primary sources only: FFmpeg official documentation (ffmpeg.org/ffmpeg-all.html, ffmpeg-filters, ffmpeg-formats, ffmpeg-codecs, trac wiki), OpenCV docs, PyAV docs, libvmaf/Netflix VMAF docs, codec specs, GitHub repos of the named tools. Mark anything unverifiable as UNVERIFIED.

## Context
PrivFrame is a video privacy de-identification system (Python 3.12, OpenCV, FFmpeg CLI via subprocess, macOS Apple Silicon dev + Docker Linux deploy). Requirement from the project owner: **the output video must be identical to the input video except for the de-identified regions** — same resolution, frame rate (including variable frame rate), frame count, timestamps, duration, audio (bit-exact), container metadata (rotation, color primaries/transfer/matrix, HDR side data), and minimal quality loss outside masked regions. The current code normalizes the input to a constant 30 fps proxy for analysis and renders from that proxy (so output fps/timing differ from the input). Inputs: typical phone/dashcam/CCTV MP4/MOV (H.264/HEVC, often VFR, AAC audio, rotation metadata), 10 s – 5 min, 720p–4K. Redactions are blur/mosaic/mask on per-frame regions (boxes/ellipses/segmentation masks) over time ranges.

## Questions
1. **Proxy analysis + original render design**: how to build a downscaled constant-fps analysis proxy while keeping an exact mapping from proxy frame index/time to the original stream's frame PTS (e.g., using `ffprobe -show_frames`/`-show_packets` to list pts_time per original frame, or decoding the original with PyAV/OpenCV and recording `pkt_pts`/`best_effort_timestamp`). What does OpenCV's VideoCapture guarantee about frame timing for VFR files (CAP_PROP_POS_MSEC accuracy, fps metadata)? Cite docs.
2. **Preserving timing on re-encode**: FFmpeg options to keep VFR timestamps (`-fps_mode passthrough` / `-vsync 0` deprecation status, `-copyts`, `-enc_time_base`, `-video_track_timescale`), to keep the same timebase, and to avoid frame drops/dups. How to feed processed frames (rawvideo pipe from Python) while re-attaching the original per-frame timestamps (e.g., `-f rawvideo` pipe has no timestamps; alternatives: PyAV encoding with explicit `frame.pts` and `time_base`; or `concat`/`setpts` with a timestamps file; or the `-fps_mode passthrough` + pipe limitations). Give a recommended concrete approach with PyAV (version, API: `av.open`, `stream.codec_context`, `frame.pts`, `time_base`, `mux`) and its macOS wheel availability.
3. **Audio and metadata preservation**: `-c:a copy`, `-map 0`, `-map_metadata 0`, `-movflags use_metadata_tags`/`+faststart`, side data preservation (rotation `displaymatrix` via `-display_rotation` or copying side data; color `-color_primaries/-color_trc/-colorspace/-color_range`; HDR mastering display/CLL metadata; `-tag:v hvc1`), stream copy of subtitle/data tracks, chapter copy. Which are automatically preserved on re-encode and which need explicit flags? Cite ffmpeg docs.
4. **Minimizing quality loss**: (a) full re-encode with high quality (libx264 `-crf 14–18`, `-preset slow`, `-tune` none, same `pix_fmt`, same profile/level; libx265 equivalents; hardware encoders `h264_videotoolbox` quality options like `-q:v`, `-b:v`, `-allow_sw`, `-prio_speed`; `-x264-params` for `qp=0` lossless), (b) **GOP-level selective re-encoding ("smart cut")**: re-encode only the GOPs/segments that contain redactions and stream-copy the untouched segments, then concat (`-c copy` on keyframe-aligned segments, `concat` demuxer/protocol, `-segment_time`/`-break_non_keyframes`, timestamp continuity, `-avoid_negative_ts`); what are the constraints (keyframe boundaries, open-GOP / B-frame references, matching encoder parameters at splice points, SPS/PPS compatibility, audio splitting at non-frame boundaries). Cite primary docs and reference projects that implement smart cutting (e.g., LosslessCut's smart cut, "smartcut" Python projects, Avidemux docs) with their documented limitations. (c) Lossless intermediate options (ffv1, x264 lossless, ProRes 4444) and the size cost per minute at 1080p.
5. **Verification metrics**: computing PSNR/SSIM/VMAF between input and output **excluding masked regions** (masked metrics via ffmpeg `psnr`/`ssim` filters with preprocessing, or Python with scikit-image/OpenCV computing masked PSNR/SSIM per frame; libvmaf availability in ffmpeg builds for macOS Homebrew), frame-count and PTS equality checks (`ffprobe -show_frames -select_streams v -of csv`), audio bit-exactness (stream hash via `-f md5`/`framemd5`/`-f hash` demuxing audio only), container metadata diff (`ffprobe -show_format -show_streams -of json`), and the expected PSNR ranges for crf 14/16/18 re-encodes of H.264 1080p (cite x264/ffmpeg trac or papers if available, else UNVERIFIED).
6. **Rendering masks with original fps/VFR**: strategy for mapping analysis-rate masks (e.g., 10–15 fps on proxy) to every original frame (interpolation of boxes/masks between analysis frames; temporal dilation), and the risk of flicker; cite any doc/paper on temporal smoothing for anonymization (e.g., deface/DashcamCleaner/EgoBlur settings) if available.
7. Recommend one primary approach and one alternative for "identical except redactions", with: pipeline steps, exact FFmpeg/PyAV commands/options, what is bit-exact (audio, metadata, timing) vs. what is lossy (video stream in re-encoded ranges), and the verification checklist. Keep under ~3500 words.
```

</details>

---

## PrivFrame — "identical except the redactions": research findings (2026-11-05)

**Method.** FFmpeg claims are quoted from the official texinfo sources that render to ffmpeg.org/ffmpeg-all.html, and cross-checked against the installed Homebrew `ffmpeg 9.0.2` (`-h` output and pipe-only round-trip tests; no files written). PyAV facts come from the current docs (pyav.basswood.io — note pyav.org/docs/stable is a stale 9.0.2 snapshot) and the `master` source. `trac.ffmpeg.org` refused connections all day (also via archive), so wiki-only claims are marked **UNVERIFIED**.

---

## 1. Proxy analysis + exact mapping to original PTS

**Design:** keep the proxy *frame-count-identical* to the original (downscale only, timestamps passed through), so proxy index *i* == original frame *i* and the mapping is a lookup table, not an interpolation. Analysis at 10–15 fps is then a subsampling of indices, not a re-timing.

```
ffmpeg -i in.mp4 -map 0:v:0 -vf "scale=-2:360" -fps_mode passthrough -enc_time_base demux \
       -c:v libx264 -preset veryfast -crf 20 -an proxy.mp4
ffprobe -v error -select_streams v:0 -show_entries frame=pts,pts_time,pkt_dts,best_effort_timestamp,duration,key_frame,pict_type -of csv=p=0 in.mp4
ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=time_base,r_frame_rate,avg_frame_rate,nb_frames,nb_read_frames,start_time -of json in.mp4
```
Assert `nb_read_frames(proxy) == nb_read_frames(original)` and that the two PTS lists are equal (same `time_base`); if they are not, fall back to the PyAV table (decode original once, record `frame.pts`/`frame.time_base` per index).

| Fact | Evidence |
|---|---|
| `-fps_mode passthrough`: "Each frame is passed with its timestamp from the demuxer to the muxer." `auto` "Chooses between cfr and vfr depending on muxer capabilities." | [ffmpeg.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/ffmpeg.texi) ([rendered](https://ffmpeg.org/ffmpeg-all.html)) |
| ffprobe frame fields (master schema): `pts, pts_time, pkt_dts, best_effort_timestamp(_time), duration, key_frame, pict_type …`; `pkt_pts` exists in the n4.4 schema and is gone from n5.0 onward | [ffprobe.xsd master](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/ffprobe.xsd), [n4.4](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n4.4/doc/ffprobe.xsd) |
| `-count_frames`: "Count the number of frames per stream and report it in the corresponding stream section" (`nb_read_frames`); `-read_intervals`: "seeking is not accurate" | [ffprobe.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/ffprobe.texi) |
| PyAV: `Frame.pts` "presentation timestamp in time_base units", `Frame.time_base`, `Packet.pts/dts/duration`; `demux()` "The last packets are dummy packets that when decoded will flush the buffers." | [frame](https://pyav.basswood.io/docs/stable/api/frame.html), [packet](https://pyav.basswood.io/docs/stable/api/packet.html), [container](https://pyav.basswood.io/docs/stable/api/container.html) |

**What OpenCV guarantees for VFR: nothing.** The header says "Reading / writing properties involves many layers. Some unexpected result might happens along this chain" and "Effective behaviour depends from device hardware, driver and API Backend" ([videoio.hpp](https://raw.githubusercontent.com/opencv/opencv/4.x/modules/videoio/include/opencv2/videoio.hpp); [docs page](https://docs.opencv.org/4.x/d4/d15/group__videoio__flags__base.html)). The FFmpeg backend actually does this ([cap_ffmpeg_impl.hpp](https://raw.githubusercontent.com/opencv/opencv/4.x/modules/videoio/src/cap_ffmpeg_impl.hpp)):

| Property | Implementation | Consequence for VFR |
|---|---|---|
| `CAP_PROP_POS_MSEC` | `dts_to_sec(picture_pts)*1000`; `picture_pts` = frame `pts`, else `pkt_dts`; minus `start_time` | Usable per-frame time, but ms-resolution double, not exact ticks |
| `CAP_PROP_PTS` (read-only, FFmpeg only) | "presentation timestamp of the most recently read frame using the FPS time base" | Not the stream time base |
| `CAP_PROP_FPS` | `r2d(avg_frame_rate)` (fallbacks `av_guess_frame_rate`, `1/time_base`) | An average; meaningless per frame |
| `CAP_PROP_FRAME_COUNT` | `nb_frames`, else `floor(duration*fps+0.5)` | Estimate when container lacks `nb_frames` |
| `set(CAP_PROP_POS_FRAMES/POS_MSEC)` | `sec = frame_number/get_fps()` then `av_seek_frame(..., AVSEEK_FLAG_BACKWARD)` and decode forward | **Assumes CFR** — frame-index seeks land on wrong frames in VFR files (affects `track_editing.py` which uses `CAP_PROP_POS_FRAMES`) |
| Rotation | `CAP_PROP_ORIENTATION_META` (from display matrix), `CAP_PROP_ORIENTATION_AUTO` "rotates output frames … considering video file's metadata" | Set `ORIENTATION_AUTO` explicitly rather than relying on defaults |

---

## 2. Preserving timing on re-encode

| Option | Doc text | Source |
|---|---|---|
| `-fps_mode` (per-stream) | `passthrough`/`cfr`/`vfr` ("dropped so as to prevent 2 frames from having the same timestamp")/`auto`. "the timestamps may be further modified by the muxer … avoid_negative_ts" | [ffmpeg.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/ffmpeg.texi) |
| `-vsync` | n5.1–n8.0 docs: "vsync is deprecated and will be removed in the future"; **absent from master docs and from ffmpeg 9.0.2 `-h full`** | [n5.1 ffmpeg.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n5.1/doc/ffmpeg.texi) |
| `-enc_time_base` | `0` (default) "For video - use 1/framerate"; `demux` "Use the timebase from the demuxer"; or `1:90000` | ffmpeg.texi |
| `-copyts` | "keep their values without trying to sanitize them … do not remove the initial start time offset" | ffmpeg.texi |
| `-copytb 1` | stream-copy only: "sometimes required to avoid non monotonically increasing timestamps when copying video streams with variable frame rate" | ffmpeg.texi |
| `-r` (input) | "ignore any timestamps stored in the file and instead generate timestamps assuming constant frame rate" — **never use** | ffmpeg.texi |
| `-video_track_timescale` | "If set to 0, the timescale is automatically set based on the native stream time base"; movenc: `track->timescale = st->time_base.den; while(track->timescale < 10000) track->timescale *= 2;` (iPhone `1/600` would become 19200 unless forced) | [muxers.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/muxers.texi), [movenc.c](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavformat/movenc.c) |
| mp4/mov muxer flags | `AVFMT_GLOBALHEADER \| AVFMT_TS_NEGATIVE \| AVFMT_VARIABLE_FPS` → VFR accepted, negative TS allowed (`avoid_negative_ts auto` "Enables shifting when required by the target format") | movenc.c, [formats.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/formats.texi) |

**Local round-trip (ffmpeg 9.0.2, synthetic VFR, tb 1/90000):**

| Transcode options | Output PTS |
|---|---|
| `-fps_mode passthrough -enc_time_base demux -video_track_timescale 90000` | identical to input (all 12 ticks) |
| default `fps_mode` (auto→vfr) `-enc_time_base demux -video_track_timescale 90000` | identical |
| defaults (`enc_time_base 0`) | tb became 1/11520, PTS rounded **and one duplicate PTS** (2176064 twice) |

So the encoder time base is the real culprit, not just `fps_mode`.

**Feeding processed frames.** The rawvideo demuxer has no timestamps: "framerate — Set input video frame rate. Default value is 25" ([demuxers.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/demuxers.texi)); the current `-f rawvideo … -r fps -fps_mode cfr` pipe therefore cannot reproduce VFR. Options:

| Approach | How timestamps get re-attached | Verdict |
|---|---|---|
| **PyAV in-process (recommended)** | set `frame.pts/time_base` from the decoded source frame; `add_stream_from_template` copies `stream.time_base = template.ptr.time_base`; `mux_one` rescales packets to the stream time base | exact, one pass, audio/data packets remuxed untouched ([output.py](https://raw.githubusercontent.com/PyAV-Org/PyAV/master/av/container/output.py)) |
| FFmpeg-only filtergraph | Python emits per-frame *mask* PNGs + a concat list with exact `duration` lines; ffmpeg decodes the original (passthrough) and applies `maskedmerge` (base, blurred, mask) — framesync picks "nearest lower or equal timestamp" | works, 2 decodes, mask stream must be timestamp-exact ([filters.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/filters.texi), demuxers.texi) |
| Raw ES + mkvmerge `--timestamps` v2 ("Each line contains a timestamp for the corresponding frame … millisecond precision") then remux to MP4 | external timestamp file | ms precision only; extra tool ([mkvmerge doc](https://mkvtoolnix.download/doc/mkvmerge.html)) |
| `setts` BSF | expression-only (`N`, `PTS`, …), cannot read a table | no ([bitstream_filters.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/bitstream_filters.texi)) |

**PyAV facts (19.0.2, released 2026-10-21):** `requires_python >=3.12`; wheels include `cp312-abi3-macosx_14_0_arm64`, `macosx_11_0_x86_64`, `manylinux_2_28_{x86_64,aarch64}`, `musllinux`, Windows ([PyPI JSON](https://pypi.org/pypi/av/19.0.2/json)). Bundled FFmpeg is **9.0.2** with x264, x265 4.3, libvmaf 3.2.1, dav1d, SVT-AV1; macOS builds add `--enable-videotoolbox --enable-audiotoolbox` ([pyav-ffmpeg](https://github.com/PyAV-Org/pyav-ffmpeg), [build script](https://raw.githubusercontent.com/PyAV-Org/pyav-ffmpeg/main/scripts/build-ffmpeg.py)). Install page: "PyAV can also be compiled against your own build of FFmpeg 8.x" vs README "This release supports FFmpeg 9.x" (inconsistent; wheels are the safe path) ([installation](https://pyav.basswood.io/docs/stable/overview/installation.html)).

API you need ([container](https://pyav.basswood.io/docs/stable/api/container.html), [video](https://pyav.basswood.io/docs/stable/api/video.html), [stream.py](https://raw.githubusercontent.com/PyAV-Org/PyAV/master/av/video/stream.py), [frame.py](https://raw.githubusercontent.com/PyAV-Org/PyAV/master/av/video/frame.py), [cookbook](https://pyav.basswood.io/docs/stable/cookbook/basics.html)):
- `av.open(file, mode, format, options, container_options, …)` — muxer options such as `video_track_timescale`, `movflags` go in `options`.
- `OutputContainer.add_stream(codec_name, rate=None, options=None, hwaccel=None, **kwargs)`; `time_base` is accepted as a kwarg (popped and applied to the codec context). "Configure every output stream before muxing the first packet."
- `add_stream_from_template(template, opaque=None, **kwargs)` — "Supports video, audio, subtitle, data and attachment streams"; copies codecpar and time base, resets `codec_tag`.
- `VideoStream.set_display_rotation(degrees, hflip, vflip)` / `set_display_matrix(matrix)` — "Set the display matrix written to the container as coded side data … Must be called before the first frame is encoded"; read rotation from `VideoFrame.rotation`.
- `VideoFrame.from_ndarray(array, format)` supports `rgb24/bgr24/gray/yuv420p/yuvj420p/nv12/yuv444p/…` (yuv420p arrays are `(h*3/2, w)` uint8); `to_ndarray(format=...)`.
- Remux rule: "Don't test `packet.dts is None` here: a valid keyframe can legitimately have no DTS" — skip `packet.size == 0` instead.
- `CodecContext.decode(None)` flushes; `coded_side_data`/`decoded_side_data` dicts exist since 19.0.0 ([CHANGELOG](https://github.com/PyAV-Org/PyAV/blob/master/CHANGELOG.rst)).

Sketch (video re-encoded, everything else bit-copied):
```python
import av
inp = av.open("in.mp4"); vin = inp.streams.video[0]
out = av.open("out.mp4", "w", options={"movflags": "+faststart",
            "video_track_timescale": str(vin.time_base.denominator)})
out.metadata.update(inp.metadata)
vout = out.add_stream("libx264", time_base=vin.time_base, width=vin.width,
            height=vin.height, pix_fmt=vin.cctx.pix_fmt,
            options={"crf": "16", "preset": "slow", "profile": "high", "level": "4.1"})
for a in ("color_range", "color_primaries", "color_trc", "colorspace"):
    setattr(vout.cctx, a, getattr(vin.cctx, a))
copies = {s.index: out.add_stream_from_template(s) for s in inp.streams if s is not vin}
rot_set = False
for pkt in inp.demux():
    if pkt.stream is vin:
        for fr in pkt.decode():                      # flush packets decode to leftovers
            if not rot_set: vout.set_display_rotation(fr.rotation); rot_set = True
            planes = fr.to_ndarray()                 # native yuv420p: (h*3/2, w) uint8
            redact_inplace(planes, masks_at(fr.pts, fr.time_base))  # touch masked pixels only
            nf = av.VideoFrame.from_ndarray(planes, format=fr.format.name)
            nf.pts, nf.time_base = fr.pts, fr.time_base
            out.mux(vout.encode(nf))
    elif pkt.size:                                   # audio/data/subtitle: byte-exact copy
        pkt.stream = copies[pkt.stream.index]; out.mux(pkt)
out.mux(vout.encode(None)); out.close(); inp.close()
```
Working in the native YUV planes (no BGR round-trip) means pixels outside masks enter the encoder bit-identical to the decoded source. Note libav* never auto-rotates: frames arrive in sensor orientation, so analysis masks computed on an auto-rotated proxy must be mapped back through the inverse rotation.

---

## 3. Audio and metadata preservation

| Item | Automatic on re-encode? | Flag / note | Source |
|---|---|---|---|
| Audio payload + timestamps | yes with `-c:a copy` (default selection picks only one audio stream) | local test: `framemd5` of audio before/after transcode identical (`6161b8b1…`) | [muxers.texi hash muxers](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/muxers.texi) |
| All streams | **no**: "Data or attachment streams are not automatically selected and can only be included using -map" | `-map 0 -c copy -c:v libx264 …`; unsupported data codecs → `-ignore_unknown` ("Ignore input streams with unknown type instead of failing") or `-copy_unknown` | [ffmpeg.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/ffmpeg.texi) |
| Global/stream/chapter metadata | yes: "By default, global metadata is copied from the first input file, per-stream and per-chapter metadata is copied along with streams/chapters"; chapters "copied from the first input file with at least one chapter" | `-map_metadata 0`, `-map_chapters 0` explicit | ffmpeg.texi |
| QuickTime keys (location, make/model) | no: need `-movflags use_metadata_tags` ("use mdta atom for metadata") | | muxers.texi |
| Rotation (display matrix) | **no by default**: `-autorotate` "Enabled by default" rotates pixels at the filtering stage and drops the matrix; `-display_rotation`: "Otherwise, the metadata will be written into the output file if the muxer supports it" | `-noautorotate` (verified locally: `side_data_type: Display Matrix, rotation: 90` survives; default: frame dims swapped, no side data); stream copy keeps it | ffmpeg.texi |
| Color primaries/trc/matrix/range | yes: libx264 sets `vui.i_colorprim/i_transfer/i_colmatrix/b_fullrange/i_chroma_loc` from `avctx`; libx265 likewise; VideoToolbox maps `color_trc/colorspace/color_primaries` to VT keys; `colr` written when known (`write_colr` only forces it "even if the color info is unspecified") | override with `-color_primaries/-color_trc/-colorspace/-color_range` | [libx264.c](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavcodec/libx264.c), [libx265.c](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavcodec/libx265.c), [videotoolboxenc.c](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavcodec/videotoolboxenc.c), [codecs.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/codecs.texi) |
| HDR10 static metadata (MDCV/CLL) | yes since 7.0: "HDR10 metadata passthrough when encoding with libx264, libx265, and libsvtav1" (read from `decoded_side_data`; libx264 needs `CONFIG_LIBX264_HDR10`) | VideoToolbox: no MDCV/CLL handling found in source (UNVERIFIED) | [Changelog](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/Changelog) |
| Dolby Vision RPU (iPhone HDR) | libx265 only: `-dolbyvision` "Enable Dolby Vision RPU coding (default auto)" (X265_BUILD ≥ 167) | present in local 9.0.2 | libx265.c |
| HEVC tag | default `hev1` (verified locally); `-tag:v hvc1` "Force a tag/fourcc" | Apple-compat rationale lives in the trac wiki — UNVERIFIED | ffmpeg.texi |
| Edit lists / AAC priming | mp4 `use_editlist` default auto → 1 for non-fragmented output; `negative_cts_offsets` reduces need for edit lists | check `start_time`, `initial_padding` with ffprobe | muxers.texi, movenc.c |
| Pixel format / profile / level | not automatic | mirror ffprobe `pix_fmt`, `profile`, `level` via `-pix_fmt`, `-profile:v`, `-level` | [encoders.texi](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/encoders.texi) |

---

## 4. Minimizing quality loss

**(a) Full re-encode settings**

| Encoder | Knobs (documented) | Source |
|---|---|---|
| libx264 | `-crf` "Set the quality for constant quality mode" (x264 help: range 0–51); `-preset slow`; no `-tune`; `-profile:v/-level`; `-x264-params key=val:…`; `-qp 0` = "Force constant QP (0-…, 0=lossless)"; `forced-idr` makes forced I-frames IDR; pix_fmts yuv420p…yuv444p/nv12/10-bit | encoders.texi, [x264.c help text (mirror)](https://raw.githubusercontent.com/mirror/x264/master/x264.c) |
| libx265 | `-crf` (x265 default 28.0, range 0–51), `-preset`, `-x265-params`, `--lossless` "bypassing scaling, transform, quantization and in-loop filter"; **`--open-gop` default enabled** | [x265 CLI docs](https://x265.readthedocs.io/en/master/cli.html) |
| h264/hevc_videotoolbox | options from `ffmpeg -h encoder`: `-profile/-level/-coder`, `-allow_sw`, `-require_sw`, `-realtime`, `-prio_speed` (default auto), `-power_efficient`, `-spatial_aq`, `-max_ref_frames`, `-constant_bit_rate` (macOS 13+); `-q:v` maps to `kVTCompressionPropertyKey_Quality` (`global_quality/…/100`) and is "constant quality only on Macs with Apple Silicon" (else "-q:v qscale not available … Use -b:v bitrate instead"); PTS carried through exactly, DTS offset by reorder delay; **not documented in ffmpeg-codecs.html** (0 mentions in encoders.texi) | videotoolboxenc.c, local `-h encoder` |
| CRF "visually lossless ≈17–18", "+6 ≈ half size" | trac wiki unreachable → **UNVERIFIED** | [trac H.264](https://trac.ffmpeg.org/wiki/Encode/H.264) |

**(b) GOP-level selective re-encoding ("smart cut")** — stream-copy untouched runs, re-encode only runs containing redactions, splice.

| Constraint | Evidence |
|---|---|
| Copy segments must start on keyframes: segment muxer "Every segment starts with a keyframe"; `break_non_keyframes` "may make things worse"; `-ss` with copy keeps "the extra segment between the seek point and position"; `-copyinkf` only copies leading non-keyframes (undecodable) | muxers.texi, ffmpeg.texi |
| Keyframe ≠ safe splice for open GOP: x264 `--open-gop` "Use recovery points to close GOPs"; x265 `--open-gop` "allow I-slices to be non-IDR. Default enabled". Local open-GOP HEVC test: ffprobe `key_frame=1` on all three I-frames although NAL types were IDR(20)×1 and CRA(21)×2 → use `-bsf:v trace_headers` ("Log trace output containing all syntax elements in the coded stream headers") to find IDRs | x264.c, x265 docs, bitstream_filters.texi |
| Concat requires identical parameters: "All files must have the same streams (same codecs, same time base, etc.)"; per-file `duration` directive overrides inaccurate durations; `auto_convert` adds `h264_mp4toannexb` ("necessary in particular if there are resolution changes") | demuxers.texi |
| SPS/PPS: `h264_mp4toannexb` prepends extradata "only to the first type 5 NAL unit of an IDR picture, if no sps/pps are already present" → in-band parameter sets at the splice; MP4 then carries one global `avcC` from the first segment while later segments differ in-band. Decoding by strict `avc1` players — UNVERIFIED | [h264_mp4toannexb.c](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavcodec/bsf/h264_mp4toannexb.c) |
| Encoder parameters must match at splice points (profile, level, pix_fmt, refs, B-frames); DTS continuity: avcut "disables B-frames in locally reencoded GOPs to keep DTS continuous where encoded and copied packets meet" | [avcut](https://github.com/anyc/avcut) |
| Audio cannot be split at video keyframes exactly (AAC frames ≠ video frames) → never split audio; copy the whole audio track from the original | LosslessCut users report "audio issues at the moment it switches from re-encoding to not re-encoding" ([issue #126](https://github.com/mifi/lossless-cut/issues/126)) |
| Reference implementations: LosslessCut smart cut "re-encodes only the part from the cutpoint to the next keyframe. It does not work for all files", "experimental, so don't expect too much"; maintainer: "success with some h264 files, and only a few h265 files". `smartcut` (PyAV-based): "only recodes around the cutpoints", "This repo is no longer being updated." Avidemux: in copy mode "the first frame you copy is an I-frame; … the frame after the insert point must be I-frame", otherwise frames "specify changes relative to an I-frame that no longer exists" | [LosslessCut docs](https://losslesscut.app/docs/troubleshooting), [smartcut](https://github.com/skeskinen/smartcut), [Avidemux](https://www.avidemux.org/admWiki/doku.php?id=using:cutting) |

**(c) Lossless intermediates (1080p, per minute)**

| Codec | Options | Size/min | Source |
|---|---|---|---|
| ProRes 4444 / 422 HQ (`prores_ks -profile:v 4444`) | 10/12-bit 4:4:4(:4) | ≈330 Mb/s → **≈2.5 GB/min**; 422 HQ ≈220 Mb/s → ≈1.65 GB/min (Apple target rates at 1920×1080/29.97); ProRes is "virtually lossless", not mathematically | [Apple ProRes](https://support.apple.com/en-us/102207) |
| FFV1 | `-c:v ffv1 -level 3 -coder range_def -context 1 -slices 24 -slicecrc 1` | UNVERIFIED (no primary figure) | encoders.texi |
| x264 lossless | `-qp 0` ("0=lossless"), requires high444 for 4:4:4 (baseline/main/high: "No lossless.") | UNVERIFIED | x264.c |

Real-world PSNR for CRF 14/16/18 at 1080p: no primary table found (search returned forum/blog measurements only) — **UNVERIFIED**; measure on your own clips with the `psnr` filter.

---

## 5. Verification metrics

| Check | Command / method | Source |
|---|---|---|
| Frame count + PTS equality | `ffprobe -select_streams v:0 -count_frames -show_entries stream=nb_read_frames,time_base:frame=pts -of csv=p=0` on both, diff the lists | ffprobe.texi/xsd |
| Audio bit-exact | `ffmpeg -i X -map 0:a -c copy -f framemd5 -` on input and output, diff (lines are `stream_index, packet_dts, packet_pts, packet_duration, packet_size, hash`; `-f hash`/`streamhash` ignore timestamps) — passed in local test | muxers.texi |
| Container/metadata diff | `ffprobe -show_format -show_streams -show_chapters -of json` → diff `tags`, `side_data_list` (`Display Matrix`/`rotation`), `color_*`, `profile`, `level`, `pix_fmt`, `time_base`, `start_time`, `initial_padding` | ffprobe.texi |
| PSNR/SSIM (full frame) | `ffmpeg -i out.mp4 -i in.mp4 -lavfi "[0:v][1:v]psnr=stats_file=-;[0:v][1:v]ssim" -f null -`; "Both video inputs must have the same resolution and pixel format … assumes that both inputs have the same number of frames, which are compared one by one"; add `settb=AVTB,setpts=PTS-STARTPTS` on both if timebases differ | filters.texi |
| **Masked** PSNR/SSIM | ffmpeg filters have no mask; compute in Python per frame (PyAV decode both): MSE over unmasked pixels only (erode mask by the blur radius + SSIM window); SSIM map via `skimage.metrics.structural_similarity(..., full=True)` "also return the full structural similarity image", then average over unmasked pixels; `peak_signal_noise_ratio(image_true, image_test, data_range=255)`. Pre-painting identical content into masked regions in *both* inputs makes the ffmpeg filters usable but biases scores upward | [scikit-image](https://scikit-image.org/docs/stable/api/skimage.metrics.html) |
| VMAF | `libvmaf` filter: "The first input is the distorted video, and the second input is the reference video"; default `model=version=vmaf_v0.6.1` (1080p HDTV, 3H); 4K: `vmaf_4k_v0.6.1` (1.5H); `n_threads`, `n_subsample`, `log_fmt=json`; Netflix's own example uses `setpts=PTS-STARTPTS` because "FFmpeg filters synchronize based on timestamps instead of frames". Homebrew ffmpeg depends on `libvmaf` (and local build has `--enable-libvmaf`); PyAV wheels bundle libvmaf 3.2.1. No masked VMAF exists | filters.texi, [models_v0.md](https://github.com/Netflix/vmaf/blob/master/resource/doc/models_v0.md), [ffmpeg.md](https://github.com/Netflix/vmaf/blob/master/resource/doc/ffmpeg.md), [brew](https://formulae.brew.sh/formula/ffmpeg) |
| Bit-exact outside re-encoded ranges (smart-cut) | compare `-f framemd5` of video packets in copied ranges, or decode both and `np.array_equal` per frame | muxers.texi |

---

## 6. Rendering masks at original fps/VFR

- Key masks by **original PTS** (not proxy index): for original frame at time *t* between analysis samples *t0 ≤ t ≤ t1* of the same track ID, linearly interpolate box corners/ellipse params; for segmentation masks, union of the two neighbours (or warp by box motion). Apply **temporal dilation**: extend every track's time range by ±(analysis interval) and dilate spatially so motion between 10–15 fps samples stays covered.
- Flicker/holes come from per-frame detection gaps. Tool precedents: deface processes "each video frame independently" and compensates spatially with `--mask-scale` "Scale factor for face masks, to make sure that masks cover the complete face. Default: 1.3" ([deface](https://github.com/ORB-HD/deface)); DashcamCleaner `--blur_memory` "Blur detected plates from n previous frames too in order to (maybe) cover up missed identifiable information" ([DashcamCleaner](https://github.com/tfaehse/DashcamCleaner)); EgoBlur `--scale_factor_detections` ("1.1 … scaling UP the predicted blurred region by 10%") and requires "a valid, fixed FPS" ([EgoBlur](https://github.com/facebookresearch/EgoBlur)); Balaji et al. note "individually anonymized frames leave a flickering effect on the video sequence" ([arXiv 2106.02328](https://arxiv.org/abs/2106.02328)).
- Hysteresis on track start/end (hold-last-K, min duration) and EMA on box size suppress jitter; never shrink a mask between keyframes.

---

## 7. Recommendation

**Primary — PyAV single-pass "re-encode video, copy everything else" (§2 sketch).**
1. ffprobe the source (time_base, pix_fmt, profile/level, color, rotation, HDR side data, stream list).
2. Build proxy with `-fps_mode passthrough -enc_time_base demux` (frame-count identical); analyse at 10–15 fps on proxy indices; store masks by original PTS.
3. Render with PyAV: decode → modify masked YUV pixels in place → `frame.pts/time_base` copied → libx264 `crf 16, preset slow`, same pix_fmt/profile/level, color props copied, `set_display_rotation(frame.rotation)`, mux audio/data/subtitles via `add_stream_from_template`, `video_track_timescale` = source timescale, `inp.metadata` copied. On macOS use `h264_videotoolbox` with `-b:v` ≈ 1.5–2× source bitrate (`-q:v` is Apple-Silicon-only); Docker/Linux: libx264. HEVC/HDR sources: libx265 (`-dolbyvision auto`, HDR10 passthrough) or hevc_videotoolbox.
4. Equivalent CLI shape, if PrivFrame stays on the FFmpeg binary for intermediates: `ffmpeg -noautorotate -i in.mp4 -map 0 -c copy -c:v libx264 -crf 16 -preset slow -pix_fmt <src> -profile:v <src> -level <src> -fps_mode passthrough -enc_time_base demux -video_track_timescale <src> -map_metadata 0 -map_chapters 0 -movflags +faststart+use_metadata_tags -ignore_unknown out.mp4`.

Bit-exact: audio, subtitle/data packets, timestamps/frame count/duration, container tags, rotation, colour/HDR side data. Lossy: every video pixel (encoder noise, ~CRF 16), including outside masks — small but non-zero.

**Alternative — GOP-level smart cut** (bit-exact video outside redacted IDR→IDR runs): find IDRs with `trace_headers`; stream-copy video-only runs (`-ss`/`-to` input seeking, `-c copy`), re-encode redacted runs with matching profile/level/pix_fmt and `-bf 0` (or `-x264-params bframes=0`) ending before the next IDR, splice with the concat demuxer (explicit `duration` per piece, `-fps_mode passthrough -enc_time_base demux`), then mux the untouched original audio/data (`-i spliced.mp4 -i in.mp4 -map 0:v -map 1:a -map 1:d? -c copy`). Only for closed-GOP H.264; validate with full decode + per-frame hashes in copied ranges and playback on target players (SPS/PPS mismatch risk, LosslessCut/avcut caveats above).

**Checklist:** nb_read_frames equal · PTS lists equal in the same time_base · `framemd5` of audio identical · ffprobe JSON diff empty except `encoder`/bit_rate · rotation side data present · masked PSNR ≥ target (e.g. >45 dB Y) and SSIM map ≥ 0.99 outside masks (UNVERIFIED thresholds) · every redacted frame's mask region detectable-free · VMAF (full frame, informational).
