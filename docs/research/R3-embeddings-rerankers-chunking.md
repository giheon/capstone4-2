# R3 — 임베딩·리랭커·벡터 스토어·하이브리드 검색·청킹

- 조사일: 2026-11-05 · 방식: 백그라운드 리서치 에이전트가 1차 출처(공식 문서·소스·모델 카드·논문·가격 페이지)만 인용해 작성한 보고서 원문 · 반영 위치: 설계 §9, §10.3
- 상태: 설계·PRD v2 승인(2026-11-05) 시점의 스냅샷. 수치·가격·버전은 조사일 기준이며, 재검증 없이는 갱신되지 않는다. `UNVERIFIED` 표시는 1차 출처로 확인하지 못한 항목이다.

<details><summary>리서치 브리프(에이전트에 전달한 요청 전문)</summary>

```text
You are a research agent. Today is 2026-11-05. Do NOT write any files; return your findings in your final message as Markdown with tables and a URL for every claim. Use WebSearch (mode "extended" when needed) and WebFetch on primary sources only: official docs, model cards (Hugging Face), papers, MTEB/MMTEB leaderboard pages, pricing pages, vendor docs. Mark anything unverifiable as UNVERIFIED.

## Project context
PrivFrame (Korean capstone): LangGraph multi-agent system that de-identifies personal info in videos and grounds every redaction decision in Korean privacy law through RAG. Corpus (Korean text): statutes (개인정보 보호법·시행령, article-level units like "제25조의2 제1항"), PIPC guidelines (영상정보처리기기 안내서, 가명정보 가이드라인), PIPC decisions/dispute mediation cases, court precedents (판시사항/판결요지/이유), and curated "similar case" notes. Queries are generated per legal issue from a video analysis JSON (Korean). Current code: LangChain 1.x, Chroma 1.x via langchain-chroma, OpenAI text-embedding-3-small (1536-d), RecursiveCharacterTextSplitter 1200/150, top_k 8, no reranker, an offline "fixture" hash embedding for tests. Dev on macOS Apple Silicon (CPU/MPS). OpenRouter is preferred for LLMs, but embeddings/rerankers may use any API or local model.

## Questions (Track R3: embeddings, rerankers, vector store, hybrid retrieval for Korean legal text)
1. Embedding candidates with Korean evidence: OpenAI text-embedding-3-large/small; Google gemini-embedding-001 (and any newer Gemini embedding); Voyage (voyage-3.5, voyage-3-large, voyage-law-2, multilingual variants); Cohere embed-v4.0; Jina embeddings v3/v4; BAAI BGE-M3; Qwen3-Embedding 0.6B/4B/8B; nlpai-lab KURE-v1 and KoE5; multilingual-e5-large-instruct; gte-multilingual; Snowflake arctic-embed-l-v2.0; Upstage solar-embedding-1-large (Upstage API); Naver HyperCLOVA X embedding (CLOVA Studio); Mistral embed; NVIDIA llama-nemoretriever / NV-Embed. For each: dims, max tokens, license/access, price, Korean retrieval scores (MMTEB Korean tasks: Ko-StrategyQA, AutoRAGRetrieval, MIRACL-ko, PublicHealthQA-ko, BelebeleRetrieval-ko, XPQARetrieval-ko; KURE's published benchmark table), legal-domain evidence if any.
2. Reranker candidates: Cohere rerank-v3.5 (multilingual incl. Korean), Jina reranker v2/v3/m0, BGE-reranker-v2-m3 and bge-reranker-v2-gemma, Qwen3-Reranker 0.6B/4B/8B, mxbai-rerank-v2, Voyage rerank-2.5, Upstage/Naver rerankers if any, LLM-as-reranker; Korean evidence, latency on CPU, cost, license.
3. Any Korean LEGAL retrieval benchmark or dataset (e.g., KBL — Korean Benchmark for Legal language understanding — its statute/precedent retrieval setting; LBox Open; AI Hub legal QA; KLAID; "KoLEG"/"LegalBench-KR" if they exist). Report what exists, with URLs.
4. Vector store & hybrid search: Chroma 1.x — does it support BM25/hybrid or only vector + full-text `$contains`? Compare Qdrant (sparse+dense hybrid, BM25/BM42, embedded local mode), LanceDB (FTS + hybrid), pgvector (+ pg_search/ParadeDB or tsvector), Milvus Lite, Weaviate, OpenSearch (nori Korean analyzer). LangChain 1.x integration status for each; Korean BM25 tokenization options (kiwipiepy, mecab-ko/python-mecab-ko, KoNLPy Okt, bm25s). Recommend whether to keep Chroma or migrate.
5. Chunking for legal text: evidence/best practices for article-level (조문 단위) chunking with hierarchical metadata and parent-document / contextual retrieval (Anthropic "contextual retrieval"), small-to-big, RAPTOR, late chunking (Jina) — cite primary posts/papers and LangChain 1.x classes available (ParentDocumentRetriever, MultiVectorRetriever, EnsembleRetriever, ContextualCompressionRetriever, rerankers in langchain-cohere / langchain-community).
6. Recommendation: primary + alternative for embedding model, reranker, vector store/hybrid approach, and a chunking scheme per corpus type (statute / precedent / guideline / decision); include an "offline/local" option for tests and a cost estimate for indexing ~50k Korean chunks (~400 tokens each) and ~200 queries/day. Keep under ~3500 words.
```

</details>

---

## Track R3 — Embeddings, rerankers, vector store, hybrid retrieval & chunking for Korean legal RAG (PrivFrame)

Research date 2026-11-05. All numbers are from the linked primary pages; anything I could not confirm on a primary page is marked **UNVERIFIED**. Numbered sources are listed at the end.

## 1. Embedding candidates (Korean evidence)

**Benchmark sources used.** The most useful single source is the KURE repo's MTEB(kor, v2) Retrieval leaderboard: 9 Korean tasks (Ko-StrategyQA, AutoRAGRetrieval, MIRACL, PublicHealthQA, Belebele, MrTidy, MultiLongDoc, **LawIRKo**, SQuADKorV1), nDCG@10, open-weight models only [1][2]. The KURE-v1 model card has an older 8-task table (adds XPQA, no LawIRKo) that *does* include `openai/text-embedding-3-large` [3]. The official MTEB(kor, v2) leaderboard page is a JS app and returned no rows to fetch [4]; the community OnAnd0n leaderboard (IR-only, modified datasets) is a secondary cross-check [5]. **No proprietary API model (Gemini, Voyage, Cohere, Upstage, CLOVA) has a public Korean-task score** in any of these.

**LawIRKo is a legal-domain signal**: queries name a law and ask for the provision; positives are single statute/regulation articles (avg ~388 chars) from law.go.kr (dataset `on-and-on/lawgov_ir-ko`, 3,562 provisions, MIT) [6][7]. The hakari NanoMTEB page reports BM25 0.52 vs a 270M dense model 0.65 nDCG@10 on it [6].

| Model | Dims / max tokens | License / access | Price (1M tok) | Korean avg nDCG@10 (9-task) · LawIRKo | Notes |
|---|---|---|---|---|---|
| **nlpai-lab/KURE-v3** | 2048 (MRL 256–2048) / 8192 | Apache-2.0; local or hosted API `kure.kullm.ai` [8] | free weights | **0.8027 · 0.8158** [2] | Base Nemotron-3-Embed-1B; 1.1B params; needs `query:`/`passage:` prefixes [8] |
| nlpai-lab/KURE-v2 (late-interaction) | 128-d/token / 8192 | Apache-2.0, PyLate [9] | free | **0.8160 · 0.7550** [2] | ColBERT-style multi-vector; needs PLAID/multi-vector store, not Chroma [9] |
| nlpai-lab/KURE-v1 | 1024 / 8192 | MIT [3] | free | 0.7616 · 0.7426 [2] | bge-m3 fine-tune, 568M; 8-task card avg 0.6947 vs OpenAI-3-large 0.6167 [3] |
| nlpai-lab/KoE5 | 1024 / 512 (e5-large base) | MIT [1] | free | 0.7337 · 0.7756 [2] | needs `query:`/`passage:` prefixes [1] |
| nvidia/Nemotron-3-Embed-1B-BF16 | 2048 (slice 1024/512) / 32768 | OpenMDW-1.1 + Apache-2.0, "ready for commercial use"; 34 langs incl. Korean [10] | free | 0.7817 · 0.7758 [2] | released 2026-07-16 [10] |
| nvidia/Nemotron-3-Embed-8B-BF16 | 4096 / 32768 | OpenMDW-1.1; commercial OK [11] | free (GPU needed) | 0.8127 · 0.8481 [2] | teacher of 1B model [10] |
| Qwen/Qwen3-Embedding-0.6B / 4B / 8B | 1024 / 2560 / 4096; 32k [12] | Apache-2.0 [12] | free | 0.7140 / 0.7737 / 0.7826 · 0.7526 / 0.7769 / 0.8171 [2] | 100+ languages; MTEB-multilingual 64.33/69.45/70.58 [12] |
| BAAI/bge-m3 | 1024 / 8192 | MIT; dense+sparse+ColBERT [13] | free | 0.7509 · 0.7174 [2] | 568M; also used by CLOVA embedding v2-style stacks |
| dragonkue/snowflake-arctic-embed-l-v2.0-ko | 1024 / 8192 (trained ≤1300 tok) | Apache-2.0 [14] | free | 0.7653 · 0.7735 [2] | trained on AI Hub MRC incl. financial/legal docs [14] |
| Snowflake/snowflake-arctic-embed-l-v2.0 | (74 langs; dims via ko card) | Apache-2.0 [15][16] | free | 8-task card: 0.6819 [3] | base of the -ko variant |
| google/embeddinggemma-300m | 768 (MRL 128–768) / 2048 | Gemma license (gated) [17] | free | 0.7254 · 0.6850 [2] | best "tiny offline" option; prompt format `task: search result \| query:` [17] |
| kakaocorp/kanana-2-3b-embedding | 2560 (MRL→128) / 4096 | Kanana Open License (commercial terms not stated) [18] | free | 0.7584 · 0.8057 [2] | Korean+English only; released 2026-10-29 [18] |
| jinaai/jina-embeddings-v5-text-small | 1024 (MRL 32–1024) / 32768 | **CC BY-NC 4.0** [19] | API: pay-per-token, rate not shown [20] | 0.7419 · 0.7483 [2] | Qwen3-0.6B base, 677M, 119 langs [19] |
| jinaai/jina-embeddings-v4 | 2048 single / 128 multi; 32768 | Qwen Research License (non-commercial); Jina says it "cannot license it commercially" even via API [21][20] | — | not on 9-task table | avoid for production |
| jinaai/jina-embeddings-v3 | 1024 (MRL→32) / 8192; 570M [22] | model license not on arXiv page; HF card too large to fetch (**UNVERIFIED**, believed CC BY-NC) | API pay-per-token [20] | 8-task card: 0.6529 [3] | late-chunking support via API [23] |
| intfloat/multilingual-e5-large-instruct | 1024 / **512** | MIT [24] | free | 8-task card: 0.6445 [3] | 512-token cap is limiting for 조문+context |
| Alibaba-NLP/gte-multilingual-base | 768 (128–768) / 8192; 305M | Apache-2.0; dense + sparse output [25] | free | 8-task card: 0.6403 [3] | cheap CPU option |
| microsoft/harrier-oss-v1-0.6b (-270m, -27b) | 1024 / 32768 (27B: 5376-d) | MIT; 94 langs incl. Korean [26] | free | 27B: 0.7667 · **0.8737** (best LawIRKo) [2] | base of Upstage Embed 2 [27] |
| OpenAI text-embedding-3-large / -small | 3072 / 1536 (reducible); max input 8192 [28] | API | $0.13 / $0.02 [29] | 3-large 8-task: 0.6167 (lowest of the strong models) [3]; small: none | MTEB 64.6 / 62.3 [28] |
| Google gemini-embedding-001 | 3072 (128–3072) / 2048, text only [30] | API | $0.15 ($0.075 batch) [31] | none public (**UNVERIFIED**) | — |
| Google gemini-embedding-2 | 3072 (128–3072) / 8192, multimodal; Apr 2026 [30] | API | $0.20 text ($0.10 batch) [31] | none public (**UNVERIFIED**) | space incompatible with -001 → re-embed [30] |
| Voyage voyage-4-large / -4 / -4-lite | 1024 default (256/512/2048) / 32k; int8/binary [32] | API; 200M free tokens [33] | $0.12 / $0.06 / $0.02 [33] | none public; v4 post lists no languages (**UNVERIFIED**) [34] | law is one of 8 eval domains; MoE [34]; voyage-4-nano open-weight Apache-2.0 [34] |
| Voyage voyage-3.5 / voyage-3-large | (older) | API | $0.06 / $0.18 [33] | none public | — |
| Voyage voyage-law-2 | 1024 / 16k; **not labeled multilingual** [32] | API | $0.12 [33] | none | English legal; not recommended for Korean |
| Cohere embed-v4.0 / embed-v5.0-pro / -fast | 1536 (256–1536) / 2048 (256–2048); 128k ctx; multimodal [35] | API (OpenRouter has `cohere/embed-5-pro`) [35] | official page shows no rate; 3rd-party: $0.12 pro / $0.08 fast (**UNVERIFIED**) [36] | none public; Korean in v3/rerank language table [37] | — |
| Upstage solar-embedding-2-query/-passage ("Embed 2") | 1024 / 8k [27] | API; fine-tuned from harrier-oss-v1-0.6b [27] | **$0.02**; legacy Embed $0.10, EOS 2026-12-31 [38] | none public (**UNVERIFIED**) | solar-embedding-1 aliases deprecated 2026-12-31 [27] |
| Naver CLOVA Studio embedding v1 / v2 | 1024 / text 1–500 (v1); 1024 / ~8,192 tok (v2) [39][40] | API (`langchain-naver` ClovaXEmbeddings) [41] | not found on pricing pages (**UNVERIFIED**) | none public | — |
| Mistral mistral-embed | 1024 [42] | API | $0.10/1M per aggregators only (**UNVERIFIED**) [43] | none | no Korean evidence |
| NVIDIA NV-Embed-v2 / llama-nemoretriever-colembed | — | not fetched (**UNVERIFIED**) | — | llama-nemotron-embed-1b-v2: 0.7564 [2] | superseded by Nemotron-3-Embed |

Learned-sparse Korean models exist for hybrid use: KURE-Sparse-base 0.7984, PIXIE-Splade-v1.5 0.7608, splade-ko-v1 0.7526 nDCG@10 [1].

## 2. Reranker candidates

Primary Korean evidence: KURE cross-encoder table (same 9 tasks; candidates = gold + BM25 top-50 negatives; bf16; 8,192 tokens; throughput "PPS" = pairs/s on one RTX A6000 48 GB) [1][44]. **No primary CPU latency measurements exist for Korean rerankers; CPU figures below are my estimates from PPS ratios (UNVERIFIED).**

| Reranker | Params / ctx | License / cost | Korean avg · LawIRKo nDCG@10 | PPS (A6000) |
|---|---|---|---|---|
| Qwen/Qwen3-Reranker-8B / 4B / 0.6B | 8.2B / 4B / 0.6B; 32k; Apache-2.0 [45] | free | 0.8990·0.9013 / 0.8916·0.8766 / 0.8528·0.7865 [44] | 15.0 / 24.3 / 101.0 [44] |
| nlpai-lab/KURE-Reranker-base (Qwen3-1.7B) | 1.7B; 8192 | Apache-2.0 (repo) [1] | 0.8908 · 0.8768 [44] | 56.2 |
| **nlpai-lab/KURE-Reranker-nano** (A.X-Encoder-base) | 149M; 8192 | Apache-2.0; sentence-transformers `CrossEncoder` [46] | 0.8757 · 0.8368 [46] | **482.4** (≈19.8× Qwen3-4B) [46] |
| jinaai/jina-reranker-v3 / v3.5 | 0.6B; 131k ctx; listwise, ≤64 docs [47][48] | **CC BY-NC 4.0**; commercial via API/contact [47][48] | LawIRKo 0.8546 / 0.8545 (avg not given) [44] | 64.0 / 86.7 |
| mixedbread-ai/mxbai-rerank-large-v2 (base-v2 also) | 1.5B; 100+ langs | Apache-2.0 [49] | 0.8607 · 0.8130 [44] | 64.8 |
| BAAI/bge-reranker-v2-m3 | 568M; examples use 512 tok | Apache-2.0 [50] | 0.8518 · 0.7906 [44] | 412.6 |
| BAAI/bge-reranker-v2-gemma | ~2.5B (gemma-2b) | apache-2.0 tag [51] | 0.8099 · 0.7572 [44] | 58.8 |
| Korean fine-tunes: dragonkue/bge-reranker-v2-m3-ko; upskyy/ko-reranker-8k; Dongjin-kr/ko-reranker | 568M / 568M / 560M | — | 0.8199·0.6721 / 0.8054·0.7770 / 0.7861·0.7343 [44] | ~410 / 410 / 488 |
| jinaai/jina-reranker-v2-base-multilingual | 278M; 1024 tok (sliding window) | CC BY-NC 4.0 [52] | not in KURE table; MKQA 54.83 [52] | — |
| jinaai/jina-reranker-m0 (multimodal) | 2.4B; 10,240 tok; 29+ langs | CC BY-NC 4.0 [53] | none | — |
| Cohere rerank-v3.5 / v4.0-pro,fast / **v5.0-pro,fast** | 4k / 32k / 32k ctx [35]; Korean in language table [37] | search unit = 1 query × ≤100 docs (docs >500 tok split) [54]; $2.00/1k searches per 3rd-party (**UNVERIFIED**) [55] | none public | API |
| Voyage rerank-2.5 / -2.5-lite (rerank-3/-3-lite newer) | 8k query, 32k query+doc, ≤1,000 docs, 600K-token cap; multilingual [56] | $0.05 / $0.02 per 1M tokens [33] | none public | API |
| FlashRank ms-marco-MultiBERT-L-12 | ~150 MB ONNX, CPU-only, 100+ langs | Apache-2.0; "benchmarking TBD" [57] | none | CPU |
| LLM-as-reranker (RankGPT-style listwise) | any OpenRouter LLM | per-token | none Korean; LegalBench-RAG found a reranker *hurt* legal precision [58][59] | slow |
| Upstage / Naver rerankers | — | **none found** on Upstage pricing page or CLOVA Studio docs [38][39] | — | — |

Qwen's own MMTEB-R comparison (top-100 from Qwen3-Embedding-0.6B): Qwen3-Reranker-0.6B 66.36 vs bge-reranker-v2-m3 58.36 vs jina-v2-base 63.73 [45].

## 3. Korean legal retrieval benchmarks / datasets

| Resource | What it is | Retrieval relevance | License / URL |
|---|---|---|---|
| **KBL** (EMNLP 2024 Findings) | 7 knowledge + 4 reasoning tasks + bar exam; closed-book vs RAG over Korean statutes + precedents [60] | RAG corpus: 220,160 statute articles (Nov-2024 dump) + LBox-Open precedents; `lbox/kbl-rag` has `retrieved_docs` per query; RAG eval via lm-eval-harness branch [61][62] | CC BY-NC [60]; https://github.com/lbox-kr/kbl |
| **KCL** (EACL 2026) | 283 MCQA + 169 essay bar-exam items with 1,103 / 550 aligned precedents and rubrics [63] | precedent alignment usable as case-retrieval gold | CC BY-NC 4.0; HF `lbox/kcl` [63] |
| **LawIRKo** (in MTEB(kor, v2)) | statute-article IR, 3,562 provisions, 1 positive/query [6][7] | directly measures 조문 retrieval; used in KURE/OnAnd0n tables | MIT (law.go.kr open license) [7] |
| **KoBLEX** (EMNLP 2025) | 226 provision-grounded multi-hop legal QA; ParSeR 3-stage retrieval (+37.91 F1 vs standard RAG w/ GPT-4o) [64] | statute retrieval + multi-hop | https://arxiv.org/abs/2509.01324 |
| **LEGAR Bench / LegalSearchLM** (EMNLP 2025) | first large-scale Korean legal *case* retrieval benchmark: 411 crime types, 1.2M candidate cases; Stricter version 169K targets [65] | precedent retrieval | https://arxiv.org/abs/2505.23832 |
| **SearchFireSafety** (ACL 2026) | statute-centric QA on Korean fire-safety law (law.go.kr crawl, 4,467 hierarchy units) [66] | compares BM25 / Qwen3-Emb-0.6B / BGE-M3 / RRF / structure-aware reranking | https://arxiv.org/abs/2604.06173 |
| LBox Open | ~150k precedent corpus; casename/statute classification; LJP; summarization [67] | precedent corpus for domain adaptation / eval | CC BY-NC 4.0 [67] |
| KLAID | 161k LJP rows with cited statutes (177 labels) [68] | fact→statute mapping | CC BY-NC-ND 4.0 [68] |
| AutoRAGRetrieval (MTEB) | Korean PDF-derived retrieval across finance, public, healthcare, **law**, commerce [3] | law slice | via MTEB |
| AI Hub | 법률/규정(판결서·약관) 텍스트 분석 데이터 [69]; 민사법/형사법/행정법 LLM 사전학습·Instruction 데이터 [70][71][72]; 생성형AI 법률 지식기반 관계 데이터 [73] | QA pairs over 판결문/법령; useful for eval-set synthesis | AI Hub terms (login) |
| HF `LLM-OS-Models/korean-legal-retrieval-source-native-250k` | 250k legal retrieval training pairs; card warns of overlap with LawIRKo/AutoRAG legal slices → results "target-adapted", not zero-shot [74] | training only | — |
| "KoLEG", "LegalBench-KR", "KoLawBench" | **not found** in searches (UNVERIFIED/nonexistent) [75] | — | — |
| MLEB (Massive Legal Embedding Benchmark) | US/UK/EU/AU/IE/SG only — **no Korean** [76] | — | — |

## 4. Vector store & hybrid search

**Chroma 1.x (your current store).** Local/single-node full-text is `where_document` `$contains/$not_contains/$regex/$not_regex`, case-sensitive, filter-only, no BM25 ranking [77]. Sparse vectors + `Search().rank(Rrf([Knn dense, Knn sparse]))` exist, but "Search API is available in Chroma Cloud only. Future support on single-node Chroma is planned" [78][79]. `langchain-chroma` 1.1.0 (2025-12-12) exposes `Chroma.hybrid_search(Search)` [80][81], which therefore only works against Cloud today. Chroma's BM25 embedding function tokenizes with snowballstemmer (no Korean) and uses a fixed `avg_doc_length` [82]. Latest `chromadb` 1.5.9 (2026-05-05) [83].

| Store | Native hybrid | Korean tokenization | Local/embedded | LangChain 1.x | Verdict |
|---|---|---|---|---|---|
| **Qdrant** | prefetch dense + sparse, RRF (configurable k, weighted since 1.17) or DBSF; docs warn against alpha-blending raw scores [84] | fastembed `Qdrant/bm25`: 18 stemmer languages, **no Korean** → `disable_stemmer=True` + custom tokens [85]; full-text "multilingual" tokenizer uses charabia, but Qdrant's build enables only greek/hebrew/thai/chinese features — **no Korean (Lindera ko-dic) segmentation** [86][87] | `QdrantClient(":memory:")` or `path=` [88] | `langchain-qdrant` `RetrievalMode.HYBRID` + `FastEmbedSparse` [88] | best "single engine" migration target; feed Kiwi-tokenized text to BM25 |
| **LanceDB** | `query_type="hybrid"`, `RRFReranker` default, Cohere/CrossEncoder rerankers; works on local path [89] | native FTS `base_tokenizer`: `simple`, `icu`, `jieba`, **`lindera/ko-dic`** (needs model files under `LANCE_LANGUAGE_MODEL_HOME`), `ngram`; stemming list excludes Korean; phrase queries need `with_position=True` [90] | single-file, embedded | `langchain-community` `LanceDB` accepts `query_type='fts'/'hybrid'` via kwargs, but builds FTS only on `text_key` and never assigns the reranked query back (bug) [91] | good embedded alternative; use raw lancedb for hybrid |
| **pgvector** | `langchain-postgres` `PGVectorStore` + `HybridSearchConfig` (tsvector, `tsv_lang` default `pg_catalog.english`, `reciprocal_rank_fusion` rrf_k=60 or `weighted_sum_ranking`) [92] | Postgres has no Korean parser: use `textsearch_ko` (mecab-ko) [93] or ParadeDB `pg_search` `korean_lindera` (KoDic) tokenizer [94]/v2 `lindera`, `icu` [95] | Docker | `langchain-postgres` [96] | heavier ops; fine if Postgres already exists |
| **Milvus** | server: `BM25BuiltInFunction` + weighted/RRF ranker [97] | Milvus ≥2.6 ships `lindera` `ko-dic` with `korean_stop_tags` filter [98] | **Milvus Lite: no BM25/full-text, Flat index only** [97][99] | `langchain-milvus` [97] | Lite insufficient; standalone needs Docker |
| **Weaviate** | hybrid `alpha`, `relativeScoreFusion` (default ≥1.24) / `rankedFusion`, BM25F [100] | `kagome_kr` (mecab-ko-dic) with `ENABLE_TOKENIZER_KAGOME_KR=true` [101] | Embedded Weaviate (Linux/macOS, experimental; env vars passable) [102] | `langchain-weaviate` `alpha` kwarg [103] | viable; heavier binary |
| **OpenSearch** | `hybrid` query + search pipeline (normalization / score-ranker processors) [104] | `analysis-nori` plugin "integrates Lucene nori analysis module" [105] | Docker | `langchain-community` `OpenSearchVectorSearch(search_type="hybrid_search", search_pipeline=...)`; mapping sets no analyzer, so create the index yourself [106] | best Korean BM25 quality; most ops |

**Korean BM25 tokenization (store-agnostic).** `kiwipiepy` 0.24.1 (2026-10-20, Apache-2.0) ships `macosx_11_0_arm64` abi3 wheels; user dictionaries supported; CoNg model warns it may be slow/erroneous on Apple Silicon (use the default model) [107][108]. `python-mecab-ko` (BSD-3) [109]; KoNLPy needs a JDK [110]. `bm25s` (MIT): NumPy BM25, custom tokenizer, `save/load` with mmap, Lucene variant default [111]. LangChain `BM25Retriever.from_documents(preprocess_func=kiwi_tokens)` wraps `rank_bm25` (default split on whitespace) [112].

**Recommendation.** Keep Chroma for dense + metadata (no migration cost for a capstone) and add **application-level hybrid**: `bm25s`/`BM25Retriever` over Kiwi morphemes (add legal terms like 개인정보처리자, 영상정보처리기기 to the user dictionary) fused with the Chroma retriever via `EnsembleRetriever` (weighted RRF) [113]. Migrate to **Qdrant local** only if you want sparse vectors stored next to dense vectors with server-side RRF/DBSF and multi-stage rescoring [84]; LanceDB is the lighter alternative.

## 5. Chunking for legal text

| Technique | Evidence | LangChain 1.x class |
|---|---|---|
| **Hierarchy-aware units (조/항/호) + title prefix** | SearchFireSafety segments Korean statutes "based on native legal hierarchies (Article, Paragraph, Item)", prepends the law title, keeps a Document ID; structure-aware reranking via citation links lifted R@10 (BGE-M3 53.77→54.70, Qwen3-0.6B 48.70→53.26); RRF did not consistently help [66]. LawIRKo positives are single articles [6]. | `RecursiveCharacterTextSplitter` is wrong for this; write a regex splitter on `제\d+조(의\d+)?` / `①②` |
| **Parent-document / small-to-big** | LegalBench-RAG: 500-char RCTS gave best precision; naive fixed chunks higher recall at k≥4; Cohere rerank-english-v3.0 lowered P@1 (6.41→6.13) [59]; the benchmark targets minimal, precise spans [58] | `langchain_classic.retrievers.ParentDocumentRetriever` (child/parent splitters) [114]; `MultiVectorRetriever` [115]; both moved to `langchain-classic` in 1.0 [116] |
| **Contextual retrieval** (LLM-written 50–100-token context prepended before embedding + BM25) | failure rate −35% (embeddings), −49% (+contextual BM25), −67% (+rerank); top-20 > top-5/10; retrieve 150 → rerank → 20; ≈$1.02 per 1M doc tokens with caching [117] | pre-processing step; any OpenRouter model |
| **Late chunking** | pool chunk embeddings after full-document encoding; gains grow with doc length (NFCorpus 23.46→29.98); needs long-context model; jina v3 API flag [23][118] | custom encode; not for API embeddings |
| **RAPTOR** | recursive summary tree; +20% absolute on QuALITY with GPT-4 [119] | custom; overkill for statutes |
| **Reranking in LangChain** | `ContextualCompressionRetriever(base_compressor=...)` [120]; `CohereRerank` in `langchain_cohere` (model e.g. `rerank-v5.0-pro`) [121]; `FlashrankRerank`, `JinaRerank`, `CrossEncoderReranker` in `langchain_community.document_compressors`; `VoyageAIRerank` in `langchain_voyageai` [122] | — |
| **Hybrid fusion** | `EnsembleRetriever(retrievers, weights, c)` = weighted RRF [113] | `langchain_classic.retrievers` |

## 6. Recommendations

**Embedding.** *Primary:* **KURE-v3** (Apache-2.0, 1.1B, 2048-d with MRL to 1024/512, 8k ctx; best open dense model on MTEB(kor,v2) overall and LawIRKo 0.8158) [2][8]; run on MPS or via its hosted API, keep `query:`/`passage:` prefixes. If 1.1B is too slow on your Mac, **KURE-v1** (568M, 1024-d, MIT; bge-m3 drop-in) [3]. *Alternative (hosted, cheap):* **Upstage Embed 2** ($0.02/1M, 8k ctx, Korean vendor, harrier-0.6B base) [27][38] — but no public Korean benchmark, so run LawIRKo/your own eval first; keep OpenAI 3-large only as the verified-but-weaker fallback [3]. Avoid Jina v4/v5 and jina-reranker-v2/v3 for anything beyond research (non-commercial licenses) [19][21][47][52].

**Reranker.** *Primary (dev/CPU):* **KURE-Reranker-nano** (149M, Apache-2.0, LawIRKo 0.8368, ~480 pairs/s on A6000 → roughly tens of pairs/s on CPU, UNVERIFIED) [46]. *Production:* **Qwen3-Reranker-4B** or KURE-Reranker-base on a GPU box [44][45], or **Cohere rerank-v5.0-pro** (32k ctx, Korean listed) via API [35][37]. Rerank top-50 → keep 8–10; validate on LawIRKo/KBL since LegalBench-RAG shows rerankers can hurt legal precision [59].

**Store/hybrid.** Keep Chroma + Kiwi-BM25 (`bm25s`) + `EnsembleRetriever` RRF; revisit Qdrant local if sparse/learned-sparse (KURE-Sparse) storage or server-side fusion becomes necessary [1][84][88].

**Chunking per corpus** (store `parent_id`, `law_id`, `article_no`, `paragraph_no`, `doc_type`, `cited_articles`, `date` as metadata):
- **Statutes (법·시행령):** 1 chunk = 1 조 with prefix `"개인정보 보호법 > 제4장 > 제25조의2(이동형 영상정보처리기기의 운영 제한)"`; split into 항 children only when the article exceeds ~800 tokens; 별표/부칙 as separate docs [66][6].
- **Precedents:** children = 판시사항, 판결요지, and ~400–500-token windows of 이유; parent = header (사건번호, 법원, 선고일, 참조조문) + 판결요지; add contextual sentence per child [117][59].
- **PIPC guidelines (안내서):** split on numbered headings (목차 path as prefix), 500–800 tokens, overlap 0–50; keep tables intact.
- **PIPC decisions / dispute mediation:** 1 chunk per 사건 (개요·판단·결정 사이 split only if >1,000 tokens), metadata `cited_articles` for cross-ref boosting [66].
- **Similar-case notes:** whole note + LLM context; tag `issue_type` to match your per-issue query generator.

**Offline/local option for tests.** Keep the hash "fixture" embedding for unit tests; for integration tests use **EmbeddingGemma-300m** (768-d, Gemma license) or bge-m3/KURE-v1 (MIT) through sentence-transformers on MPS [17][13], **KURE-Reranker-nano** as `CrossEncoder` [46], and `bm25s`+`kiwipiepy` for BM25 [107][111].

**Cost estimate (index 50k chunks × 400 tok = 20M tokens; 200 queries/day ≈ 12k query tokens/day, negligible for every API).**

| Component | Option | One-time index | Monthly (200 q/day) |
|---|---|---|---|
| Embedding | KURE-v3 / KURE-v1 local | $0 (compute: order of 1–3 h on Apple Silicon, **estimate, UNVERIFIED**) | $0 |
| | Upstage Embed 2 $0.02/1M [38] | $0.40 | <$0.01 |
| | OpenAI 3-small / 3-large [29] | $0.40 / $2.60 | <$0.01 |
| | Gemini-embedding-001 / -2 [31] | $3.00 / $4.00 (batch $1.50 / $2.00) | <$0.01 |
| | Voyage voyage-4-large / -lite [33] | $2.40 / $0.40 → **$0** inside 200M free tokens | $0 |
| | Cohere embed-v5-pro (3rd-party $0.12, UNVERIFIED) [36] | ~$2.40 | <$0.01 |
| Contextual-retrieval contexts | cheap LLM, ~$1/M doc tokens (Anthropic figure) [117] | ~$20 | $0 |
| Reranker | KURE-Reranker-nano / bge-reranker-v2-m3 local | $0 | $0 |
| | Voyage rerank-2.5 ($0.05/1M; ~23k tok per 50-doc query) [33][56] | — | ≈ $7 (lite ≈ $3) |
| | Cohere rerank ($2/1k searches, 3rd-party, UNVERIFIED) [55] | — | ≈ $12 |

Net: a fully local stack (KURE-v3 or KURE-v1 + KURE-Reranker-nano + bm25s/Kiwi + Chroma) costs $0 in API fees; the cheapest hosted alternative with Korean vendor support (Upstage Embed 2 + Voyage rerank-2.5-lite) is under $5/month at your volumes.

---

### Sources
[1] https://github.com/nlpai-lab/KURE
[2] https://github.com/nlpai-lab/KURE/blob/main/eval/dense/results/README.md
[3] https://huggingface.co/nlpai-lab/KURE-v1
[4] https://mteb-leaderboard.hf.space/benchmark/MTEB(kor,%20v2)
[5] https://github.com/OnAnd0n/ko-embedding-leaderboard
[6] https://hakari-bench-leaderboard.hf.space/docs/benchmark-tasks/NanoMTEB-Korean/lawir_ko
[7] https://huggingface.co/datasets/on-and-on/lawgov_ir-ko
[8] https://huggingface.co/nlpai-lab/KURE-v3
[9] https://huggingface.co/nlpai-lab/KURE-v2
[10] https://huggingface.co/nvidia/Nemotron-3-Embed-1B-BF16
[11] https://huggingface.co/nvidia/Nemotron-3-Embed-8B-BF16
[12] https://huggingface.co/Qwen/Qwen3-Embedding-8B
[13] https://huggingface.co/BAAI/bge-m3
[14] https://huggingface.co/dragonkue/snowflake-arctic-embed-l-v2.0-ko
[15] https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0
[16] https://arxiv.org/abs/2412.04506
[17] https://huggingface.co/google/embeddinggemma-300m
[18] https://huggingface.co/kakaocorp/kanana-2-3b-embedding
[19] https://jina.ai/news/jina-embeddings-v5-text-distilling-4b-quality-into-sub-1b-multilingual-embeddings
[20] https://jina.ai/embeddings/
[21] https://huggingface.co/jinaai/jina-embeddings-v4
[22] https://arxiv.org/abs/2409.10173
[23] https://jina.ai/news/late-chunking-in-long-context-embedding-models/
[24] https://huggingface.co/intfloat/multilingual-e5-large-instruct
[25] https://huggingface.co/Alibaba-NLP/gte-multilingual-base
[26] https://huggingface.co/microsoft/harrier-oss-v1-0.6b
[27] https://console.upstage.ai/docs/capabilities/embeddings
[28] https://developers.openai.com/api/docs/guides/embeddings
[29] https://developers.openai.com/api/docs/pricing
[30] https://aistudio.google.com/docs/embeddings
[31] https://aistudio.google.com/docs/pricing
[32] https://docs.voyageai.com/docs/embeddings
[33] https://docs.voyageai.com/docs/pricing
[34] https://blog.voyageai.com/2026/01/15/voyage-4/
[35] https://docs.cohere.com/docs/models
[36] https://www.eesel.ai/blog/cohere-embed-5-pricing (secondary)
[37] https://docs.cohere.com/docs/rerank-overview
[38] https://www.upstage.ai/pricing
[39] https://api.ncloud-docs.com/docs/clovastudio-embedding
[40] https://api.ncloud-docs.com/docs/clovastudio-embeddingv2
[41] https://docs.langchain.com/oss/python/integrations/text_embedding/naver
[42] https://docs.mistral.ai/inference/embeddings/text_embeddings
[43] https://www.getmaxim.ai/bifrost/llm-cost-calculator/provider/mistral/model/mistral-embed (secondary)
[44] https://github.com/nlpai-lab/KURE/blob/main/eval/cross-encoder/results/README.md
[45] https://huggingface.co/Qwen/Qwen3-Reranker-0.6B
[46] https://huggingface.co/nlpai-lab/KURE-Reranker-nano
[47] https://huggingface.co/jinaai/jina-reranker-v3
[48] https://huggingface.co/jinaai/jina-reranker-v3.5
[49] https://huggingface.co/mixedbread-ai/mxbai-rerank-large-v2
[50] https://huggingface.co/BAAI/bge-reranker-v2-m3
[51] https://huggingface.co/BAAI/bge-reranker-v2-gemma
[52] https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual
[53] https://huggingface.co/jinaai/jina-reranker-m0
[54] https://cohere.com/pricing
[55] https://metronome.com/pricing-index/cohere (secondary)
[56] https://docs.voyageai.com/docs/reranker
[57] https://github.com/PrithivirajDamodaran/FlashRank
[58] https://arxiv.org/abs/2304.09542
[59] https://arxiv.org/html/2408.10343
[60] https://arxiv.org/abs/2410.08731
[61] https://github.com/lbox-kr/kbl
[62] https://huggingface.co/datasets/lbox/kbl-rag
[63] https://arxiv.org/abs/2512.24572
[64] https://arxiv.org/abs/2509.01324
[65] https://arxiv.org/abs/2505.23832
[66] https://arxiv.org/html/2604.06173
[67] https://github.com/lbox-kr/lbox-open
[68] https://huggingface.co/datasets/lawcompany/KLAID
[69] https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=580
[70] https://www.aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100&dataSetSn=71841
[71] https://www.aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100&dataSetSn=71848
[72] https://aihub.or.kr/aihubdata/data/view.do?currMenu115=&topMenu100=&aihubDataSerealm=&aihubDataSe=data&dataSetSn=71847
[73] https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71722
[74] https://huggingface.co/datasets/LLM-OS-Models/korean-legal-retrieval-source-native-250k
[75] https://github.com/embeddings-benchmark/mteb/pull/5496 (LawIRKo listed as an MTEB legal task)
[76] https://arxiv.org/abs/2510.19365
[77] https://docs.trychroma.com/docs/querying-collections/full-text-search.md
[78] https://docs.trychroma.com/cloud/search-api.md
[79] https://docs.trychroma.com/cloud/schema/sparse-vector-search.md
[80] https://reference.langchain.com/python/langchain-chroma/vectorstores/Chroma/hybrid_search
[81] https://pypi.org/project/langchain-chroma/
[82] https://docs.trychroma.com/integrations/embedding-models/chroma-bm25.md
[83] https://pypi.org/project/chromadb/
[84] https://qdrant.tech/documentation/concepts/hybrid-queries/
[85] https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/sparse/bm25.py
[86] https://raw.githubusercontent.com/qdrant/qdrant/master/lib/segment/Cargo.toml
[87] https://github.com/meilisearch/charabia
[88] https://docs.langchain.com/oss/python/integrations/vectorstores/qdrant
[89] https://docs.lancedb.com/search/hybrid-search.md
[90] https://docs.lancedb.com/search/full-text-search.md
[91] https://raw.githubusercontent.com/langchain-ai/langchain-community/main/libs/community/langchain_community/vectorstores/lancedb.py
[92] https://reference.langchain.com/python/langchain-postgres/v2/hybrid_search_config/HybridSearchConfig
[93] https://hub.docker.com/r/karoid/postgres-textsearch_ko
[94] https://api.pgxn.org/src/pg_search/pg_search-0.19.4/docs/documentation/indexing/tokenizers.mdx
[95] https://api.pgxn.org/src/pg_search/pg_search-0.25.5/docs/documentation/tokenizers/search-tokenizer.mdx
[96] https://docs.langchain.com/oss/python/integrations/vectorstores/pgvector
[97] https://docs.langchain.com/oss/python/integrations/vectorstores/milvus
[98] https://milvus.io/docs/lindera-tokenizer.md
[99] https://milvus.io/docs/milvus_lite.md
[100] https://docs.weaviate.io/weaviate/search/hybrid
[101] https://docs.weaviate.io/weaviate/config-refs/collections
[102] https://docs.weaviate.io/deploy/installation-guides/embedded
[103] https://docs.langchain.com/oss/python/integrations/vectorstores/weaviate
[104] https://docs.opensearch.org/latest/query-dsl/compound/hybrid/
[105] https://raw.githubusercontent.com/opensearch-project/OpenSearch/main/plugins/analysis-nori/build.gradle
[106] https://raw.githubusercontent.com/langchain-ai/langchain-community/main/libs/community/langchain_community/vectorstores/opensearch_vector_search.py
[107] https://pypi.org/project/kiwipiepy/
[108] https://github.com/bab2min/kiwipiepy
[109] https://github.com/jonghwanhyeon/python-mecab-ko
[110] https://konlpy.org/en/latest/install/
[111] https://github.com/xhluca/bm25s
[112] https://raw.githubusercontent.com/langchain-ai/langchain-community/main/libs/community/langchain_community/retrievers/bm25.py
[113] https://reference.langchain.com/python/langchain-classic/retrievers/ensemble/EnsembleRetriever
[114] https://reference.langchain.com/python/langchain-classic/retrievers/parent_document_retriever/ParentDocumentRetriever
[115] https://reference.langchain.com/python/langchain-classic/retrievers/multi_vector/MultiVectorRetriever
[116] https://docs.langchain.com/oss/python/releases/langchain-v1
[117] https://www.anthropic.com/news/contextual-retrieval
[118] https://arxiv.org/abs/2409.04701
[119] https://arxiv.org/abs/2401.18059
[120] https://reference.langchain.com/python/langchain-classic/retrievers/contextual_compression/ContextualCompressionRetriever
[121] https://docs.cohere.com/docs/rerank-on-langchain
[122] https://github.com/langchain-ai/docs/pull/1196/files

**Key caveats:** (a) Proprietary embedding APIs have no public Korean-task scores; only OpenAI 3-large has one (and it trails Korean open models by ~8 nDCG points on the 8-task set). (b) Chroma hybrid/sparse search is Cloud-only as of the fetched docs. (c) Cohere, Mistral and CLOVA prices could not be read from official pages in this session. (d) CPU reranker latencies are not published anywhere I found; the A6000 pairs/s ratios are the only primary relative-speed evidence.
