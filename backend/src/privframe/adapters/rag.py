"""Local document extraction, replaceable chunking, and explicit vector retrieval.

Only the worker owns the embedded Chroma client. Source text is never executed.
Chunk offsets are character offsets within the extracted SourceUnit (a PDF page
or HTML article), rather than byte offsets into the uploaded container file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from privframe.domain.models import (
    Chunk, DocumentRecord, Evidence, EvidenceBundle, IndexRecord, SourceUnit,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DocumentExtractor:
    def extract(self, path: Path, format: str) -> list[SourceUnit]:
        kind = format.lower().lstrip(".")
        if kind == "pdf":
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                units = [
                    SourceUnit(text=text.strip(), page=number)
                    for number, page in enumerate(pdf.pages, 1)
                    if (text := page.extract_text()) and text.strip()
                ]
            if not units:
                raise ValueError("PDF has no extractable text; scanned PDFs require OCR")
            return units
        if kind in {"txt", "text"}:
            text = path.read_text(encoding="utf-8-sig").strip()
            if not text:
                raise ValueError("Document contains no text")
            return [SourceUnit(text=text)]
        if kind in {"html", "htm"}:
            return self._html(path.read_text(encoding="utf-8-sig"))
        raise ValueError(f"Unsupported document format: {format}")

    @staticmethod
    def _html(html: str) -> list[SourceUnit]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select("script, style, noscript, template, button, select"):
            node.decompose()
        # law.go.kr's downloaded lsInfoR HTML supplies actual article bodies here.
        article_nodes = soup.select("div.lawcon")
        if article_nodes:
            units = []
            for node in article_nodes:
                text = node.get_text(" ", strip=True)
                match = re.match(r"(제\s*\d+\s*조(?:의\s*\d+)?(?:\([^)]*\))?)", text)
                if text:
                    units.append(SourceUnit(text=text, article=match.group(1) if match else None))
            if units:
                return units
        for node in soup.select("nav, header, footer, aside, [hidden], [aria-hidden='true']"):
            node.decompose()
        body = soup.find("main") or soup.find("article") or soup.body or soup
        text = body.get_text("\n", strip=True)
        if not text.strip():
            raise ValueError("HTML contains no extractable text")
        # Also accept ordinary HTML exports where each article starts a new line.
        starts = list(re.finditer(r"(?m)^(제\s*\d+\s*조(?:의\s*\d+)?(?:\([^\n)]*\))?)", text))
        if not starts:
            return [SourceUnit(text=text)]
        units = []
        if text[:starts[0].start()].strip():
            units.append(SourceUnit(text=text[:starts[0].start()].strip()))
        for number, match in enumerate(starts):
            end = starts[number + 1].start() if number + 1 < len(starts) else len(text)
            units.append(SourceUnit(text=text[match.start():end].strip(), article=match.group(1)))
        return units


class LangChainChunker:
    def __init__(self, config: dict[str, Any]):
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        if config.get("chunker", "recursive") != "recursive":
            raise ValueError("Unsupported chunker")
        size, overlap = int(config.get("chunk_size", 1200)), int(config.get("chunk_overlap", 150))
        if size <= 0 or overlap < 0 or overlap >= size:
            raise ValueError("Require chunk_size > chunk_overlap >= 0")
        self.signature = {"chunker": "recursive", "size": size, "overlap": overlap}
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=size, chunk_overlap=overlap, add_start_index=True,
            separators=["\n\n", "\n", "。", ". ", " ", ""],
        )

    def split(self, document: DocumentRecord) -> list[Chunk]:
        chunks = []
        for unit_number, unit in enumerate(document.units):
            for part in self.splitter.create_documents([unit.text]):
                offset = int(part.metadata["start_index"])
                if offset < 0 or unit.text[offset:offset + len(part.page_content)] != part.page_content:
                    raise ValueError("Chunker could not establish the source text position")
                text_hash = _hash(part.page_content)
                identity = json.dumps(
                    [document.id, document.content_hash, unit_number, offset, text_hash, self.signature],
                    sort_keys=True, ensure_ascii=False,
                )
                chunks.append(Chunk(
                    id=f"chunk_{_hash(identity)}", document_id=document.id,
                    text=part.page_content, page=unit.page, article=unit.article,
                    offset=offset, metadata=document.metadata.model_copy(deep=True),
                    content_hash=text_hash,
                ))
        if not chunks:
            raise ValueError("Document contains no chunks")
        return chunks


class FixtureHashEmbeddings:
    """Explicitly synthetic lexical hash vectors; never a production fallback."""

    def __init__(self, dimensions: int):
        if dimensions < 8:
            raise ValueError("Fixture embedding dimensions must be at least 8")
        self.dimensions = dimensions

    def embed_query(self, text: str) -> list[float]:
        normalized = re.sub(r"\s+", " ", text.casefold()).strip()
        if not normalized:
            raise ValueError("Cannot embed empty text")
        features = re.findall(r"\w+", normalized) + [normalized[i:i + 2] for i in range(len(normalized) - 1)]
        values = [0.0] * self.dimensions
        for feature in features or [normalized]:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            values[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in values))
        if not norm:
            values[0], norm = 1.0, 1.0
        return [value / norm for value in values]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


class ChromaRetriever:
    def __init__(self, config: dict[str, Any], chroma_path: Path):
        import chromadb

        self.config = dict(config)
        self.model = str(config.get("embedding_model", "text-embedding-3-small"))
        self.dimensions = int(config.get("embedding_dimensions", 1536))
        self.fixture = config.get("mode", "real") == "fixture"
        if self.dimensions <= 0:
            raise ValueError("embedding_dimensions must be positive")
        if self.fixture:
            if self.model != "fixture-hash-v1":
                raise ValueError("Fixture mode requires embedding_model=fixture-hash-v1")
            self.embeddings = FixtureHashEmbeddings(self.dimensions)
        else:
            if self.model.startswith("fixture"):
                raise ValueError("Fixture embeddings require explicit fixture mode")
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY is required for real embeddings; no fixture fallback is used")
            from langchain_openai import OpenAIEmbeddings

            self.embeddings = OpenAIEmbeddings(
                model=self.model, dimensions=self.dimensions,
                request_timeout=float(config.get("embedding_timeout", 60)),
                max_retries=int(config.get("embedding_max_retries", 2)),
            )
        self.client = chromadb.PersistentClient(path=str(chroma_path))

    @staticmethod
    def _name(index: IndexRecord) -> str:
        return "privframe_" + _hash(index.fingerprint)

    def _contract(self, index: IndexRecord) -> dict[str, Any]:
        if str(index.config.get("embedding_model", self.model)) != self.model:
            raise ValueError("Index embedding model differs from retriever configuration")
        if int(index.config.get("embedding_dimensions", self.dimensions)) != self.dimensions:
            raise ValueError("Index embedding dimensions differ from retriever configuration")
        if (index.config.get("mode", "real") == "fixture") != self.fixture:
            raise ValueError("Index mode differs from retriever configuration")
        return {
            "fingerprint": index.fingerprint, "embedding_model": self.model,
            "embedding_dimensions": self.dimensions, "fixture": self.fixture,
            "distance_metric": "cosine",
        }

    def _validate_collection(self, collection: Any, index: IndexRecord) -> None:
        actual = collection.metadata or {}
        for key, value in self._contract(index).items():
            if actual.get(key) != value:
                raise ValueError(f"Collection retrieval contract mismatch: {key}")

    def _vectors(self, vectors: list[list[float]], expected: int) -> None:
        if len(vectors) != expected or any(
            len(vector) != self.dimensions or not all(math.isfinite(x) for x in vector)
            for vector in vectors
        ):
            raise ValueError("Embedding provider returned an invalid dimension or vector")

    def build(self, index: IndexRecord, chunks: list[Chunk]) -> int:
        if not chunks:
            raise ValueError("Cannot build an empty index")
        expected = {chunk.id: chunk for chunk in chunks}
        if len(expected) != len(chunks):
            raise ValueError("Duplicate chunk IDs")
        if any(chunk.document_id not in index.document_ids for chunk in chunks):
            raise ValueError("Chunk belongs to a document outside this index")
        if any(_hash(chunk.text) != chunk.content_hash for chunk in chunks):
            raise ValueError("Chunk content hash does not match its text")
        payloads = {key: chunk.model_dump_json() for key, chunk in expected.items()}
        manifest = _hash(json.dumps(payloads, sort_keys=True, ensure_ascii=False))
        contract = self._contract(index)
        collection = self.client.get_or_create_collection(
            name=self._name(index), embedding_function=None,
            metadata={**contract, "chunk_manifest": manifest, "build_status": "building"},
            configuration={"hnsw": {"space": "cosine"}},
        )
        self._validate_collection(collection, index)
        if (collection.metadata or {}).get("chunk_manifest") != manifest:
            raise ValueError("Immutable index already contains a different chunk manifest")
        existing = collection.get(include=["documents", "metadatas"])
        existing_ids = set(existing["ids"])
        if existing_ids - expected.keys():
            raise ValueError("Immutable index contains unexpected chunk IDs")
        for key, text, metadata in zip(existing["ids"], existing["documents"], existing["metadatas"]):
            if text != expected[key].text or (metadata or {}).get("chunk") != payloads[key]:
                raise ValueError("Immutable index chunk content or provenance mismatch")
        missing = [chunk for chunk in chunks if chunk.id not in existing_ids]
        for start in range(0, len(missing), 128):
            batch = missing[start:start + 128]
            vectors = self.embeddings.embed_documents([chunk.text for chunk in batch])
            self._vectors(vectors, len(batch))
            collection.add(
                ids=[chunk.id for chunk in batch], documents=[chunk.text for chunk in batch],
                embeddings=vectors,
                metadatas=[{"chunk": payloads[chunk.id], "content_hash": chunk.content_hash} for chunk in batch],
            )
        count = collection.count()
        if count != len(chunks):
            raise ValueError("Index build did not preserve the complete chunk manifest")
        collection.modify(metadata={**contract, "chunk_manifest": manifest, "build_status": "ready"})
        return count

    def retrieve(self, index: IndexRecord, query: str) -> EvidenceBundle:
        if not query.strip():
            raise ValueError("Retrieval query must contain text")
        collection = self.client.get_collection(name=self._name(index), embedding_function=None)
        self._validate_collection(collection, index)
        if (collection.metadata or {}).get("build_status") != "ready":
            raise ValueError("Index is not ready")
        count = collection.count()
        if not count:
            return EvidenceBundle(query=query, index_id=index.id)
        top_k = int(self.config.get("top_k", 8))
        budget = int(self.config.get("evidence_char_budget", 14000))
        if top_k <= 0 or budget < 0:
            raise ValueError("Require top_k > 0 and evidence_char_budget >= 0")
        from langchain_chroma import Chroma

        # Reuse the framework's vector-search integration with this already
        # validated collection and explicit embedder. Never create/download a default.
        store = Chroma(
            client=self.client, collection_name=self._name(index),
            embedding_function=self.embeddings, create_collection_if_not_exists=False,
        )
        results = store.similarity_search_with_score(query, k=min(top_k, count))
        candidates, provided, used = [], [], 0
        for result, distance in results:
            text = result.page_content
            chunk = Chunk.model_validate_json(result.metadata["chunk"])
            if chunk.id != result.id or chunk.text != text or _hash(text) != chunk.content_hash:
                raise ValueError("Retrieved chunk provenance is inconsistent")
            score = min(1.0, max(-1.0, 1.0 - float(distance)))
            evidence = Evidence(**chunk.model_dump(), index_id=index.id, score=score)
            candidates.append(evidence)
            if used + len(text) <= budget:
                provided.append(evidence.model_copy(deep=True))
                used += len(text)
        return EvidenceBundle(query=query, index_id=index.id, candidates=candidates, provided=provided)


class NoOpReranker:
    def rerank(self, query: str, bundle: EvidenceBundle) -> EvidenceBundle:
        return bundle.model_copy(deep=True)


def create_retriever(config: dict[str, Any], path: Path) -> ChromaRetriever:
    if config.get("retriever", "chroma") != "chroma":
        raise ValueError("Unsupported retriever")
    return ChromaRetriever(config, path)


def create_reranker(config: dict[str, Any]) -> NoOpReranker:
    if config.get("reranker", "none") != "none":
        raise ValueError("Unsupported reranker")
    return NoOpReranker()
