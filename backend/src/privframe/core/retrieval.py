"""Index and search over normalized corpus documents: the engine behind the search_law and get_document tools.

Every moving part is picked by a profile's rag.* parts (configs/pipeline/catalog.yaml) and looked up in a registry
below, so a new chunker, embedding model, tokenizer, store or reranker is one implementation plus one entry.
An index is immutable and named by a fingerprint of its documents and index-time settings; query-time settings
(search mode, reranker, k) can change without rebuilding.
"""
import hashlib
import json
import re
import shutil
import time
import zlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .util import now_iso
from .corpus import CorpusStore, Document
from .pipeline import Candidate, ResolvedProfile

INDEX_FORMAT = 1
RRF_K = 60
RERANK_POOL = 20


class RetrievalError(RuntimeError):
    pass


# ---------- what a profile asks for ----------


@dataclass(frozen=True)
class RetrievalSpec:
    chunking: str = "article-prefix"
    embedding_adapter: str = "sentence-transformers"
    embedding_ref: str = "nlpai-lab/KURE-v1"
    embedding_options: tuple[tuple[str, str], ...] = ()  # e.g. (("query_prefix", "query: "),)
    tokenizer: str = "kiwi"
    store: str = "chroma"
    mode: str = "hybrid-rrf"  # query time
    reranker_adapter: str = "cross-encoder"  # query time
    reranker_ref: str | None = "nlpai-lab/KURE-Reranker-nano"
    top_k: int = 8  # query time

    @classmethod
    def from_profile(cls, profile: ResolvedProfile) -> "RetrievalSpec":
        part = lambda slot: profile.part(slot).candidate  # noqa: E731
        return cls(
            chunking=part("rag.chunking").id,
            embedding_adapter=part("rag.embedding").adapter,
            embedding_ref=part("rag.embedding").ref or part("rag.embedding").id,
            embedding_options=tuple(sorted((str(k), str(v)) for k, v in part("rag.embedding").options.items())),
            tokenizer=part("rag.bm25_tokenizer").id,
            store=part("rag.store").id,
            mode=part("rag.retrieval").id,
            reranker_adapter=part("rag.reranker").adapter,
            reranker_ref=part("rag.reranker").ref,
            top_k=int(profile.params.get("rag.top_k", 8)),
        )

    def index_fields(self) -> dict[str, Any]:
        return {
            "chunking": self.chunking,
            "embedding_adapter": self.embedding_adapter,
            "embedding_ref": self.embedding_ref,
            "embedding_options": dict(self.embedding_options),
            "tokenizer": self.tokenizer,
            "store": self.store,
        }


# ---------- chunking ----------


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    unit_id: str
    label: str
    prefix: str  # "개인정보 보호법 › 제25조의2(…)": travels with the text so a fragment is never read without its source
    text: str
    kind: str
    authority: int | None
    citation: str

    @property
    def indexed_text(self) -> str:
        return f"{self.prefix}\n{self.text}"


BREAK = re.compile(r"\n+|(?<=[.다])\s+(?=[①-⑳\d가-힣(])|\s+(?=[①-⑳])")


def split_text(text: str, max_chars: int, overlap: int = 0) -> list[str]:
    """Split at paragraph, sentence or 항(①②…) boundaries when one exists before the limit, else at the limit."""
    pieces, start = [], 0
    while len(text) - start > max_chars:
        window = text[start : start + max_chars]
        cut = max((m.start() for m in BREAK.finditer(window) if m.start() > max_chars // 3), default=None)
        end = start + (cut if cut is not None else max_chars)
        pieces.append(text[start:end].strip())
        start = end if cut is not None else max(end - overlap, start + 1)
    pieces.append(text[start:].strip())
    return [p for p in pieces if p]


def chunk_units(document: Document, max_chars: int = 1200) -> list[Chunk]:
    """One chunk per unit (article, page, field); a long unit is split and every piece keeps the unit's prefix."""
    record, chunks = document.record, []
    for unit in document.units:
        for number, piece in enumerate(split_text(unit.text, max_chars)):
            chunks.append(Chunk(f"{record.doc_id}::{unit.unit_id}::{number}", record.doc_id, unit.unit_id, unit.label, f"{record.cite} › {unit.label}", piece, record.kind, record.authority, record.cite))
    return chunks


def chunk_fixed(document: Document, size: int = 1200, overlap: int = 150) -> list[Chunk]:
    """Fixed windows over the whole text, ignoring unit boundaries (the pre-2.0 setting, kept for comparison)."""
    record, chunks = document.record, []
    starts, parts, offset = [], [], 0
    for unit in document.units:
        starts.append((offset, unit))
        parts.append(unit.text)
        offset += len(unit.text) + 1
    text = "\n".join(parts)
    for number, begin in enumerate(range(0, max(len(text), 1), size - overlap)):
        piece = text[begin : begin + size].strip()
        if not piece:
            continue
        unit = next(u for o, u in reversed(starts) if o <= begin)
        chunks.append(Chunk(f"{record.doc_id}::w{number}", record.doc_id, unit.unit_id, unit.label, record.cite, piece, record.kind, record.authority, record.cite))
    return chunks


CHUNKERS: dict[str, Callable[[Document], list[Chunk]]] = {"article-prefix": chunk_units, "fixed-1200": chunk_fixed}


# ---------- embedding ----------


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def resolve_device(device: str = "auto") -> str:
    if device != "auto":
        return device
    try:
        import torch

        return "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class SentenceTransformerEmbedder:
    """A Hugging Face embedding model run locally. `options` may give query_prefix / passage_prefix for models
    that need them (set per candidate when the model card asks for it)."""

    def __init__(self, ref: str, device: str = "auto", options: dict[str, Any] | None = None):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RetrievalError("sentence-transformers가 없습니다. `uv sync --group models`를 실행하세요.") from error
        options = options or {}
        self.device = resolve_device(device)
        self.model = SentenceTransformer(ref, device=self.device)
        self.query_prefix, self.passage_prefix = options.get("query_prefix", ""), options.get("passage_prefix", "")

    def _encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True, batch_size=8, show_progress_bar=False).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode([self.passage_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self.query_prefix + text])[0]


class HashEmbedder:
    """Deterministic character-bigram vectors. No model, no download: for tests and offline wiring checks only."""

    def __init__(self, ref: str = "fixture-hash", device: str = "cpu", options: dict[str, Any] | None = None, dimensions: int = 256):
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        compact = re.sub(r"\s+", "", text)
        for i in range(len(compact) - 1):
            vector[zlib.crc32(compact[i : i + 2].encode("utf-8")) % self.dimensions] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


EMBEDDERS: dict[str, Callable[..., Embedder]] = {"sentence-transformers": SentenceTransformerEmbedder, "fixture-hash": HashEmbedder}


# ---------- BM25 tokenizers ----------

_KIWI = None
KIWI_TAGS = ("N", "VV", "VA", "XR", "SL", "SH", "SN")  # nouns, verbs, adjectives, roots, foreign words, numbers


def kiwi_tokens(text: str) -> list[str]:
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi

        _KIWI = Kiwi()
    return [t.form for t in _KIWI.tokenize(text) if t.tag.startswith(KIWI_TAGS)]


def whitespace_tokens(text: str) -> list[str]:
    return text.split()


TOKENIZERS: dict[str, Callable[[str], list[str]]] = {"kiwi": kiwi_tokens, "whitespace": whitespace_tokens}


# ---------- vector store ----------


class ChromaStore:
    def __init__(self, path: Path):
        import chromadb

        self.client = chromadb.PersistentClient(path=str(path), settings=chromadb.Settings(anonymized_telemetry=False))
        self.collection = self.client.get_or_create_collection(name="chunks", embedding_function=None, configuration={"hnsw": {"space": "cosine"}})

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for start in range(0, len(chunks), 128):
            batch = chunks[start : start + 128]
            self.collection.add(
                ids=[c.chunk_id for c in batch],
                embeddings=vectors[start : start + 128],
                metadatas=[{"doc_id": c.doc_id, "kind": c.kind, "authority": c.authority if c.authority is not None else 99} for c in batch],
            )

    def count(self) -> int:
        return self.collection.count()

    def query(self, vector: list[float], n: int, kinds: list[str] | None, max_authority: int | None) -> list[tuple[str, float]]:
        conditions: list[dict[str, Any]] = []
        if kinds:
            conditions.append({"kind": {"$in": list(kinds)}})
        if max_authority is not None:
            conditions.append({"authority": {"$lte": max_authority}})
        where = None if not conditions else conditions[0] if len(conditions) == 1 else {"$and": conditions}
        result = self.collection.query(query_embeddings=[vector], n_results=max(1, min(n, self.count())), where=where, include=["distances"])
        return [(chunk_id, 1.0 - float(distance)) for chunk_id, distance in zip(result["ids"][0], result["distances"][0])]


STORES: dict[str, Callable[[Path], ChromaStore]] = {"chroma": ChromaStore}


# ---------- rerankers ----------


class CrossEncoderReranker:
    def __init__(self, ref: str, device: str = "auto"):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as error:
            raise RetrievalError("sentence-transformers가 없습니다. `uv sync --group models`를 실행하세요.") from error
        self.model = CrossEncoder(ref, device=resolve_device(device))

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self.model.predict([(query, t) for t in texts], show_progress_bar=False)]


RERANKERS: dict[str, Callable[..., CrossEncoderReranker]] = {"cross-encoder": CrossEncoderReranker}
MODES = ("hybrid-rrf", "dense", "bm25")


def support(slot: str, candidate: Candidate) -> tuple[bool, str]:
    """Can this backend run the catalog candidate today? Used by the Lab's profile check (core/support.py) and the smoke report."""
    table: dict[str, tuple[Any, str]] = {
        "rag.chunking": (candidate.id in CHUNKERS, "청커 미구현"),
        "rag.embedding": (candidate.adapter in EMBEDDERS, f"'{candidate.adapter}' 임베딩 어댑터 미구현"),
        "rag.retrieval": (candidate.id in MODES, "검색 방식 미구현"),
        "rag.bm25_tokenizer": (candidate.id in TOKENIZERS, "토크나이저 미구현"),
        "rag.reranker": (candidate.adapter in RERANKERS or candidate.adapter == "none", f"'{candidate.adapter}' 리랭커 어댑터 미구현"),
        "rag.store": (candidate.id in STORES, "저장소 어댑터 미구현"),
    }
    if slot not in table:
        return True, "에이전트 단계의 설정"
    ok, reason = table[slot]
    return bool(ok), "" if ok else reason


# ---------- index ----------


@dataclass
class Evidence:
    chunk_id: str
    doc_id: str
    unit_id: str
    label: str
    citation: str
    kind: str
    authority: int | None
    text: str
    score: float
    ranks: dict[str, float] = field(default_factory=dict)


def fingerprint(documents: list[Document], spec: RetrievalSpec) -> str:
    body = {
        "format": INDEX_FORMAT,
        "spec": spec.index_fields(),
        "docs": sorted([d.record.doc_id, d.record.sha256, d.parser, d.record.cite, d.record.kind, d.record.authority] for d in documents),
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


class Index:
    def __init__(self, directory: Path, spec: RetrievalSpec, *, device: str = "auto", embedder: Embedder | None = None):
        self.directory = directory
        self.spec = spec
        self.device = device
        self.meta: dict[str, Any] = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        self.chunks: list[Chunk] = [Chunk(**json.loads(line)) for line in (directory / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line]
        self._by_id = {c.chunk_id: c for c in self.chunks}
        self._embedder = embedder
        self._store: ChromaStore | None = None
        self._bm25: Any = None
        self._rerankers: dict[str, CrossEncoderReranker] = {}

    @property
    def fingerprint(self) -> str:
        return self.meta["fingerprint"]

    def _dense(self, query: str, n: int, kinds: list[str] | None, max_authority: int | None) -> list[str]:
        if self._embedder is None:
            self._embedder = EMBEDDERS[self.spec.embedding_adapter](self.spec.embedding_ref, self.device, dict(self.spec.embedding_options))
        if self._store is None:
            self._store = STORES[self.spec.store](self.directory / "store")
        return [chunk_id for chunk_id, _ in self._store.query(self._embedder.embed_query(query), n, kinds, max_authority)]

    def _lexical(self, query: str, n: int, kinds: list[str] | None, max_authority: int | None) -> list[str]:
        import bm25s
        import numpy as np

        tokens = TOKENIZERS[self.spec.tokenizer](query)
        if not tokens:
            return []
        if self._bm25 is None:
            self._bm25 = bm25s.BM25.load(str(self.directory / "bm25"), show_progress=False, verbosity=0)
        allowed = np.array([(not kinds or c.kind in kinds) and (max_authority is None or (c.authority is not None and c.authority <= max_authority)) for c in self.chunks], dtype="float32")
        if not allowed.any():
            return []
        found, scores = self._bm25.retrieve([tokens], k=min(n, len(self.chunks)), show_progress=False, weight_mask=allowed)
        return [self.chunks[int(i)].chunk_id for i, s in zip(found[0], scores[0]) if s > 0]

    def search(self, query: str, *, k: int | None = None, kinds: list[str] | None = None, max_authority: int | None = None, mode: str | None = None, rerank: bool = True) -> list[Evidence]:
        if not query.strip():
            raise RetrievalError("검색어가 비어 있습니다")
        mode = mode or self.spec.mode
        if mode not in MODES:
            raise RetrievalError(f"검색 방식 '{mode}'은(는) 구현되지 않았습니다 (쓸 수 있는 것: {', '.join(MODES)})")
        k = k or self.spec.top_k
        pool = max(k * 4, RERANK_POOL)
        dense = self._dense(query, pool, kinds, max_authority) if mode in ("hybrid-rrf", "dense") else []
        lexical = self._lexical(query, pool, kinds, max_authority) if mode in ("hybrid-rrf", "bm25") else []
        scores: dict[str, float] = {}
        ranks: dict[str, dict[str, float]] = {}
        for name, ranked in (("dense", dense), ("bm25", lexical)):
            for rank, chunk_id in enumerate(ranked, 1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)  # reciprocal rank fusion
                ranks.setdefault(chunk_id, {})[name] = rank
        ordered = sorted(scores, key=lambda c: -scores[c])
        if rerank and self.spec.reranker_adapter != "none" and ordered:
            head = ordered[:RERANK_POOL]
            if self.spec.reranker_adapter not in RERANKERS:
                raise RetrievalError(f"리랭커 어댑터 '{self.spec.reranker_adapter}'은(는) 구현되지 않았습니다")
            key = f"{self.spec.reranker_adapter}:{self.spec.reranker_ref}"
            if key not in self._rerankers:
                self._rerankers[key] = RERANKERS[self.spec.reranker_adapter](self.spec.reranker_ref, self.device)
            for chunk_id, value in zip(head, self._rerankers[key].score(query, [self._by_id[c].indexed_text for c in head])):
                ranks[chunk_id]["rerank"] = value
            ordered = sorted(head, key=lambda c: -ranks[c]["rerank"])
            scores = {c: ranks[c]["rerank"] for c in ordered}
        out = []
        for chunk_id in ordered[:k]:
            c = self._by_id[chunk_id]
            out.append(Evidence(c.chunk_id, c.doc_id, c.unit_id, c.label, c.citation, c.kind, c.authority, c.text, round(scores[chunk_id], 6), ranks[chunk_id]))
        return out


def build_index(store: CorpusStore, spec: RetrievalSpec, *, device: str = "auto", embedder: Embedder | None = None, kinds: list[str] | None = None) -> Index:
    """Build the index for the normalized documents (or reuse the one with the same fingerprint)."""
    documents = [d for d in store.documents(kinds) if d.record.index]
    if not documents:
        raise RetrievalError("색인할 표준 문서가 없습니다. 먼저 fetch와 parse를 실행하세요.")
    for name, registry in (("chunking", CHUNKERS), ("embedding_adapter", EMBEDDERS), ("tokenizer", TOKENIZERS), ("store", STORES)):
        if getattr(spec, name) not in registry and not (name == "embedding_adapter" and embedder is not None):
            raise RetrievalError(f"{name} '{getattr(spec, name)}'은(는) 이 백엔드에 구현되지 않았습니다 (쓸 수 있는 것: {', '.join(registry)})")
    digest = fingerprint(documents, spec)
    directory = store.index_dir / digest
    if (directory / "meta.json").exists():
        return Index(directory, spec, device=device, embedder=embedder)
    if directory.exists():  # an interrupted build left no meta.json; start over
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    import bm25s

    started = time.perf_counter()
    chunks = [c for d in documents for c in CHUNKERS[spec.chunking](d)]
    if len({c.chunk_id for c in chunks}) != len(chunks):
        raise RetrievalError("청크 ID가 겹칩니다")
    embedder = embedder or EMBEDDERS[spec.embedding_adapter](spec.embedding_ref, device, dict(spec.embedding_options))
    loaded = time.perf_counter()
    vectors = []
    for start in range(0, len(chunks), 32):
        vectors.extend(embedder.embed_documents([c.indexed_text for c in chunks[start : start + 32]]))
    embedded = time.perf_counter()
    STORES[spec.store](directory / "store").add(chunks, vectors)
    lexical = bm25s.BM25()
    lexical.index([TOKENIZERS[spec.tokenizer](c.indexed_text) or ["∅"] for c in chunks], show_progress=False)
    lexical.save(str(directory / "bm25"), show_progress=False)
    (directory / "chunks.jsonl").write_text("".join(json.dumps(asdict(c), ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
    meta = {
        "fingerprint": digest,
        "format": INDEX_FORMAT,
        "spec": spec.index_fields(),
        "documents": [{"doc_id": d.record.doc_id, "sha256": d.record.sha256, "kind": d.record.kind, "units": len(d.units)} for d in documents],
        "chunks": len(chunks),
        "dimensions": len(vectors[0]) if vectors else 0,
        "device": getattr(embedder, "device", "cpu"),
        "seconds": {"load_model": round(loaded - started, 1), "embed": round(embedded - loaded, 1), "total": round(time.perf_counter() - started, 1)},
        "built_at": now_iso(),
    }
    (directory / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")  # written last: its presence means "ready"
    return Index(directory, spec, device=device, embedder=embedder)


def get_document(store: CorpusStore, ref: str, unit_id: str | None = None) -> dict[str, Any]:
    """A document's text by id or citation; one unit when `unit_id` is given. The original text, never a summary."""
    record = store.find(ref)
    document = store.load_document(record.doc_id) if record else None
    if record is None or document is None:
        raise RetrievalError(f"문서를 찾을 수 없습니다: {ref}")
    units = document.units
    if unit_id is not None:
        unit = document.unit(unit_id)
        if unit is None:
            raise RetrievalError(f"{record.doc_id}에 '{unit_id}' 단위가 없습니다 (예: {', '.join(u.unit_id for u in units[:5])})")
        units = [unit]
    return {
        "doc_id": record.doc_id,
        "citation": record.cite,
        "kind": record.kind,
        "authority": record.authority,
        "url": record.url,
        "dates": record.dates,
        "units": [{"unit_id": u.unit_id, "label": u.label, "text": u.text} for u in units],
    }
