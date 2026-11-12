import httpx

from privframe.core.corpus import CorpusStore
from privframe.core.lawgokr import LawGoKr
from privframe.core.sources import FetchContext, fetch_all

PDF = b"%PDF-1.7 fake body"


def context(tmp_path, handler, with_law: bool = False) -> FetchContext:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return FetchContext(root=tmp_path, http=http, law=LawGoKr("secret-oc", client=http) if with_law else None)


def test_http_file_is_stored_with_its_address(tmp_path):
    store = CorpusStore(tmp_path / "corpus")
    entry = {"id": "prec-1", "source": "http_file", "kind": "prec", "title": "판결", "url": "https://example.test/j.pdf", "authority": 2}
    outcome = fetch_all([entry], store, context(tmp_path, lambda request: httpx.Response(200, content=PDF)))[0]
    assert outcome.status == "new"
    record = store.get("prec-1")
    assert (record.format, record.authority, record.url) == ("pdf", 2, "https://example.test/j.pdf")
    assert store.raw_bytes(record) == PDF


def test_a_block_page_served_as_pdf_is_rejected(tmp_path):
    store = CorpusStore(tmp_path / "corpus")
    entry = {"id": "guide", "source": "http_file", "kind": "guideline", "title": "안내서", "url": "https://example.test/down.do", "format": "pdf"}
    outcome = fetch_all([entry], store, context(tmp_path, lambda request: httpx.Response(200, content=b"<script>alert('expired')</script>")))[0]
    assert outcome.status == "failed" and "PDF가 아닌" in outcome.detail
    assert store.records() == []


def test_api_document_is_found_by_search_then_fetched(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["OC"] == "secret-oc"
        if request.url.path.endswith("lawSearch.do"):
            return httpx.Response(200, json={"PrecSearch": {"totalCnt": "1", "prec": [{"판례일련번호": "217373", "사건번호": "2021다219116"}]}})
        assert request.url.params["ID"] == "217373"
        return httpx.Response(200, json={"PrecService": {"사건번호": "2021다219116", "법원명": "대법원", "판결요지": "촬영 동의와 공표 동의는 별개이다. " * 5}})

    store = CorpusStore(tmp_path / "corpus")
    entry = {"id": "prec-2021da219116", "source": "lawgokr", "target": "prec", "title": "대법원 2021다219116", "search": {"nb": "2021다219116"}}
    outcome = fetch_all([entry], store, context(tmp_path, handler, with_law=True))[0]
    assert outcome.status == "new"
    record = store.get("prec-2021da219116")
    assert record.format == "json" and record.extra["api"]["법원명"] == "대법원"
    assert "secret-oc" not in record.url and "ID=217373" in record.url  # the credential never reaches the registry
    assert b"secret-oc" not in (tmp_path / "corpus" / "registry.jsonl").read_bytes()


def test_api_entries_wait_for_a_key_without_failing(tmp_path):
    entry = {"id": "pipa", "source": "lawgokr", "target": "law", "title": "개인정보 보호법", "search": {"query": "개인정보 보호법"}}
    outcome = fetch_all([entry], CorpusStore(tmp_path / "corpus"), context(tmp_path, lambda request: httpx.Response(500)))[0]
    assert outcome.status == "unavailable" and "LAW_OC" in outcome.detail


def test_hand_downloaded_file(tmp_path):
    store = CorpusStore(tmp_path / "corpus")
    entry = {"id": "guide", "source": "local_file", "kind": "guideline", "title": "안내서", "path": "inbox/guide.pdf", "page": "https://example.test/board/1"}
    ctx = context(tmp_path, lambda request: httpx.Response(500))
    missing = fetch_all([entry], store, ctx)[0]
    assert missing.status == "unavailable" and "https://example.test/board/1" in missing.detail
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "guide.pdf").write_bytes(PDF)
    assert fetch_all([entry], store, ctx)[0].status == "new"
    assert store.get("guide").url == "https://example.test/board/1"


def test_one_bad_entry_does_not_stop_the_rest(tmp_path):
    store = CorpusStore(tmp_path / "corpus")
    entries = [
        {"id": "x", "source": "ftp", "kind": "law", "title": "모르는 수집기"},
        {"id": "ok", "source": "http_file", "kind": "prec", "title": "판결", "url": "https://example.test/ok.pdf"},
    ]
    outcomes = fetch_all(entries, store, context(tmp_path, lambda request: httpx.Response(200, content=PDF)))
    assert [o.status for o in outcomes] == ["failed", "new"] and "알 수 없는 수집기" in outcomes[0].detail


def test_match_picks_the_exact_hit_among_search_results(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("lawSearch.do"):
            return httpx.Response(200, json={"LawSearch": {"law": [{"법령명한글": "개인정보 보호법 시행령", "법령일련번호": "2"}, {"법령명한글": "개인정보 보호법", "법령일련번호": "1"}]}})
        return httpx.Response(200, json={"법령": {"기본정보": {"법령명_한글": "개인정보 보호법"}, "served": request.url.params["MST"]}})

    store = CorpusStore(tmp_path / "corpus")
    entry = {"id": "pipa", "source": "lawgokr", "target": "law", "title": "개인정보 보호법", "search": {"query": "개인정보 보호법"}, "match": {"법령명한글": "개인정보 보호법"}}
    assert fetch_all([entry], store, context(tmp_path, handler, with_law=True))[0].status == "new"
    assert "MST=1" in store.get("pipa").url
