import pytest

from privframe.core.corpus import CorpusStore, Document, Unit


def put(store: CorpusStore, content: bytes = b"hello", **meta):
    return store.put_raw(doc_id="doc-a", kind="law", title="법", source="local_file", format="txt", content=content, **meta)


def test_same_bytes_are_stored_once(tmp_path):
    store = CorpusStore(tmp_path)
    first, status = put(store)
    assert status == "new" and (tmp_path / "raw" / "doc-a.txt").read_bytes() == b"hello"
    again, status = put(store)
    assert status == "unchanged" and again.fetched_at == first.fetched_at
    assert len(CorpusStore(tmp_path).records()) == 1  # the registry is read back from disk


def test_changed_bytes_replace_the_document(tmp_path):
    store = CorpusStore(tmp_path)
    first, _ = put(store)
    second, status = put(store, b"hello, amended")
    assert status == "updated" and second.sha256 != first.sha256
    assert store.raw_bytes(second) == b"hello, amended"


def test_description_can_change_without_refetching(tmp_path):
    store = CorpusStore(tmp_path)
    first, _ = put(store, authority=3)
    record, status = put(store, authority=1, citation="개인정보 보호법")
    assert status == "unchanged" and record.fetched_at == first.fetched_at
    assert store.get("doc-a").authority == 1 and store.find("개인정보 보호법").doc_id == "doc-a"


@pytest.mark.parametrize("doc_id", ["A", "한글", "has space", "x"])
def test_document_ids_are_plain_ascii(tmp_path, doc_id):
    with pytest.raises(ValueError):
        CorpusStore(tmp_path).put_raw(doc_id=doc_id, kind="law", title="t", source="s", format="txt", content=b"x")


def test_a_parse_of_older_bytes_is_not_served(tmp_path):
    store = CorpusStore(tmp_path)
    record, _ = put(store)
    store.save_document(Document(record=record, parser="text", units=[Unit("text", "본문", "hello")]))
    assert [d.record.doc_id for d in store.documents()] == ["doc-a"]
    put(store, b"new bytes")  # the stored parse now describes bytes that are gone
    assert store.documents() == []


def test_documents_carry_current_registry_metadata(tmp_path):
    store = CorpusStore(tmp_path)
    record, _ = put(store, authority=3)
    store.save_document(Document(record=record, parser="text", units=[Unit("text", "본문", "hello")]))
    put(store, authority=1)
    assert store.documents()[0].record.authority == 1
