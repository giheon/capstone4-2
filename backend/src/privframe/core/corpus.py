"""Legal corpus storage: raw files, a document registry and normalized documents.

Layout under <data_dir>/corpus:
  raw/<doc_id>.<ext>     source bytes exactly as fetched
  registry.jsonl         one record per document: where it came from, when, and its sha256
  docs/<doc_id>.json     normalized document: the record plus ordered text units (article, page, field …)
  index/<fingerprint>/   search index built from docs (retrieval.py)

Nothing here decides which documents belong in the corpus. Lists live in configs/corpus/*.yaml and are passed
to the fetch command, so the same tools serve a five-document smoke set and the service corpus.
"""
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .util import now_iso, write_atomic, write_json

DOC_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")


@dataclass
class DocRecord:
    doc_id: str
    kind: str  # law · admrul · prec · detc · expc · ppc · guideline … an open vocabulary, not an enum
    title: str
    source: str  # the fetcher that produced it (sources.py)
    format: str  # pdf · html · json · txt
    sha256: str
    size: int
    fetched_at: str
    url: str | None = None  # public address, never containing a credential
    citation: str | None = None  # how the document is cited ("개인정보 보호법", "대법원 2020다253423")
    authority: int | None = None  # 1 규범 · 2 해석 · 3 참고 (design §9.1); None when not classified
    dates: dict[str, str] = field(default_factory=dict)  # effective_from, decided_on, published_at …
    extra: dict[str, Any] = field(default_factory=dict)
    parser: str | None = None  # parser override; default is chosen from source and format
    index: bool = True  # False = keep the original on file but leave it out of search indexes

    @property
    def raw_name(self) -> str:
        return f"{self.doc_id}.{self.format}"

    @property
    def cite(self) -> str:
        return self.citation or self.title


@dataclass
class Unit:
    unit_id: str  # stable inside the document: "제25조의2", "p3", "판결요지"
    label: str  # what a reader calls it: "제25조의2(이동형 영상정보처리기기의 운영 제한)", "3쪽"
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Document:
    record: DocRecord
    parser: str
    units: list[Unit]

    def unit(self, unit_id: str) -> Unit | None:
        return next((u for u in self.units if u.unit_id == unit_id), None)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()




class CorpusStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.docs_dir = self.root / "docs"
        self.index_dir = self.root / "index"
        self.registry_file = self.root / "registry.jsonl"

    # ---------- registry ----------

    def records(self, kinds: list[str] | None = None) -> list[DocRecord]:
        if not self.registry_file.exists():
            return []
        latest: dict[str, DocRecord] = {}
        for line in self.registry_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = DocRecord(**json.loads(line))
                latest[record.doc_id] = record
        records = sorted(latest.values(), key=lambda r: r.doc_id)
        return [r for r in records if not kinds or r.kind in kinds]

    def get(self, doc_id: str) -> DocRecord | None:
        return next((r for r in self.records() if r.doc_id == doc_id), None)

    def find(self, ref: str) -> DocRecord | None:
        """A document by id, or by its exact citation or title."""
        records = self.records()
        return next((r for r in records if r.doc_id == ref), None) or next((r for r in records if ref in (r.citation, r.title)), None)

    def _write_registry(self, records: list[DocRecord]) -> None:
        body = "".join(json.dumps(asdict(r), ensure_ascii=False, sort_keys=True) + "\n" for r in sorted(records, key=lambda r: r.doc_id))
        write_atomic(self.registry_file, body)

    # ---------- raw documents ----------

    def put_raw(self, *, doc_id: str, kind: str, title: str, source: str, format: str, content: bytes, **meta: Any) -> tuple[DocRecord, str]:
        """Store source bytes. Returns the record and what happened: "new", "updated" or "unchanged"."""
        if not DOC_ID.match(doc_id):
            raise ValueError(f"문서 ID는 영문 소문자·숫자·'.'·'_'·'-'로 2~80자여야 합니다: {doc_id!r}")
        if not content:
            raise ValueError(f"{doc_id}: 내용이 비어 있습니다")
        digest = sha256_bytes(content)
        records = {r.doc_id: r for r in self.records()}
        previous = records.get(doc_id)
        same_bytes = bool(previous and previous.sha256 == digest and (self.raw_dir / previous.raw_name).exists())
        record = DocRecord(
            doc_id=doc_id,
            kind=kind,
            title=title,
            source=source,
            format=format,
            sha256=digest,
            size=len(content),
            fetched_at=previous.fetched_at if same_bytes else now_iso(),
            **{k: v for k, v in meta.items() if v is not None},
        )
        if same_bytes:
            if record != previous:  # only the description changed (title, citation, authority …)
                records[doc_id] = record
                self._write_registry(list(records.values()))
            return record, "unchanged"
        if previous and previous.raw_name != record.raw_name:
            (self.raw_dir / previous.raw_name).unlink(missing_ok=True)
        write_atomic(self.raw_dir / record.raw_name, content)
        records[doc_id] = record
        self._write_registry(list(records.values()))
        return record, "updated" if previous else "new"

    def raw_bytes(self, record: DocRecord) -> bytes:
        return (self.raw_dir / record.raw_name).read_bytes()

    # ---------- normalized documents ----------

    def save_document(self, document: Document) -> Path:
        body = {"record": asdict(document.record), "parser": document.parser, "units": [asdict(u) for u in document.units]}
        path = self.docs_dir / f"{document.record.doc_id}.json"
        write_json(path, body, indent=1)
        return path

    def load_document(self, doc_id: str) -> Document | None:
        path = self.docs_dir / f"{doc_id}.json"
        if not path.exists():
            return None
        body = json.loads(path.read_text(encoding="utf-8"))
        return Document(record=DocRecord(**body["record"]), parser=body["parser"], units=[Unit(**u) for u in body["units"]])

    def documents(self, kinds: list[str] | None = None) -> list[Document]:
        """Normalized documents whose raw source is still the registered one (stale parses are skipped).
        Descriptive metadata always comes from the registry, so editing a list entry needs no re-parse."""
        out = []
        for record in self.records(kinds):
            document = self.load_document(record.doc_id)
            if document and document.record.sha256 == record.sha256:
                out.append(Document(record=record, parser=document.parser, units=document.units))
        return out
