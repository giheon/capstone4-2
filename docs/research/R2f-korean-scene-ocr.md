# R2f — 한국어 장면 텍스트 OCR (R2 하위 조사)

- 조사일: 2026-11-05 · 방식: 백그라운드 리서치 에이전트가 1차 출처(공식 문서·소스·모델 카드·논문·가격 페이지)만 인용해 작성한 보고서 원문 · 반영 위치: 설계 §10.2
- 상태: 설계·PRD v2 승인(2026-11-05) 시점의 스냅샷. 수치·가격·버전은 조사일 기준이며, 재검증 없이는 갱신되지 않는다. `UNVERIFIED` 표시는 1차 출처로 확인하지 못한 항목이다.

<details><summary>리서치 브리프(에이전트에 전달한 요청 전문)</summary>

```text
You are a READ-ONLY research subagent. Today is 2026-11-05. Do NOT write, edit, or create any files, and do NOT run shell commands. Use only WebSearch (use mode "extended" for recent or niche facts, e.g. 2025-2026 releases) and WebFetch on PRIMARY sources: official GitHub repos/READMEs/LICENSE files, official docs sites, Hugging Face model cards, arXiv papers, PyPI pages. Do not take numbers from blogs or from memory. Every numeric claim and every license claim must have the primary-source URL right next to it. If you cannot verify something, write UNVERIFIED and say what you looked for. Your final message IS the deliverable: Markdown with tables + short notes, at most ~1300 words. No report files.

Project context: PrivFrame is a Korean capstone: Python 3.12, OpenCV, ONNX Runtime CPU, dev on Apple Silicon Mac (no CUDA; PyTorch MPS possible), de-identifying personal information in 10 s–5 min 720p–1080p everyday videos (street signs, name tags, documents on desks, whiteboards, computer screens, hospital charts — Korean + English + digits). For privacy, the system mostly needs to DETECT text regions (to mask them) and only sometimes RECOGNIZE text (to decide whether it is a name, phone number, address, etc.). Research project but strongly prefers Apache-2.0/MIT over AGPL/GPL/non-commercial. CPU speed matters.

TASK: Compare OCR options for Korean scene text, split into (A) text detection only, (B) detection+recognition pipelines, (C) VLM/end-to-end OCR models. For each: Korean support (explicit statement from docs/model card), accuracy evidence on Korean or multilingual scene text (e.g. ICDAR2019-MLT, their own benchmark tables; any Korean-specific numbers), official CPU speed evidence + hardware, model size, ONNX availability, license of code AND weights, pip install path, Apple Silicon install viability (e.g. does paddlepaddle have arm64 macOS wheels?).
(A) Detection: PP-OCRv5 det (mobile/server) from PaddleOCR 3.x, DBNet/DBNet++ (MMOCR Apache-2.0), CRAFT (clovaai/CRAFT-pytorch; MIT; used in EasyOCR), EAST (OpenCV DNN), OpenCV Zoo 'text_detection_ppocr' / 'text_detection_db' models (license), FAST (any permissive impl), any 2025-2026 lightweight text detectors.
(B) Pipelines: PaddleOCR 3.x PP-OCRv5 (verify Korean recognition: docs mention 'korean_PP-OCRv5_mobile_rec' or multilingual PP-OCRv5 models? Which languages does PP-OCRv5 server model cover — the docs say Simplified/Traditional Chinese, English, Japanese, pinyin; is there a separate Korean v5 model, or is Korean still PP-OCRv3 'korean_PP-OCRv3_mobile_rec'? Quote), EasyOCR (JaidedAI; Apache-2.0; 'ko' language; CRAFT + CRNN; Korean model name 'korean_g2'; accuracy evidence), Surya (datalab-to/surya; license — code GPL-3.0 and model weights have a revenue-based restriction? quote; Korean in language list; benchmark table vs Tesseract/Google), docTR (mindee/doctr; Apache-2.0; does it support Korean recognition? vocab), Tesseract 5 (Apache-2.0; 'kor' traineddata tessdata_best; accuracy caveats), RapidOCR (RapidAI; Apache-2.0; ONNX Runtime port of PaddleOCR models — does it ship Korean rec models? Great fit for ONNX Runtime CPU), MMOCR, 'OnnxOCR'. 
(C) VLM/E2E: DeepSeek-OCR (deepseek-ai/DeepSeek-OCR; MIT; 3B; multilingual claims incl. Korean?; GPU need; OmniDocBench numbers), GOT-OCR2.0 (Ucas-HaoranWei/GOT-OCR2.0; Apache-2.0; 580M; languages), Florence-2 OCR (MIT; OCR_WITH_REGION task; multilingual?), Qwen3-VL (2B/4B/8B; Apache-2.0; '32 languages OCR' claim; Korean; CPU feasibility via llama.cpp/MLX), PaddleOCR-VL (0.9B; Apache-2.0; 109 languages; Korean?), Chandra/dots.ocr/MinerU 2.5 (licenses), Mistral OCR (API). For VLMs note realistic CPU/MPS throughput evidence from primary sources (if none, UNVERIFIED).
Also: Korean OCR datasets for evaluation (AI Hub 'OCR 데이터(공공)', 'Korean scene text' sets; ICDAR2019-MLT Korean subset) with URLs.

OUTPUT FORMAT: Table A (detector | Korean? | benchmark | size | CPU speed+HW | ONNX | license | URLs), Table B (pipeline | Korean rec model | benchmark | CPU speed+HW | ONNX/ORT port | macOS arm64 install | license | URLs), Table C (VLM | params | Korean | benchmark | CPU/MPS evidence | license | URLs), then bullets with caveats and UNVERIFIED items.
```

</details>

---

## Korean scene-text OCR options for PrivFrame (verified 2026-11-05)

Default hardware in PaddleOCR tables: Intel Xeon Gold 6271C, FP32, 8 threads, model-only time ("standard / high-perf"). PP-OCRv6 end-to-end table: 200 images incl. I/O + pre/post.

## Table A — Text detection only

| Detector | Korean? | Benchmark | Size | CPU speed + HW | ONNX | License | URLs |
|---|---|---|---|---|---|---|---|
| **PP-OCRv6 det** tiny/small/medium (PaddleOCR 3.7.0, 2026-06-11) | Family covers "Chinese, English, Japanese, and 46 Latin-script languages"; Korean not named | Hmean 80.6 / 84.1 / 86.2 (internal multi-scene set, not comparable to v5 column) | 0.43M params, 1.9 MB / 9.6 MB / 59.4 MB | **Det+rec e2e per image: Apple M4 ONNX Runtime 0.35 / 1.29 / 5.55 s; Xeon 8350C OpenVINO 0.20 / 0.59 / 1.40 s**; PP-OCRv5_mobile on M4 Paddle 5.82 s vs tiny 0.96 s | Official HF ONNX cards; `engine="onnxruntime"` | Apache-2.0 | [PP-OCRv6 doc](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html), [det table](http://www.paddleocr.ai/main/en/version3.x/module_usage/text_detection.html), [HF tiny_det_onnx](https://huggingface.co/PaddlePaddle/PP-OCRv6_tiny_det_onnx), [release](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0) |
| **PP-OCRv5 det** mobile / server | No language statement | Hmean 79.0 / 83.8 ("general evaluation set") | 4.7 / 84.3 MB | 57.77 (28.15 HP) / 383.15 ms model-only; T4 GPU 10.67 / 89.55 ms | [HF mobile_det_onnx](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det_onnx) | Apache-2.0 | [det doc](http://www.paddleocr.ai/latest/en/version3.x/module_usage/text_detection.html) |
| **DBNet / DBNet++** (MMOCR; last release v1.0.0, 2023-04-06) | English-only checkpoints (IC15/TotalText) | IC15 hmean 0.8169 (r18), 0.8504 (r50); DB++ r50 0.8622 | n/a | None published | Via MMDeploy (ORT "Y" for DBNet/DBNet++) | Apache-2.0 | [model zoo](https://mmocr.readthedocs.io/en/dev-1.x/textdet_models.html), [MMDeploy](https://github.com/open-mmlab/mmdeploy/blob/main/docs/en/04-supported-codebases/mmocr.md) |
| **CRAFT** (clovaai) | "General" weights trained SynthText+IC13+IC17-MLT (MLT contains Korean) | Paper: IC13 H 95.2, IC15 86.9, **IC17-MLT 73.9**, TD500 82.9; 8.6 FPS (GPU unstated) | n/a | None | None official (PyTorch) | MIT (no separate weight license stated) | [repo](https://github.com/clovaai/CRAFT-pytorch), [paper](https://arxiv.org/abs/1904.01941) |
| **EAST** (OpenCV DNN `.pb`) | IC13+IC15 English | IC15 F1 80.83; "~16 fps" 720p on GTX 1080 Ti | n/a | None | TF frozen graph | **GPL-3.0** (argman/EAST; OpenCV says its model "is based on" that repo) | [argman/EAST](https://github.com/argman/EAST), [OpenCV tutorial](https://github.com/opencv/opencv/blob/4.x/doc/tutorials/dnn/dnn_text_spotting/dnn_text_spotting.markdown) |
| **OpenCV Zoo text_detection_ppocr** (PP-OCRv3 det, en/cn) | cn model "English & Chinese"; en model IC15 "English text only" | No metrics given | int8 variant available | **18.6–18.8 ms mean @640×480 on i7-12700K**, OpenCV DNN CPU | Yes | Apache-2.0 | [README](https://github.com/opencv/opencv_zoo/blob/main/models/text_detection_ppocr/README.md), [benchmark](https://github.com/opencv/opencv_zoo/blob/main/benchmark/README.md) |
| OpenCV Zoo `text_detection_db` | — | — | — | — | — | **Removed** from zoo model list; OpenCV tutorial still links DB_IC15/TD500 ONNX from MhLiao/DB, whose repo shows **no LICENSE file** | [zoo models](https://github.com/opencv/opencv_zoo/tree/main/models), [MhLiao/DB](https://github.com/MhLiao/DB) |
| **FAST** (czczup) | Pre-trained on IC17-MLT, fine-tuned English sets | Total-Text F 81.6 @152.8 FPS (FAST-T-448), IC15 F 81.7 @60.9 FPS (T-736); 1080Ti, batch 1 | 8.5M / 9.7M / 10.6M | GPU only | None official. **Permissive impl: docTR `fast_tiny` 8.5M, 0.4–0.7 s/it @1024² on i7-11800H** | Apache-2.0 | [repo](https://github.com/czczup/FAST), [docTR models](https://mindee.github.io/doctr/using_doctr/using_models.html) |
| docTR `db_mobilenet_v3_large` | Doc-trained (FUNSD/CORD) | FUNSD R/P 82.69/84.63 | 4.2M | 0.5 s/it @1024² i7-11800H | `export_model_to_onnx` + OnnxTR | Apache-2.0 | same |

## Table B — Detection + recognition pipelines

| Pipeline | Korean rec model | Benchmark | CPU speed + HW | ONNX/ORT | macOS arm64 install | License | URLs |
|---|---|---|---|---|---|---|---|
| **PaddleOCR 3.7** | `korean_PP-OCRv5_mobile_rec` ("Korean, English and numeric"), 14 MB; `korean_PP-OCRv3_mobile_rec` 9.6 MB. **PP-OCRv6 unified rec does not include Korean**; v5 Korean is the latest | **88.0 %** (v3: 60.21 %) on 5,007 Korean images, self-built | Rec 21.20 ms (5.32 HP) Xeon 6271C; v6 e2e M4 numbers above | Official ONNX + ORT engine | `paddlepaddle` 3.3.1 (2026-03-24) ships `macosx_11_0_arm64` wheels cp39–cp313 | Apache-2.0 | [multi-lang](http://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html), [rec table](https://www.paddleocr.ai/main/en/version3.x/module_usage/text_recognition.html), [PyPI](https://pypi.org/project/paddlepaddle/#files) |
| **RapidOCR 3.10.0** (2026-10-08) | `korean_PP-OCRv5_rec_mobile` (lang `ko`/`korean`), `korean_PP-OCRv4_rec_mobile`; det `PP-OCRv6_det_tiny/small/medium`; v6 `multi` rec list has no `korean` | Inherits PaddleOCR | UNVERIFIED (no numbers published) | Native ORT / OpenVINO / Torch | Pure-Python `py3-none-any`, Py3.8–3.13; `pip install rapidocr onnxruntime` | Apache-2.0 (code + weights) | [model list](https://rapidai.github.io/RapidOCRDocs/main/model_list/), [repo](https://github.com/RapidAI/RapidOCR), [PyPI](https://pypi.org/project/rapidocr/) |
| **EasyOCR** | `ko` → `korean_g2` (CRAFT + CRNN) | None published (UNVERIFIED) | None published; `gpu=False` | None official | `pip install easyocr` (PyTorch) | Apache-2.0; CRAFT MIT | [repo](https://github.com/JaidedAI/EasyOCR), [modelhub](https://www.jaided.ai/easyocr/modelhub/), [langs](https://www.jaided.ai/easyocr/) |
| **Surya 2.5** (689M VLM + EfficientViT line detector) | `ko` **77.9 %** pass rate (91 langs, 36,089 tests) | OmniParseBench 85.9, olmOCR-bench 83.7 | H100: 4.5 pages/s; **M4 Max 36 GB, llama.cpp Metal, 96 DPI: 0.19 pages/s, 38.9 s median** | GGUF via llama.cpp; MLX "experimental" | `pip install surya-ocr` | Code Apache-2.0 (LICENSE file); weights "modified AI Pubs Open Rail-M … under $5M funding/revenue" | [README](https://github.com/datalab-to/surya), [langs](https://github.com/datalab-to/surya/blob/master/static/docs/multilingual.md) |
| **docTR** | `korean` vocab (11,237 chars) exists, but "most of our recognition models were trained on our french vocab" → Korean needs training | FUNSD/CORD only | `crnn_mobilenet_v3_small` 0.05 s/it (B64) i7-11800H | Yes + OnnxTR | `pip install python-doctr` | Apache-2.0 | [vocabs](https://mindee.github.io/doctr/modules/datasets.html), [export](https://mindee.github.io/doctr/using_doctr/using_model_export.html) |
| **Tesseract 5** | `kor.traineddata`, `kor_vert` (tessdata_best) | None official; docs: "works best on images which have a DPI of at least 300" | UNVERIFIED | n/a | binary/brew | Apache-2.0 (code + data) | [tessdata_best](https://github.com/tesseract-ocr/tessdata_best), [quality](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html) |
| MMOCR / OnnxOCR | No Korean rec in either (OnnxOCR: PP-OCRv5 zh/en/ja only) | — | — | OnnxOCR = ONNX | — | Apache-2.0 | [MMOCR](https://github.com/open-mmlab/mmocr), [OnnxOCR](https://github.com/jingsongliujing/OnnxOCR) |

## Table C — VLM / end-to-end

| VLM | Params | Korean | Benchmark | CPU/MPS evidence | License | URLs |
|---|---|---|---|---|---|---|
| **DeepSeek-OCR** (2025-10) | 3B MoE, 570M active; encoder ~380M | "nearly 100 languages" (PDF data); **"For natural scene OCR, our model mainly supports Chinese and English"**; Korean not named | OmniDocBench ED EN/ZH: Gundam 0.127/0.181 (795 tok); Small 0.221/0.284 (100 tok) vs GOT 0.287/0.411, MinerU2.0 0.133/0.238, dots.ocr 0.182/0.261 | CUDA 11.8 + flash-attn 2.7.3; "~2500 tokens/s (A100-40G)". CPU/MPS UNVERIFIED | MIT | [repo](https://github.com/deepseek-ai/DeepSeek-OCR), [paper](https://arxiv.org/html/2510.18234) |
| DeepSeek-OCR-2 (2026-01) | 3B | Not named | olmOCR-bench 76.3 | CUDA/vLLM; CPU UNVERIFIED | Apache-2.0 | [HF](https://huggingface.co/deepseek-ai/DeepSeek-OCR-2) |
| **GOT-OCR2.0** | 580M (paper; HF shows 0.7B) | Not stated (base Qwen "English and Chinese") | None on README/card | CUDA 11.8 + flash-attn; CPU UNVERIFIED | Code Apache-2.0; HF weights apache-2.0; data CC BY-NC | [repo](https://github.com/Ucas-HaoranWei/GOT-OCR2.0), [HF](https://huggingface.co/stepfun-ai/GOT-OCR2_0) |
| **Florence-2** | 0.23B / 0.77B | No language statement on card | TextVQA ft 73.5 (large); no OCR bench | UNVERIFIED | MIT | [HF](https://huggingface.co/microsoft/Florence-2-large) |
| **Qwen3-VL** 2B/4B/8B | 2/4/8B | "Supports 32 languages"; Korean not named | Charts only, no text numbers | **Official GGUF** (2B: Q4_K_M 1.11 GB + mmproj) with `llama-mtmd-cli`; throughput UNVERIFIED | Apache-2.0 | [repo](https://github.com/QwenLM/Qwen3-VL), [GGUF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF) |
| **PaddleOCR-VL** 0.9B / 1.6 | 0.9B (NaViT + ERNIE-4.5-0.3B) | "109 languages … including … Korean" | 1.6: "96.33% on OmniDocBench v1.6" | CUDA 12.6; "For macOS users, please use Docker"; no CPU numbers | Apache-2.0 | [VL](https://huggingface.co/PaddlePaddle/PaddleOCR-VL), [VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6) |
| Chandra 2 | Not stated | 90+ langs; **Korean 81.5 %** | olmOCR-bench 85.8 | H100 vLLM only | Code Apache-2.0; weights OpenRAIL-M "<$2M" | [repo](https://github.com/datalab-to/chandra) |
| dots.ocr | 1.7B LLM (HF: 3B) | 100-lang in-house set; Korean not named | OmniDocBench overall ED 0.125 EN / 0.160 ZH | CUDA, vLLM ≥0.11 | MIT | [HF](https://huggingface.co/rednote-hilab/dots.ocr) |
| MinerU 2.5 | 1.2B | zh/en tags only | olmOCR-bench 75.2 | 2.12 fps on A100 | Code Apache-2.0 + conditions (MAU>100M or >$20M/mo); **weights AGPL-3.0** | [LICENSE](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md), [HF](https://huggingface.co/opendatalab/MinerU2.5-2509-1.2B) |
| Mistral OCR | API only | "more than 40 languages"; Korean UNVERIFIED | None published | n/a | Proprietary ("Premier"); pricing UNVERIFIED | [models](https://docs.mistral.ai/getting-started/models/models_overview) |

## Korean evaluation datasets

- **ICDAR2019-MLT**: 20,000 real images, 10 languages incl. **Korean** (2,000/lang; 10k train/10k test), word-level 4-corner boxes + script label; 277k synthetic images. Task-1 top Hmean 83.6 %. [Paper](https://arxiv.org/abs/1907.00945); portal rrc.cvc.uab.es/?ch=15 (returned 403 to fetch).
- **AI Hub 야외 실제 촬영 한글 이미지** (sn=105): ~450k signboard + ~50k book-cover JPEGs, 1600×1200, Hangul word bboxes in JSON, 2020. [Link](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=105)
- **AI Hub 한국어 글자체 이미지** (sn=81): Text-in-the-Wild 100k (signs 17k, trademarks 37k, signboards 30k, other 16k), bbox, 2019. [Link](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=81)
- **AI Hub OCR 데이터(공공)** (sn=71299): 615,053 scanned public-document pages (printed/typed/handwritten), JSON bboxes, 2022 — documents, not scene text. [Link](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71299)
- All AI Hub sets: "내국인만 데이터 신청이 가능합니다" + approval; license text not on page.

## Caveats / UNVERIFIED

- **No detector publishes Korean-specific detection numbers.** Only CRAFT (IC17-MLT 73.9 H) and FAST (MLT pre-training) touch MLT; PaddleOCR's det metrics are on unnamed in-house sets.
- Best fit for the stated constraints: **PP-OCRv6 det (tiny/small) ONNX + `korean_PP-OCRv5_mobile_rec`**, run through RapidOCR or PaddleOCR's ORT engine — all Apache-2.0, official Apple M4 numbers exist. The v6 50-language recognizer skips Korean, so v5 Korean rec remains required.
- PaddlePaddle arm64 macOS wheels are verified on PyPI, but PaddleOCR's install page makes no macOS statement; PaddleOCR-VL says "use Docker" on macOS.
- EasyOCR, Tesseract, RapidOCR, OnnxOCR, Qwen3-VL, Florence-2, GOT, DeepSeek, dots.ocr: no official CPU/MPS throughput found.
- Surya: code switched to Apache-2.0 (LICENSE file checked); weights still revenue-restricted ($5M). Chandra weights $2M.
- OpenCV Zoo DB and EAST: avoid (no license / GPL-3.0).
- Not found: any other 2025–2026 permissive lightweight detector repo beyond PP-OCRv6 (search surfaced only journal papers without code/license).
