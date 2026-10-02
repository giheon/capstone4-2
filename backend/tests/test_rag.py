import hashlib
import json
from pathlib import Path

import pytest

from privframe.adapters.rag import (
    ChromaRetriever,
    DocumentExtractor,
    FixtureHashEmbeddings,
    LangChainChunker,
    create_reranker,
    create_retriever,
)
from privframe.domain.models import (
    Chunk, DocumentRecord, IndexRecord, SourceMetadata, SourceUnit,
)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def config(**changes):
    return {
        "mode": "fixture", "retriever": "chroma", "embedding_model": "fixture-hash-v1",
        "embedding_dimensions": 256, "top_k": 8, "evidence_char_budget": 14000,
        **changes,
    }


def document(units=None):
    return DocumentRecord(
        id="doc1", filename="source.txt", format="txt", content_hash="source-hash",
        metadata=SourceMetadata(title="개인정보 보호법", source_url="https://www.law.go.kr/", kind="law"),
        units=units or [SourceUnit(text="얼굴 영상 개인정보를 보호한다.", page=1, article="제1조(목적)")],
    )


def index(settings=None, fingerprint="first", identity="index1"):
    return IndexRecord(id=identity, document_ids=["doc1"], config=settings or config(), fingerprint=fingerprint)


def chunks(texts=None):
    return [Chunk(
        id=f"chunk{number}", document_id="doc1", text=text, content_hash=digest(text),
        offset=number * 50, page=number + 1, article=f"제{number + 1}조", metadata=document().metadata,
    ) for number, text in enumerate(texts or ["얼굴 영상 개인정보 보호", "고양이 강아지 동물 음식"])]


def write_pdf(path, pages):
    """A tiny real text PDF, so extraction exercises pdfplumber rather than a stub."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = [4 + 2 * number for number in range(len(pages))]
    objects.append(f"<< /Type /Pages /Kids [{' '.join(f'{n} 0 R' for n in page_ids)}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for number, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 30 100 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * number} 0 R >>".encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    data = bytearray(b"%PDF-1.4\n")
    positions = []
    for number, obj in enumerate(objects, 1):
        positions.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in positions:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(data)


def test_extract_real_pdf_preserves_page_numbers_and_rejects_no_text(tmp_path):
    path = tmp_path / "source.pdf"
    write_pdf(path, ["First page", "", "Third page"])
    result = DocumentExtractor().extract(path, "pdf")
    assert [(unit.text, unit.page) for unit in result] == [("First page", 1), ("Third page", 3)]
    write_pdf(path, [""])
    with pytest.raises(ValueError, match="require OCR"):
        DocumentExtractor().extract(path, "pdf")


def test_extract_txt_bom_and_official_law_html(tmp_path):
    path = tmp_path / "text.txt"
    path.write_text("\ufeff  법령 내용\n", encoding="utf-8")
    assert DocumentExtractor().extract(path, "txt") == [SourceUnit(text="법령 내용")]
    path = tmp_path / "law.html"
    path.write_text("""<html><nav>검색 메뉴</nav><script>injected()</script>
      <div class="lawcon"><p>제1조(목적)</p><div>얼굴 개인정보 보호</div></div>
      <div class="lawcon"><p>제25조의2(이동형 영상정보처리기기)</p><div>공개된 장소</div></div>
      </html>""", encoding="utf-8")
    units = DocumentExtractor().extract(path, "html")
    assert [unit.article for unit in units] == ["제1조(목적)", "제25조의2(이동형 영상정보처리기기)"]
    assert "검색" not in " ".join(unit.text for unit in units)
    assert "injected" not in " ".join(unit.text for unit in units)
    assert "공개된 장소" in units[1].text


def test_extract_generic_html_groups_articles_and_empty_or_unknown_fails(tmp_path):
    path = tmp_path / "law.html"
    path.write_text("<main><h1>법률</h1><h2>제1조(목적)</h2><p>보호 목적</p><h2>제2조(정의)</h2><p>개인정보</p></main>")
    units = DocumentExtractor().extract(path, "html")
    assert [unit.article for unit in units] == [None, "제1조(목적)", "제2조(정의)"]
    path.write_text("<script>only code</script><nav>only menu</nav>")
    with pytest.raises(ValueError, match="no extractable text"):
        DocumentExtractor().extract(path, "html")
    with pytest.raises(ValueError, match="Unsupported document"):
        DocumentExtractor().extract(path, "docx")


def test_chunking_offsets_hashes_and_provenance_are_stable():
    units = [SourceUnit(text="얼굴 영상 촬영에 관한 법률 문장입니다. " * 20, page=3, article="제25조")]
    source = document(units)
    splitter = LangChainChunker({"chunk_size": 75, "chunk_overlap": 15})
    result = splitter.split(source)
    assert len(result) > 2
    assert result == splitter.split(source)
    assert len({part.id for part in result}) == len(result)
    for part in result:
        assert units[0].text[part.offset:part.offset + len(part.text)] == part.text
        assert part.content_hash == digest(part.text)
        assert (part.document_id, part.page, part.article) == ("doc1", 3, "제25조")
        assert part.metadata == source.metadata
        assert part.metadata is not source.metadata
    changed = LangChainChunker({"chunk_size": 90, "chunk_overlap": 15}).split(source)
    assert {part.id for part in result}.isdisjoint(part.id for part in changed)


def test_chunker_keeps_identical_pages_distinct_and_checks_configuration():
    source = document([SourceUnit(text="same words", page=1), SourceUnit(text="same words", page=2)])
    parts = LangChainChunker({}).split(source)
    assert len(parts) == 2 and parts[0].id != parts[1].id
    with pytest.raises(ValueError, match="chunk_size"):
        LangChainChunker({"chunk_size": 10, "chunk_overlap": 10})
    with pytest.raises(ValueError, match="no chunks"):
        LangChainChunker({}).split(document([SourceUnit(text="")]))


def test_actual_chroma_retry_retrieve_budget_and_persistence(tmp_path):
    settings = config(evidence_char_budget=len("얼굴 영상 개인정보 보호"))
    retriever = create_retriever(settings, tmp_path / "chroma")
    record, source_chunks = index(settings), chunks()
    assert retriever.build(record, source_chunks) == 2
    # Retrying a completed build must not call the provider or duplicate any chunk.
    original = retriever.embeddings
    class NoBuildCalls:
        def embed_documents(self, texts):
            raise AssertionError("Completed build was embedded again")
    retriever.embeddings = NoBuildCalls()
    assert retriever.build(record, source_chunks) == 2
    retriever.embeddings = original
    collection = retriever.client.get_collection(retriever._name(record), embedding_function=None)
    assert collection.configuration["hnsw"]["space"] == "cosine"
    assert collection.metadata["fixture"] is True
    bundle = retriever.retrieve(record, "얼굴 영상 개인정보 보호")
    assert bundle.candidates[0].id == source_chunks[0].id
    assert bundle.provided == [bundle.candidates[0]]
    assert bundle.provided[0] is not bundle.candidates[0]
    assert bundle.provided[0].metadata == source_chunks[0].metadata
    assert bundle.provided[0].page == 1 and bundle.provided[0].offset == 0
    reopened = ChromaRetriever(settings, tmp_path / "chroma")
    assert reopened.retrieve(record, bundle.query).provided == bundle.provided
    copied = create_reranker({}).rerank(bundle.query, bundle)
    assert copied == bundle and copied is not bundle


def test_partial_build_can_retry_without_overwriting_chunks(tmp_path):
    retriever = ChromaRetriever(config(), tmp_path)
    source_chunks = chunks([f"얼굴 영상 보호 {number}" for number in range(130)])
    base = retriever.embeddings
    class InterruptedEmbeddings:
        calls = 0
        def embed_documents(self, texts):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("provider unavailable")
            return base.embed_documents(texts)
    retriever.embeddings = InterruptedEmbeddings()
    with pytest.raises(RuntimeError, match="provider unavailable"):
        retriever.build(index(), source_chunks)
    collection = retriever.client.get_collection(retriever._name(index()), embedding_function=None)
    assert collection.count() == 128 and collection.metadata["build_status"] == "building"
    with pytest.raises(ValueError, match="not ready"):
        retriever.retrieve(index(), "얼굴")
    class RemainingOnly:
        def embed_documents(self, texts):
            assert len(texts) == 2
            return base.embed_documents(texts)
    retriever.embeddings = RemainingOnly()
    assert retriever.build(index(), source_chunks) == 130
    assert collection.get()["ids"] and collection.count() == 130


def test_immutable_index_contracts_and_embedding_dimensions(tmp_path):
    retriever = ChromaRetriever(config(), tmp_path)
    source_chunks = chunks()
    retriever.build(index(), source_chunks)
    changed = source_chunks[0].model_copy(update={"text": "changed", "content_hash": digest("changed")})
    with pytest.raises(ValueError, match="different chunk manifest"):
        retriever.build(index(), [changed, source_chunks[1]])
    with pytest.raises(ValueError, match="Duplicate chunk"):
        retriever.build(index(), [source_chunks[0], source_chunks[0]])
    with pytest.raises(ValueError, match="hash"):
        retriever.build(index(fingerprint="bad-hash"), [changed.model_copy(update={"content_hash": "bad"})])
    with pytest.raises(ValueError, match="dimensions differ"):
        retriever.retrieve(index(config(embedding_dimensions=128)), "얼굴")
    with pytest.raises(ValueError, match="model differs"):
        retriever.retrieve(index(config(embedding_model="another")), "얼굴")
    with pytest.raises(ValueError, match="mode differs"):
        retriever.retrieve(index(config(mode="real")), "얼굴")
    other = index(fingerprint="second", identity="index2")
    retriever.build(other, [changed])
    assert retriever._name(other) != retriever._name(index())
    assert retriever.retrieve(other, "changed").provided[0].index_id == "index2"
    retriever.embeddings = type("BadDimensions", (), {"embed_documents": lambda self, texts: [[0.0]] * len(texts)})()
    with pytest.raises(ValueError, match="invalid dimension"):
        retriever.build(index(fingerprint="bad-dim"), chunks())


def test_fixture_is_explicit_and_real_embedding_errors_never_fall_back(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        ChromaRetriever(config(mode="real", embedding_model="text-embedding-3-small"), tmp_path)
    with pytest.raises(ValueError, match="explicit fixture mode"):
        ChromaRetriever(config(mode="real"), tmp_path)
    with pytest.raises(ValueError, match="fixture-hash-v1"):
        ChromaRetriever(config(embedding_model="text-embedding-3-small"), tmp_path)
    embedding = FixtureHashEmbeddings(256)
    assert embedding.embed_query("얼굴 영상") == embedding.embed_query("얼굴 영상")
    with pytest.raises(ValueError, match="empty text"):
        embedding.embed_query(" ")


def test_seed_manifest_has_three_official_sources_with_distinct_date_semantics():
    manifest = json.loads((Path(__file__).parents[2] / "configs/seed-manifest.json").read_text())
    assert len(manifest["documents"]) == 3
    metadata = [SourceMetadata.model_validate(item["metadata"]) for item in manifest["documents"]]
    law = next(item for item in metadata if item.kind == "law")
    assert law.effective_from == "2026-09-11" and law.published_at == "2026-03-10"
    assert all(item.effective_from is None for item in metadata if item.kind == "guide")
    assert all(item.source_url.startswith(("https://www.law.go.kr/", "https://www.privacy.go.kr/")) for item in metadata)
