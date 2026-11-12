from conftest import PIPELINE
import pytest

from privframe.core.corpus import CorpusStore, Document, Unit
from privframe.core.pipeline import load_catalog, load_profile, resolve
from privframe.core.retrieval import HashEmbedder, RetrievalError, RetrievalSpec, build_index, chunk_units, get_document, split_text, support

# no model downloads in tests: hash vectors instead of KURE, no reranker
SPEC = RetrievalSpec(embedding_adapter="fixture-hash", embedding_ref="fixture", reranker_adapter="none", reranker_ref=None, top_k=3)

DOCS = {
    "pipa": ("law", "개인정보 보호법", 1, [
        ("제2조", "제2조(정의)", "개인정보란 살아 있는 개인에 관한 정보로서 성명, 주민등록번호 및 영상 등을 통하여 개인을 알아볼 수 있는 정보를 말한다."),
        ("제25조의2", "제25조의2(이동형 영상정보처리기기의 운영 제한)", "이동형 영상정보처리기기로 촬영하는 경우에는 불빛, 소리, 안내판 등으로 촬영 사실을 표시하고 알려야 한다."),
    ]),
    "prec-consent": ("prec", "대법원 2021다219116", 2, [
        ("판결요지", "판결요지", "사진촬영에 관한 동의를 받았다 하더라도 허용 범위를 벗어나 공표하려면 그에 관하여도 피촬영자의 동의를 받아야 한다."),
    ]),
    "guide-mobile": ("guideline", "이동형 영상정보처리기기 안내서", 3, [
        ("p1", "1쪽", "배달 로봇과 드론 등 이동형 기기는 촬영 사실을 안내판이나 불빛으로 표시한다."),
    ]),
}


def make_store(tmp_path, docs=DOCS, **flags) -> CorpusStore:
    store = CorpusStore(tmp_path)
    for doc_id, (kind, citation, authority, units) in docs.items():
        body = "\n".join(text for _, _, text in units).encode()
        record, _ = store.put_raw(doc_id=doc_id, kind=kind, title=citation, source="local_file", format="txt", content=body, citation=citation, authority=authority, **flags.get(doc_id, {}))
        store.save_document(Document(record=record, parser="text", units=[Unit(*u) for u in units]))
    return store


class CountingEmbedder(HashEmbedder):
    calls = 0

    def embed_documents(self, texts):
        CountingEmbedder.calls += 1
        return super().embed_documents(texts)


def test_hybrid_search_finds_the_passage_and_names_its_source(tmp_path):
    index = build_index(make_store(tmp_path), SPEC, embedder=HashEmbedder())
    top = index.search("촬영 동의를 받았어도 공표에는 다시 동의가 필요한가")[0]
    assert (top.doc_id, top.unit_id, top.citation) == ("prec-consent", "판결요지", "대법원 2021다219116")
    assert set(top.ranks) == {"dense", "bm25"}


def test_search_mode_is_a_query_time_choice(tmp_path):
    index = build_index(make_store(tmp_path), SPEC, embedder=HashEmbedder())
    assert all(set(e.ranks) == {"bm25"} for e in index.search("촬영 사실 표시", mode="bm25"))
    assert all(set(e.ranks) == {"dense"} for e in index.search("촬영 사실 표시", mode="dense"))
    with pytest.raises(RetrievalError):
        index.search("촬영", mode="colbert")


def test_filters_by_kind_and_authority(tmp_path):
    index = build_index(make_store(tmp_path), SPEC, embedder=HashEmbedder())
    assert {e.kind for e in index.search("촬영 사실 표시", kinds=["guideline"])} == {"guideline"}
    assert {e.doc_id for e in index.search("촬영 사실 표시", max_authority=1)} == {"pipa"}


def test_same_documents_and_settings_reuse_the_index(tmp_path):
    store = make_store(tmp_path)
    CountingEmbedder.calls = 0
    first = build_index(store, SPEC, embedder=CountingEmbedder())
    calls = CountingEmbedder.calls
    again = build_index(store, SPEC, embedder=CountingEmbedder())
    assert again.fingerprint == first.fingerprint and CountingEmbedder.calls == calls
    # query-time parts do not change the index …
    bm25_only = RetrievalSpec(**{**SPEC.__dict__, "mode": "bm25"})
    assert build_index(store, bm25_only, embedder=CountingEmbedder()).fingerprint == first.fingerprint
    # … index-time parts and document changes do
    fixed = RetrievalSpec(**{**SPEC.__dict__, "chunking": "fixed-1200"})
    assert build_index(store, fixed, embedder=CountingEmbedder()).fingerprint != first.fingerprint
    store.put_raw(doc_id="pipa", kind="law", title="개인정보 보호법", source="local_file", format="txt", content="개정된 본문".encode(), citation="개인정보 보호법", authority=1)
    record = store.get("pipa")
    store.save_document(Document(record=record, parser="text", units=[Unit("제2조", "제2조(정의)", "개정된 본문")]))
    assert build_index(store, SPEC, embedder=CountingEmbedder()).fingerprint != first.fingerprint


def test_documents_kept_on_file_only_are_not_searched(tmp_path):
    index = build_index(make_store(tmp_path, **{"guide-mobile": {"index": False}}), SPEC, embedder=HashEmbedder())
    assert "guide-mobile" not in {c.doc_id for c in index.chunks}


def test_get_document_returns_original_text(tmp_path):
    store = make_store(tmp_path)
    unit = get_document(store, "개인정보 보호법", "제25조의2")["units"][0]
    assert unit["text"] == DOCS["pipa"][3][1][2]
    assert len(get_document(store, "pipa")["units"]) == 2
    with pytest.raises(RetrievalError, match="단위가 없습니다"):
        get_document(store, "pipa", "제99조")
    with pytest.raises(RetrievalError, match="찾을 수 없습니다"):
        get_document(store, "민법")


def test_long_units_split_at_paragraph_marks_and_keep_their_prefix(tmp_path):
    assert split_text("① 첫째 항이다. ② 둘째 항이다. ③ 셋째 항이다.", 22) == ["① 첫째 항이다. ② 둘째 항이다.", "③ 셋째 항이다."]
    long_article = "① " + "가" * 700 + ". ② " + "나" * 700 + "."
    store = make_store(tmp_path, {"law": ("law", "어느 법", 1, [("제1조", "제1조(목적)", long_article)])})
    chunks = chunk_units(store.documents()[0])
    assert len(chunks) == 2 and all(c.prefix == "어느 법 › 제1조(목적)" for c in chunks)
    assert chunks[1].text.startswith("②") and chunks[0].indexed_text.startswith("어느 법 › 제1조(목적)\n①")


def test_profile_parts_become_the_retrieval_settings():
    catalog = load_catalog(PIPELINE)
    base = load_profile(PIPELINE, "baseline-v2")
    spec = RetrievalSpec.from_profile(resolve(catalog, base))
    assert (spec.embedding_ref, spec.tokenizer, spec.mode, spec.reranker_ref, spec.store) == ("nlpai-lab/KURE-v1", "kiwi", "hybrid-rrf", "nlpai-lab/KURE-Reranker-nano", "chroma")
    swapped = RetrievalSpec.from_profile(resolve(catalog, base, {"rag.retrieval": "bm25", "rag.reranker": "none"}))
    assert (swapped.mode, swapped.reranker_adapter) == ("bm25", "none")


def test_parts_without_a_backend_adapter_are_refused_not_guessed(tmp_path):
    catalog = load_catalog(PIPELINE)
    assert support("rag.chunking", catalog.slot("rag.chunking").candidate("article-prefix")) == (True, "")
    assert support("rag.store", catalog.slot("rag.store").candidate("qdrant-local"))[0] is False
    qdrant = RetrievalSpec(**{**SPEC.__dict__, "store": "qdrant-local"})
    with pytest.raises(RetrievalError, match="구현되지 않았습니다"):
        build_index(make_store(tmp_path), qdrant, embedder=HashEmbedder())
