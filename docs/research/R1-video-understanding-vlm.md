# R1 — 영상 이해·VLM 후보 조사

- 조사일: 2026-11-05 · 방식: 백그라운드 리서치 에이전트가 1차 출처(공식 문서·소스·모델 카드·논문·가격 페이지)만 인용해 작성한 보고서 원문 · 반영 위치: 설계 §10.1 (07-models-and-tools.md), §7.3
- 상태: 설계·PRD v2 승인(2026-11-05) 시점의 스냅샷. 수치·가격·버전은 조사일 기준이며, 재검증 없이는 갱신되지 않는다. `UNVERIFIED` 표시는 1차 출처로 확인하지 못한 항목이다.

<details><summary>리서치 브리프(에이전트에 전달한 요청 전문)</summary>

```text
You are a research agent. Today is 2026-11-05. Do NOT write any files; return your findings in your final message as Markdown with tables and a URL for every claim. Use WebSearch (use mode "extended" for recent/niche facts) and WebFetch on primary sources only: official model docs, model cards, papers (arXiv), pricing pages, OpenRouter model pages (https://openrouter.ai/models and https://openrouter.ai/docs), leaderboards' own pages. Mark anything you could not verify as UNVERIFIED.

## Project context
PrivFrame is a Korean capstone project: a LangGraph multi-agent system that de-identifies personal information in user-uploaded videos (faces, full bodies, license plates, name tags, on-screen text, etc.), grounds each redaction decision in Korean privacy law via RAG, and produces an explainable per-second report. Stack: Python 3.12, LangChain/LangGraph 1.x, dev on macOS Apple Silicon (CPU/MPS, no CUDA), Docker for deploy. OpenRouter is the preferred LLM/VLM access path, but non-OpenRouter models must still be surveyed. Videos are typically short (10 s – 5 min), 720p–1080p, everyday scenes (street, office, school, hospital, SNS vlog, dashcam). Legal reasoning happens in a separate LLM stage; this track is only about VIDEO UNDERSTANDING: turning video into structured JSON (scene/segment descriptions, place/context cues such as hospital/school/protest/religious site, people attributes relevant to identifiability or vulnerability such as uniform/name tag/child, object captions, visible text, temporal segments with timestamps).

## Questions to answer (Track R1: video understanding / VLM)
1. Which models (as of Nov 2026) can take native video input (with timestamp references) and which take only image frames? Cover at least: Google Gemini current family (3.x and 2.5: Pro/Flash/Flash-Lite) incl. video FPS control, media_resolution, tokens per second of video, max duration, timestamp referencing (MM:SS), video clipping via videoMetadata; OpenAI GPT-5.x (image frames; any video input?); Anthropic Claude 5.x family (image only?); open-weight: Qwen3-VL (all sizes; video support; license), InternVL3.5, VideoLLaMA3, LLaVA-OneVision-1.5, MiniCPM-V 4.5, Kimi-VL, GLM-4.5V/4.6V, Molmo 2, Mistral vision models, Llama 4, and any newer model that leads the Video-MME leaderboard.
2. Benchmark table with sources: Video-MME, MVBench, MLVU, LongVideoBench, TempCompass, VideoMMMU, temporal grounding (Charades-STA R1@0.5), and OCR-oriented (OCRBench v2 / TextVQA). Only report numbers you found on a primary page (paper, model card, official leaderboard).
3. OpenRouter: which of these models are listed; does OpenRouter support video input (e.g., a `video_url` content part) and for which models; image input pricing; structured output (json_schema) support per model; zero-data-retention / `data_collection` provider preference. Cite OpenRouter docs pages.
4. Korean capability evidence: Korean-language captioning/instruction following and reading Korean scene text (Korean OCR inside the VLM). Cite any benchmark or model-card statement.
5. Local inference on Apple Silicon (MLX-VLM, Ollama, llama.cpp, vLLM-mlx) for Qwen3-VL-4B/8B or similar: feasibility, approximate speed/memory from official or first-party sources.
6. Data protection: provider data retention and training policies for API inputs — OpenRouter (ZDR endpoints), OpenAI API, Google Gemini API (paid tier) and Vertex AI, Anthropic API; data region options. Cite policy pages.
7. Cost estimate for one 60-second 1080p video sampled at 1 fps (give the token math) for the top 3 cloud options, and the cost of an image-frame approach (e.g., 30 frames) for GPT-5.x / Claude.
8. Recommendation with rationale (primary + alternative) for three sub-tasks: (a) segment/scene captioning + context cues (place type, activity, sensitivity signals like medical/religious/child/protest), (b) object-level attribute captions for detected people/objects (uniform, name tag, badge, visible text, distinctive features) given a crop or a frame with a box, (c) VLM-based OCR fallback for Korean text. Compare "native video model" vs "sampled frames + image model" vs "local open model" on quality, cost, latency, privacy. Keep total output under ~3500 words.
```

</details>

---

## Track R1 — Video understanding / VLM survey (as of 2026-11-05)

Scope note: every number below comes from a vendor doc, model card, paper, or official leaderboard; third-party aggregators were excluded. Anything I could not confirm on a primary page is marked **UNVERIFIED**. The web-search budget ran out at the end, so a few secondary items (Molmo2/MiniCPM on OpenRouter, Kimi K3) stay unverified.

## 1. Native video input vs. image frames

| Model / family | Video input? | Key facts | Source |
|---|---|---|---|
| **Gemini 4 Argon, 3.8/3.7/3.6 Flash, 3.5 Flash-Lite, 3.1 Pro Preview, 3.1 Flash-Lite, 3 Flash Preview; 2.5 Pro/Flash (deprecated, auto-rerouted 2026-12-01), 2.5 Flash-Lite** | **Native video + audio** | Static mode (default, all models): frames at **1 FPS**, timestamp every second; **agentic** mode (4 Argon, 3.8/3.7/3.6 Flash, 3.5 Flash-Lite) navigates the timeline on demand. Tokens: AI Studio doc says 66 tok/frame (low) or 258 (default), ≈100 tok/s low vs ≈300 tok/s high, audio 32 tok/s; the Cloud doc says Gemini 3.x uses **70 tok/frame (default/low/medium) and 280 (high)**. Custom FPS: `videoMetadata.fps` (Cloud) / `processing.fps` (AI Studio); clipping via start/end offsets; timestamps **`MM:SS`** (`MM:SS.sss` above 1 FPS); up to 10 videos/request; ≈45 min with audio / ≈1 h without per request (Cloud), up to 3 h low-res / 1 h high-res in 1M context (AI Studio); inline ≤20 MB, Files API ≤20 GB paid | [AI Studio video](https://aistudio.google.com/docs/video-understanding), [Cloud video](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/video-understanding), [models](https://aistudio.google.com/docs/models) |
| **OpenAI GPT-5.5 / 5.6 (sol/terra/luna) / 5.4 family** | **No** (text + image; "audio and video not listed") | Image tokens = ⌈w/32⌉×⌈h/32⌉ patches × 1.2; detail `low` fits 512×512, `high` ≤2,500 patches/2048 px, `original` ≤10,000 patches; up to 1,500 images/request | [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5), [vision guide](https://developers.openai.com/api/docs/guides/images-vision) |
| **Claude Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5 (+5.x/4.x legacy)** | **No** ("Text and images → text") | 28-px patches; 5.x are high-res tier (2,576 px long edge, ≤4,784 visual tokens; 1920×1080 = 2,691 tokens, not resized); ≤600 images/request; refuses to name people | [models](https://platform.claude.com/docs/en/about-claude/models/overview), [vision](https://platform.claude.com/docs/en/build-with-claude/vision), [Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) |
| **Qwen3-VL** 2B/4B/8B/32B dense, 30B-A3B/235B-A22B MoE (Instruct+Thinking), Apache-2.0 | **Native** | Text timestamp tokens (`<3.0 seconds>`), interleaved-MRoPE, DeepStack; 256K ctx (1M via YaRN); default `fps=2`; OCR 39 languages (32 >70%) | [GitHub](https://github.com/QwenLM/Qwen3-VL), [tech report](https://arxiv.org/abs/2511.21631) |
| **Qwen3.5** (0.8B…27B, 35B-A3B, 122B-A10B, 397B-A17B) and **Qwen3.6** (27B, 35B-A3B), Apache-2.0 | **Native** (natively multimodal LLMs) | 262,144 ctx (→1,010,000 YaRN); default `fps=2`; hour-scale video via `longest_edge` ≈224k video tokens | [Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B), [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B), [Qwen3.5-27B](https://huggingface.co/Qwen/Qwen3.5-27B) |
| **InternVL3.5** 4B/8B/14B/38B/241B-A28B, Apache-2.0 | Frame sequence | Uniformly sampled frames tagged `Frame1: <image>`; eval at 16–64 frames; no timestamp tokens | [HF](https://huggingface.co/OpenGVLab/InternVL3_5-8B), [paper](https://arxiv.org/abs/2508.18265) |
| **VideoLLaMA3** 2B/7B | Native (1 fps, ≤180 frames, 16K visual tokens) | Apache-2.0 repo but README says "research preview… non-commercial use ONLY" | [GitHub](https://github.com/DAMO-NLP-SG/VideoLLaMA3), [paper](https://arxiv.org/abs/2501.13106) |
| **LLaVA-OneVision-1.5** 4B/8B | Not evaluated on video (LLaVA-OV-2 "bootstraps video ability" from 1.5) | Image/doc model in practice | [GitHub](https://github.com/EvolvingLMMs-Lab/LLaVA-OneVision-1.5), [paper](https://arxiv.org/abs/2509.23661) |
| **MiniCPM-V 4.5** (8B, Apache-2.0); **4.6** (2026-05-11), **4.7** 35B-A3B + 1B (2026-10-12) | Native | 3D-Resampler, 96× video-token compression, ≤10 FPS, ≤1,080 frames (4.5); 4.7 "supports video understanding" | [4.5 card](https://huggingface.co/openbmb/MiniCPM-V-4_5), [4.5 paper](https://arxiv.org/abs/2509.18154), [GitHub](https://github.com/openbmb/MiniCPM-V) |
| **Kimi-VL-A3B-Thinking-2506** (16B MoE, MIT); **Kimi K2.5** (1T-A32B, Modified MIT) | Native | K2.5: "Chat with video content is an experimental feature and is only supported in our official API for now" | [Kimi-VL](https://huggingface.co/moonshotai/Kimi-VL-A3B-Thinking-2506), [K2.5](https://huggingface.co/moonshotai/Kimi-K2.5) |
| **GLM-4.6V** (106B) / **GLM-4.6V-Flash** (9B), MIT, 128K | Native (OpenRouter lists `video`) | GLM-V repo "will no longer be maintained" as of 2026-09-02; GLM-5V-Turbo mentioned without details | [HF](https://huggingface.co/zai-org/GLM-4.6V), [GLM-V](https://github.com/zai-org/GLM-V) |
| **Molmo2** 4B/8B (Qwen3 LLM), O-7B (OLMo), Apache 2.0 | Native + multi-image, pointing/tracking | 2 fps, ≤384 frames at inference; some training data non-commercial | [HF](https://huggingface.co/allenai/Molmo2-8B), [paper](https://arxiv.org/abs/2601.10611) |
| **Mistral** Large 3 / Medium 3.1 / Small 3.2 / Ministral 3 | Image only (video not mentioned) | Limits/pricing not in extracted docs (**UNVERIFIED**) | [docs](https://docs.mistral.ai/capabilities/vision/) |
| **Llama 4 Scout/Maverick** | Image only ("tested… up to 5 input images") | Korean not among 12 listed languages; Llama 4 Community License | [HF](https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct) |

Video-MME official leaderboard leader is still **video-SALMONN 2+ 72B (79.7 w/o subs, 81.6 w/ subs)**; 2026 models are not submitted there ([leaderboard](https://video-mme.github.io/home_page.html), [paper](https://arxiv.org/abs/2506.15220)). The successor **Video-MME-v2** (Apr 2026, 800 videos/3,200 Qs) ranks Gemini-3-Pro first (49.4 non-linear score w/ sub), then Doubao-Seed-2.0-Pro 43.3, Gemini-3-Flash 42.5, Qwen3.5-397B-A17B-Think 39.1, GPT-5 37.0, Kimi-K2.5 36.1 ([paper](https://arxiv.org/html/2604.05015)).

## 2. Benchmarks (primary-page numbers only)

Video-MME = w/o sub / w/ sub. Charades-STA is reported only as **mIoU** by every primary source found; **R1@0.5 UNVERIFIED**. "†" = number reproduced by another vendor's paper (noted).

| Model | Video-MME | MVBench | MLVU | LongVideoBench | TempCompass | VideoMMMU | Charades-STA mIoU | OCRBench (/1000 or %) · OCRBench v2 EN/ZH | TextVQA | Source |
|---|---|---|---|---|---|---|---|---|---|---|
| Gemini 3 Pro | 87.7/88.4† | 74.1† | 83.0† | 77.7† | – | **87.6** | – | 90.4† · v2 63.4/63.8 (2026.09) | – | [card](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf), †[Qwen3.5-397B card](https://huggingface.co/Qwen/Qwen3.5-397B-A17B), †[Kimi K2.5](https://huggingface.co/moonshotai/Kimi-K2.5), [OCRBench v2](https://99franklin.github.io/ocrbench_v2/) |
| Gemini 3 Flash | – | – | – | – | – | 86.9 | – | OmniDocBench 0.121 | – | [card](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Flash-Model-Card.pdf) |
| Gemini 3.8 Flash / 4 Argon | LVBench 87.8 (agentic) / 91.7 only | – | – | – | – | not reported | – | – | – | [3.8](https://deepmind.google/models/model-cards/gemini-3-8-flash/), [4 Argon](https://deepmind.google/models/model-cards/gemini-4-argon/) |
| Gemini 2.5 Pro | 85.1 (thinking)† / 87.8† | 69.9† | 85.6† | 76.8† | 81.9† | 83.6 | – | 866† · v2 54.3/48.5† | – | [3 Pro card](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf), †[Qwen3-VL report](https://arxiv.org/pdf/2511.21631), †[Molmo2](https://arxiv.org/html/2601.10611v4) |
| GPT-5 (high) | 84.7† / 83.3†‑86.9† | 75.3† | 86.2† | 72.6† | 80.4† | 84.6† | – | 810† | – | †[Qwen3-VL](https://arxiv.org/pdf/2511.21631), †[Molmo2](https://arxiv.org/html/2601.10611v4) |
| GPT-5.2 | 85.8/86.0† | 78.1† | 85.6† | 76.5† | – | 85.9 | – | 80.7† · v2 50.5/52.6 (2026.03) | – | [3 Flash card](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Flash-Model-Card.pdf), †[Qwen3.5](https://huggingface.co/Qwen/Qwen3.5-397B-A17B), [v2](https://99franklin.github.io/ocrbench_v2/) |
| GPT-5.5 / 5.6 | **UNVERIFIED** (no video benchmarks found) | | | | | | | | | [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5) |
| Claude Opus 4.5 / Sonnet 4.5 | 81.4/77.6† (Opus) | 67.2† | 81.7† | 67.2† | – | 84.4† / 77.8 (Sonnet 4.5) | – | 85.8† · Opus 4.6 v2 48.4/59.8 | – | †[Qwen3.5](https://huggingface.co/Qwen/Qwen3.5-397B-A17B), [3 Pro card](https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf), [v2](https://99franklin.github.io/ocrbench_v2/) |
| Claude 5.x | **UNVERIFIED** (system card PDF >10 MB, not parsed) | | | | | | | | | [Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) |
| Qwen3-VL-8B-Instruct (Thinking) | 71.4 (71.8) | 68.7 (69.0) | 78.1 (75.1) | 62.4† | 74.3† | 65.3 (72.8) | 56.0 (59.9) | 896 (819) · 65.4/61.2 | – | [report Tbl 4](https://arxiv.org/pdf/2511.21631), †[Molmo2](https://arxiv.org/html/2601.10611v4) |
| Qwen3-VL-4B-Instruct | 69.3 | 68.9 | 75.3 | 62.8† | 70.8† | 56.2 | 55.5 | 881 · 63.7/57.6 | – | same |
| Qwen3-VL-32B-Instruct | 76.6 | 72.8 | 82.1 | – | – | 71.9 | 61.2 | 895 · 67.4/59.2 | – | [report Tbl 3](https://arxiv.org/pdf/2511.21631) |
| Qwen3-VL-235B-A22B-Instruct | 79.2 / 83.8 | 76.5 | 84.3 | – | – | 74.7 | 64.8 | 920 · 67.1/61.8 | – | [report Tbl 2](https://arxiv.org/pdf/2511.21631) |
| Qwen3.5-27B | 82.8/87.0 | 74.6 | 85.9 | – | – | 82.3 | – | 89.4 | – | [card](https://huggingface.co/Qwen/Qwen3.5-27B) |
| Qwen3.6-27B | –/87.7 | 75.5 | 86.6 | – | – | 84.4 | – | 89.4 · v2 68.3 (EN #5)/58.7 | – | [card](https://huggingface.co/Qwen/Qwen3.6-27B), [v2](https://99franklin.github.io/ocrbench_v2/) |
| Qwen3.6-35B-A3B | 82.5/86.6 | 74.6 | 86.2 | LVBench 71.4 | – | 83.7 | – | v2 65.5/60.8 | – | [card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) |
| Qwen3.5-397B-A17B | 83.7/87.5 | 77.6 | 86.7 | – | – | 84.7 | – | 93.1 | – | [card](https://huggingface.co/Qwen/Qwen3.5-397B-A17B) |
| InternVL3.5-8B (4B / 38B / 241B) | 66.0/68.6 (65.4/68.6; 70.9/74.2; 72.9/76.0) | 72.1 (71.2; 75.0; 76.5) | 70.2 (70.4; 77.0; 78.2) | 62.1 (60.8; 65.7; 67.1) | 70.3† | – | – | 840 (822; 870; 907) · 8B v2 46.0/50.3 (older) | 78.2 (77.9; 82.7; 84.5) | [paper](https://arxiv.org/html/2508.18265), [v2](https://99franklin.github.io/ocrbench_v2/) |
| VideoLLaMA3-7B (2B) | 66.2/70.3 (59.6/63.4) | 69.7 (65.5) | 73.0 (65.4) | 59.8 (57.1) | 68.1 (63.4) | – | 60.7 (55.5) | 828 (779) | – | [paper](https://arxiv.org/html/2501.13106) |
| MiniCPM-V 4.5 | 67.9/73.5 | 60.5† | 75.1 | 63.9 | 72.7† | – | – | 89.0 · v2 ZH 58.8 | 82.2 | [paper](https://arxiv.org/html/2509.18154), †[Molmo2](https://arxiv.org/html/2601.10611v4) |
| Kimi-VL-A3B-Thinking-2506 / Kimi K2.5 | –/71.9 / 87.4 | – | – | – / 79.8 | – | 65.2 / 86.6 | – | 869 / 92.3 | – | [Kimi-VL](https://huggingface.co/moonshotai/Kimi-VL-A3B-Thinking-2506), [K2.5](https://huggingface.co/moonshotai/Kimi-K2.5) |
| GLM-4.6V / -Flash | – | – | – | – | – | 74.7 (model-index metadata in a repo PR, **UNVERIFIED** as official) | – | Flash v2 59.0/59.5 | – | [HF](https://huggingface.co/zai-org/GLM-4.6V), [v2](https://99franklin.github.io/ocrbench_v2/) |
| Molmo2-8B (4B) | 69.9/75.8 (69.6/75.7) | 75.9 (75.1) | 60.2 (63.0) | 67.5 (68.0) | 73.4 (72.8) | – | – (pointing/tracking only) | – | – | [paper](https://arxiv.org/html/2601.10611v4) |
| LLaVA-OV-1.5-8B | no video results | | | | | | | 82.9 | – | [paper](https://arxiv.org/html/2509.23661) |

Caveat from Qwen's own report: proprietary models were evaluated with fewer frames (512 Gemini 2.5 Pro, 256 GPT-5, 100 Claude Opus 4.1), so cross-vendor comparisons are not frame-matched ([§5.9](https://arxiv.org/pdf/2511.21631)).

## 3. OpenRouter

**Video input**: Chat Completions uses a `video_url` content part, Responses uses `input_video`; Messages API has no video. URLs or base64 data URLs; formats mp4/mpeg/mov/webm. Caveats: Gemini via AI Studio accepts **YouTube links only**, Gemini via Vertex accepts **base64 only**; a Gemini-only `processing: "agentic" | "static"` field exists; filter models by `input_modalities` containing `video` ([videos doc](https://openrouter.ai/docs/features/multimodal/videos)). Images: `image_url`/`input_image` parts, URL or base64, png/jpeg/webp/gif ([images doc](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding.md)).

**Structured output**: `response_format: {type:"json_schema", strict:true}`; support is **per endpoint**, filter `supported_parameters=structured_outputs`, enforce with `provider.require_parameters: true` ([doc](https://openrouter.ai/docs/features/structured-outputs)).

| OpenRouter model | Modalities | Price in/out per 1M (image price) | `structured_outputs` | Source (endpoints API) |
|---|---|---|---|---|
| google/gemini-3.8-flash | text, image, **video**, audio, file; 1,048,576 ctx | $0.75/$3.75 (image tokens $0.75/M); flex $0.375/$1.875; vertex/us $0.825/$4.125 | yes (all 7 endpoints) | [link](https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints) |
| google/gemini-3.5-flash-lite | same incl. **video** | $0.30/$2.50 (image $0.30/M); eu/us $0.33/$2.75 | yes (8/8) | [link](https://openrouter.ai/api/v1/models/google/gemini-3.5-flash-lite/endpoints) |
| google/gemini-3.1-pro-preview | incl. **video** | $2/$12 (image $2/M) | yes | [link](https://openrouter.ai/api/v1/models/google/gemini-3.1-pro-preview/endpoints) |
| google/gemini-4-argon | incl. **video** | $2/$10 (flex $1/$5) | yes | [link](https://openrouter.ai/api/v1/models/google/gemini-4-argon/endpoints) |
| qwen/qwen3.6-35b-a3b | text, image, **video**; 262,144 | $0.10/$0.90 typical (PrimeIntellect $0.01/$0.094 fp8 … AtlasCloud $0.25/$1.25); no image price listed | yes on 9/11 (no: Venice, AtlasCloud; Phala lacks `response_format`) | [link](https://openrouter.ai/api/v1/models/qwen/qwen3.6-35b-a3b/endpoints) |
| qwen/qwen3.6-flash (Alibaba) | text, image, **video**; 1,000,000 | $0.1875/$1.125 (>256k: $0.75/$3) | yes | [link](https://openrouter.ai/api/v1/models/qwen/qwen3.6-flash/endpoints) |
| z-ai/glm-4.6v | image, text, **video**; 131,072 | $0.30/$0.90 (Novita bf16, Z.AI fp8) | **no** (`response_format` only on Novita) | [link](https://openrouter.ai/api/v1/models/z-ai/glm-4.6v/endpoints) |
| qwen/qwen3-vl-8b-instruct | image, text (**no video on OR**); 262,144 | $0.25/$0.75 (Parasail) | yes | [link](https://openrouter.ai/api/v1/models/qwen/qwen3-vl-8b-instruct/endpoints) |
| openai/gpt-5.5 | file, image, text | $5/$30 (flex $2.5/$15; Azure/Bedrock regional $5.5/$33) | yes | [link](https://openrouter.ai/api/v1/models/openai/gpt-5.5/endpoints) |
| anthropic/claude-sonnet-5.5 | text, image, file | $2/$10 (regional $2.2/$11) | yes on Anthropic/Vertex/Azure; **no** on Bedrock & Claude-on-AWS endpoints | [link](https://openrouter.ai/api/v1/models/anthropic/claude-sonnet-5.5/endpoints) |
| moonshotai/kimi-k2.5 | text, image (**no video**) | $0.49–0.60 / $2.5–3.0 | yes (not Bedrock) | [link](https://openrouter.ai/api/v1/models/moonshotai/kimi-k2.5/endpoints) |
| meta-llama/llama-4-maverick; mistralai/mistral-medium-3.1 | text+image (+file) | $0.19–0.35/$0.65–1.15; $0.40/$2.00 (Mistral has a `zdr` endpoint) | yes | [L4](https://openrouter.ai/api/v1/models/meta-llama/llama-4-maverick/endpoints), [Mistral](https://openrouter.ai/api/v1/models/mistralai/mistral-medium-3.1/endpoints) |
| Kimi-VL-A3B (record exists, **zero endpoints**); InternVL3/3.5 (404) | not served | | | [Kimi-VL](https://openrouter.ai/api/v1/models/moonshotai/kimi-vl-a3b-thinking/endpoints) |
| Molmo2, MiniCPM-V, VideoLLaMA3, LLaVA-OV-1.5 | not found via search page (**UNVERIFIED**) | | | – |

Image pricing: Gemini endpoints bill image input at the same per-token rate as text; other endpoints list no `image` price, so the provider's own token rules (§1) apply; pricing records can be per image, megapixel or token ([images doc](https://openrouter.ai/docs/features/multimodal/images)).

**Data policy**: `provider: {"data_collection": "deny", "zdr": true}` restricts routing; account-level per-model-group ZDR and "third-party training" toggles; "ZDR only" vs Safety Retention (self-serve default allows Safety Retention); list at `/api/v1/endpoints/zdr`. Important for video: "Starting October 18, 2026, Google requires Safety Retention for Google models served through Vertex", and Google-group ZDR enforcement removes AI Studio endpoints; Anthropic/OpenAI first-party endpoints are removed under ZDR (Bedrock/Vertex/Azure remain) ([routing](https://openrouter.ai/docs/features/provider-routing), [ZDR](https://openrouter.ai/docs/guides/features/zdr)). OpenRouter itself: no training on inputs; image/audio/video inputs not persisted "beyond the duration necessary to route the request"; servers in the US; EU in-region routing on Business plans ([privacy](https://openrouter.ai/privacy)).

## 4. Korean capability evidence

| Evidence | Finding | Source |
|---|---|---|
| Gemini language list | "All Gemini models can understand the following languages… text-capable models can also respond in these languages" — Korean (`ko`) listed | [Google models](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/google-models) |
| Korean document OCR (synthetic mC4 pages, WER %) | olmOCR-2-7B 4.03, **Gemini-3-Flash 4.85**, MinerU2.5-Pro 5.27, **Qwen3.5-4B 6.46**, **Qwen3-VL-4B 6.99**, Gemini-2.5-Flash 7.84, GPT-5.4-mini 8.23, Gemma4-E4B 12.84, InternVL3.5-4B 18.60, PaddleOCR-VL-1.5 20.55, Tesseract 40.10; "Both CJK languages are far harder than English for every system" (documents, not scene text) | [arXiv 2607.21617](https://arxiv.org/html/2607.21617) |
| Korean VLM reasoning (KorMedMCQA-V, 1,534 Qs) | Gemini-3.0-Pro 96.9%, Qwen3-VL-32B-Thinking 83.7%, VARCO-VISION-2.0-14B 43.2% | [arXiv 2602.13650](https://arxiv.org/abs/2602.13650) |
| Qwen OCR lineage | Qwen2-VL card names "Japanese, Korean, Arabic, Vietnamese"; Qwen3-VL expands to 39 languages (32 >70% accuracy); Qwen3.5 claims "201 languages and dialects" | [Qwen2-VL](https://huggingface.co/Qwen/Qwen2-VL-7B-Instruct), [report Fig. 2](https://arxiv.org/pdf/2511.21631), [Qwen3.5-27B](https://huggingface.co/Qwen/Qwen3.5-27B) |
| Korean-native VLMs | VARCO-VISION-2.0-14B (CC-BY-NC-4.0, no video): K-LLaVA-W 96.5 vs Qwen2.5-VL-7B 94.1; Korean OCR (CORD/ICDAR) 90.7 vs PaddleOCR 85.7. HyperCLOVAX-SEED-Vision-3B (video, 108 frames): VideoMME-Ko 48.2 vs Qwen2.5-VL-7B 60.6, KoNet 69.2 vs 68.4 | [VARCO](https://huggingface.co/NCSOFT/VARCO-VISION-2.0-14B), [HyperCLOVAX](https://huggingface.co/naver-hyperclovax/HyperCLOVAX-SEED-Vision-Instruct-3B) |
| Negative/absent | Llama 4: Korean not in supported list; MiniCPM-V 4.5: Korean not named ("30+ languages"); Claude/OpenAI: only generic "multilingual" claims; KOFFVQA leaderboard values not retrievable (**UNVERIFIED**) | [Llama 4](https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct), [MiniCPM](https://huggingface.co/openbmb/MiniCPM-V-4_5), [KOFFVQA](https://arxiv.org/abs/2503.23730) |

No primary Korean *scene-text* benchmark covering current models was found; treat Korean OCR rankings above as document-OCR proxies.

## 5. Local inference on Apple Silicon

| Stack | Status | Numbers | Source |
|---|---|---|---|
| **mlx-vlm** | `qwen3_vl` and `qwen3_5` model dirs exist; README's video list names Qwen2-VL/2.5-VL, Idefics3, LLaVA/LLaVA-OV, MiniMax M3, Mage-VL, Muse Spark — **Qwen3-VL/3.5/3.6 video via mlx-vlm UNVERIFIED**; `--video --fps` CLI; 4-bit Qwen3-VL-8B = 5.76 GB | TurboQuant KV: Qwen3.5-4B-4bit at 128k, peak 18.3→17.3 GB | [repo](https://github.com/Blaizzy/mlx-vlm), [qwen3_vl dir](https://github.com/Blaizzy/mlx-vlm/tree/main/mlx_vlm/models/qwen3_vl), [4-bit card](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-4bit) |
| **vllm-mlx** (paper, M4 Max 128 GB) | Qwen3-VL-4B, 10-s clip: 4 frames@1fps 2.4 s, 62.5 tok/s, 3.8 GB; 16 frames 5.8 s, 25.9 tok/s, 6.2 GB; 64 frames 18.2 s, 8.2 tok/s, 12.1 GB; vision-embedding cache 24.7× on repeat | [arXiv 2601.19139](https://arxiv.org/html/2601.19139v2) |
| **llama.cpp** | Video input merged 2026-06-08 (ffmpeg subprocess, `--video`, `/chat/completions`); Qwen3-VL timestamped video template merged 2026-11-03; Metal supported; Qwen3-VL-8B GGUF Q4_K_M 5.03 GB / Q8_0 8.71 GB / F16 16.4 GB; no Metal video benchmarks published | [PR 24269](https://github.com/ggml-org/llama.cpp/pull/24269), [PR 31619](https://github.com/ggml-org/llama.cpp/pull/31619), [GGUF](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF) |
| **Ollama** | Qwen3-VL images only; video-input request closed "not planned" (2026-09-21, ffmpeg dependency blocker); MLX backend preview (0.19, needs >32 GB; Qwen3.5-35B-A3B 112 tok/s decode) with no vision statement | [issue 18151](https://github.com/ollama/ollama/issues/18151), [MLX blog](https://ollama.com/blog/mlx) |

Feasibility: 8B-class Qwen3-VL/3.6 at 4-bit fits a 16–24 GB Mac for frame-level work; 1-fps 60-frame clips land in the 10–20 GB range and tens of seconds per clip at 4B (above table) — fine for batch, not for interactive use.

## 6. Data protection

| Provider | Training on API inputs | Retention | ZDR | Region options | Source |
|---|---|---|---|---|---|
| OpenRouter | No (OpenRouter); third-party providers vary → use `data_collection: deny` | Media inputs not persisted beyond routing; metadata kept | `zdr: true` / account ZDR; Vertex-Google = Safety Retention from 2026-10-18 | US servers; EU in-region routing (Business) | [privacy](https://openrouter.ai/privacy), [ZDR](https://openrouter.ai/docs/guides/features/zdr) |
| OpenAI API | No by default | Abuse logs ≤30 days | By approval (chat/responses eligible; Batches/Files not) | US & EU storage+processing; **South Korea = regional storage only, processing outside**; residency +10% | [your data](https://developers.openai.com/api/docs/guides/your-data), [pricing](https://developers.openai.com/api/docs/pricing) |
| Gemini API (AI Studio) | Free tier: yes, human review; **paid tier: no** | Paid: logged "for a limited period" for abuse | none documented | data "may be stored transiently… in any country" | [terms](https://aistudio.google.com/docs/terms), [pricing](https://aistudio.google.com/docs/pricing) |
| Vertex AI | No ("won't use your data to train") | Cache ≤24 h (disable via `disableCache`); abuse logging under GCP ToS | via caching off + store=false (some features excluded) | Regional endpoints give residency; **Seoul (asia-northeast3): only Gemini 2.5 Flash 128k; Tokyo: 3.5 Flash, 2.5 Flash/Pro; Singapore: 4 Argon, 3.5 Flash, Claude Opus/Sonnet 5.5**; global endpoint = no guarantee | [governance](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/data-governance), [residency](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/data-residency) |
| Anthropic API | "never used for model training without your express permission" | ≤30 days default; Fable/Mythos **require** 30-day retention | By sales agreement, per org; Messages/structured outputs eligible; Batches (29 d) and Files not | `inference_geo`: `"global"` or `"us"` only (1.1× price); workspace geo `"us"` only; Asia only via Bedrock/Vertex regions | [retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention), [residency](https://platform.claude.com/docs/en/manage-claude/data-residency), [privacy](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-personal-data) |

## 7. Cost: one 60-s 1080p video at 1 fps (+≈200 prompt tokens, ≈1,500 output tokens of JSON)

Gemini token math: static mode at 1 FPS → 60 frames. AI Studio doc: ≈100 tok/s low/default → **≈6,000 video tokens**; high ≈300 tok/s → **≈18,000**; add 1,920 if audio kept (32 tok/s) ([doc](https://aistudio.google.com/docs/video-understanding)). Cloud doc for Gemini 3: 70 tok/frame → 4,200 (default) / 280 → 16,800 (high) ([doc](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/video-understanding)). Prices from [Gemini pricing](https://aistudio.google.com/docs/pricing).

| Option | Input tokens | Cost (default res) | Cost (high res) |
|---|---|---|---|
| Gemini 3.5 Flash-Lite ($0.30/$2.50) | 6.2k / 18.2k | $0.0019 + $0.0038 out = **≈$0.006** | ≈$0.009 |
| Gemini 3.8 Flash ($0.75/$3.75 through 2026-12-31; doubles 2027) | 6.2k / 18.2k | **≈$0.010** | ≈$0.019 |
| Gemini 3.1 Pro Preview ($2/$12) | 6.2k / 18.2k | ≈$0.030 | ≈$0.054 |
| Qwen3.6-35B-A3B via OpenRouter ($0.10/$0.90) | ≤60×640 = 38.4k (Qwen caps 640 tok/frame in its evals; actual depends on processor settings — **estimate**) | ≈$0.004 + $0.0014 = **≈$0.005** | – |

Frame approach, 30 frames (0.5 fps) at 1920×1080:

- **GPT-5.5**: ⌈1920/32⌉×⌈1080/32⌉ = 60×34 = 2,040 patches (fits `high`), ×1.2 = **2,448 tok/frame** → 73,440 tokens × $5/M = $0.37 + $0.045 out ≈ **$0.41**; `detail: low` (512×288 → 144 patches → 173 tok) ≈ $0.03; GPT-5.4-mini ($0.75/M) ≈ $0.06 ([vision](https://developers.openai.com/api/docs/guides/images-vision), [pricing](https://developers.openai.com/api/docs/pricing)).
- **Claude 5.x** (high-res tier, 1920×1080 = **2,691 tok**, not resized): 80,730 tokens → Sonnet 5.5 $0.16 + $0.015 ≈ **$0.18**; Opus 5.5 ≈ $0.35; **Haiku 5.5 ≈ $0.009** ($0.10/M under 100k prompt). Pre-resizing to 1456×819 (1,560 tok) cuts Sonnet to ≈$0.11 ([vision](https://platform.claude.com/docs/en/build-with-claude/vision), [pricing](https://platform.claude.com/docs/en/about-claude/pricing)).
- Gemini image-frame fallback: 258 tok per 768×768 tile; a 1080p frame ≈6 tiles ≈1,548 tok → 30 frames ≈ 46k tok ≈ $0.035 on 3.8 Flash ([image doc](https://aistudio.google.com/docs/image-understanding)).

## 8. Recommendations

| Axis | Native video (Gemini 3.8 Flash / Qwen3.6 via OR) | Sampled frames + image model (GPT-5.5 / Claude 5.5) | Local open model (Qwen3-VL-8B / Qwen3.6-27B on MLX/llama.cpp) |
|---|---|---|---|
| Quality | Best temporal grounding (timestamps native; Gemini 3 Pro VideoMMMU 87.6, Qwen3.6-27B Video-MME 87.7) | Strong per-frame attributes/OCR but no temporal model; GPT-5.5/Claude 5.x video scores unpublished | Qwen3-VL-8B ≈71 Video-MME; Qwen3.6-27B near frontier on paper, slow on Mac |
| Cost (60 s) | $0.005–0.02 | $0.01 (Haiku) – $0.41 (GPT-5.5 high) | electricity only |
| Latency | seconds; agentic mode slower | 30 calls or one 30-image call | 10–60 s per clip at 4–8B |
| Privacy | Paid Gemini: no training, abuse logs; ZDR via Vertex config; Korea residency weak (Seoul: 2.5 Flash only) | OpenAI/Anthropic ZDR by agreement; Korea = storage-only (OpenAI) / none (Anthropic direct) | Data never leaves the machine |

**(a) Segment/scene captioning + context cues** — Primary: **Gemini 3.8 Flash** via the direct Gemini API (Files API for >20 MB, `MM:SS` timestamps, `videoMetadata.fps`, JSON structured output, 1M context, paid tier not used for training); use static 1 FPS for ≤5-min clips. Alternative: **Qwen3.6-35B-A3B** on OpenRouter with `video_url`, `require_parameters: true`, `data_collection: deny` (Apache-2.0, ≈$0.005/clip), or **Gemini 3.5 Flash-Lite** for the cheapest native-video path. Self-host the same Qwen3.6 on a Linux GPU box for Docker deploys when residency matters.

**(b) Object-level attribute captions (crop or frame + box)** — Primary: **Qwen3.6-27B/35B-A3B** (OpenRouter or local; OCRBench v2 EN top-10, normalized 0–1000 boxes, `structured_outputs`), feeding crops from your detector. Alternative: **Gemini 3.8 Flash** for crops (≤384 px crop = 258 tokens; native `box_2d` output) or **Claude Haiku 5.5** for very cheap crop captions (absolute-pixel coordinates; it refuses to *identify* people, which is compatible with attribute-only prompts). Local: Qwen3-VL-8B-4bit on mlx-vlm (5.76 GB).

**(c) Korean OCR fallback** — Primary: **Gemini 3.8 Flash** (Gemini-3-Flash had the best general-VLM Korean WER, 4.85%). Alternative: **Qwen3.5-9B / Qwen3.6-35B-A3B** locally or via OpenRouter (Qwen3.5-4B 6.46% WER; Qwen3.5-9B #3 on OCRBench v2 ZH); a specialist such as olmOCR-2-7B (4.03% WER) is document-trained, so its scene-text behavior is **UNVERIFIED**. Avoid Tesseract (40.1% WER). Validate on your own Korean scene-text set before committing, since no primary Korean scene-text benchmark exists for these models.

Cross-cutting: route PII-bearing video through paid Gemini or ZDR-eligible endpoints, keep frames/crops rather than full videos when a frame model suffices, and treat all model-card cross-vendor numbers as self-reported.
