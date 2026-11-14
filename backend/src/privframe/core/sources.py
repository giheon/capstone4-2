"""Fetchers: where corpus documents come from. One function per source type, registered in FETCHERS.

A list entry (configs/corpus/*.yaml → documents[]) names a fetcher in `source`; the rest of the entry is that
fetcher's input plus descriptive metadata (id, kind, title, citation, authority, dates, extra, parser).
Adding a source type = one function with the Fetcher signature and one FETCHERS entry.
"""
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .corpus import CorpusStore, DocRecord
from .lawgokr import LawGoKr

USER_AGENT = "PrivFrame capstone research"
META_KEYS = ("citation", "authority", "dates", "extra", "parser", "index")


class SourceError(RuntimeError):
    pass


class SourceUnavailable(SourceError):
    """The source cannot be used right now (for example a missing credential); not a failure of the entry."""


@dataclass
class Fetched:
    doc_id: str
    kind: str
    title: str
    format: str
    content: bytes
    url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchContext:
    root: Path  # repository root, for local_file paths
    http: httpx.Client
    law: LawGoKr | None = None  # None when LAW_OC is not configured


@dataclass
class Outcome:
    entry_id: str
    status: str  # new · updated · unchanged · unavailable · failed
    detail: str = ""
    records: list[DocRecord] = field(default_factory=list)


Fetcher = Callable[[dict[str, Any], FetchContext], list[Fetched]]


def _meta(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry[k] for k in META_KEYS if entry.get(k) is not None}


def _require(entry: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if not entry.get(k)]
    if missing:
        raise SourceError(f"목록 항목에 {', '.join(missing)}이(가) 없습니다: {entry.get('id') or entry.get('title')}")


def _format_of(entry: dict[str, Any], name: str, content_type: str = "") -> str:
    if entry.get("format"):
        return str(entry["format"])
    suffix = Path(name.split("?")[0]).suffix.lower().lstrip(".")
    if suffix in {"pdf", "html", "htm", "json", "txt"}:
        return "html" if suffix == "htm" else suffix
    for marker, fmt in (("pdf", "pdf"), ("html", "html"), ("json", "json"), ("text/plain", "txt")):
        if marker in content_type:
            return fmt
    raise SourceError(f"형식을 알 수 없습니다. 항목에 format을 적으세요: {name}")


def _check(fmt: str, content: bytes, where: str) -> None:
    if fmt == "pdf" and content[:5] != b"%PDF-":
        raise SourceError(f"PDF가 아닌 응답입니다(차단 페이지일 수 있음): {where}")
    if not content:
        raise SourceError(f"빈 응답입니다: {where}")


def http_file(entry: dict[str, Any], ctx: FetchContext) -> list[Fetched]:
    """A file at a direct address (guideline PDF, court judgment PDF …)."""
    _require(entry, "id", "kind", "title", "url")
    response = ctx.http.get(entry["url"], follow_redirects=True)
    if response.status_code != 200:
        raise SourceError(f"HTTP {response.status_code}: {entry['url']}")
    fmt = _format_of(entry, entry["url"], response.headers.get("content-type", ""))
    _check(fmt, response.content, entry["url"])
    return [Fetched(entry["id"], entry["kind"], entry["title"], fmt, response.content, entry["url"], _meta(entry))]


def law_html(entry: dict[str, Any], ctx: FetchContext) -> list[Fetched]:
    """A statute page from law.go.kr (lsInfoR.do). Needs no API key; articles sit in div.lawcon blocks."""
    _require(entry, "id", "title", "url")
    response = ctx.http.get(entry["url"], follow_redirects=True)
    if response.status_code != 200:
        raise SourceError(f"HTTP {response.status_code}: {entry['url']}")
    if b"lawcon" not in response.content:
        raise SourceError(f"조문 블록(div.lawcon)이 없는 응답입니다: {entry['url']}")
    return [Fetched(entry["id"], entry.get("kind", "law"), entry["title"], "html", response.content, entry["url"], _meta(entry))]


def local_file(entry: dict[str, Any], ctx: FetchContext) -> list[Fetched]:
    """A file someone downloaded by hand (sites that block automated requests), given as `path`."""
    _require(entry, "id", "kind", "title", "path")
    path = Path(entry["path"])
    path = path if path.is_absolute() else ctx.root / path
    if not path.exists():
        raise SourceUnavailable(f"파일이 아직 없습니다: {entry['path']}" + (f" (받을 곳: {entry['page']})" if entry.get("page") else ""))
    content = path.read_bytes()
    fmt = _format_of(entry, path.name)
    _check(fmt, content, str(path))
    return [Fetched(entry["id"], entry["kind"], entry["title"], fmt, content, entry.get("page") or entry.get("url"), _meta(entry))]


# lawService.do identifies a document by MST (statutes) or ID; the serial comes from the search hit
LAW_DETAIL = {"law": ("MST", "법령일련번호"), "admrul": ("ID", "행정규칙일련번호"), "prec": ("ID", "판례일련번호"), "detc": ("ID", "헌재결정례일련번호"), "expc": ("ID", "법령해석례일련번호"), "ppc": ("ID", "결정문일련번호")}


def _serial(hit: dict[str, Any], preferred: str) -> str | None:
    if hit.get(preferred):
        return str(hit[preferred])
    return next((str(v) for k, v in hit.items() if v and k.endswith("일련번호")), None)


def lawgokr(entry: dict[str, Any], ctx: FetchContext) -> list[Fetched]:
    """국가법령정보 Open API. `target` plus either `params` (direct lawService call) or `search` (lawSearch
    parameters; the first `limit` hits are fetched, after keeping only hits whose fields equal `match` when
    given, e.g. match: {법령명한글: 개인정보 보호법}). The stored file is the API's JSON, unchanged."""
    _require(entry, "id", "target", "title")
    if ctx.law is None:
        raise SourceUnavailable("LAW_OC가 없어 국가법령정보 API를 쓸 수 없습니다")
    target = entry["target"]
    kind = entry.get("kind", target)
    param, serial_key = LAW_DETAIL.get(target, ("ID", ""))
    calls: list[tuple[str, dict[str, Any], str]] = []  # (doc id, lawService params, title)
    if entry.get("params"):
        calls.append((entry["id"], {"target": target, **entry["params"]}, entry["title"]))
    else:
        _require(entry, "search")
        hits = LawGoKr.items(ctx.law.search(target, **entry["search"]))
        wanted = entry.get("match") or {}
        hits = [h for h in hits if all(str(h.get(k, "")).strip() == str(v) for k, v in wanted.items())]
        limit = int(entry.get("limit", 1))
        if not hits:
            raise SourceError(f"검색 결과가 없습니다: {target} {entry['search']}" + (f" (match {wanted})" if wanted else ""))
        for hit in hits[:limit]:
            serial = _serial(hit, serial_key)
            if not serial:
                raise SourceError(f"검색 결과에서 일련번호를 찾지 못했습니다: {sorted(hit)[:8]}")
            doc_id = entry["id"] if limit == 1 else f"{entry['id']}-{serial}"
            title = entry["title"] if limit == 1 else next((str(hit[k]) for k in ("사건명", "안건명", "법령명한글", "행정규칙명") if hit.get(k)), entry["title"])
            calls.append((doc_id, {"target": target, param: serial}, title))
    out = []
    for doc_id, params, title in calls:
        data = scrub(ctx.law.service(**params))
        content = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        meta = _meta(entry)
        meta["extra"] = {**meta.get("extra", {}), "api": _identifiers(data)}
        out.append(Fetched(doc_id, kind, title, "json", content, LawGoKr.public_url("lawService.do", {"type": "JSON", **params}), meta))
    return out


def scrub(value: Any) -> Any:
    """API data with the credential blanked: search hits link to themselves with the caller's OC in the address.
    Nothing else is touched (an original is stored as it came)."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str) and "OC=" in value:
        return re.sub(r"OC=[^&\"'\s]*", "OC=", value)
    return value


def _identifiers(data: Any) -> dict[str, str]:
    """The response's short fields (사건번호, 선고일자, 법원명, 시행일자, 공포번호 …), kept as registry metadata."""
    while isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), dict):
        data = next(iter(data.values()))
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for holder in (data, *(v for k, v in data.items() if isinstance(v, dict) and k.endswith("기본정보"))):
            out.update({k: v.strip() for k, v in holder.items() if isinstance(v, str) and 0 < len(v.strip()) < 40})
    return out


FETCHERS: dict[str, Fetcher] = {"http_file": http_file, "law_html": law_html, "local_file": local_file, "lawgokr": lawgokr}


def fetch_all(entries: list[dict[str, Any]], store: CorpusStore, ctx: FetchContext, default_index: bool | None = None) -> list[Outcome]:
    """Fetch every entry and store what changed. One bad entry does not stop the rest.
    `default_index` is what a list says about its new documents (`defaults.index`); an entry's own `index`
    wins, and a document already stored keeps its setting."""
    outcomes = []
    for entry in entries:
        entry_id = str(entry.get("id") or entry.get("title") or "?")
        fetcher = FETCHERS.get(str(entry.get("source")))
        if fetcher is None:
            outcomes.append(Outcome(entry_id, "failed", f"알 수 없는 수집기 '{entry.get('source')}' (쓸 수 있는 것: {', '.join(FETCHERS)})"))
            continue
        try:
            fetched = fetcher(entry, ctx)
        except SourceUnavailable as error:
            outcomes.append(Outcome(entry_id, "unavailable", str(error)))
            continue
        except (SourceError, httpx.HTTPError, OSError, ValueError, RuntimeError) as error:
            outcomes.append(Outcome(entry_id, "failed", f"{type(error).__name__}: {error}"))
            continue
        statuses, records = [], []
        for item in fetched:
            if "index" not in item.meta and default_index is not None and store.get(item.doc_id) is None:
                item.meta["index"] = default_index
            record, status = store.put_raw(doc_id=item.doc_id, kind=item.kind, title=item.title, source=str(entry["source"]), format=item.format, content=item.content, url=item.url, **item.meta)
            statuses.append(status)
            records.append(record)
        status = "new" if "new" in statuses else "updated" if "updated" in statuses else "unchanged"
        outcomes.append(Outcome(entry_id, status, f"{len(records)}건", records))
    return outcomes


def http_client(timeout: float = 60) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
