"""Parsers: raw bytes → ordered text units. One function per format, registered in PARSERS.

A unit is the smallest piece a citation can point at (an article, a page, a field of a judgment). Chunking for
search happens later (retrieval.py); parsers only recover the document's own structure and never rewrite text.
"""
import io
import json
import re
from collections.abc import Callable
from typing import Any

from .corpus import CorpusStore, DocRecord, Document, Unit

ARTICLE = re.compile(r"(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*(\([^)]*\))?")
TAG = re.compile(r"<[^>]+>")


class ParseError(RuntimeError):
    pass


Parser = Callable[[DocRecord, bytes], list[Unit]]


def _unique(units: list[Unit]) -> list[Unit]:
    """Unit ids must be unique inside a document (a statute can list a deleted article twice)."""
    seen: dict[str, int] = {}
    for unit in units:
        seen[unit.unit_id] = seen.get(unit.unit_id, 0) + 1
        if seen[unit.unit_id] > 1:
            unit.unit_id = f"{unit.unit_id}#{seen[unit.unit_id]}"
    return units


def law_html(record: DocRecord, content: bytes) -> list[Unit]:
    """law.go.kr statute page: one unit per article (div.lawcon)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content.decode("utf-8-sig", errors="replace"), "html.parser")
    for node in soup.select("script, style, noscript, template, button, select"):
        node.decompose()
    units = []
    for node in soup.select("div.lawcon"):
        text = node.get_text(" ", strip=True)
        match = ARTICLE.match(text)
        if not text or not match:
            continue
        number = re.sub(r"\s+", "", match.group(1))
        units.append(Unit(unit_id=number, label=number + (match.group(2) or ""), text=text))
    if not units:
        raise ParseError(f"{record.doc_id}: 조문(div.lawcon)을 찾지 못했습니다")
    return _unique(units)


def pdf_pages(content: bytes) -> list[str]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [(page.extract_text() or "").strip() for page in pdf.pages]


def units_from_pages(pages: list[str]) -> list[Unit]:
    return [Unit(unit_id=f"p{number}", label=f"{number}쪽", text=text, meta={"page": number}) for number, text in enumerate(pages, 1) if text]


def pdf(record: DocRecord, content: bytes) -> list[Unit]:
    """Any text PDF: one unit per page. Scanned PDFs have no text layer and need OCR, which is not done here."""
    units = units_from_pages(pdf_pages(content))
    if not units:
        raise ParseError(f"{record.doc_id}: PDF에 추출할 글자가 없습니다(스캔본이면 OCR 필요)")
    return units


def _clean(value: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", TAG.sub("", re.sub(r"<br\s*/?>", "\n", value))).strip()


def _leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_clean(value)] if value.strip() else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    return []


def _body(data: Any) -> dict[str, Any]:
    """The API wraps a document in one top-level object (PrecService, 법령 …); unwrap single-key dicts."""
    while isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), dict):
        data = next(iter(data.values()))
    return data if isinstance(data, dict) else {"본문": data}


def _nested_text(node: Any) -> list[str]:
    """An article's own text followed by its 항 · 호 · 목, in document order."""
    if isinstance(node, list):
        return [line for item in node for line in _nested_text(item)]
    if not isinstance(node, dict):
        return _leaves(node)
    lines = [leaf for key, value in node.items() if key.endswith("내용") for leaf in _leaves(value)]
    for key in ("항", "호", "목"):
        if key in node:
            lines.extend(_nested_text(node[key]))
    return lines


def _statute_units(articles: list[dict[str, Any]]) -> list[Unit]:
    """법령 API: 조문단위 entries marked 조문 (전문 entries are chapter headings)."""
    units = []
    for a in articles:
        if a.get("조문여부") != "조문":
            continue
        number = f"제{a.get('조문번호', '').strip()}조" + (f"의{a['조문가지번호'].strip()}" if a.get("조문가지번호") else "")
        title = a.get("조문제목", "").strip()
        lines = _nested_text(a) + _leaves(a.get("조문참고자료", ""))
        meta = {k: a[k] for k in ("조문시행일자", "조문키") if a.get(k)}
        units.append(Unit(unit_id=number, label=f"{number}({title})" if title else number, text="\n".join(lines), meta=meta))
    return units


def _rule_units(lines: list[Any]) -> list[Unit]:
    """행정규칙 API: 조문내용 is a list of strings, one per article, with chapter headings in between."""
    units: list[Unit] = []
    for line in _leaves(lines):
        match = ARTICLE.match(line)
        if match:
            number = re.sub(r"\s+", "", match.group(1))
            units.append(Unit(unit_id=number, label=number + (match.group(2) or ""), text=line))
        elif units and not re.match(r"제\s*\d+\s*[장절관]", line):
            units[-1].text += "\n" + line  # continuation of the previous article
    return units


# fields that name or number a document rather than say something citable
IDENTIFIER_SUFFIXES = ("명", "코드", "번호", "일자", "일시", "링크", "키", "여부")


def lawgokr_json(record: DocRecord, content: bytes) -> list[Unit]:
    """국가법령정보 API JSON (shapes checked against real responses, 2026-11-11).
    Statutes and administrative rules become one unit per article. Everything else is read without assuming
    field names: each passage field (판시사항, 판결요지, 판례내용, 결정요지, 전문, 질의요지, 회답, 이유, 주문 …)
    becomes a unit; identifiers stay in the registry (sources._identifiers)."""
    try:
        body = _body(json.loads(content.decode("utf-8")))
    except ValueError as error:
        raise ParseError(f"{record.doc_id}: JSON이 아닙니다 ({error})") from error
    articles = (body.get("조문") or {}).get("조문단위") if isinstance(body.get("조문"), dict) else None
    if isinstance(articles, list):
        units = _statute_units(articles)
    elif isinstance(body.get("조문내용"), list):
        units = _rule_units(body["조문내용"])
    else:
        units = []
        for key, value in body.items():
            if isinstance(value, dict):  # one level of nesting (PpcService.의결서)
                continue
            text = "\n".join(_leaves(value))
            if len(text) >= 40 and not key.endswith(IDENTIFIER_SUFFIXES):
                units.append(Unit(unit_id=key, label=key, text=text))
    if not units:
        raise ParseError(f"{record.doc_id}: 본문 항목을 찾지 못했습니다 (최상위 항목: {sorted(body)[:8]})")
    return _unique(units)


def text(record: DocRecord, content: bytes) -> list[Unit]:
    body = content.decode("utf-8-sig", errors="replace").strip()
    if not body:
        raise ParseError(f"{record.doc_id}: 내용이 비어 있습니다")
    return [Unit(unit_id="text", label="본문", text=body)]


PARSERS: dict[str, Parser] = {"law_html": law_html, "pdf": pdf, "lawgokr_json": lawgokr_json, "text": text}
# default parser for a (source, format) pair; a list entry can override it with `parser`
DEFAULTS = {("law_html", "html"): "law_html", ("lawgokr", "json"): "lawgokr_json"}
BY_FORMAT = {"pdf": "pdf", "txt": "text", "json": "lawgokr_json", "html": "law_html"}


def parser_for(record: DocRecord) -> str:
    name = record.parser or DEFAULTS.get((record.source, record.format)) or BY_FORMAT.get(record.format)
    if name not in PARSERS:
        raise ParseError(f"{record.doc_id}: '{record.format}' 형식의 파서가 없습니다 (쓸 수 있는 것: {', '.join(PARSERS)})")
    return name


def parse(store: CorpusStore, record: DocRecord) -> Document:
    name = parser_for(record)
    document = Document(record=record, parser=name, units=PARSERS[name](record, store.raw_bytes(record)))
    store.save_document(document)
    return document


def parse_all(store: CorpusStore, doc_ids: list[str] | None = None, force: bool = False) -> list[tuple[DocRecord, str]]:
    """Parse registered documents that have no current normalized form. Returns (record, "parsed N" | "current" | error)."""
    out = []
    for record in store.records():
        if doc_ids and record.doc_id not in doc_ids:
            continue
        existing = store.load_document(record.doc_id)
        if existing and existing.record.sha256 == record.sha256 and existing.parser == parser_for(record) and not force:
            out.append((record, "current"))
            continue
        try:
            out.append((record, f"parsed {len(parse(store, record).units)}"))
        except (ParseError, ValueError, OSError) as error:
            out.append((record, f"error: {error}"))
    return out
