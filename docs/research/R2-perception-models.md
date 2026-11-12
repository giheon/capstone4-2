# R2 — 지각 모델 조사(검출·추적·분할·OCR·번호판·연령) — 종합

- 조사일: 2026-11-05 · 방식: 백그라운드 리서치 에이전트가 1차 출처(공식 문서·소스·모델 카드·논문·가격 페이지)만 인용해 작성한 보고서 원문 · 반영 위치: 설계 §10.2 (07-models-and-tools.md)
- 상태: 설계·PRD v2 승인(2026-11-05) 시점의 스냅샷. 수치·가격·버전은 조사일 기준이며, 재검증 없이는 갱신되지 않는다. `UNVERIFIED` 표시는 1차 출처로 확인하지 못한 항목이다.

<details><summary>리서치 브리프(에이전트에 전달한 요청 전문)</summary>

```text
You are a research agent. Today is 2026-11-05. Do NOT write any files; return your findings in your final message as Markdown with tables and a URL for every claim. Use WebSearch (mode "extended" for recent/niche facts) and WebFetch on primary sources only: official GitHub repos/READMEs, model cards, papers, license files, official benchmark pages. Mark anything unverifiable as UNVERIFIED.

## Project context
PrivFrame is a Korean capstone project: a LangGraph multi-agent system that de-identifies personal information in user-uploaded short videos (10 s – 5 min, 720p–1080p, everyday scenes: street, office, school, hospital, vlog, dashcam) and grounds redaction decisions in Korean privacy law. Current code (Python 3.12, OpenCV, ONNX Runtime CPU) already has: YuNet face detection (ONNX), a simple IoU tracker, PySceneDetect scene boundaries, ArcFace w600k_r50 for identity grouping (non-commercial license), and bounding-box blur/mosaic/mask rendering. The professor criticized bbox-only mosaics (shape mismatch) and asked to extend targets beyond faces to full bodies and person-related objects (name tags, license plates, on-screen text/documents, distinctive items). Dev machine: macOS Apple Silicon (no CUDA); must run CPU/MPS-friendly models, optionally GPU in Docker later. Research project, but prefer permissive licenses (Apache-2.0/MIT) over AGPL/non-commercial; note license per model.

## Questions (Track R2: perception models for privacy targets)
1. Object detection for targets {person/full body, face, license plate, text regions, documents/screens, name tags/badges, bags/distinctive items}: compare closed-set detectors (Ultralytics YOLO11 / YOLO26 or current, with AGPL-3.0 license note; RT-DETRv2/v3; D-FINE; DEIM / DEIMv2; RF-DETR (Apache-2.0); RTMDet) and open-vocabulary detectors (Grounding DINO 1.5/1.6, OWLv2, YOLO-World, YOLOE, Florence-2, SAM 3 if released with concept prompts). Report COCO mAP, parameters, CPU/Apple-Silicon speed evidence, ONNX/CoreML export, license.
2. Segmentation for shape-aware masking (replacing bbox mosaics): SAM 2.1 / SAM 3, EfficientSAM, MobileSAM, FastSAM, YOLO11-seg, Mask2Former; video object segmentation with SAM 2 memory tracking; CPU feasibility and per-frame latency evidence; license.
3. Multi-object tracking: ByteTrack, BoT-SORT, OC-SORT / Deep OC-SORT, StrongSORT, SAM2-based tracking, Ultralytics built-in trackers; person Re-ID for re-association across scenes (OSNet, CLIP-ReID, SOLIDER, KPR); which are pip-installable and license-permissive.
4. Face: compare YuNet (current), SCRFD, RetinaFace, YOLO-face/YOLOv8-face; face embedding alternatives to ArcFace/InsightFace with permissive licenses (e.g., FaceNet-pytorch, AdaFace, any Apache/MIT ONNX models) and their accuracy (LFW/IJB-C numbers from primary sources).
5. OCR for Korean scene text: PaddleOCR 3.x PP-OCRv5 (Korean support?), EasyOCR (Korean), Surya, docTR, Tesseract, and VLM/OCR-models (DeepSeek-OCR, GOT-OCR2, Florence-2, Qwen3-VL): Korean accuracy evidence, CPU speed, license.
6. License plates (Korean): open-source Korean LPR datasets/models; generic plate detectors + OCR; privacy-specific toolkits: Meta EgoBlur (faces + license plates), deface, DashcamCleaner, understand.ai anonymizer, "Brighter AI"-like commercial; which are usable (license) and their reported accuracy.
7. Age/child estimation (legal relevance of minors): MiVOLO, InsightFace age models, VLM-based estimation; accuracy and license.
8. Recommendation: for each target (face, body, plate, text, documents/screens, distinctive items), give primary + alternative model, a CPU-first pipeline shape (detect → track → segment-on-demand → render), expected per-frame latency on CPU, and how masks (not boxes) can be produced cheaply (e.g., SAM on keyframes + propagation, or seg-head detectors). Include what to keep from the current stack (YuNet, IoU tracker, ArcFace) and what to replace. Keep under ~3500 words.
```

</details>

---

## PrivFrame Track R2 — perception models for privacy targets (verified 2026-11-05)

**Conventions.** COCO = val2017 mAP50-95 unless noted. "CPU ONNX" = Ultralytics' own ONNX Runtime tables (hardware is stated inconsistently — README says EC2 P4d, the YOLO26 paper says "Intel Xeon CPU @ 2.00 GHz" — so treat as relative). No vendor publishes ONNX Runtime numbers on Apple Silicon except where noted; anything marked *est.* is my estimate, not a measurement. License = code / weights. Seven read-only subagents gathered primary sources; I re-fetched the load-bearing ones (RF-DETR, YOLO26, D-FINE, DEIMv2, EfficientTAM, EdgeTAM, SAM-2 CPU table, PP-OCRv6/v5-Korean, RapidOCR, EgoBlur, trackers, YuNet, SFace, AdaFace, open-image-models, yakhyo RetinaFace).

## 1. Object detection

### 1a. Closed-set

| Model (variants) | COCO | Params | Official latency | CPU / Apple-Si evidence | ONNX / CoreML | License |
|---|---|---|---|---|---|---|
| YOLO11 n/s/m | 39.5/47.0/51.5 | 2.6/9.4/20.1M | T4 TRT 1.5/2.5/4.7 ms | CPU ONNX 56.1/90.0/183.2 ms | both official | **AGPL-3.0** code + weights (paid Enterprise) — [docs](https://docs.ultralytics.com/models/yolo11/), [license](https://www.ultralytics.com/license) |
| YOLO26 n/s/m (Jan 2026; optional NMS-free head) | 40.9/48.6/53.1 (e2e 40.1/47.8/52.5) | 2.4/9.5/20.4M | T4 1.7/2.5/4.7 ms | CPU ONNX 38.9/87.2/220.0 ms; YOLO26n CoreML on Mac mini M4: 14.4 ms CPU-only, 7.6 ms CPU+NE | both | AGPL-3.0 — [docs](https://docs.ultralytics.com/models/yolo26/), [Mac perf](https://github.com/ultralytics/yolo-ios-app/blob/main/docs/performance.md) |
| RT-DETRv2 S/M/L | 48.1/51.9/53.4 | 20/36/42M | T4 TRT 217/145/108 FPS | none | ONNX script; no CoreML | Apache-2.0; HF `RTDetrV2ForObjectDetection` — [repo](https://github.com/lyuwenyu/RT-DETR), [HF](https://huggingface.co/docs/transformers/model_doc/rt_detr_v2) |
| RT-DETRv3 | same inference cost/AP as v2 (aux head is training-only) | — | — | none | PaddlePaddle only | Apache-2.0 — [repo](https://github.com/clxia12/RT-DETRv3) |
| D-FINE N/S/M/L | 42.8/48.5/52.3/54.0 (Obj365→COCO S 50.7) | 4/10/19/31M, 7/25/57/91 GFLOPs | T4 TRT10 fp16 2.12/3.49/5.62/8.07 ms | none | ONNX script; HF `DFineForObjectDetection` | Apache-2.0, but Obj365 checkpoints "should not be assumed to be commercially cleared" — [repo](https://github.com/Peterande/D-FINE), [HF](https://huggingface.co/docs/transformers/model_doc/d_fine) |
| DEIM (D-FINE) N/S/M/L | 43.0/49.0/52.7/54.7 | as D-FINE | as D-FINE | none | ONNX script | Apache-2.0 — [repo](https://github.com/ShihuaHuang95/DEIM) |
| DEIMv2 Pico/N/S/M (DINOv3-distilled) | 38.5/43.0/50.9/53.0 | 1.5/3.6/9.7/18.1M | 2.13/2.32/5.78/8.80 ms (hardware UNVERIFIED) | none | ONNX script | **Custom non-commercial** ("Commercial Use … is not permitted", weights included) — [LICENSE](https://raw.githubusercontent.com/Intellindust-AI-Lab/DEIMv2/main/LICENSE.md) |
| RF-DETR Nano/Small/Medium/Large (@384/512/576/704) | 48.4/53.0/54.7/56.5 | 30.5/32.1/33.7/33.9M | T4 TRT fp16 2.3/3.5/4.4/6.8 ms | **Nano CoreML, M3 Pro: fp32 CPU-only 73.6 ms; fp16 CPU+NE 20.8 ms (mAP 48.0→45.1)** | ONNX, CoreML, OpenVINO, TFLite (official) | Apache-2.0 for Nano–Large and all Seg; Atto/Femto/Pico/XL/2XL under Roboflow PML 1.0; `pip install rfdetr` — [repo](https://github.com/roboflow/rf-detr), [CoreML](https://rfdetr.roboflow.com/latest/exports/coreml/) |
| RTMDet tiny/s/m | 41.1/44.6/49.4 | 4.8/8.9/24.7M | 3090 TRT 0.98/1.22/1.62 ms | none | mmdeploy ORT | Apache-2.0, but mmdet last release 2024-01 — [README](https://github.com/open-mmlab/mmdetection/blob/main/configs/rtmdet/README.md), [PyPI](https://pypi.org/project/mmdet/) |

Notes: YOLO12 and YOLOv13 are also AGPL-3.0 ([v12](https://github.com/sunsmarterjie/yolov12), [v13](https://github.com/iMoonLab/yolov13)). Objects365 (RF-DETR and D-FINE pretraining) has *Person, Backpack, Handbag/Satchel, Luggage, Monitor/TV, Laptop, Cell Phone, Book, Notepaper, Blackboard/Whiteboard, Mask, Glasses* but **no license-plate, name-tag or face class** ([class list](https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/cfg/datasets/Objects365.yaml)). RT-DETRv4 (2025-11) exists but was not evaluated — UNVERIFIED.

### 1b. Open-vocabulary

| Model | Zero-shot benchmark | Params | Speed evidence | Masks / video | License / install |
|---|---|---|---|---|---|
| Grounding DINO 1.0 Swin-T | COCO 48.4; LVIS-minival 27.4 | 172M | A100 9.4 FPS; Orin NX 1.1 FPS; "CPU-only mode" supported, no numbers | no / no | Apache-2.0; `transformers` — [repo](https://github.com/IDEA-Research/GroundingDINO), [HF](https://huggingface.co/IDEA-Research/grounding-dino-tiny), [speed](https://arxiv.org/html/2405.10300) |
| MM-Grounding-DINO Swin-T | COCO 50.6; LVIS 41.4 | ~0.2B | none | no / no | Apache-2.0; HF `openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det` — [README](https://github.com/open-mmlab/mmdetection/blob/main/configs/mm_grounding_dino/README.md) |
| Grounding DINO 1.5/1.6 Pro/Edge; DINO-X | 1.6 Pro COCO 55.4, LVIS 57.7; 1.6 Edge COCO 44.8 | n/p | Edge: Orin NX 10–15 FPS | DINO-X Pro masks | **API-only weights** — [1.5 API](https://github.com/IDEA-Research/Grounding-DINO-1.5-API), [DINO-X](https://github.com/IDEA-Research/DINO-X-API) |
| OWLv2 B/16 | LVIS AP 41.8 | 0.2B | none published | no / no | Apache-2.0; `transformers` — [paper](https://arxiv.org/html/2306.09683), [HF](https://huggingface.co/google/owlv2-base-patch16-ensemble) |
| OmDet-Turbo Tiny | COCO 42.5; LVIS 30.3 | ~0.1B | A100 21.5 FPS PyTorch | no / no | Apache-2.0; `transformers` — [repo](https://github.com/om-ai-lab/OmDet), [HF](https://huggingface.co/omlab/omdet-turbo-swin-tiny-hf) |
| YOLO-World v2.1 S/M/L | LVIS 23.6/30.6/33.8 | 13/29/48M | V100 74/58/52 FPS (v1) | no / no | **GPL-3.0** (Ultralytics wrapper AGPL) — [repo](https://github.com/AILab-CVC/YOLO-World) |
| YOLOE v8-S/M/L | LVIS 27.9/32.6/35.9; prompt-free S 21.0 | 12/27/45M | T4 TRT 306/157/103 FPS; iPhone 12 CoreML 64/42/27 FPS | **yes** / no | **AGPL-3.0** — [repo](https://github.com/THU-MIG/yoloe) |
| YOLOE-26 n/s/m | LVIS 24.7/30.8/35.4 | 3.9/10.7/21.3M | none (YOLO26 det CPU ms is a proxy) | yes / no | AGPL-3.0 — [docs](https://docs.ultralytics.com/models/yoloe/) |
| Florence-2 base/large | COCO zero-shot 34.7/37.5 (ft 41.4/43.4) | 0.23/0.77B | none (autoregressive decoding) | polygons via RES / no | MIT; `transformers`; onnx-community ports — [HF](https://huggingface.co/microsoft/Florence-2-large), [ONNX](https://huggingface.co/onnx-community/Florence-2-base) |
| **SAM 3 / 3.1** (released 2025-11 / 2026-03) | LVIS zero-shot mask AP 48.5, box 53.6; COCO box 56.4 | 848M | H200 30 ms/img; README requires CUDA 12.6+, Python 3.12+; no CPU/MPS statement | **yes / yes** (text + exemplar prompts, built-in tracker) | custom "SAM License", gated HF — [repo](https://github.com/facebookresearch/sam3), [LICENSE](https://github.com/facebookresearch/sam3/blob/main/LICENSE), [paper](https://arxiv.org/abs/2511.16719) |

No primary source gives CPU latency for any open-vocabulary detector; Grounding DINO-T at 1.1 FPS on an Orin NX GPU implies seconds per frame on CPU (UNVERIFIED). Keyframe use only.

## 2. Segmentation for shape-aware masks

| Model | Params | Benchmark | Official latency | CPU / Apple-Si evidence | Video memory | Export | License |
|---|---|---|---|---|---|---|---|
| SAM 2.1 T/S/B+/L | 38.9/46/80.8/224M | SA-V test J&F 76.5/76.6/78.2/79.5 | A100 91/85/64/40 FPS | Ultralytics table, M4 Air CPU, PyTorch, no prompt: SAM2-t **23,430 ms**, SAM2-b 28,867 ms; official notebook calls MPS "preliminary" | yes (`propagate_in_video`) | no official; Apple CoreML (image parts only); community ONNX incl. memory | Apache-2.0 — [repo](https://github.com/facebookresearch/sam2), [M4 table](https://docs.ultralytics.com/models/sam-2/), [CoreML](https://huggingface.co/apple/coreml-sam2.1-small/tree/main), [ONNX](https://huggingface.co/square-zero-labs/sam2.1-tiny-video-onnx) |
| SAM 3/3.1 | 848M | §1b | H200 only | none; unmerged PR #627 claims ~1.3 fps 720p on M5 MPS | yes | none | SAM License — [PR](https://github.com/facebookresearch/sam3/pull/627) |
| **EfficientTAM S/Ti** | 34/18M | SA-V test 74.5/70.7; DAVIS 89.2 | A100 85/96 FPS; iPhone 15 Pro Max S@512 **80.6 ms/frame** | README: "support for running … on Macs with MPS backend" | yes | not mentioned | Apache-2.0 — [repo](https://github.com/yformer/EfficientTAM), [paper](https://arxiv.org/html/2411.18933) |
| **EdgeTAM** | not stated | SA-V test 71.7; DAVIS 87.7 | A100 150.9 FPS; iPhone 15 Pro Max 15.7 FPS (SAM2-B+ 0.7) | none for CPU | yes | CoreML (3 parts) | Apache-2.0 — [repo](https://github.com/facebookresearch/EdgeTAM) |
| MobileSAM | 9.7M | COCO AP 38.7 | GPU 12 ms | "Mac i5 CPU ~3 s"; M4 Air 23.8 s (no prompt) | no | ONNX | Apache-2.0 — [repo](https://github.com/ChaoningZhang/MobileSAM) |
| EfficientSAM Ti/S | 10/25M | COCO 42.3/44.4 | A100 54/47 img/s | none | no | ONNX script | Apache-2.0 — [repo](https://github.com/yformer/EfficientSAM) |
| RepViT-SAM | n/s | COCO 44.4 | **M1 Pro CoreML enc 44.8 + dec 11.8 ms** | that row | no | CoreML | Apache-2.0 — [README](https://github.com/THU-MIG/RepViT/blob/main/sam/README.md) |
| EfficientViT-SAM L0 | 34.8M | COCO 45.7 | A100 TRT 762 img/s | none | no | ONNX/TRT | Apache-2.0, "no longer maintain" — [README](https://github.com/mit-han-lab/efficientvit/blob/master/applications/efficientvit_sam/README.md) |
| EdgeSAM | 9.6M | COCO 42.2 | iPhone 14 38.7 FPS | none | no | ONNX/CoreML | **S-Lab non-commercial** — [LICENSE](https://github.com/chongzhou96/EdgeSAM/blob/master/LICENSE) |
| FastSAM | 68M | COCO AP 37.9 | 3090 40 ms | FastSAM-s 58 ms M4 Air | no | — | **AGPL-3.0** LICENSE file (README says Apache) — [LICENSE](https://github.com/CASIA-IVA-Lab/FastSAM/blob/main/LICENSE) |
| YOLO11-seg n/s/m; YOLO26n-seg | 2.9/10.1/22.4M; 2.7M | mask 32.0/37.8/41.5; 33.9 | T4 1.8/2.9/6.3 ms | CPU ONNX 65.9/117.6/281.6 ms; YOLO26n-seg 53.3 ms; YOLO11n-seg 24.3 ms M4 Air ORT | no | both | AGPL-3.0 — [yolo11](https://docs.ultralytics.com/models/yolo11/), [segment](https://docs.ultralytics.com/tasks/segment/) |
| **RF-DETR-Seg N/S/M** | 33.6/33.7/35.7M | AP50:95 40.3/43.1/45.3 @312/384/432 (column not labeled "mask") | T4 3.4/4.4/5.9 ms | none (Seg-Nano M3 Pro figures UNVERIFIED; CoreML page only lists det-Nano) | no | ONNX/CoreML/OpenVINO | Apache-2.0 — [repo](https://github.com/roboflow/rf-detr) |
| RTMDet-Ins tiny/s | 5.6/10.2M | mask 35.4/38.7 | 3090 TRT 1.7/1.9 ms | none | no | mmdeploy (not listed) | Apache-2.0, unmaintained |
| Mask2Former Swin-T | 47.5M | COCO inst AP 45.0 | V100 R50 9.7 fps | none | no | PyTorch | code MIT, **weights CC BY-NC 4.0**, archived — [MODEL_ZOO](https://github.com/facebookresearch/Mask2Former/blob/main/MODEL_ZOO.md) |

Person-silhouette shortcuts (semantic, not instance): MediaPipe Selfie (Apache-2.0, 33 ms Pixel 6 CPU) [docs](https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter); PP-HumanSeg V2-Lite (Apache-2.0, 15.9 ms Snapdragon 855) [README](https://github.com/PaddlePaddle/PaddleSeg/blob/release/2.9/contrib/PP-HumanSeg/README.md); MODNet Apache-2.0; BiRefNet MIT; RVM **GPL-3.0** [repo](https://github.com/PeterL1n/RobustVideoMatting). They need a per-detection crop to become per-person masks.

## 3. Multi-object tracking and Re-ID

| Tracker | Paradigm | MOT17 HOTA/MOTA/IDF1 | GPU ReID? | pip | License |
|---|---|---|---|---|---|
| ByteTrack | Kalman + IoU | 63.1/80.3/77.3 | no | none official | MIT — [repo](https://github.com/ifzhang/ByteTrack) |
| BoT-SORT | + camera-motion comp.; optional ReID | 64.6/80.6/79.5 (ReID 65.0/80.5/80.2) | optional | none official | MIT — [repo](https://github.com/NirAharon/BoT-SORT) |
| OC-SORT | motion-only; active (2026 refactor) | 63.2/78.0/77.5 | no | none | MIT — [repo](https://github.com/noahcao/OC_SORT) |
| Deep OC-SORT | + FastReID appearance | 64.9/79.4/80.6 | yes (CPU possible) | none | MIT, stale — [repo](https://github.com/GerardMaggiolino/Deep-OC-SORT) |
| StrongSORT | appearance + ECC | 63.5/78.3/78.5 | yes | none | **GPL-3.0** — [repo](https://github.com/dyhBUPT/StrongSORT) |
| BoostTrack++ / UCMCTrack | motion (+ReID) / ground-plane (needs camera params) | 66.6/80.7/82.2 / 65.8/80.6/81.0 | optional / no | none | MIT / MIT — [BoostTrack](https://github.com/vukasin-stanojevic/BoostTrack), [UCMC](https://github.com/corfyi/UCMCTrack) |
| **roboflow/trackers** | SORT, ByteTrack, OC-SORT, C-BIoU, BoT-SORT(+ReID), McByte | own harness HOTA: 58.4/60.1/61.9/63.0/63.7(+ReID 63.9)/64.1 | optional (`trackers[reid]`) | `pip install trackers` (py≥3.10) | **Apache-2.0** — [repo](https://github.com/roboflow/trackers) |
| supervision ByteTrack | motion-only | none | no | `pip install supervision` | MIT — [source](https://raw.githubusercontent.com/roboflow/supervision/main/supervision/tracker/byte_tracker/core.py) |
| BoxMOT (9 trackers, 37 ReID weights) | mixed | own split: BoT-SORT 69.7 | optional | `pip install boxmot` | **AGPL-3.0** — [repo](https://github.com/mikel-brostrom/boxmot) |
| Ultralytics (bytetrack/botsort/ocsort/tracktrack…) | mixed | none published | optional | `ultralytics` | **AGPL-3.0** — [docs](https://docs.ultralytics.com/modes/track/) |
| SAM2-based: SAMURAI, DAM4SAM (SOT), SAM2MOT (MOT) | SAM2.1-L memory | SAMURAI LaSOT AUC 74.2; SAM2MOT DanceTrack HOTA 75.5, MOT17 n/r, "primary limitation … inference speed" | GPU | none | Apache-2.0 — [SAMURAI](https://github.com/yangchris11/samurai), [SAM2MOT](https://github.com/TripleJoy/SAM2MOT), [DAM4SAM](https://github.com/jovanavidenovic/DAM4SAM) |

| ReID model | Market-1501 mAP/R1 | MSMT17 mAP/R1 | Params | ONNX | License |
|---|---|---|---|---|---|
| **OSNet** x1_0 / x0_25 (torchreid) | 82.6/94.2 / 75.0/91.2 | 43.8/74.9 / 29.5/61.4 | 2.2M / 0.2M | `tools/export.py` (ONNX/OpenVINO/TFLite) | MIT; cross-domain MSMT→Market R1 drops to 66.6 — [MODEL_ZOO](https://kaiyangzhou.github.io/deep-person-reid/MODEL_ZOO.html), [repo](https://github.com/KaiyangZhou/deep-person-reid) |
| FastReID SBS-R50 | 88.2/95.4 | 58.4/81.8 | n/s | `onnx_export.py` | Apache-2.0 — [zoo](https://raw.githubusercontent.com/JDAI-CV/fast-reid/master/MODEL_ZOO.md) |
| CLIP-ReID ViT-B/16 | 89.6/95.5 | 73.4/88.7 | UNVERIFIED | no | MIT (weights unstated) — [repo](https://github.com/Syliz517/CLIP-ReID) |
| SOLIDER-REID Swin-T | 91.6/96.1 | 67.4/85.9 | 28M | no | MIT (weights unstated) — [repo](https://github.com/tinyvision/SOLIDER-REID) |
| KPR | 93.2/96.6 | n/r | n/s | no | **Hippocratic 3.0** (not OSI) — [repo](https://github.com/VlSomers/keypoint_promptable_reidentification) |
| TransReID ViT-B | 89.0/95.1 | 67.8/85.3 | 89M | no | MIT — [repo](https://github.com/damo-cv/TransReID) |
| CION / PBR-ReID (DINOv3) | 93.3/97.3 / 94.0/97.1 | 74.3/89.8 / 79.7/91.2 | 23.5M / ViT-B | no | Apache code but **gated weights** / MIT + DINOv3 license — [CION](https://github.com/Zplusdragon/CION_ReIDZoo), [PBR](https://github.com/psaltaath/pbr-reid) |

## 4. Face detection and embeddings

| Detector | WIDER FACE E/M/H | Size | CPU latency | ONNX | License |
|---|---|---|---|---|---|
| **YuNet 2023mar** (current) | README intro 0.834/0.824/0.708; eval table 0.884/0.866/0.750 (gap unexplained) | 75.9k params, 233 kB | i7-12700K 0.69 ms @160×120; paper 1.6 ms @320; no Apple row | yes; new `2026may` dynamic-shape re-export for OpenCV 5 | MIT; faces "10×10 to 300×300" — [README](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/README.md), [bench](https://github.com/opencv/opencv_zoo/blob/main/benchmark/README.md) |
| SCRFD 0.5GF / 2.5GF / 10GF | 91.0/88.4/69.5; 93.8/92.0/77.1; 95.4/94.0/82.8 | 0.57/0.82/4.2M | 0.5GF 28.3 ms @640×480 single-thread Ryzen 3950X | yes | code MIT; **weights "non-commercial research purposes only"** — [README](https://github.com/deepinsight/insightface/blob/master/detection/scrfd/README.md), [zoo](https://github.com/deepinsight/insightface/blob/master/model_zoo/README.md) |
| **RetinaFace (yakhyo)** MNetV1-0.25 / MNetV2 / R34 | orig-scale 90.7/88.1/73.8; 94.0/92.3/83.6; 95.1/93.5/84.4 | n/s | none | pretrained ONNX + `--dynamic`; `uniface` pip | MIT — [repo](https://github.com/yakhyo/retinaface-pytorch); biubug6 original MIT [repo](https://github.com/biubug6/Pytorch_Retinaface) |
| CenterFace (used by deface) | 0.935/0.924/0.875 | 7.3 MB | GPU only (2080Ti 5.5 ms VGA) | yes | MIT — [repo](https://github.com/Star-Clouds/CenterFace) |
| YOLOv8-face (derronqi) v8n/s | 94.6/92.3/79.6; 96.1/94.2/83.1 | n/s | none | — | GPL-3.0 + Ultralytics AGPL — [repo](https://github.com/derronqi/yolov8-face) |
| MediaPipe BlazeFace | none (internal data) | 224 kB | Pixel 6 2.94 ms | TFLite | Apache-2.0; faces must be ≥20% of image side — [docs](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector) |

| Embedding | LFW | CFP-FP | AgeDB | IJB-C 1e-4 | ONNX | License |
|---|---|---|---|---|---|---|
| InsightFace w600k_r50 (current, buffalo_l) | 99.83 | 99.33 | 98.23 | 97.25 | yes | code MIT; **weights non-commercial** — [zoo](https://github.com/deepinsight/insightface/blob/master/model_zoo/README.md) |
| **AdaFace** IR-50 WebFace4M / IR-101 WebFace12M | 99.78 / 99.82 | 98.97 / 99.26 | 97.78 / 98.00 | 96.98 / 97.66; TinyFace R1 72.3 (ArcFace MS1MV2 IJB-S 57.4 vs AdaFace 65.3) | export yourself (PyTorch) | MIT — [README](https://github.com/mk-minchul/AdaFace) |
| **SFace** (OpenCV Zoo, MobileFaceNet) | 0.9940 (`tools/eval`) | — | — | — | yes; i7-12700K 5.09 ms | Apache-2.0 — [README](https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_recognition_sface/README.md) |
| facenet-pytorch (VGGFace2) | 99.65 | — | — | — | no | MIT — [README](https://github.com/timesler/facenet-pytorch) |
| EdgeFace XS / S | 99.73 / 99.78 | 94.4 / 95.8 | 96.0 / 96.9 | 94.9 / 95.6 (1.8M / 3.7M) | no | code BSD-3, **HF weights CC BY-NC-SA 4.0** — [repo](https://github.com/otroshi/edgeface), [HF](https://huggingface.co/Idiap/EdgeFace-XS-GAMMA) |
| GhostFaceNets / dlib | 99.73 / 99.38 | 96.8 / — | 98.0 / — | 94.9 / — | no | MIT / CC0 model — [Ghost](https://github.com/HamadYA/GhostFaceNets), [dlib](https://github.com/davisking/dlib-models) |

## 5. Korean scene-text OCR

| Option | Korean? | Evidence | CPU speed + hardware | ONNX/ORT | macOS arm64 | License |
|---|---|---|---|---|---|---|
| **PP-OCRv6 det** tiny/small/medium (PaddleOCR 3.7, 2026-06) | det is script-agnostic; v6 *rec* covers 50 languages, **Korean not listed** | Hmean 80.6/84.1/86.2 (in-house) | **Apple M4, ONNX Runtime, det+rec e2e: 0.35 / 1.29 / 5.55 s/img; PP-OCRv5_mobile 1.10 s** | official HF ONNX | paddlepaddle 3.3.1 ships arm64 wheels | Apache-2.0 — [v6 doc](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html), [HF](https://huggingface.co/PaddlePaddle/PP-OCRv6_tiny_det_onnx), [PyPI](https://pypi.org/project/paddlepaddle/#files) |
| **korean_PP-OCRv5_mobile_rec** | "Korean, English" | **88.0%** on 5,007-image Korean set (v3 was ~60%) | rec 21.2 ms Xeon 6271C | yes | yes | Apache-2.0 — [doc](http://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html) |
| **RapidOCR 3.10** | `korean_PP-OCRv5_rec_mobile` (`ko`) + PP-OCRv6 det tiny/small/medium | inherits | UNVERIFIED | native ORT/OpenVINO; pure-python wheel | yes | Apache-2.0 — [models](https://rapidai.github.io/RapidOCRDocs/main/model_list/), [repo](https://github.com/RapidAI/RapidOCR) |
| CRAFT (det) | trained incl. IC17-MLT (has Korean) | IC17-MLT H 73.9 | none | no | yes | MIT — [repo](https://github.com/clovaai/CRAFT-pytorch), [paper](https://arxiv.org/abs/1904.01941) |
| OpenCV Zoo `text_detection_ppocr` (v3 det) | en/cn only | none | 18.6 ms @640×480 i7-12700K | yes | yes | Apache-2.0 — [README](https://github.com/opencv/opencv_zoo/blob/main/models/text_detection_ppocr/README.md) |
| EasyOCR (`ko` → korean_g2) | yes | none published | none | no | yes | Apache-2.0 — [repo](https://github.com/JaidedAI/EasyOCR) |
| Surya 2.5 (689M VLM) | `ko` 77.9% pass | olmOCR-bench 83.7 | M4 Max llama.cpp 0.19 pages/s | GGUF | yes | code Apache-2.0, **weights revenue-restricted** — [repo](https://github.com/datalab-to/surya) |
| docTR / Tesseract | Korean vocab exists but models trained on French vocab / `kor.traineddata` | FUNSD only / none | 0.05 s/it i7 / UNVERIFIED | yes / n/a | yes | Apache-2.0 — [docTR](https://mindee.github.io/doctr/modules/datasets.html), [tessdata](https://github.com/tesseract-ocr/tessdata_best) |
| EAST (OpenCV) | English | IC15 F1 80.8 | — | — | — | **GPL-3.0** — [repo](https://github.com/argman/EAST) |
| DeepSeek-OCR (3B MoE) | "natural scene OCR … mainly supports Chinese and English" | OmniDocBench ED 0.127 EN | CUDA + flash-attn only | no | no | MIT — [repo](https://github.com/deepseek-ai/DeepSeek-OCR), [paper](https://arxiv.org/html/2510.18234) |
| GOT-OCR2.0 (580M) / Florence-2 OCR | not stated / not stated | none | CUDA / UNVERIFIED | no / onnx-community | — | Apache-2.0 / MIT — [GOT](https://github.com/Ucas-HaoranWei/GOT-OCR2.0) |
| Qwen3-VL 2B/4B/8B | "32 languages" (Korean not named) | no text benchmark | official GGUF for llama.cpp; throughput UNVERIFIED | GGUF | yes | Apache-2.0 — [repo](https://github.com/QwenLM/Qwen3-VL), [GGUF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF) |
| PaddleOCR-VL 0.9B / 1.6 | "109 languages … including Korean" | OmniDocBench v1.6 96.3% | CUDA 12.6; "For macOS users, please use Docker" | no | Docker | Apache-2.0 — [HF](https://huggingface.co/PaddlePaddle/PaddleOCR-VL) |

Evaluation data: ICDAR2019-MLT (2,000 Korean images) [paper](https://arxiv.org/abs/1907.00945); AI Hub 야외 실제 촬영 한글 이미지 (~450k signboards) [sn=105](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=105); 한국어 글자체 이미지 Text-in-the-Wild 100k [sn=81](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=81). AI Hub: "내국인만", approval, training-only, non-redistributable [policy](https://www.aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105).

## 6. Korean license plates and privacy toolkits

| Item | What / accuracy | License / access |
|---|---|---|
| AI Hub 자동차 차종·연식·번호판 인식용 영상 | 600k crops (100k plates); baseline OCR 99.75% | 내국인만, approval — [page](https://aihub.or.kr/aidata/27727) |
| AI Hub CCTV 기반 차량정보 계측 | 2.0M images, plate det mAP 85.2%, OCR 91.44% (YOLOv4) | same — [page](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71573) |
| Synthetic generators: Usmankhujaev (7 plate types, blur/glare aug) / yakhyo (YOLO labels) | no metrics | Apache-2.0 / MIT — [Usman](https://github.com/Usmankhujaev/KoreanCarPlateGenerator), [yakhyo](https://github.com/yakhyo/korean-license-plate-generator) |
| Korean LPR repos: RoadoneP, jhlee508 (YOLOv4+LPRNet) / KR_LPR_Jax (99.12%, 0.42 ms M2) / EasyKoreanLpDetector | no metrics / 99.12% own set / none | MIT / **GPL-3.0** / no LICENSE — [RoadoneP](https://github.com/RoadoneP/Korean_license_plate_recognition), [KR_LPR](https://github.com/noahzhy/KR_LPR_Jax), [EasyKo](https://github.com/gyupro/EasyKoreanLpDetector) |
| **open-image-models** yolo-v9-t-384 / t-512 / s-608 plate detectors | P/R/mAP50/mAP50-95: .942/.863/.920/.687; .955/.901/.948/.724; .957/.917/.966/.772; training data undisclosed | MIT; `pip install open-image-models[onnx]` — [repo](https://github.com/ankandrew/open-image-models) |
| fast-plate-ocr / fast-alpr | global alphabet `0-9A-Z` — **no Hangul** | MIT — [repo](https://github.com/ankandrew/fast-plate-ocr) |
| RT-DETRv2 plate models (HF) | justjuu mAP50-95 0.97 (882 test imgs); Topurrra ONNX r18 | Apache-2.0 — [justjuu](https://huggingface.co/justjuu/rtdetr-v2-license-plate-detection), [ONNX](https://huggingface.co/Topurrra/rtdetr-license-plate-detection-onnx) |
| **Meta EgoBlur** (faces + plates) | Faster R-CNN ResNeXt-101 (~400 MB `.jit`); CCV2 face AP 0.99; Aria RGB face AP 0.895, plate AP 0.929; "We do not recommend running this tool on macOS … CPU only on Mac … very slow" (~1.5 s/2 MP) | Apache-2.0; `pip install egoblur`; models via projectaria.com — [repo](https://github.com/facebookresearch/EgoBlur), [paper](https://ar5iv.labs.arxiv.org/html/2308.13093) |
| deface | CenterFace ONNX, CPU, ellipse/blur/mosaic/solid, `--mask-scale` | MIT, pip — [repo](https://github.com/ORB-HD/deface) |
| DashcamCleaner | YOLOv8 faces+plates; ~10 fps 1080p GTX1060 | **LICENSE.txt is AGPL-3.0** (README says MIT) — [LICENSE](https://raw.githubusercontent.com/tfaehse/DashcamCleaner/main/LICENSE.txt) |
| understand.ai anonymizer / Blur-Anything / DeepPrivacy2 | TF OD, archived 2022 / SAM+XMem prompted blur / face-body synthesis | Apache-2.0 / MIT / Apache-2.0 (parts) — [uai](https://github.com/understand-ai/anonymizer), [Blur](https://github.com/Y-T-G/Blur-Anything), [DP2](https://github.com/hukkelas/deep_privacy2) |
| Commercial | Celantur, Gallio PRO (macOS desktop, free demo), Sighthound Redactor, Brighter AI — trials exist, no public metrics | — [Celantur](https://www.celantur.com/), [Gallio](https://gallio.pro/), [Sighthound](https://www.sighthound.com/products/redactor), [Brighter](https://product.brighter.ai/) |

## 7. Age / minor estimation

| Model | MAE / accuracy | License | ONNX |
|---|---|---|---|
| MiVOLO v1/v2 (face+body) | IMDB-clean MAE 4.22; LAGENDA 3.65 (v2); Adience 69.4% | repo LICENSE Apache-2.0 **but HF v2 card CC BY-NC 4.0** — treat v2 weights as non-commercial — [repo](https://github.com/WildChlamydia/MiVOLO), [HF](https://huggingface.co/iitolstykh/mivolo_v2) | "not advisable" |
| InsightFace genderage (buffalo_l) | none published | non-commercial — [zoo](https://github.com/deepinsight/insightface/tree/master/model_zoo) | yes |
| FairFace res34 (9 bins) | coarse age acc .536 | CC BY 4.0 — [repo](https://github.com/joojs/fairface) | no |
| faceage-onnx (DINOv3 ViT-L + CORAL) | LAGENDA MAE 3.56; 1.55 on 0–12, 2.85 on 13–17 (self-reported) | Apache-2.0 + DINOv3 license — [HF](https://huggingface.co/imbcmdth/faceage-onnx) | yes |
| Age-Classification-SigLIP2 (5 classes) | Child 0–12 F1 .965; Teen 13–20 recall .703 (self-reported) | Apache-2.0 — [HF](https://huggingface.co/prithivMLmods/Age-Classification-SigLIP2) | no |
| age-gender-prediction-ONNX (ViT-B) | UTKFace MAE 4.5; "Children (0-12): Reduced accuracy" | Apache-2.0 — [HF](https://huggingface.co/onnx-community/age-gender-prediction-ONNX) | yes |
| VLMs | 34-model study: VLM avg MAE 5.65 vs 9.88 for non-LLM; minors mislabeled adult 39–100% (specialized) vs 16–29% (VLM) | API terms — [arXiv 2602.07815](https://arxiv.org/abs/2602.07815), [VLAgeBench](https://arxiv.org/html/2603.26015) | n/a |
| NIST FATE AEV (commercial) | MAE 3.1–4.3 yrs; higher error on female faces; "no recommendations on … fit for particular use cases" | — [report](https://pages.nist.gov/frvt/reports/aev/fate_aev_report.pdf) | — |

## 8. Recommendation

### Per-target choice (permissive-first)

| Target | Primary | Alternative | Why |
|---|---|---|---|
| Face | **keep YuNet** (MIT, 76k params, 10–300 px faces) | RetinaFace-MNetV2 (yakhyo, MIT, ONNX; Hard 83.6) on keyframes / hard scenes; CenterFace (MIT) | Only SCRFD beats them on Hard and its weights are non-commercial |
| Face identity grouping | **AdaFace IR-50 WebFace4M** (MIT; IJB-C 96.98; best on TinyFace/IJB-S, which matches your far/profile faces) | **SFace** (Apache-2.0, 5 ms, already in OpenCV) as a drop-in | w600k_r50 is non-commercial; your `align_face` (5-point, 112×112) is reusable for both — only preprocessing changes |
| Body / bags / screens / books | **RF-DETR Nano or Small** (Apache-2.0, Objects365-pretrained, pip, official CoreML; M3 Pro 73.6 ms fp32 CPU, 20.8 ms fp16 CPU+NE) | D-FINE-S / DEIM-D-FINE-S COCO weights (Apache-2.0, 10M, via HF transformers → ONNX; CPU ms unpublished) | YOLO26n is the fastest documented CPU detector (38.9 ms) but AGPL; DEIMv2 is strongest-per-param but non-commercial |
| Shape masks for people | **RF-DETR-Seg-Nano** (Apache-2.0; person mask in the same pass) | RTMDet-Ins-tiny (Apache, unmaintained) | Seg-head masks every frame beat SAM-on-CPU; Seg-Nano CPU latency UNVERIFIED — benchmark first |
| Masks for plates/text/items + propagation | **EfficientTAM-S@512** (Apache-2.0; MPS supported; 80.6 ms/frame on iPhone NPU) prompted by detector boxes on keyframes, masks propagated across the track | EdgeTAM (Apache-2.0, CoreML); SAM 2.1-tiny offline (≈20 s/img on M4 CPU — keyframes only) | SAM 3 needs CUDA 12.6+ and a custom license → Docker-GPU phase only |
| License plate | **open-image-models yolo-v9-t-384/512** (MIT, ONNX) | EgoBlur plate model (Apache-2.0, strongest numbers, but Faster R-CNN ~1.5 s/2 MP CPU, "not recommended on macOS") as an offline validator; fine-tune on Usmankhujaev synthetic + AI Hub if eligible | Masking needs detection only; no permissive Hangul plate OCR exists (KR_LPR_Jax is GPL) |
| Text regions | **PP-OCRv6 det tiny via RapidOCR** (Apache-2.0; 0.35 s e2e on M4 incl. rec; det-only faster, UNVERIFIED) | CRAFT (MIT) | Run on keyframes; recognize with `korean_PP-OCRv5_mobile_rec` (88.0%) only when the decision agent needs to know *what* the text is |
| Name tags / ID badges / documents | **OWLv2-B/16 or Grounding DINO-T** (Apache-2.0) text prompts on keyframes (seconds/frame on CPU, UNVERIFIED) | Florence-2-base `<OPEN_VOCABULARY_DETECTION>` (MIT) | No closed-set class exists; Objects365 covers *Notepaper/Book/Monitor* for documents/screens |
| Tracking | **roboflow/trackers BoT-SORT or C-BIoU** (Apache-2.0, motion-only, CPU) | supervision ByteTrack (MIT) | Replace `iou-class-aware-v2` (max_gap 2) — these handle occlusion/camera motion with ~1 ms cost |
| Cross-scene re-association | **OSNet x0_25** (MIT, 0.2M, ONNX export) body embeddings + AdaFace face embeddings, fused per track | FastReID SBS-R50 (Apache-2.0) | Expect cross-domain degradation (MSMT→Market R1 94→67); keep it a soft signal |
| Minor estimation | faceage-onnx or SigLIP2 classifier as a **soft, conservative signal** | VLM on crops (lower minor-as-adult error) in a GPU/API phase | Treat ambiguous (≈10–17) as minor; no model is a legal determination |

### CPU-first pipeline shape

1. **Normalize** (keep ffmpeg CFR/autorotate) → analysis at 10–15 fps, render at native fps with interpolated masks.
2. **Dense pass, every analysis frame**: YuNet @640 (*est.* 5–15 ms on M-series; 0.69 ms @160×120 on i7) + RF-DETR(-Seg)-Nano (≈74 ms fp32 CPU CoreML, ≈21 ms fp16 CPU+NE on M3 Pro; ONNX Runtime CPU UNVERIFIED) + plate detector (*est.* 20–40 ms, UNVERIFIED).
3. **Track** per class with BoT-SORT/C-BIoU; reset at PySceneDetect cuts; OSNet/AdaFace embeddings only on new tracks (≤5 samples, as now).
4. **Keyframe pass** (per scene start + every ~1–2 s + new track): text detection, open-vocab detection for badges/documents, age signal.
5. **Segment-on-demand**: for each track needing a shape mask (plates, text, items, or people when Seg-head confidence is low), prompt EfficientTAM with the box on the keyframe, propagate forward/backward along the track (*est.* 150–400 ms/frame on CPU, UNVERIFIED — run it on every 3rd–5th frame and interpolate masks).
6. **Render**: mask-based blur/mosaic with 5–10% dilation, feathered edges, temporal smoothing; fallback = ellipse (deface-style) or rounded, dilated box when no mask exists.

**Latency expectation** (720p, Apple-Silicon CPU, *estimates*): dense pass ≈100–150 ms/frame (≈7–10 fps analysis), keyframe passes amortized to ≈50–100 ms/frame, so a 5-min clip at 10 fps analysis ≈ 8–15 min. Measured only pieces: RF-DETR Nano M3 Pro, YOLO26/YOLO11 Xeon CPU ONNX, PP-OCRv6 M4, EfficientTAM iPhone; everything else needs your own benchmark.

### Keep / replace in the current stack

- **Keep**: YuNet (switch to the `2026may` dynamic-shape export when moving to OpenCV 5), PySceneDetect, ffmpeg normalization, ONNX Runtime CPU sessions, `align_face` and the embedding/grouping code paths.
- **Replace**: IoU tracker → roboflow/trackers; box-only renderer → mask renderer; ArcFace w600k_r50 → AdaFace (MIT) or SFace (Apache-2.0) — retain ArcFace only while the project is strictly research-only.
- **Add**: RF-DETR(-Seg) Nano, open-image-models plate detector, RapidOCR (PP-OCRv6 det + Korean v5 rec), EfficientTAM, OSNet x0_25, age soft signal.
- **Defer to Docker-GPU**: SAM 3 (concept prompts + tracker), EgoBlur, Grounding DINO/OWLv2 at full frame rate, VLM-based text/age classification.

### Cross-cutting UNVERIFIED items

ONNX Runtime CPU latency for every DETR/SAM-family model on Apple Silicon; RF-DETR-Seg-Nano Apple numbers; DEIMv2 latency hardware; Korean-specific detection accuracy for any text detector; open-image-models training data (Korean plate coverage); EfficientTAM/EdgeTAM x86/ARM CPU latency; all self-reported HF age-model metrics; Roboflow Korean plate datasets (HTTP 403).
