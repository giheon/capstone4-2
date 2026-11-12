# R4 — LLM(한국어 법률 추론)·OpenRouter 연동·LLM Judge

- 조사일: 2026-11-05 · 방식: 백그라운드 리서치 에이전트가 1차 출처(공식 문서·소스·모델 카드·논문·가격 페이지)만 인용해 작성한 보고서 원문 · 반영 위치: 설계 §10.4, §12
- 상태: 설계·PRD v2 승인(2026-11-05) 시점의 스냅샷. 수치·가격·버전은 조사일 기준이며, 재검증 없이는 갱신되지 않는다. `UNVERIFIED` 표시는 1차 출처로 확인하지 못한 항목이다.

<details><summary>리서치 브리프(에이전트에 전달한 요청 전문)</summary>

```text
You are a research agent. Today is 2026-11-05. Do NOT write any files; return your findings in your final message as Markdown with tables and a URL for every claim. Use WebSearch (mode "extended" when needed) and WebFetch on primary sources only: official provider docs/pricing, model cards, papers, OpenRouter docs (https://openrouter.ai/docs) and model pages, benchmark pages (KMMLU, KBL, LegalBench, Artificial Analysis only as secondary). Mark anything unverifiable as UNVERIFIED.

## Project context
PrivFrame (Korean capstone): LangGraph multi-agent system that de-identifies personal info in videos and grounds each redaction decision in Korean privacy law via RAG. LLM roles needed: (a) legal issue spotting from a video-analysis JSON (Korean), (b) retrieval sufficiency judge + question generation for the user (Active RAG), (c) decision planner producing a strict JSON plan (per object/track: action, strength, time range, cited evidence ids), (d) privacy auditor re-checking a user-modified plan for residual risk, (e) Korean report writer, (f) LLM-as-judge for offline evaluation. Current code uses OpenAI gpt-5.4-mini via LangChain ChatOpenAI with Responses API + strict json_schema structured output. The user wants OpenRouter as the primary access path (but survey others too), Python 3.12, LangChain/LangGraph 1.x.

## Questions (Track R4: LLMs for Korean legal reasoning + OpenRouter integration + judges)
1. Candidate table (as of Nov 2026) with model id, provider, context window, price per 1M input/output tokens, structured-output/JSON-schema support, tool calling, reasoning mode, availability on OpenRouter (exact OpenRouter model slug) or direct API only: OpenAI GPT-5.x family (incl. gpt-5.4, gpt-5.4-mini and newer), Anthropic Claude (Opus 5.5 / Sonnet 5.5 / Haiku 5.5 or current), Google Gemini 3 Pro / 3 Flash / 2.5 family, xAI Grok 4.x, DeepSeek V3.x/R1 current, Qwen3 / Qwen3.5 large, Moonshot Kimi K2.x, Zhipu GLM-4.6/5, Meta Llama 4, Mistral Large/Medium 3, MiniMax M2; Korean-focused: Naver HyperCLOVA X (HCX-007 / THINK), Upstage Solar Pro 2/3, LG EXAONE 4.0/4.5, Kakao Kanana-2, SKT A.X 4.0 — for these note access (own API? HF weights? OpenRouter?).
2. Korean & legal benchmark evidence: KMMLU (overall and Law category), KMMLU-Pro / KMMLU-Redux if available, KBL (Korean Benchmark for Legal language understanding; arXiv 2024) results, HAE-RAE Bench, CLIcK, LogicKor/Ko-Arena, LegalBench / LexGLUE (English proxy). Report numbers only from primary sources (papers, official leaderboards, provider model cards).
3. OpenRouter integration specifics for LangChain 1.x Python: is there an official `langchain-openrouter` package, or is `ChatOpenAI(base_url="https://openrouter.ai/api/v1")` the documented path; how OpenRouter handles `response_format` json_schema structured outputs (which providers/models support it; `require_parameters`), tool calling, `reasoning` parameter, provider routing (`provider` object: order, allow_fallbacks, data_collection "deny" for ZDR, `only`), model variants (`:nitro`, `:floor`, `:exacto`, `:online`), rate limits, fees, prompt caching passthrough, image/video input support, Responses-API compatibility. Cite OpenRouter docs URLs.
4. LLM-as-judge for evaluation: recommendations and evidence for which model families to use as judges in Korean (bias toward own outputs; use a different family than the generator), openevals prebuilt judges (LangChain), cost-control strategies.
5. Data policy: training/retention policies for OpenAI API, Anthropic API, Google Gemini API, OpenRouter (and its ZDR), DeepSeek/Qwen hosted providers — cite policy pages; relevance: inputs include descriptions of people in videos (personal data).
6. Recommendation with rationale: primary + alternative model for each role (a)–(f), considering Korean legal quality, structured-output reliability, cost, latency, OpenRouter availability, and independence between planner and auditor (different model families). Also propose an "offline fixture" strategy for tests without API keys. Keep under ~3500 words.
```

</details>

---

## PrivFrame Track R4 — LLMs for Korean legal reasoning, OpenRouter integration, judges (as of 2026-11-05)

**Method/caveats.** Everything below comes from pages fetched today (official docs/pricing, model cards, arXiv, OpenRouter docs + its `/api/v1/models`, `/models/{id}/endpoints`, `/endpoints/zdr` JSON). Several models (GPT‑5.5/5.6/6, Claude 5.5/Fable 5.1, Gemini 3.8/4 Argon, Grok 4.7) post-date my training data; I report only what the official pages state. Prices are USD per 1M input/output tokens. "SO/tools/reason" = `structured_outputs` / `tools` / `reasoning` in OpenRouter's `supported_parameters`. Items I could not reach a primary source for are marked **UNVERIFIED**.

---

### 1. Candidate table

Sources for slug/context/supported_parameters: [OpenRouter models API](https://openrouter.ai/api/v1/models) and per-model [endpoints API](https://openrouter.ai/docs/guides/overview/models); prices from the linked official page unless noted "OR listing".

| Model (OpenRouter slug) | Ctx | $/1M in / out | SO / tools / reason | Direct-API notes |
|---|---|---|---|---|
| `openai/gpt-5.4` | 1.05M | 2.50 / 15 (>272K in: 5 / 22.50) ([OpenAI pricing](https://developers.openai.com/api/docs/pricing)) | ✓/✓/✓ (effort none…xhigh) | `gpt-5.4-2026-03-05`, 128K out, cutoff Aug‑2025 ([model page](https://developers.openai.com/api/docs/models/gpt-5.4)) |
| `openai/gpt-5.4-mini` (current PrivFrame model) | 400K (272K in) | 0.75 / 4.50 | ✓/✓/✓ | `gpt-5.4-mini-2026-03-17`; Responses + Chat Completions ([page](https://developers.openai.com/api/docs/models/gpt-5.4-mini)) |
| `openai/gpt-5.4-nano` | 400K | 0.20 / 1.25 | ✓/✓/✓ | — |
| `openai/gpt-5.5` | 1.05M | 5 / 30 | ✓/✓/✓ | — |
| `openai/gpt-5.6-terra` / `-sol` / `-luna` | 1.05M | 2 / 12 · 4 / 20 (promo to 2026‑11‑30) · 0.20 / 1.20 | ✓/✓/✓ | GPT‑5.6+ charges cache writes (1.25×) ([OR caching](https://openrouter.ai/docs/features/prompt-caching)) |
| `openai/gpt-6.1-terra` / `gpt-6.1-sol` / `gpt-6-astra` / `gpt-6-luna` | 1.05M | 1 / 5 · 2 / 10 · 10 / 50 · 0.10 / 0.50 | ✓/✓/✓ | `gpt-6-astra-law` 10 / 50 promo to 2026‑12‑31 (direct; OR availability not checked) |
| `anthropic/claude-fable-5.1` | 1M | 10 / 50 (cache read 0.25) ([Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)) | ✓/✓/✓ | `claude-fable-5-1`, 128K out, adaptive thinking always on ([models](https://platform.claude.com/docs/en/about-claude/models/overview)) |
| `anthropic/claude-opus-5.5` | 1M | 4 / 20 (cache read 0.20) | ✓/✓/✓ | `claude-opus-5-5` |
| `anthropic/claude-sonnet-5.5` | 1M | 2 / 10 (cache read 0.10) | ✓/✓/✓ | `claude-sonnet-5-5` |
| `anthropic/claude-haiku-5.5` | 1M | 0.10 / 0.50 (≤100K prompt; 0.50 / 2.50 above) | ✓/✓/✓ | `claude-haiku-5-5`; note Claude ≥4.7 tokenizer yields ~30% more tokens per text |
| `google/gemini-4-argon` | 1,048,576 | 2 / 10 (→ 4 / 20 from 2026‑12‑01) ([Gemini pricing](https://aistudio.google.com/docs/pricing)) | ✓/✓/✓ (low/med/high) | `gemini-4-argon`; video+PDF input; 65K out (1M long‑decode) ([page](https://aistudio.google.com/docs/models/gemini-4-argon)) |
| `google/gemini-3.8-flash` | 1,048,576 | 0.75 / 3.75 (→ 1.50 / 7.50 from 2027‑01‑01) | ✓/✓/✓ | `gemini-3.8-flash`, 65,536 out, video input ([page](https://aistudio.google.com/docs/models/gemini-3.8-flash)) |
| `google/gemini-3.1-pro-preview` | 1,048,576 | 2 / 12 (≤200K) | ✓/✓/✓ | preview; `gemini-3-pro-preview` shut down; 2.5 Pro/Flash deprecated, rerouted 2026‑12‑01 ([models](https://aistudio.google.com/docs/models)) |
| `x-ai/grok-4.7` | 500K | 2 / 6 (<200K; 4 / 12 above) ([xAI models](https://docs.x.ai/docs/models)) | ✓/✓/✓ | cutoff May‑2026; encrypted reasoning on Responses API; strict JSON schema ([xAI SO](https://docs.x.ai/docs/guides/structured-outputs)) |
| `x-ai/grok-4.3` | 1M | 1.25 / 2.50 | ✓/✓/✓ | — |
| `deepseek/deepseek-v4.1-flash` / `-1017` | 1,048,576 | OR listing 0.045 / 0.50; hosts range 0.006–0.45 / 0.40–1.80 | ✓/✓/✓ (varies per host) | First-party pricing/policy **UNVERIFIED** (api-docs.deepseek.com unreachable from here); `deepseek-v4-pro-0813` OR listing 0.107 / 4.278 |
| `qwen/qwen3.8-max-0902` | 1M | 2 / 6 (Alibaba endpoint) | ✓/✓/✓ | Alibaba Model Studio price page lists no numbers ([page](https://www.alibabacloud.com/help/en/model-studio/models)) |
| `qwen/qwen3.5-397b-a17b` · `qwen/qwen3.8-27b` | 262K · 1.02M | 0.50 / 3.60 · 0.15 / 1.875 (OR) | ✓/✓/✓ | open weights; many ZDR hosts |
| `moonshotai/kimi-k2.6` | 262K | OR listing 0.65 / 3.41; Moonshot endpoint 0.95 / 4.00 | ✓/✓/✓ | Moonshot: K2 series retires end‑Dec‑2026, prices not rendered ([Kimi pricing](https://platform.kimi.ai/docs/pricing/chat)); `kimi-k3.1-pro` 2 / 10 on OR |
| `z-ai/glm-5.3` · `z-ai/glm-5.3-flash` | 1,048,576 | 1.40 / 4.40 · 0.15 / 0.50 ([Z.ai pricing](https://docs.z.ai/guides/overview/pricing)) | ✓/✓/✓ | `z-ai/glm-5.3-flashx-legal` added 2026‑11‑02 (0.37 / 1.25, Z.AI only) |
| `meta-llama/llama-4-maverick` | 524K | 0.35 / 1.00 (OR) | ✓/✓/✗ | no reasoning mode |
| `mistralai/mistral-large-4-0` · `mistral-medium-3-5` | 1,048,576 · 262K | 0.70 / 2.10 · 1.50 / 7.50 (OR, Mistral endpoint) | ✓/✓/✓ | Mistral site says "Large $0.5/$1.5" without version ([pricing](https://mistral.ai/pricing)) — conflict, verify |
| `minimax/minimax-m3.1-flash` · `minimax-m3` | 1,048,576 | 0.15 / 0.60 · 0.30 / 1.20 ([MiniMax pricing](https://platform.minimax.io/docs/guides/pricing)) | ✓/✓/✓ | — |

**Korean-focused models**

| Model | Access | Ctx | Price / license | SO / tools / reason |
|---|---|---|---|---|
| Upstage Solar Pro 4 (`upstage/solar-pro4`, `solar-pro4-260806`) | OpenRouter + Upstage Console (OpenAI‑compatible) | 512K, 128K out | Official 0.30 / 1.20 (cached 0.06) ([console](https://console.upstage.ai/docs/models/solar-pro-4)); OR listing 0.15 / 0.60 (launch promo per [blog](https://www.upstage.ai/blog/en/solar-pro-4)) — verify current. Weights closed; EN/KO/JA | ✓/✓/✓; Upstage endpoint is ZDR‑listed |
| Upstage Solar Mini 4 (`upstage/solar-mini4`) | OR | 524K | 0.05 / 0.20 (OR) | ✓/✓/✓ |
| Naver HCX‑007 (HyperCLOVA X THINK productized) | CLOVA Studio API only; not on OR | 128K, 32,768 out | KRW/1K‑token price page is JS‑rendered → **UNVERIFIED** ([model docs](https://guide.ncloud-docs.com/docs/en/clovastudio-model)) | function calling ✓, structured outputs ✓, thinking ✓ (cannot combine features) |
| Naver HyperCLOVAX‑SEED‑Think‑32B | HF open weights, custom license; no hosted provider | 128K | [HF card](https://huggingface.co/naver-hyperclovax/HyperCLOVAX-SEED-Think-32B) | self‑host (vLLM/SGLang) |
| LG EXAONE 4.5 33B / K‑EXAONE 236B‑A23B | HF; EXAONE License 1.2‑**NC** / K‑EXAONE license; K‑EXAONE has FriendliAI API link; not on OR | 262K / 256K | [EXAONE 4.5](https://huggingface.co/LGAI-EXAONE/EXAONE-4.5-33B), [K‑EXAONE](https://huggingface.co/LGAI-EXAONE/K-EXAONE-236B-A23B) | self‑host |
| Kakao Kanana‑2 30B‑A3B (instruct/thinking) | HF, "Kanana License"; no hosted provider; not on OR | 32K native (128K YaRN) | [HF card](https://huggingface.co/kakaocorp/kanana-2-30b-a3b-thinking) | self‑host |
| SKT A.X 4.0 72B · A.X K1 519B‑A33B · A.X K2 688B‑A33B | HF; A.X 4.0 license tag "qwen", K1/K2 Apache‑2.0; no hosted provider; not on OR; "free API" link in A.X 4.0 card **UNVERIFIED** | 131K · 131K · 262K | [A.X 4.0](https://huggingface.co/skt/A.X-4.0), [K1](https://huggingface.co/skt/A.X-K1), [K2](https://huggingface.co/skt/A.X-K2) | tool calling via vLLM hermes parser (A.X 4.0) |

Not on OpenRouter as of today: any `naver/`, `lgai/`, `kakao/`, `skt/` slug; `upstage/solar-pro-3` returns 404 (replaced by `solar-pro4`).

---

### 2. Korean & legal benchmark evidence (primary sources only)

**KMMLU‑Pro / KMMLU‑Redux** ([arXiv 2507.08924](https://arxiv.org/html/2507.08924v2), Table 2 + Table 9; "Lawyer" exam cut‑off ≈54.22):

| Model | Redux | Pro | Licenses passed | Lawyer | Judicial Scrivener | Labor Att. | Patent Att. |
|---|---|---|---|---|---|---|---|
| o1 | 81.14 | 78.09 | 10/14 | 49.33 | 54.55 | 71.55 | 65.14 |
| Claude 3.7 Sonnet (thinking) | 79.36 | 77.70 | 12/14 | **56.00** | 50.00 | 77.41 | 74.31 |
| GPT‑4.1 | 75.86 | 72.99 | 10/14 | 50.00 | 47.47 | 66.95 | 63.30 |
| DeepSeek R1 | 78.51 | 71.33 | 7/14 | 38.00 | 45.45 | 61.51 | 57.80 |
| Qwen3‑235B (thinking) | 74.49 | 68.22 | 6/14 | 42.67 | 43.43 | 56.90 | 57.80 |
| EXAONE 4.0 32B (thinking) | 72.71 | 67.67 | 6/14 | 39.33 | 41.92 | 60.25 | 59.63 |
| Llama‑4‑Maverick | 77.58 | 68.10 | 4/14 | 41.33 | 38.38 | 62.34 | 55.96 |

Only Claude 3.7 Sonnet (thinking) clears the Lawyer cut‑off; no model passes Judicial Scrivener; "models equipped with dense reasoning usually outperform" non‑reasoning variants.

**Vendor‑reported KMMLU‑Pro for current models** (self‑reported, mixed settings): A.X K2 80.5, DeepSeek‑V4 Flash 78.8, Qwen3.5 78.4, GLM‑5.1 76.5, Kimi‑K2.6 73.4, Nemotron 3 Ultra 69.2, A.X K1 68.9, MiniMax M2.7 66.6 ([SKT A.X K2 card](https://huggingface.co/skt/A.X-K2)); Solar Pro 4 79.2 and "KBL (law) 77.5" vs Solar Open 2 78.4/75.5 ([Upstage blog](https://www.upstage.ai/blog/en/solar-pro-4)); GPT‑5 mini 72.5, Qwen3.5 27B 73.0, EXAONE 4.5 67.6, K‑EXAONE 67.3 ([LG card](https://huggingface.co/LGAI-EXAONE/EXAONE-4.5-33B)); DeepSeek‑V3.2 72.1, Qwen3‑Thinking‑2507 71.6 ([K‑EXAONE card](https://huggingface.co/LGAI-EXAONE/K-EXAONE-236B-A23B)); A.X 4.0 72.43 vs GPT‑4o 66.97 ([A.X 4.0 card](https://huggingface.co/skt/A.X-4.0)).

**KMMLU (original)** ([arXiv 2402.11548](https://arxiv.org/html/2402.11548v2)): overall GPT‑4 59.95, HyperCLOVA X 53.40; Law subject GPT‑4 58.6 / HyperCLOVA X 58.7 / Gemini‑Pro 48.5; Criminal Law 50.5 / 48.5 / 39.0 (old models; useful only as a floor). Newer KMMLU overall (cards): A.X K1 80.2 vs GLM‑4.6 79.9 vs DeepSeek‑V3.1 76.5 ([K1](https://huggingface.co/skt/A.X-K1)); A.X 4.0 78.32 vs GPT‑4o 72.51; Kanana‑2 30B instruct 67.32 ([card](https://huggingface.co/kakaocorp/kanana-2-30b-a3b-thinking)); HyperCLOVA X 32B Think 71.3 vs EXAONE 4.0 75.2 ([arXiv 2601.03286](https://arxiv.org/html/2601.03286v1)).

**KBL** ([arXiv 2410.08731](https://arxiv.org/html/2410.08731v1), EMNLP‑Findings 2024): closed‑book knowledge avg / bar‑exam avg — GPT‑4 (row label; footnote maps rows to gpt‑4o models, labels may be swapped) 72.0 / 48.1; Claude‑3.5‑Sonnet 70.0 / 42.5; Claude‑3‑Opus 67.0 / 41.0; Qwen2‑72B 60.2 / 31.1; EXAONE‑3.0‑7.8B 59.3 / 20.8. RAG (BM25 precedents+statutes) lifted GPT‑4 knowledge to 75.3 but Claude‑3‑Sonnet bar fell to 18.8 → retrieval helps only strong readers. Newer models are not in KBL; use **KCL** instead.

**KCL — Korean Canonical Legal Benchmark** ([arXiv 2512.24572](https://arxiv.org/html/2512.24572v2), EACL 2026; vanilla → with aligned precedents; human threshold 68.7% MCQA):

| Model | MCQA | Essay (rubric) |
|---|---|---|
| Gemini 2.5 Pro | 66.1 → **89.3** | 60.5 → **74.9** |
| Claude Sonnet 4.5 | 54.9 → 85.2 | 47.6 → 68.5 |
| GPT‑5 | 53.9 → 84.2 | 57.5 → 72.8 |
| o3 | 50.2 → 83.4 | 51.2 → 69.4 |
| GPT‑4.1 | 47.9 → 72.0 | 42.4 → 65.1 |
| Qwen3 235B Thinking | 41.5 → 76.9 | 39.2 → 63.9 |
| DeepSeek‑R1 | 40.9 → 72.4 | 35.0 → 53.1 |
| A.X 4.0 | 49.1 → 71.3 | 34.7 → 52.4 |
| EXAONE 4.0 32B | 36.4 → 72.3 | 32.6 → 47.5 |
| HCX SEED Think 14B | 31.3 → 57.6 | 26.2 → 42.8 |

Finding: Korean‑native models have more Korean knowledge but weaker expert reasoning once precedents are supplied; frontier reasoning models dominate the grounded setting — exactly PrivFrame's RAG regime.

**CLIcK / HAE‑RAE / KoBALT** (vendor cards): A.X K2 CLIcK 91.6, KoBALT 73.0; DeepSeek‑V4 Flash 89.6 / 75.3; Qwen3.5 88.4 / 69.9; GLM‑5.1 88.8 / 72.0 ([K2 card](https://huggingface.co/skt/A.X-K2)); HyperCLOVA X 32B Think HAE‑RAE 87.4 vs EXAONE 4.0 64.3 ([report](https://arxiv.org/html/2601.03286v1)); A.X 4.0 CLIcK 83.51 vs GPT‑4o 80.22. Original CLIcK/HAE‑RAE papers list no current models ([CLIcK](https://arxiv.org/abs/2403.06412), [HAE‑RAE](https://arxiv.org/abs/2309.02706)).

**LogicKor / Ko‑Arena:** LogicKor repo archived 2024‑10‑17, read‑only ([GitHub](https://github.com/instructkr/LogicKor)); Ko‑Arena‑Hard‑Auto uses an ensemble judge (gemini‑2.0‑flash, gpt‑4o‑mini, deepseek‑chat‑v3‑0324) vs claude‑3.7‑sonnet baseline "to mitigate self‑preference bias" ([GitHub](https://github.com/qwopqwop200/ko-arena-hard-auto)).

**LegalBench (English proxy; secondary, Vals AI, 2026‑10‑13)** ([leaderboard](https://www.vals.ai/benchmarks/legal_bench)): Claude Fable 5 88.56, Claude Fable 5.1 88.51, Gemini 4 Argon 88.30, Gemini 3.1 Pro 87.40, Gemini 3.8 Flash 86.99, Claude Opus 5 86.97, GPT‑5.6 Sol 86.97, GPT‑5.5 86.52, Grok 4.6 86.31, "GLM Legal" 86.23 — a ~2‑point band; LegalBench does not separate planners. LexGLUE: no current leaderboard found (**UNVERIFIED**).

---

### 3. OpenRouter integration for LangChain 1.x (Python 3.12)

| Topic | Finding | Source |
|---|---|---|
| Official package | `langchain-openrouter` 0.2.9 (2026‑09‑22), PyPI owner "langchain", deps `langchain-core>=1.5.5`, `openrouter>=0.9.2`, Python ≥3.10; class `ChatOpenRouter` | [PyPI JSON](https://pypi.org/pypi/langchain-openrouter/json), [docs](https://docs.langchain.com/oss/python/integrations/chat/openrouter) |
| `ChatOpenAI(base_url=…)` | Explicitly discouraged: ChatOpenAI "targets only official OpenAI specifications… for OpenRouter… use a provider-specific package"; non‑standard fields (`reasoning_details`) are dropped and `stream_usage` defaults off when `OPENAI_BASE_URL` is set | [ChatOpenAI docs](https://docs.langchain.com/oss/python/integrations/chat/openai) |
| Endpoint used | `ChatOpenRouter` calls `client.chat.send(...)` (Chat Completions), not Responses | [source](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/partners/openrouter/langchain_openrouter/chat_models.py) |
| Structured output | `with_structured_output(method="function_calling"\|"json_schema", strict=…)`; `json_mode` warns and falls back to `json_schema`; `json_schema` sends `response_format={"type":"json_schema",…}` with `strict` | same source |
| OpenRouter‑specific params | `openrouter_provider` → `provider` (`order`, `only`, `ignore`, `sort`, `data_collection`, `quantizations`, `allow_fallbacks`, `require_parameters`), `reasoning={"effort","summary"}`, `route`, `plugins`, `session_id`, `app_url`/`app_title`; `models` fallback array is **not** a field (passes via `model_kwargs`, untested) | same source |
| Metadata | `usage_metadata.input_token_details.cache_read/cache_creation`, `output_token_details.reasoning`; `response_metadata.cost`, `provider`, `native_finish_reason`; reasoning text in `additional_kwargs["reasoning_content"]` | same source |
| JSON‑schema structured outputs | Supported per **endpoint** (not model); set `provider.require_parameters: true` so routing only hits endpoints supporting `response_format`; `strict: true` enforced natively by some providers, "a strong hint" at others; Response Healing plugin for non‑streaming | [SO guide](https://openrouter.ai/docs/guides/features/structured-outputs) |
| Tool calling | OpenAI‑style `tools`/`tool_choice`/`parallel_tool_calls`; filter models by `supported_parameters=tools`; "Auto Exacto" orders providers by tool‑call error rate | [tool calling](https://openrouter.ai/docs/guides/features/tool-calling) |
| `reasoning` | `effort` (xhigh/high/medium/low/minimal/none) or `max_tokens` (not both), `exclude`; maps to OpenAI/Grok effort, Anthropic budget (`max(min(max_out*ratio,128000),1024)`), Gemini 3 `thinkingLevel`; reasoning tokens billed as output | [reasoning](https://openrouter.ai/docs/use-cases/reasoning-tokens) |
| Provider routing | `order`, `allow_fallbacks` (default true), `require_parameters`, `data_collection: "allow"\|"deny"`, `only`, `ignore`, `quantizations`, `sort`, `max_price`, `zdr: true`; default = weighted by price/throughput/TTFT/reliability/tool quality; sticky routing while cache warm | [provider routing](https://openrouter.ai/docs/features/provider-routing) |
| Variants | Routing: `:nitro` (throughput + priority tier, billed at tier rate), `:floor` (price + flex tier), `:exacto` (tool‑call quality); `:online` **deprecated** (use `openrouter:web_search`); catalog: `:free`, `:batch`; `:thinking`/`:extended` deprecated; last sort suffix wins | [variants](https://openrouter.ai/docs/guides/routing/model-variants/overview) |
| Model fallbacks | `models: [...]` priority list; any error (context, moderation, 429, downtime) triggers next; billed at served model | [fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks) |
| Rate limits | Free models 20 RPM, 50/day (<10 credits) or 1000/day; paid: no fixed RPM, in‑flight budget → 402; 429 with `X-RateLimit-*`; extra keys/accounts don't raise limits | [limits](https://openrouter.ai/docs/api-reference/limits) |
| Fees | Inference at provider list price; 5.5% on credit purchases (Standard), 8% (Business); BYOK free to $25K/month list price then 5% | [pricing](https://openrouter.ai/pricing), [FAQ](https://openrouter.ai/docs/faq) |
| Prompt caching | Passthrough: OpenAI automatic (1024‑token min), Anthropic `cache_control` (write 1.25×/2×, read 0.1×), Gemini implicit (read 0.25×), DeepSeek/Grok/Moonshot automatic; inspect via `prompt_tokens_details.cached_tokens`, `cache_discount` | [caching](https://openrouter.ai/docs/features/prompt-caching) |
| Image/video | Images via `image_url`/`input_image`; video via Responses `input_video` or Chat `video_url` (URL or base64; mp4/mpeg/mov/webm); URLs only to providers that support them (AI Studio = YouTube only; Vertex needs base64); filter models by `input_modalities: video` | [videos](https://openrouter.ai/docs/guides/overview/multimodal/videos) |
| Responses API | `POST /api/v1/responses` exists alongside `/chat/completions`; `text.format` json_schema supported; takes same `models`/`provider`/`plugins`; GA/beta status not stated | [API overview](https://openrouter.ai/docs/api/reference/overview) |

**Migration implication:** PrivFrame's `ChatOpenAI(use_responses_api=True)` + strict `json_schema` becomes `ChatOpenRouter(model=..., openrouter_provider={"require_parameters": True, "data_collection": "deny", "zdr": True}).with_structured_output(Plan, method="json_schema", strict=True)` on Chat Completions; keep a `ChatOpenAI` branch for direct OpenAI runs if you want the Responses API.

---

### 4. LLM‑as‑judge for Korean evaluation

| Evidence | Finding | Source |
|---|---|---|
| Self‑preference | GPT‑4/GPT‑3.5/Llama‑2 favor their own summaries; self‑recognition accuracy correlates linearly with bias (NeurIPS 2024) | [arXiv 2404.13076](https://arxiv.org/abs/2404.13076) |
| Panel of judges | PoLL of smaller, disjoint‑family models beats a single GPT‑4 judge with "less intra‑model bias", "over seven times less expensive" | [arXiv 2404.18796](https://arxiv.org/abs/2404.18796) |
| Korean meta‑eval (KUDGE, 5,012 Korean annotations) | Avg (pointwise acc, pairwise acc): GPT‑4o 74.51 (61.3/87.8), Mistral‑Large 73.19, Llama‑3.1‑405B 73.15, Claude‑3.5‑Sonnet 72.79 (pairwise best 89.11), HyperCLOVA X 68.47; English RewardBench score predicts Korean judging (R²≈0.5) while Korean‑specific capability does not; no judge >70% on hard subset | [arXiv 2409.11239](https://arxiv.org/html/2409.11239v2) |
| Multilingual reliability (25 langs incl. Korean) | Mean Fleiss' κ ≈0.3; GPT‑4o best (WikiLingua κ 0.54); majority‑vote ensemble of 3 open models lifts worst‑case κ by +0.1–0.25 | [arXiv 2505.12201](https://arxiv.org/html/2505.12201) |
| Korean practice | Ko‑Arena‑Hard‑Auto ensembles gemini‑2.0‑flash + gpt‑4o‑mini + deepseek‑chat to cut self‑preference and cost | [GitHub](https://github.com/qwopqwop200/ko-arena-hard-auto) |
| openevals (LangChain) | `create_llm_as_judge(prompt, model|judge, continuous|choices, use_reasoning, few_shot_examples, system, feedback_key, output_schema)` — judge must support structured output; prebuilt `CORRECTNESS_PROMPT`, `HALLUCINATION_PROMPT`, `RAG_GROUNDEDNESS_PROMPT`, `RAG_RETRIEVAL_RELEVANCE_PROMPT`, `RAG_HELPFULNESS_PROMPT`, `PLAN_ADHERENCE_PROMPT`, `PII_LEAKAGE_PROMPT`; `create_json_match_evaluator` for per‑key exact/LLM rubric scoring of JSON | [README](https://raw.githubusercontent.com/langchain-ai/openevals/main/README.md) |

**Recommendation:** judge family ≠ generator family (planner on OpenAI → judges from Anthropic/Google); use a 3‑judge panel (e.g., `anthropic/claude-sonnet-5.5`, `google/gemini-3.8-flash`, `z-ai/glm-5.3` or `qwen/qwen3.8-max`) with majority vote, rubric prompts in Korean, `create_json_match_evaluator` for deterministic plan checks (action/strength/time‑range/evidence ids) before any LLM judging. Cost control: judge only diffs vs. golden plans, sample 10–20% for essay‑style rubrics, use `:batch` slugs (50% off at OpenAI/Anthropic/Gemini) and `reasoning.effort: "low"`, cache the shared rubric prefix.

---

### 5. Data policy (inputs describe identifiable people → personal data)

| Provider | Training on API data | Retention | ZDR | Source |
|---|---|---|---|---|
| OpenAI API | Not used "unless you explicitly opt in" | Abuse‑monitoring logs ≤30 days; Responses `store=true` ≥30 days; ZDR/MAM by approval; data‑residency regions incl. South Korea (storage only), +10% price | Approval | [Your data](https://developers.openai.com/api/docs/guides/your-data) |
| Anthropic API | "Anthropic may not train models on Customer Content from Services" | Deleted within 30 days (flagged: up to 2 years); structured‑output schema cached 24 h | Approval, per org | [Commercial terms](https://www.anthropic.com/legal/commercial-terms), [retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data), [SO docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |
| Google Gemini API | Paid: "Google doesn't use your prompts … or responses to improve our products"; logs kept "for a limited period" for abuse; **Unpaid tier (incl. AI Studio without billing)**: used to improve products, human review, "Do not submit … personal information" | Unspecified | — | [Terms, eff. 2026‑03‑23](https://aistudio.google.com/docs/terms) |
| OpenRouter | Prompts/completions not stored by default (metadata only; opt‑in logging = 1% discount); `data_collection: "deny"` excludes training providers; `provider.zdr: true` → ZDR (+ Safety‑Retention if allowed); CSAM screening of all traffic from 2026‑10‑27 | Per endpoint tier | Yes | [ZDR](https://openrouter.ai/docs/guides/features/zdr), [data collection](https://openrouter.ai/docs/guides/privacy/data-collection), [provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging) |
| OpenRouter ZDR list (today) | GPT‑5.x/6: **Azure only** (OpenAI first‑party absent); Claude 5.x: Bedrock + Google (Anthropic first‑party absent); Gemini 3.8/4: Google Vertex (Safety Retention); Grok 4.7: xAI + Bedrock; Kimi K2.6: Moonshot AI + hosts; GLM‑5.3: Z.AI + hosts; Solar Pro 4: Upstage; DeepSeek V4.1 Flash: ~30 third‑party hosts, **DeepSeek first‑party absent**; Qwen3.8‑Max (Alibaba) absent | — | — | [`/api/v1/endpoints/zdr`](https://openrouter.ai/api/v1/endpoints/zdr) |
| Alibaba Model Studio (Intl) | "will not use your business data … to train" | Prompts/responses ≤30 days; ZDR for enterprise with account manager, non‑mainland regions only | Enterprise | [policy, 2026‑10‑27](https://www.alibabacloud.com/help/en/model-studio/data-retention-policy) |
| DeepSeek first‑party | **UNVERIFIED** (policy/terms hosts unreachable from this environment); OpenRouter's provider record links [privacy](https://chat.deepseek.com/downloads/DeepSeek%20Privacy%20Policy.html) / [terms](https://chat.deepseek.com/downloads/DeepSeek%20Terms%20of%20Use.html) | — | not ZDR‑listed | [providers API](https://openrouter.ai/api/v1/providers) |

Cross‑border note: PIPA Art. 28‑8 restricts overseas provision/storage of personal information absent separate consent/adequacy/certification and requires prior notice of transfer details ([law.go.kr English](https://www.law.go.kr/LSW/eng/engLsInfoR.do?lsiSeq=270351)). Practical mitigation for PrivFrame: send the LLM only derived descriptions (no frames), pseudonymize track ids, and route with `zdr: true`.

---

### 6. Recommendations

| Role | Primary (OpenRouter slug) | Alternative | Rationale |
|---|---|---|---|
| (a) Legal issue spotting (Korean JSON in) | `anthropic/claude-sonnet-5.5` (effort medium) | `google/gemini-3.8-flash` | Claude/Gemini lead grounded Korean legal reasoning (KCL 85–89% MCQA, best essays); Sonnet 5.5 $2/$10, 1M ctx, strict SO, ZDR via Bedrock/Vertex |
| (b) Retrieval‑sufficiency judge + question generation | `openai/gpt-5.4-mini` (current; `reasoning.effort: low`) | `google/gemini-3.8-flash`, `upstage/solar-pro4` | Cheap, strict json_schema, Azure ZDR; Solar Pro 4 adds native Korean + 512K ctx at $0.30/$1.20 |
| (c) Decision planner (strict JSON plan) | `openai/gpt-5.4` (effort medium) | `google/gemini-4-argon` | Native strict schemas + Responses/Chat parity; 1.05M ctx; `require_parameters: true`, `models: ["openai/gpt-5.4","openai/gpt-5.6-terra"]` fallback |
| (d) Privacy auditor (independent family) | `anthropic/claude-opus-5.5` (adaptive thinking) | `google/gemini-3.1-pro-preview` / `x-ai/grok-4.7` | Different family from planner (self‑preference evidence); Opus 5.5 $4/$20 with 0.20 cache reads for the shared statute context |
| (e) Korean report writer | `upstage/solar-pro4` | `anthropic/claude-haiku-5.5` ($0.10/$0.50 ≤100K) | Korean‑native fluency; cheap; Upstage endpoint ZDR‑listed |
| (f) Offline LLM‑judge | Panel: `anthropic/claude-sonnet-5.5` + `google/gemini-3.8-flash` + `z-ai/glm-5.3` (majority) | `qwen/qwen3.8-max-0902` | Disjoint from planner (OpenAI); GPT‑4o‑class judges reached ~74% on KUDGE, so human spot‑checks remain necessary |

Avoid for production now: Korean open models without hosted endpoints (EXAONE NC license; Kanana/A.X self‑host only), Kimi K2 (retiring Dec 2026 upstream), DeepSeek first‑party (unverified policy; use a ZDR host via OpenRouter if you want V4.1 Flash as a cheap alternative).

**Offline fixture strategy (tests without API keys).** (1) Unit‑test graph logic with `GenericFakeChatModel`/`FakeListChatModel` from `langchain_core.language_models.fake_chat_models` ([reference](https://reference.langchain.com/python/langchain-core/language_models/fake_chat_models)), wrapping scripted `AIMessage`s with tool calls/JSON content; (2) implement a `FixtureChatModel(BaseChatModel)` whose `with_structured_output` returns Pydantic objects loaded from `tests/fixtures/<role>/<case>.json`, keyed by `sha256(model, prompt_version, input_fixture)` so prompt edits invalidate fixtures deliberately; (3) record real HTTP exchanges with `pytest-recording` 0.14.1 (built on `vcrpy`; filter `Authorization` headers) and replay in CI ([pytest-recording](https://pypi.org/pypi/pytest-recording/json), [vcrpy](https://pypi.org/pypi/vcrpy/json)); (4) a nightly "live" job with `OPENROUTER_API_KEY` reruns the same cases against `:batch` slugs, validates against the strict schemas and `create_json_match_evaluator`, and refreshes fixtures only on explicit approval; (5) keep one deterministic rule‑based planner baseline so the audit/judge tests have a model‑independent oracle.
