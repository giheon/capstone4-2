"""Selecting what belongs in the legal corpus (M4-1). The criteria are the team's D1–D13
(docs/design/13-corpus-selection-definitions.md); the rules file (configs/corpus/selection.yaml) turns them
into lists and numbers, and its hash is part of the list version.

One run goes through these steps and writes down what each did, so the result can be checked and repeated:

  seeds     norms and guides named in the rules (whole originals; articles are graded for question coverage)
  discover  candidates from the search paths ①②③, every search logged with its conditions and counts
  process   metadata rule → original secured (stored unindexed) → text rule → grading → adoption
  follow    ④ cases an adopted case cites and cases that cite it, at most `rounds` times
  balance   a question with cases on one side only gets one more search with wider words
  changes   later full-bench judgments that say they change an adopted case
  trail     the same case in other instances: the judgment under review, the first instance, after remand
  norms     statutes the adopted cases cite, against what was collected
  publish   the list (a fetch list other machines can reproduce the collection from) and the check report

What the model says about a document is a selection record. It is kept beside the corpus, never inside a
document, and it never stands in for the original text. Nothing here indexes anything: originals are stored
with index=False, and what gets indexed is decided after collection.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from . import casetext
from .corpus import CorpusStore, DocRecord, Document
from .lawgokr import LawApiAuthError, LawApiError, LawApiUnavailable, LawGoKr
from .parsers import NO_TEXT, ParseError, _body, parse, pdf_pages
from .sources import LAW_DETAIL, _identifiers, scrub
from .util import now_iso, write_atomic, write_json

SUPREME = "400201"  # 법원종류코드 of the Supreme Court in the precedent API
INBOX = ("inbox", "judgments")  # under the corpus folder: judgments a person obtained because the API does not have them
INBOX_SUFFIXES = (".pdf", ".txt")
CHECK_SHARE = 0.85  # the second reading stops at this share of the cost cap: what is left grades the candidates of later rounds
KIND_LABEL = {"prec": "판례", "detc": "헌재 결정", "expc": "법령해석례", "ppc": "개인정보위 결정문", "law": "법령 조문", "admrul": "행정규칙 조문", "guideline": "안내서", "mediation": "분쟁조정 사례집", "casebook": "판례집"}
SERIAL = {target: key for target, (_, key) in LAW_DETAIL.items()}
QUESTIONS = Literal["Q0", "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"]


class Halted(RuntimeError):
    """The run stops and nothing is published; running it again continues it. Raised when 국가법령정보 API does
    not answer (maintenance, no network, credential refused) or refuses a search: going on would record
    "not found" for what was never asked."""


class Answer(BaseModel):
    question: QUESTIONS
    score: Literal[1, 2, 3]
    direction: Literal["protect", "against", "mixed", "none"]
    quote: str
    why: str


class Grade(BaseModel):
    scope: Literal["in", "out", "hold"]
    scope_reason: str
    about_images: bool
    answers: list[Answer]


Grader = Callable[[str, str], tuple[Grade, float]]  # (instructions, document) → (grade, cost in USD)


class Rules:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, Any] = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.sha = hashlib.sha256(json.dumps(self.data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def version(self, name: str) -> dict[str, Any]:
        if name not in self.data["versions"]:
            raise ValueError(f"규칙에 '{name}' 버전이 없습니다 (있는 것: {', '.join(self.data['versions'])})")
        return self.data["versions"][name]

    def instructions(self) -> str:
        scope, scale = self.data["scope"], self.data["grading"]["scale"]
        lines = [
            "당신은 영상 비식별화 시스템의 법률 코퍼스에 넣을 글을 고르는 선별 담당이다. 주어진 글(판결, 결정, 해석, 조문, 안내서)을 끝까지 읽고 아래 기준으로 판정한다. 요약이나 해설은 쓰지 않는다.",
            "",
            "[범위 기준]",
            scope["criterion"],
            "",
            "[질문]",
            *[f"{key}: {text}" for key, text in scope["questions"].items()],
            "",
            '[범위 밖] 아래에만 해당하면 scope = "out" 이고 scope_reason 에 해당 항목을 적는다.',
            *[f"- {item}" for item in scope["out"]],
            '어느 질문에도 닿지 않으면 scope = "out", scope_reason = "무관".',
            "",
            '[보류] 아래가 쟁점이면 scope = "hold" 이고 scope_reason 에 해당 항목을 적는다.',
            *[f"- {item}" for item in scope["hold"]],
            "",
            "[점수] 질문마다 따로 매긴다. 1점 이상인 질문만 answers 에 넣는다.",
            *[f"{score}: {text}" for score, text in sorted(scale.items(), reverse=True)],
            "",
            "[방향] 그 질문에 대한 이 글의 결론이 누구 쪽인가.",
            "protect: 찍힌 사람(정보주체) 쪽. 침해를 인정했다, 가리거나 동의를 받아야 한다, 제공 · 열람을 제한했다",
            "against: 찍거나 공개한 쪽. 침해를 부정했다, 허용했다, 예외를 인정했다",
            "mixed: 쟁점마다 갈린다",
            "none: 결론이 없거나 알 수 없다 (조문과 안내서는 대개 none)",
            "주문의 문구만으로 정하지 않는다. 파기환송은 다시 심리하라는 뜻일 뿐이다. 판단 이유를 보고 정하고, 알 수 없으면 none 이다.",
            "",
            "[근거 문장] quote 에는 그 판정의 근거가 된 문장 하나를 원문에서 한 글자도 바꾸지 않고 옮긴다(200자 이내). 원심의 판단, 당사자의 주장, 반대의견이 아니라 이 글을 낸 법원 · 기관 자신의 판단에서 고른다. why 에는 왜 그 점수인지 한 문장만 쓴다.",
            "",
            "[about_images] 사건이나 조문 자체가 촬영, 영상, 사진, 초상, 영상정보처리기기, 화면에 보이는 것(얼굴, 번호판, 명찰 등), 촬영 위치를 다루면 true. 영상이 증거로만 나오면 false.",
        ]
        return "\n".join(lines)

    @property
    def rubric_sha(self) -> str:
        return hashlib.sha256(self.instructions().encode("utf-8")).hexdigest()[:12]


def top_scores(grade: dict[str, Any] | None) -> dict[str, int]:
    """question → score, for a grade whose scope is in."""
    if not grade or grade.get("scope") != "in":
        return {}
    out: dict[str, int] = {}
    for answer in grade.get("answers") or []:
        out[answer["question"]] = max(out.get(answer["question"], 0), int(answer["score"]))
    return out


class Selection:
    def __init__(self, rules: Rules, store: CorpusStore, law: LawGoKr | None, directory: Path, *, version: str, graders: dict[str, Grader], root: Path, say: Callable[[str], None] = print, pause: float = 0.15, grade_cache: Path | None = None, cost_cap: float | None = None):
        self.rules, self.store, self.law, self.dir, self.root = rules, store, law, Path(directory), Path(root)
        self.version_name, self.version = version, rules.version(version)
        self.graders, self.say, self.pause = graders, say, pause
        # how much the models may cost in all. A limit on the run, not a selection rule: raising it continues the same run
        self.cost_cap = float(cost_cap if cost_cap is not None else rules["grading"].get("cost_cap_usd", 15))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"
        fresh = {"rules_sha": rules.sha, "version": version, "started_at": now_iso(), "candidates": {}, "norms": {}, "guides": {}, "relations": [], "balance": {}, "done": [], "cost_usd": 0.0, "grading": {}, "norm_refs": []}
        self.state: dict[str, Any] = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else fresh
        if self.state["rules_sha"] != rules.sha or self.state["version"] != version:
            raise ValueError(f"{self.dir}에는 다른 규칙이나 버전으로 한 수집이 있습니다. 규칙을 바꿨으면 새 폴더에서 실행하세요.")
        self.cands: dict[str, dict[str, Any]] = self.state["candidates"]
        self._grades: dict[str, dict[str, Any]] = {}
        self._calls: list[dict[str, Any]] = []  # every model call that was paid for, also two for the same text
        self._grade_file = Path(grade_cache) if grade_cache else self.dir / "grades.jsonl"  # shared by runs: a grade depends on text, instructions and model only
        self._lock = threading.Lock()
        if self._grade_file.exists():
            for line in self._grade_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._grades[row["cache"]] = row
                    self._calls.append(row)
        self._docs: dict[str, Document] = {}
        self._bodies: dict[str, dict[str, Any]] = {}

    # ---------- plumbing ----------

    def save(self) -> None:
        write_json(self.state_file, self.state)

    def _log(self, name: str | Path, row: dict[str, Any]) -> None:
        path = name if isinstance(name, Path) else self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _api(self, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """One API call, tried three times. An API that does not answer stops the run; an answer that refuses
        this one request is that request's failure (LawApiError), for the caller to record."""
        if self.law is None:
            raise Halted("LAW_OC가 없어 국가법령정보 API를 쓸 수 없습니다")
        for attempt in range(3):
            try:
                time.sleep(self.pause)
                return scrub(call())
            except LawApiAuthError as error:
                raise Halted(str(error)) from error
            except LawApiError as error:
                if attempt < 2:
                    time.sleep((2 if self.pause else 0) * (attempt + 1))
                elif isinstance(error, LawApiUnavailable):
                    raise Halted(f"국가법령정보 API가 응답하지 않습니다: {error}") from error
                else:
                    raise
        raise AssertionError("unreachable")

    def _asked(self, target: str, path: str, params: dict[str, Any], **paging: Any) -> dict[str, Any]:
        """One request of a search. A search that fails stops the run: no answer is not an answer with no hits."""
        try:
            return self._api(lambda: self.law.search(target, **paging, **params))
        except (LawApiError, Halted) as error:
            self._log("searches.jsonl", {"at": now_iso(), "path": path, "target": target, "params": params, "error": scrub(str(error))})
            if isinstance(error, Halted):
                raise
            raise Halted(f"검색이 실패했습니다 ({path} · {target} · {params}): {error}") from error

    def search(self, target: str, path: str, **params: Any) -> list[dict[str, Any]]:
        """Every hit of one search (all pages up to the limit), logged with its conditions, counts and time."""
        limit = int(self.rules["paths"].get("max_hits_per_search", 1000))
        hits: list[dict[str, Any]] = []
        total, page = 0, 1
        while True:
            result = self._asked(target, path, params, display=100, page=page)
            total = LawGoKr.count(result)
            batch = LawGoKr.items(result)
            hits.extend(batch)
            if not batch or len(hits) >= min(total, limit):
                break
            page += 1
        self._log("searches.jsonl", {"at": now_iso(), "path": path, "target": target, "params": params, "total": total, "read": len(hits), "cut": len(hits) < total})
        return hits[:limit]

    def count(self, target: str, path: str, **params: Any) -> int:
        total = LawGoKr.count(self._asked(target, path, params, display=1))
        self._log("searches.jsonl", {"at": now_iso(), "path": path, "target": target, "params": params, "total": total, "read": 0, "count_only": True})
        return total

    # ---------- candidates ----------

    def _from_hit(self, target: str, hit: dict[str, Any]) -> dict[str, Any]:
        serial = str(hit.get(SERIAL[target]) or "")
        if target == "prec":
            number = casetext.squeeze(str(hit.get("사건번호") or "")).split(",")[0]
            court = casetext.squeeze(str(hit.get("법원명") or "")) or casetext.court_of(number)
            base = {"court": court, "no": number, "date": casetext.iso_date(str(hit.get("선고일자") or "")), "title": str(hit.get("사건명") or ""), "case_type": str(hit.get("사건종류명") or ""), "judgment_type": str(hit.get("판결유형") or ""), "supreme": str(hit.get("법원종류코드") or "") == SUPREME or court == "대법원"}
            key = f"prec:{court}:{number}"
        elif target == "detc":
            number = casetext.squeeze(str(hit.get("사건번호") or ""))
            base = {"court": "헌법재판소", "no": number, "date": casetext.iso_date(str(hit.get("종국일자") or "")), "title": str(hit.get("사건명") or ""), "case_type": "헌법", "judgment_type": "결정", "supreme": False}
            key = f"detc:{number}"
        elif target == "expc":
            base = {"court": str(hit.get("회신기관명") or "법제처"), "no": str(hit.get("안건번호") or ""), "date": casetext.iso_date(str(hit.get("회신일자") or "")), "title": str(hit.get("안건명") or ""), "case_type": "", "judgment_type": "해석", "supreme": False}
            key = f"expc:{serial}"
        else:
            base = {"court": "개인정보보호위원회", "no": str(hit.get("의안번호") or ""), "date": casetext.iso_date(str(hit.get("의결일") or "")), "title": str(hit.get("안건명") or ""), "case_type": "", "judgment_type": "결정", "supreme": False}
            key = f"ppc:{serial}"
        return {"key": key, "kind": target, "serial": serial, **base}

    @staticmethod
    def _mark(cand: dict[str, Any], path: str, by: str, purpose: str | None) -> dict[str, Any]:
        if not any(f["path"] == path and f["by"] == by for f in cand["found"]):
            cand["found"].append({"path": path, "by": by, "at": now_iso()})
        if purpose and purpose not in cand["purposes"]:
            cand["purposes"].append(purpose)
        return cand

    def add(self, target: str, hit: dict[str, Any], path: str, by: str, round_: int = 0, purpose: str | None = None) -> dict[str, Any]:
        seed = self._from_hit(target, hit)
        cand = self.cands.get(seed["key"])
        if cand is None:
            cand = self.cands[seed["key"]] = {**seed, "found": [], "round": round_, "purposes": [], "flags": []}
        elif not cand.get("serial") and seed["serial"]:  # named before, and the API has it after all
            cand.update({k: v for k, v in seed.items() if v})
            cand.pop("secured", None)
        return self._mark(cand, path, by, purpose)

    def lookup(self, number: str, court: str, date: str, path: str, by: str, round_: int, purpose: str | None = None) -> dict[str, Any]:
        """The candidate for a case named by its number (a citation, a judgment under review). When the API
        does not have it, the candidate stays without an original and says where it was looked for."""
        court = court or casetext.court_of(number)
        known = next((c for c in self.cands.values() if c["no"] == number and (not court or not c["court"] or c["court"] == court)), None)
        if known:
            return self._mark(known, path, by, purpose)
        constitutional = court == "헌법재판소"
        target = "detc" if constitutional else "prec"
        hits = self.search(target, path, **({"query": number} if constitutional else {"nb": number}))
        exact = [h for h in hits if casetext.squeeze(str(h.get("사건번호") or "")).split(",")[0] == number]
        if not constitutional and court:
            same = [h for h in exact if casetext.squeeze(str(h.get("법원명") or "")) == court]
            exact = same or (exact if casetext.court_of(number) == "대법원" else [])  # a lower court's number is only unique at that court
        elif not constitutional and len(exact) > 1:
            exact = []
        if exact:
            return self.add(target, exact[0], path, by, round_, purpose)
        key = f"detc:{number}" if constitutional else f"prec:{court}:{number}"
        cand = self.cands.setdefault(key, {"key": key, "kind": target, "serial": "", "court": court, "no": number, "date": date, "title": "", "case_type": "", "judgment_type": "", "supreme": court == "대법원", "found": [], "round": round_, "purposes": [], "flags": []})
        cand["secured"] = {"status": "not_found", "why": f"국가법령정보 API({target})에서 사건번호 {number}을(를) 찾지 못함", "looked_at": now_iso()}
        return self._mark(cand, path, by, purpose)

    # ---------- originals ----------

    @staticmethod
    def cite(cand: dict[str, Any]) -> tuple[str, str]:
        """(title, citation) the way the document is cited."""
        date = cand.get("date") or ""
        when = f"{int(date[:4])}. {int(date[5:7])}. {int(date[8:10])}. " if len(date) == 10 else ""
        if cand["kind"] == "prec":
            word = "전원합의체 판결" if "전원합의체" in cand.get("judgment_type", "") else ("결정" if "결정" in cand.get("judgment_type", "") else "판결")
            return f"{cand['court']} {when}선고 {cand['no']} {word}".replace("  ", " "), f"{cand['court']} {cand['no']}"
        if cand["kind"] == "detc":
            return f"헌법재판소 {when}선고 {cand['no']} 결정", f"헌법재판소 {cand['no']}"
        if cand["kind"] == "expc":
            return f"{cand['court']} {when}회신 {cand['no']} 해석례 ({cand['title'][:60]})", f"법령해석례 {cand['no']}"
        return f"개인정보보호위원회 {when}의결 {cand['no']} ({cand['title'][:60]})".replace("의결  (", "의결 ("), f"개인정보보호위원회 {cand['no'] or '결정문 ' + cand['serial']}"

    @staticmethod
    def _ppc_fields(cand: dict[str, Any], body: dict[str, Any]) -> None:
        """The list hit of a committee decision is empty: its name, number and date are in the body."""
        if cand["kind"] == "ppc":
            number = str(body.get("안건번호") or cand["no"] or "").strip()
            cand.update(no="" if number.lower() == "null" else number, date=casetext.iso_date(str(body.get("의결일자") or body.get("의결연월일") or "")) or cand["date"], title=str(body.get("안건명") or cand["title"]).strip())

    def _parsed(self, record: DocRecord) -> tuple[DocRecord, Document]:
        """The normalized form of a stored original. A PDF without a text layer is read by OCR, and the record says so."""
        document = self.store.load_document(record.doc_id)
        if document is not None and document.record.sha256 == record.sha256:
            return record, document
        try:
            return record, parse(self.store, record)
        except ParseError as error:
            if NO_TEXT not in str(error):
                raise
        self.say(f"  {record.doc_id}: 글자 층이 없는 PDF라 OCR로 읽는다 (시간이 걸린다)")
        record, _ = self.store.put_raw(doc_id=record.doc_id, kind=record.kind, title=record.title, source=record.source, format=record.format, content=self.store.raw_bytes(record), url=record.url, citation=record.citation, authority=record.authority, dates=record.dates, extra={**(record.extra or {}), "read_by": "ocr"}, parser="pdf_ocr", index=record.index)
        return record, parse(self.store, record)

    def document(self, doc_id: str) -> Document | None:
        if doc_id not in self._docs:
            loaded = self.store.load_document(doc_id)
            if loaded is None:
                return None
            self._docs[doc_id] = loaded
        return self._docs[doc_id]

    def body(self, doc_id: str) -> dict[str, Any]:
        """The API's own fields of a stored document (참조조문, 참조판례 and the like are too short to be units)."""
        if doc_id not in self._bodies:
            record = self.store.get(doc_id)
            try:
                self._bodies[doc_id] = _body(json.loads(self.store.raw_bytes(record).decode("utf-8"))) if record and record.format == "json" else {}
            except (ValueError, OSError):
                self._bodies[doc_id] = {}
        return self._bodies[doc_id]

    def _existing(self, target: str, serial: str) -> DocRecord | None:
        wanted = SERIAL[target].replace("판례일련번호", "판례정보일련번호")
        for record in self._records():
            api = (record.extra or {}).get("api") or {}
            if record.kind == target and serial in (api.get(wanted), api.get(SERIAL[target])):
                return record
        return None

    def _records(self) -> list[DocRecord]:
        if not hasattr(self, "_record_cache"):
            self._record_cache = self.store.records()
        return self._record_cache

    def secure(self, cand: dict[str, Any]) -> None:
        """Fetch and store the original of a candidate (unindexed), and read what the text states about itself."""
        if (cand.get("secured") or {}).get("status") == "ok":
            return
        if cand["kind"] == "prec" and (cand.get("secured") or {}).get("status") in (None, "not_found", "no_body"):
            local = self.inbox_file(cand)
            if local is not None:
                self._secure_local(cand, local)
                return
        if cand.get("secured") or not cand.get("serial"):
            return
        target = cand["kind"]
        record = self._existing(target, cand["serial"])
        try:
            if record is None:
                data = self._api(lambda: self.law.service(target, ID=cand["serial"]))
                body = _body(data)
                if set(body) == {"Law"}:  # {"Law": "일치하는 판례가 없습니다 …"}: listed, but the API has no text for it
                    cand["secured"] = {"status": "no_body", "why": "국가법령정보 API가 목록에는 싣고 본문은 주지 않음", "looked_at": now_iso()}
                    return
                self._ppc_fields(cand, body)
                title, citation = self.cite(cand)
                content = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
                url = LawGoKr.public_url("lawService.do", {"type": "JSON", "target": target, "ID": cand["serial"]})
                record, _ = self.store.put_raw(doc_id=f"{target}-{cand['serial']}", kind=target, title=title, source="lawgokr", format="json", content=content, url=url, citation=citation, authority=2, dates={"decided_on": cand["date"]} if cand["date"] else {}, extra={"api": _identifiers(data)}, index=False)
                self._record_cache = self.store.records()
            document = self.store.load_document(record.doc_id)
            if document is None or document.record.sha256 != record.sha256:
                document = parse(self.store, record)
        except (LawApiError, ParseError, ValueError, OSError) as error:
            cand["secured"] = {"status": "failed", "why": f"{type(error).__name__}: {error}"[:300], "looked_at": now_iso()}
            return
        self._docs[record.doc_id] = document
        cand["doc_id"] = record.doc_id
        units = {unit.unit_id: unit.text for unit in document.units}
        body = self.body(record.doc_id)
        self._ppc_fields(cand, body)
        title, citation = self.cite(cand)
        if record.doc_id == f"{target}-{cand['serial']}" and (record.title, record.citation) != (title, citation):  # described before its fields were read right
            record, _ = self.store.put_raw(doc_id=record.doc_id, kind=target, title=title, source=record.source, format=record.format, content=self.store.raw_bytes(record), url=record.url, citation=citation, authority=record.authority, dates=record.dates, extra=record.extra, index=record.index)
            self._record_cache = self.store.records()
        whole, missing = casetext.complete(target, {**{k: casetext.plain(v) for k, v in body.items() if isinstance(v, str)}, **units})
        text_sha = hashlib.sha256(casetext.squeeze("".join(units.values())).encode("utf-8")).hexdigest()[:16]
        cand["secured"] = {"status": "ok", "complete": whole, "missing": missing, "sha256": record.sha256, "text_sha": text_sha, "fetched_at": record.fetched_at, "url": record.url, "chars": sum(len(t) for t in units.values())}
        if not whole:
            cand["flags"].append("원문 일부만 확보: " + ", ".join(missing) + " 없음")
        if target in ("prec", "detc"):
            text = casetext.plain(str(body.get("판례내용") or body.get("전문") or ""))
            parts = casetext.sections(text)
            reviewed, first = casetext.reviewed(text), casetext.reviewed(text, "제1심판결")
            # the judgment under review is the same case, not a precedent this one relies on (D9): it is not followed as a citation
            own = {cand["no"], *(named["no"] for named in (reviewed, first) if named)}
            cited = {c["no"]: c for c in [*casetext.citations(casetext.plain(str(body.get("참조판례") or ""))), *casetext.citations(text)] if c["no"] not in own}
            cand["facts"] = {
                "reviewed": reviewed,
                "first": first,
                "order": re.sub(r"\s+", " ", parts.get("주문", ""))[:400],
                "disposition": casetext.disposition(parts.get("주문", "")) if target == "prec" and cand.get("supreme") else "",
                "full_bench": "전원합의체" in cand.get("judgment_type", ""),
                "cites": list(cited.values()),
                "statutes": [list(pair) for pair in casetext.statute_refs(str(body.get("참조조문") or ""))],
            }

    # ---------- judgments obtained by hand ----------

    @property
    def inbox(self) -> Path:
        return self.store.root.joinpath(*INBOX)

    def inbox_file(self, cand: dict[str, Any]) -> Path | None:
        """The file a person put in the inbox for this case: `<법원>_<사건번호>.pdf` (or .txt)."""
        for name in (f"{cand['court']}_{cand['no']}", cand["no"]):
            for suffix in INBOX_SUFFIXES:
                path = self.inbox / f"{name}{suffix}"
                if path.exists():
                    return path
        return None

    def inbox_texts(self) -> dict[Path, str]:
        """Every inbox file's text without spaces, read once (to find the judgment after a remand, whose number nobody knows in advance)."""
        if not hasattr(self, "_inbox_texts"):
            self._inbox_texts: dict[Path, str] = {}
            for path in sorted(self.inbox.glob("*")) if self.inbox.is_dir() else []:
                if path.suffix.lower() not in INBOX_SUFFIXES:
                    continue
                try:
                    text = "\n".join(pdf_pages(path.read_bytes())) if path.suffix.lower() == ".pdf" else path.read_text(encoding="utf-8-sig", errors="replace")
                except Exception:  # noqa: BLE001 - an unreadable file is reported when its case is looked for, not here
                    text = ""
                self._inbox_texts[path] = text
        return self._inbox_texts

    def _secure_local(self, cand: dict[str, Any], path: Path) -> None:
        """Store a judgment a person obtained (the court's reading service, a copy request) as the original of a candidate."""
        title, citation = self.cite(cand)
        doc_id = "prec-inbox-" + hashlib.sha256(f"{cand['court']}:{cand['no']}".encode()).hexdigest()[:10]
        previous = self.store.get(doc_id)
        try:
            record, _ = self.store.put_raw(doc_id=doc_id, kind="prec", title=title, source="local_file", format=path.suffix.lstrip(".").lower(), content=path.read_bytes(), citation=citation, authority=2, dates={"decided_on": cand["date"]} if cand.get("date") else {}, extra={**(previous.extra if previous else {}), "provided": "사람이 받아 둔 판결서", "file": path.name}, parser=previous.parser if previous else None, index=False)
            record, document = self._parsed(record)
        except (ParseError, ValueError, OSError) as error:
            cand["secured"] = {"status": "failed", "why": f"받아 둔 파일을 읽지 못함({path.name}): {error}"[:300], "looked_at": now_iso()}
            return
        self._record_cache = self.store.records()
        self._docs[record.doc_id] = document
        text = "\n".join(unit.text for unit in document.units)
        squeezed = casetext.squeeze(text)
        missing = [name for name in ("주문", "이유") if name not in squeezed]
        cand["doc_id"], cand["local_file"] = record.doc_id, str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)
        cand["secured"] = {"status": "ok", "complete": not missing, "missing": missing, "sha256": record.sha256, "text_sha": hashlib.sha256(squeezed.encode("utf-8")).hexdigest()[:16], "fetched_at": record.fetched_at, "url": f"{'/'.join(INBOX)}/{path.name}", "chars": len(text), "by_hand": True}
        cand["flags"] = [f for f in cand["flags"] if not f.startswith("원문 일부만 확보")] + (["원문 일부만 확보: " + ", ".join(missing) + " 없음"] if missing else [])
        cand["facts"] = {"reviewed": casetext.reviewed(text), "first": casetext.reviewed(text, "제1심판결"), "order": "", "disposition": "", "full_bench": False, "cites": [], "statutes": []}

    # ---------- rules ----------

    def meta_rule(self, cand: dict[str, Any]) -> None:
        courts, types = self.version["courts"], self.rules["screen"].get("case_types") or []
        if cand["kind"] == "prec" and courts != "all" and cand["court"] not in courts:
            cand["meta_rule"] = {"pass": False, "why": f"{self.version_name}의 주요 채택 대상이 아닌 법원({cand['court'] or '법원 미상'})"}
        elif cand["kind"] == "prec" and cand.get("case_type") and types and cand["case_type"] not in types:
            cand["meta_rule"] = {"pass": False, "why": f"사건 종류 '{cand['case_type']}'"}
        else:
            cand["meta_rule"] = {"pass": True, "why": "법원급 · 사건 종류 통과"}

    def text_rule(self, cand: dict[str, Any]) -> None:
        document = self.document(cand["doc_id"])
        text = casetext.squeeze(" ".join(unit.text for unit in document.units)) if document else ""
        words = [word for word in self.rules["screen"]["words"] if casetext.squeeze(word) in text]
        proven = set()
        for found in cand["found"]:
            mark = found["path"][0]
            if mark in "①②":
                proven.add(mark)
            elif mark == "④" and found["by"].endswith("을(를) 인용"):
                # found by searching for the cited case's number, and that search matches loosely: the number must be in the text
                number = casetext.case_no(found["by"])
                found["confirmed"] = bool(number and number in text)
                if found["confirmed"]:
                    proven.add(mark)
            elif mark == "④":
                proven.add(mark)
        if proven:
            cand["text_rule"] = {"pass": True, "why": f"찾은 경로 {' · '.join(sorted(proven))}", "words": words}
        elif words:
            cand["text_rule"] = {"pass": True, "why": "주제어가 원문에 있음", "words": words}
        else:
            cand["text_rule"] = {"pass": False, "why": "주제어가 원문에 없음(검색이 느슨하게 맞춘 결과)", "words": []}

    # ---------- grading ----------

    def _prompt(self, kind: str, label: str, document: Document, only_unit: str | None = None) -> tuple[str, bool]:
        limit = int(self.rules["grading"].get("max_chars", 150000))
        units = [u for u in document.units if only_unit is None or u.unit_id == only_unit]
        text = "\n\n".join(f"[{unit.label}]\n{unit.text}" for unit in units)
        cut = len(text) > limit
        return f"종류: {KIND_LABEL.get(kind, kind)}\n표시: {label}\n\n{text[:limit]}", cut

    def _graded(self, role: str, kind: str, label: str, document: Document, only_unit: str | None = None) -> dict[str, Any] | None:
        """One grading by the model of `role` ("model" or "check_model"), from the cache when the same text was
        graded with the same instructions by the same model. None when the cost cap is reached or the call fails."""
        model = str(self.rules["grading"][role])
        prompt, cut = self._prompt(kind, label, document, only_unit)
        cache = f"{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:24]}:{self.rules.rubric_sha}:{model}"
        row = self._grades.get(cache)
        if row is None:
            if self.state["cost_usd"] >= (self.cost_cap if role == "model" else self.cost_cap * CHECK_SHARE):
                return None
            try:
                grade, cost = self.graders[role](self.rules.instructions(), prompt)
            except Exception as error:  # noqa: BLE001 - a model call that fails leaves the document ungraded, not the run dead
                self._log("grade_errors.jsonl", {"at": now_iso(), "model": model, "label": label, "error": f"{type(error).__name__}: {error}"[:400]})
                return None
            row = {"cache": cache, "model": model, "rubric": self.rules.rubric_sha, "at": now_iso(), "cost_usd": round(cost, 6), "partial": cut, **grade.model_dump()}
            self._grades[cache] = row
            self._calls.append(row)
            self._log(self._grade_file, row)
            with self._lock:
                self.state["cost_usd"] = round(self.state["cost_usd"] + cost, 6)
        units = [u for u in document.units if only_unit is None or u.unit_id == only_unit]
        answers = [{**a, "located": casetext.locate(units, a["quote"])} for a in row["answers"]]
        return {**{k: row[k] for k in ("model", "rubric", "at", "partial", "scope", "scope_reason", "about_images")}, "answers": answers}

    def _would_adopt(self, cand: dict[str, Any], grade: dict[str, Any] | None) -> bool:
        if not grade or grade["scope"] != "in":
            return False
        if cand["kind"] in (self.version.get("about_images_only") or []) and not grade["about_images"]:
            return False
        return max(top_scores(grade).values(), default=0) >= int(self.version["min_score"][cand["kind"]])

    def _sampled(self, cand: dict[str, Any]) -> bool:
        conf = self.rules["grading"]
        return random.Random(f"{conf.get('seed', 0)}:{cand['key']}").random() < float(conf.get("check_sample", 0))

    def grade(self, cands: list[dict[str, Any]]) -> None:
        """Grade with the light model; let the stronger model look again at what could be adopted and at a random
        part of the rest. When the two disagree too often on that random part, the stronger model grades all."""
        workers = int(self.rules["grading"].get("parallel", 4))

        def run(role: str, batch: list[dict[str, Any]], field: str) -> None:
            def one(cand: dict[str, Any]) -> None:
                grade = self._graded(role, cand["kind"], self.cite(cand)[0] + (f" · {cand['title']}" if cand.get("title") else ""), self.document(cand["doc_id"]))
                if grade is None:
                    if "채점 못 함" not in cand["flags"] and field == "grade":
                        cand["flags"].append("채점 못 함")
                else:
                    cand[field] = grade
                    if "채점 못 함" in cand["flags"]:
                        cand["flags"].remove("채점 못 함")

            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(one, batch))
            self.save()

        run("model", [c for c in cands if not c.get("grade")], "grade")
        graded = [c for c in cands if c.get("grade")]
        info = self.state["grading"]
        near = [c for c in graded if max(top_scores(c["grade"]).values(), default=0) >= 2 or c["grade"]["scope"] == "hold"]
        near_keys = {c["key"] for c in near}
        rest = [c for c in graded if c["key"] not in near_keys and self._sampled(c)]
        run("check_model", [c for c in [*near, *rest] if not c.get("check")], "check")
        sample = [c for c in self.cands.values() if c.get("grade") and c.get("check") and self._sampled(c) and not (max(top_scores(c["grade"]).values(), default=0) >= 2 or c["grade"]["scope"] == "hold")]
        differ = [c["key"] for c in sample if self._would_adopt(c, c["grade"]) != self._would_adopt(c, c["check"])]
        info.update(sample=len(sample), sample_differs=len(differ), sample_keys=differ[:20], cost_cap_usd=self.cost_cap)
        if sample and len(differ) / len(sample) > float(self.rules["grading"].get("escalate_above", 1)) and len(sample) >= 20:
            info["escalated"] = True
        if info.get("escalated"):
            run("check_model", [c for c in self.cands.values() if c.get("grade") and not c.get("check")], "check")
        wanted = {c["key"] for c in [*near, *rest]} | ({c["key"] for c in graded} if info.get("escalated") else set())
        info["not_checked"] = sum(1 for c in self.cands.values() if c.get("grade") and c["key"] in wanted and not c.get("check"))  # the cap or a failed call
        for cand in graded:
            cand["final"] = cand.get("check") or cand["grade"]

    def adopt(self, cand: dict[str, Any]) -> bool:
        """Decide whether a graded candidate is a main source of this list version. Returns whether it is new."""
        was = bool((cand.get("adopted") or {}).get("main"))
        final = cand.get("final")
        if not final:
            return False
        blocked = f"같은 문서가 다른 일련번호로 한 번 더 실림 ({cand['duplicate_of']})" if cand.get("duplicate_of") else "" if cand.get("text_rule", {}).get("pass") else cand.get("text_rule", {}).get("why", "규칙을 통과하지 못함")
        if blocked:
            cand["adopted"] = {"main": False, "why": ("중복: " if cand.get("duplicate_of") else "규칙: ") + blocked, "questions": {}, "version": self.version_name}
            if "main" in cand["purposes"]:
                cand["purposes"].remove("main")
            return False
        scores = top_scores(final)
        need = int(self.version["min_score"][cand["kind"]])
        if final["scope"] != "in":
            verdict, why = False, f"{'보류' if final['scope'] == 'hold' else '범위 밖'}: {final['scope_reason']}"
        elif cand["kind"] in (self.version.get("about_images_only") or []) and not final["about_images"]:
            verdict, why = False, "영상 관련이 아님"
        elif max(scores.values(), default=0) < need:
            verdict, why = False, f"최고 점수 {max(scores.values(), default=0)} < 기준 {need}"
        else:
            hit = {q: s for q, s in scores.items() if s >= need}
            verdict, why = True, "기준 이상: " + ", ".join(f"{q}={s}" for q, s in sorted(hit.items()))
        cand["adopted"] = {"main": verdict, "why": why, "questions": scores, "version": self.version_name}
        if verdict and "main" not in cand["purposes"]:
            cand["purposes"].append("main")
        if not verdict and "main" in cand["purposes"]:
            cand["purposes"].remove("main")
        unlocated = [a["question"] for a in final["answers"] if a["score"] >= need and not a.get("located")]
        note = "근거 문장의 위치 미확인: " + ", ".join(sorted(set(unlocated)))
        cand["flags"] = [f for f in cand["flags"] if not f.startswith("근거 문장의 위치 미확인")]
        if verdict and unlocated:
            cand["flags"].append(note)
        if final.get("partial") and "원문의 앞부분만 읽고 채점" not in cand["flags"]:
            cand["flags"].append("원문의 앞부분만 읽고 채점")
        return verdict and not was

    def process(self, label: str) -> list[dict[str, Any]]:
        """Take every candidate as far as it goes: rule → original → rule → grade → adoption. Returns the
        candidates adopted in this pass."""
        todo = [c for c in self.cands.values() if "meta_rule" not in c]
        for cand in todo:
            self.meta_rule(cand)
        fetch = [c for c in self.cands.values() if not c.get("secured") and c.get("serial") and (c["meta_rule"]["pass"] or "trail" in c["purposes"] or "check" in c["purposes"])]
        for index, cand in enumerate(fetch, 1):
            self.secure(cand)
            if index % 50 == 0:
                self.say(f"  {label}: 원문 {index}/{len(fetch)}")
                self.save()
        copies: dict[tuple[str, str], str] = {}
        for cand in sorted((c for c in self.cands.values() if (c.get("secured") or {}).get("status") == "ok"), key=lambda c: (c["kind"], int(c["serial"]) if c["serial"].isdigit() else 0)):
            first = copies.setdefault((cand["kind"], cand["secured"].get("text_sha") or cand["key"]), cand["key"])
            if first != cand["key"]:  # the API lists some decisions twice under two serial numbers: read and adopted once
                cand["duplicate_of"] = first
                cand["adopted"] = {"main": False, "why": f"중복: 같은 문서가 다른 일련번호로 한 번 더 실림 ({first})", "questions": {}, "version": self.version_name}
        for cand in self.cands.values():
            if cand.get("doc_id") and cand["meta_rule"]["pass"]:
                self.text_rule(cand)
        self.grade([c for c in self.cands.values() if c.get("text_rule", {}).get("pass") and not c.get("duplicate_of")])
        new = [c for c in self.cands.values() if c.get("final") and self.adopt(c)]
        self.save()
        self.say(f"  {label}: 후보 {len(self.cands)} · 원문 확보 {sum(1 for c in self.cands.values() if (c.get('secured') or {}).get('status') == 'ok')} · 채점 {sum(1 for c in self.cands.values() if c.get('final'))} · 새 채택 {len(new)} · 비용 ${self.state['cost_usd']:.2f}")
        return new

    # ---------- steps ----------

    def seeds(self) -> None:
        """Norms (whole originals; articles graded for question coverage) and the guides that are on file."""
        for norm in self.rules["norms"]:
            entry = self.state["norms"].setdefault(norm["id"], {"name": norm["name"], "target": norm["target"], "decision": norm.get("decision", ""), "articles": {}})
            if entry.get("status") == "ok":
                continue
            try:
                name_key = "법령명한글" if norm["target"] == "law" else "행정규칙명"
                hits = [h for h in self.search(norm["target"], "규범", query=norm["name"]) if str(h.get(name_key, "")).strip() == norm["name"]]
                if not hits:
                    raise LawApiError(f"검색 결과에 '{norm['name']}'이(가) 없습니다")
                param, serial_key = LAW_DETAIL[norm["target"]]
                serial = str(hits[0][serial_key])
                data = self._api(lambda: self.law.service(norm["target"], **{param: serial}))  # noqa: B023
                dates = {k: v for k, v in {"promulgated_on": casetext.iso_date(str(hits[0].get("공포일자") or hits[0].get("발령일자") or "")), "effective_from": casetext.iso_date(str(hits[0].get("시행일자") or ""))}.items() if v}
                previous = self.store.get(norm["id"])
                record, _ = self.store.put_raw(doc_id=norm["id"], kind=norm["target"], title=norm["name"], source="lawgokr", format="json", content=json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"), url=LawGoKr.public_url("lawService.do", {"type": "JSON", "target": norm["target"], param: serial}), citation=norm["name"], authority=norm.get("authority", 1), dates=dates, extra={"api": _identifiers(data)}, index=previous.index if previous else False)
                document = parse(self.store, record)
            except (LawApiError, ParseError, ValueError, OSError) as error:
                entry.update(status="failed", why=f"{type(error).__name__}: {error}"[:300])
                continue
            self._docs[record.doc_id] = document
            entry.update(status="ok", doc_id=record.doc_id, serial=serial, param=param, sha256=record.sha256, fetched_at=record.fetched_at, url=record.url, dates=dates, units=len(document.units))
            wanted = norm.get("articles")
            have = {unit.unit_id for unit in document.units}
            entry["missing_articles"] = [a for a in (wanted or []) if a not in have]
            for unit in document.units:
                if wanted is None or unit.unit_id in wanted:
                    entry["articles"].setdefault(unit.unit_id, {"label": unit.label, "adopted": True})
        self._record_cache = self.store.records()
        named_ids = {n["id"] for n in self.rules["norms"] if n.get("articles")}
        jobs = [(norm_id, entry, article) for norm_id, entry in self.state["norms"].items() if entry.get("status") == "ok" for article, info in entry["articles"].items() if "grade" not in info or (norm_id in named_ids and "check" not in info)]

        named = {n["id"] for n in self.rules["norms"] if n.get("articles")}  # adopted by a decision, article by article

        def one(job: tuple[str, dict[str, Any], str]) -> None:
            norm_id, entry, article = job
            label = f"{entry['name']} {entry['articles'][article]['label']}"
            grade = self._graded("model", entry["target"], label, self.document(norm_id), only_unit=article)
            if grade is not None:
                entry["articles"][article]["grade"] = grade
            if norm_id in named:  # the list adopts it whatever the grade says, so a second reading shows whether the two agree
                check = self._graded("check_model", entry["target"], label, self.document(norm_id), only_unit=article)
                if check is not None:
                    entry["articles"][article]["check"] = check

        with ThreadPoolExecutor(max_workers=int(self.rules["grading"].get("parallel", 4))) as pool:
            list(pool.map(one, jobs))
        for guide in self.rules.data.get("guides") or []:
            entry = self.state["guides"].setdefault(guide["id"], {"title": guide["title"], "kind": guide["kind"], "page": guide.get("page", "")})
            path = Path(guide["path"]) if Path(guide["path"]).is_absolute() else self.root / guide["path"]
            if not path.exists():
                entry.update(status="missing", why=f"파일이 없음: {guide['path']} (자동으로 받을 수 없어 사람이 받아 두어야 한다)")
                continue
            previous = self.store.get(guide["id"])
            record, _ = self.store.put_raw(doc_id=guide["id"], kind=guide["kind"], title=guide["title"], source="local_file", format=path.suffix.lstrip(".").lower() or "pdf", content=path.read_bytes(), url=guide.get("page"), citation=(previous.citation if previous else None) or guide["title"], authority=3, dates={"published_at": guide["published_at"]} if guide.get("published_at") else {}, extra=previous.extra if previous else None, parser=previous.parser if previous else None, index=previous.index if previous else False)
            try:
                record, document = self._parsed(record)
            except (ParseError, ValueError, OSError) as error:
                entry.update(status="failed", why=str(error)[:300])
                continue
            self._docs[record.doc_id] = document
            entry.update(status="ok", doc_id=record.doc_id, sha256=record.sha256, fetched_at=record.fetched_at, url=record.url, units=len(document.units), dates=record.dates, read_by="ocr" if (record.extra or {}).get("read_by") == "ocr" else "text")
            entry.pop("why", None)
            if "grade" not in entry:
                grade = self._graded("model", guide["kind"], guide["title"], document)
                if grade is not None:
                    entry["grade"] = grade
        self._record_cache = self.store.records()
        self.save()

    def discover(self, only_institution: bool = False) -> None:
        paths = self.rules["paths"]
        only_supreme = self.version["courts"] != "all" and set(self.version["courts"]) == {"대법원"}
        court = {"org": SUPREME} if only_supreme else {}
        for name in [] if only_institution else (paths.get("statute_refs") or {}).get("prec", []):
            for hit in self.search("prec", "②", JO=name, **court):
                self.add("prec", hit, "②", f"참조법령 {name}")
            if only_supreme:
                self.count("prec", "② (법원 제한 없이 건수만)", JO=name)
        for target, words in ({} if only_institution else paths.get("keywords") or {}).items():
            for word in words:
                for hit in self.search(target, "③", query=word, search=2, **(court if target == "prec" else {})):
                    self.add(target, hit, "③", f"주제어 {word}")
                if target == "prec" and only_supreme:
                    self.count("prec", "③ (법원 제한 없이 건수만)", query=word, search=2)
        kinds = (paths.get("institution") or {}).get("from_kinds") or []
        for guide_id, entry in self.state["guides"].items():
            if entry.get("status") != "ok" or entry["kind"] not in kinds:
                continue
            document = self.document(guide_id)
            for cited in casetext.citations("\n".join(unit.text for unit in document.units)):
                self.lookup(cited["no"], cited["court"], cited["date"], "①", f"{entry['title']}에 인용", 0)
        self.save()

    def _mentions(self, cand: dict[str, Any]) -> list[dict[str, Any]]:
        """Judgments whose text contains this case's number (the API's body search), found once and kept.
        The search matches loosely: a short number ("2007다71") returns over a thousand unrelated judgments.
        A result larger than `max_mentions` is not used, and that is recorded."""
        if "mentions" not in cand:
            limit = int((self.rules["paths"].get("citations") or {}).get("max_mentions", 300))
            total = self.count("prec", "④ (건수 확인)", query=cand["no"], search=2)
            if total > limit:
                cand["mentions"], cand["mentions_note"] = [], f"피인용 검색 결과가 {total}건으로, 사건번호와 무관한 판결까지 맞춘 것이라 쓰지 않음"
            else:
                hits = self.search("prec", "④", query=cand["no"], search=2)
                cand["mentions"] = [{"court": casetext.squeeze(str(h.get("법원명") or "")), "no": casetext.squeeze(str(h.get("사건번호") or "")).split(",")[0], "date": casetext.iso_date(str(h.get("선고일자") or "")), "judgment_type": str(h.get("판결유형") or ""), "hit": h} for h in hits]
            cand["mentions_at"] = now_iso()
        return [m for m in cand["mentions"] if m["no"] != cand["no"]]

    def follow(self) -> None:
        """④: cases an adopted case cites, and later cases that cite it. A case found by following is itself
        followed only while it is fewer than `rounds` steps away from the search paths."""
        rounds = int((self.rules["paths"].get("citations") or {}).get("rounds", 0))
        while True:
            frontier = [c for c in self.cands.values() if (c.get("adopted") or {}).get("main") and not c.get("followed") and c.get("round", 0) < rounds]
            if not frontier:
                break
            for cand in frontier:
                cand["followed"] = True
                depth = cand.get("round", 0) + 1
                title = self.cite(cand)[1]
                facts = cand.get("facts") or {}
                same_case = {named["no"] for named in (facts.get("reviewed"), facts.get("first")) if named}
                for cited in facts.get("cites") or []:
                    if cited["no"] not in same_case:
                        self.lookup(cited["no"], cited["court"], cited["date"], "④", f"{title}이(가) 인용", depth)
                document = self.document(cand["doc_id"])
                if cand["kind"] in ("expc", "ppc") and document:
                    for cited in casetext.citations("\n".join(unit.text for unit in document.units)):
                        self.lookup(cited["no"], cited["court"], cited["date"], "④", f"{title}이(가) 인용", depth)
                if cand["kind"] == "prec":
                    for mention in self._mentions(cand):
                        if not cand["date"] or mention["date"] >= cand["date"]:
                            self.add("prec", mention["hit"], "④", f"{title}을(를) 인용", depth)
            new = self.process("인용 따라가기")
            self.state.setdefault("follow", []).append({"followed": len(frontier), "new_main": len(new), "at": now_iso()})
        self.save()

    def _sides(self) -> dict[str, dict[str, int]]:
        need = min(int(v) for v in self.version["min_score"].values())
        sides = {q: {"protect": 0, "against": 0, "mixed": 0, "none": 0} for q in self.rules["scope"]["questions"]}
        for cand in self.cands.values():
            if not (cand.get("adopted") or {}).get("main"):
                continue
            best: dict[str, dict[str, Any]] = {}
            for answer in cand["final"]["answers"]:
                if answer["score"] >= need and answer["score"] > best.get(answer["question"], {"score": 0})["score"]:
                    best[answer["question"]] = answer
            for question, answer in best.items():
                sides[question][answer["direction"]] += 1
        return sides

    def balance(self) -> None:
        """A question whose adopted cases all point one way gets one more search with wider words (once)."""
        only_supreme = self.version["courts"] != "all" and set(self.version["courts"]) == {"대법원"}
        widen = self.rules["paths"].get("widen") or {}
        before = self._sides()
        widened = []
        for question, sides in before.items():
            one_sided = (sides["protect"] + sides["mixed"] == 0) != (sides["against"] + sides["mixed"] == 0) or sum(sides.values()) == 0
            if one_sided and widen.get(question) and question not in self.state["balance"]:
                widened.append(question)
                for word in widen[question]:
                    for hit in self.search("prec", "③", query=word, search=2, **({"org": SUPREME} if only_supreme else {})):
                        self.add("prec", hit, "③", f"넓힌 주제어 {word} ({question})")
        if widened:
            self.process("넓힌 주제어")
        after = self._sides()
        for question, sides in after.items():
            both = sides["mixed"] > 0 or (sides["protect"] > 0 and sides["against"] > 0)
            status = "양쪽 사례 있음" if both else "채택 사례 없음" if sum(sides.values()) == 0 else "한쪽 방향만 있음(확인한 범위 안에서)" if sides["protect"] + sides["against"] > 0 else "방향을 정할 수 없는 사례만 있음"
            self.state["balance"][question] = {**sides, "before": before[question], "widened": question in widened or self.state["balance"].get(question, {}).get("widened", False), "status": status}
        self.save()

    def changes(self) -> None:
        """Step 3 of D10: a later full-bench judgment that names an adopted case and says it changes it."""
        for cand in self.cands.values():
            if not (cand.get("adopted") or {}).get("main") or cand.get("change"):
                continue
            if cand["kind"] != "prec" or not cand.get("supreme"):
                cand["change"] = {"status": "검사 안 함", "why": "전원합의체의 판례 변경은 대법원 판례에만 해당한다", "at": now_iso()}
                continue
            later = [m for m in self._mentions(cand) if "전원합의체" in m["judgment_type"] and m["date"] > cand["date"]]
            if cand.get("mentions_note"):
                cand["change"] = {"status": "검사 못 함", "why": cand["mentions_note"], "at": now_iso()}
                continue
            found = None
            for mention in later:
                bench = self.add("prec", mention["hit"], "변경 확인", f"{self.cite(cand)[1]}을(를) 언급한 전원합의체", cand.get("round", 0), "check")
                self.secure(bench)
                text = casetext.plain(str(self.body(bench["doc_id"]).get("판례내용") or "")) if bench.get("doc_id") else ""
                for sentence in re.split(r"(?<=다\.)\s+|\n", text):
                    squeezed = casetext.squeeze(sentence)
                    if cand["no"] in squeezed and "변경" in squeezed:
                        found = {"by": bench["key"], "doc_id": bench["doc_id"], "quote": sentence.strip()[:400]}
                        break
                if found:
                    break
            scope = f"국가법령정보 API 본문 검색에서 이 사건번호를 언급한 판결 {len(cand.get('mentions') or []) - 1}건 중 이후 전원합의체 {len(later)}건"
            cand["change"] = {"status": "변경 문구 발견" if found else "발견 못 함", **(found or {}), "scope": scope, "at": now_iso()}
            if found:
                cand["flags"].append("이후 전원합의체 판결에 변경 문구가 있음")
        self.save()

    def _relate(self, kind: str, source: dict[str, Any], other: dict[str, Any] | None, evidence: dict[str, Any], **more: Any) -> None:
        got = bool(other and (other.get("secured") or {}).get("status") == "ok")
        row = {"type": kind, "from": source["key"], "to": other["key"] if other else None, "status": "원문 확보" if got else "원문 미확보" if other else "미확인", "evidence": evidence, "at": now_iso(), **more}
        # one row per pair; a judgment that was found replaces the row that said none was
        self.state["relations"] = [r for r in self.state["relations"] if not (r["type"] == kind and r["from"] == source["key"] and (r["to"] == row["to"] or (row["to"] and r["to"] is None)))] + [row]

    def _trail_doc(self, main: dict[str, Any], holder: dict[str, Any], named: dict[str, str], level: str) -> dict[str, Any]:
        """Look for a judgment that `holder`'s text names, and record how it relates to the adopted case."""
        lower = self.lookup(named["no"], named["court"], named["date"], "경과", f"{self.cite(holder)[1]}의 {'원심' if level == '원심' else '앞 심급'}", main.get("round", 0), "trail")
        self.secure(lower)
        document = self.document(holder["doc_id"]) if holder.get("doc_id") else None
        evidence = {"doc_id": holder.get("doc_id"), "quote": named["as_written"], "located": casetext.locate(document.units, named["as_written"]) if document else None}
        self._relate(level, main, lower, evidence, outcome=(main.get("facts") or {}).get("disposition", "") if level == "원심" else "")
        return lower

    def trail(self) -> None:
        """D11: the other judgments of the same case, by what the text itself names (never by similarity)."""
        if not self.version.get("trail"):
            return
        mains = [c for c in self.cands.values() if (c.get("adopted") or {}).get("main") and c["kind"] == "prec" and (c.get("secured") or {}).get("status") == "ok"]
        for cand in mains:  # every run: a judgment put in the inbox since the last run is picked up
            facts = cand.get("facts") or {}
            if not facts.get("reviewed"):
                self._relate("원심", cand, None, {"note": "원문에 원심 표시가 없음"})
            else:
                lower = self._trail_doc(cand, cand, facts["reviewed"], "원심")
                before = (lower.get("facts") or {}).get("first") or (lower.get("facts") or {}).get("reviewed")
                if (lower.get("secured") or {}).get("status") == "ok" and before:
                    self._trail_doc(cand, lower, before, "제1심")
            if facts.get("disposition") in ("파기환송", "일부 파기"):
                later = [m for m in self._mentions(cand) if m["court"] != "대법원" and m["date"] > cand["date"]]
                found = False
                sent_back = casetext.squeeze(facts.get("order") or "")  # the order names the court the case went back to
                for mention in later:
                    if not mention["court"] or mention["court"] not in sent_back:
                        continue  # a later judgment of another court cites the case; it is not its continuation
                    lower = self.add("prec", mention["hit"], "경과", f"{self.cite(cand)[1]}을(를) 언급한 이후 하급심", cand.get("round", 0), "check")
                    self.secure(lower)
                    text = casetext.plain(str(self.body(lower["doc_id"]).get("판례내용") or "")) if lower.get("doc_id") else ""
                    sentence = next((s.strip() for s in re.split(r"(?<=다\.)\s+|\n", text) if cand["no"] in casetext.squeeze(s) and "환송" in s), None)
                    if sentence:
                        self._mark(lower, "경과", f"{self.cite(cand)[1]}의 환송 후 판결", "trail")
                        self._relate("환송 후", cand, lower, {"doc_id": lower["doc_id"], "quote": sentence[:300]})
                        found = True
                for path, text in self.inbox_texts().items():  # a judgment someone obtained that names this case as the remanding judgment
                    if cand["no"] not in casetext.squeeze(text) or cand["no"] in path.stem:
                        continue
                    flat = re.sub(r"\s*\n\s*", " ", text)  # a PDF breaks lines inside sentences
                    sentence = next((s.strip() for s in re.split(r"(?<=다\.)\s+", flat) if cand["no"] in casetext.squeeze(s) and "환송" in s), None)
                    number = casetext.case_no(path.stem)
                    court = casetext.court_of(number or "", path.stem.split("_")[0]) if "_" in path.stem else ""  # 서울서부지법 → 서울서부지방법원
                    if not sentence or not number or not court or court not in sent_back:
                        continue
                    lower = self.lookup(number, court, "", "경과", f"{self.cite(cand)[1]}의 환송 후 판결(받아 둔 파일)", cand.get("round", 0), "trail")
                    self.secure(lower)
                    if (lower.get("secured") or {}).get("status") == "ok":
                        self._relate("환송 후", cand, lower, {"doc_id": lower["doc_id"], "quote": sentence[:300]})
                        found = True
                if not found:
                    looked = cand.get("mentions_note") or f"국가법령정보 API 본문 검색에서 이 사건번호를 언급한 이후 하급심 {len(later)}건 가운데 환송 후 판결을 찾지 못함"
                    self._relate("환송 후", cand, None, {"note": looked + ". 없다는 뜻이 아니다"})
        self.save()

    def norm_refs(self) -> None:
        """What the adopted cases cite as 참조조문, against the norms collected: articles nobody listed show up here."""
        by_name = {entry["name"]: (norm_id, entry) for norm_id, entry in self.state["norms"].items()}
        counts: Counter[tuple[str, str]] = Counter()
        for cand in self.cands.values():
            if (cand.get("adopted") or {}).get("main"):
                for law, article in (cand.get("facts") or {}).get("statutes") or []:
                    counts[(law, article)] += 1
        rows = []
        for (law, article), cited in counts.most_common():
            old = law.startswith("구 ")
            name = re.sub(r"\(.*?\)", "", law[2:] if old else law).strip()
            name = {"헌법": "대한민국헌법", "개인정보보호법": "개인정보 보호법"}.get(name, name)
            hit = by_name.get(name)
            if hit is None:
                status = "법령 미수집"
            elif old:
                status = "구법 표기: 현행 조문과의 대응 미확인"
            elif article in hit[1]["articles"]:
                status = "수집 · 채택됨 (같은 번호 조문. 판결 당시 조문과의 대응은 미확인)"
            elif self.document(hit[0]) and self.document(hit[0]).unit(article):
                status = "법령은 수집, 이 조문은 채택 목록에 없음"
            else:
                status = "현행본에 이 번호의 조문이 없음"
            rows.append({"law": law, "article": article, "cited_by": cited, "status": status})
        self.state["norm_refs"] = rows
        self.save()

    # ---------- run ----------

    STEPS = ("seeds", "discover", "process", "follow", "balance", "changes", "trail", "norm_refs")

    def run(self, until: str | None = None) -> None:
        for step in self.STEPS:
            if step == "discover" and step in self.state["done"]:
                self.discover(only_institution=True)  # a continued run searches nothing again, but a guide added since may cite cases
                continue
            self.say(f"[{step}]")
            if step == "process":
                self.process("탐색 경로 ①②③")
            else:
                getattr(self, step)()
            if step in ("changes", "trail"):
                self.process(step)  # originals fetched for the check or the trail
            if step not in self.state["done"]:
                self.state["done"].append(step)
            self.save()
            if until == step:
                break


def cited_counts(selection: Selection) -> Counter[str]:
    """How many candidates with a secured original cite each case number (the fourth step of D10 records it)."""
    counts: Counter[str] = Counter()
    for cand in selection.cands.values():
        for cited in (cand.get("facts") or {}).get("cites") or []:
            counts[cited["no"]] += 1
    return counts


def level(cand: dict[str, Any]) -> str:
    if cand["kind"] != "prec":
        return ""
    return "전원합의체" if "전원합의체" in cand.get("judgment_type", "") else "대법원" if cand.get("supreme") else "하급심"


def version_id(selection: Selection) -> str:
    """List rules + basis date + the hash of every adopted original (D12)."""
    state = selection.state
    hashes = sorted([c["secured"]["sha256"] for c in selection.cands.values() if {"main", "trail"} & set(c["purposes"]) and (c.get("secured") or {}).get("status") == "ok"] + [e["sha256"] for e in [*state["norms"].values(), *state["guides"].values()] if e.get("status") == "ok"])
    digest = hashlib.sha256((selection.rules.sha + "".join(hashes)).encode("utf-8")).hexdigest()[:8]
    return f"{selection.version_name}-{str(selection.rules['basis_date']).replace('-', '')}-{digest}"


def list_entries(selection: Selection) -> dict[str, Any]:
    """The collection as a fetch list (the format of configs/corpus/*.yaml): another machine gets the same
    originals with `privframe-corpus fetch`. `selection` on an entry is the record of why it is here; the
    fetcher ignores it."""
    state = selection.state
    documents: list[dict[str, Any]] = []
    for norm_id, entry in state["norms"].items():
        if entry.get("status") != "ok":
            continue
        every = not next(n for n in selection.rules["norms"] if n["id"] == norm_id).get("articles")
        documents.append({"id": norm_id, "source": "lawgokr", "target": entry["target"], "params": {entry["param"]: entry["serial"]}, "kind": entry["target"], "title": entry["name"], "citation": entry["name"], "authority": 1, "dates": entry.get("dates") or {},
                          "selection": {"purpose": "main", "units": "all" if every else sorted(entry["articles"]), "decision": entry.get("decision") or "", "sha256": entry["sha256"]}})  # fmt: skip
    for guide_id, entry in state["guides"].items():
        if entry.get("status") != "ok":
            continue
        guide = next(g for g in selection.rules["guides"] if g["id"] == guide_id)
        documents.append({"id": guide_id, "source": "local_file", "path": guide["path"], "page": guide.get("page"), "kind": entry["kind"], "title": entry["title"], "citation": entry["title"], "authority": 3, "dates": entry.get("dates") or {},
                          "selection": {"purpose": "main", "units": "all", "sha256": entry["sha256"]}})  # fmt: skip
    cited = cited_counts(selection)
    for cand in sorted(selection.cands.values(), key=lambda c: (c["kind"], c.get("date") or "", c["key"])):
        if not cand["purposes"] or (cand.get("secured") or {}).get("status") != "ok" or set(cand["purposes"]) == {"check"}:
            continue
        title, citation = selection.cite(cand)
        purposes = [p for p in ("main", "trail") if p in cand["purposes"]]
        record = {"purpose": purposes[0] if len(purposes) == 1 else purposes, "sha256": cand["secured"]["sha256"], "complete": cand["secured"]["complete"]}
        if "main" in purposes:
            record.update(questions=cand["adopted"]["questions"], model=cand["final"]["model"], found=sorted({f["path"] for f in cand["found"]}), cited_by=cited.get(cand["no"], 0))
            if level(cand):
                record["level"] = level(cand)
            if (cand.get("change") or {}).get("status"):
                record["change"] = cand["change"]["status"]
        if cand["flags"]:
            record["unconfirmed"] = cand["flags"]
        where = {"source": "local_file", "path": cand["local_file"]} if cand.get("local_file") else {"source": "lawgokr", "target": cand["kind"], "params": {"ID": cand["serial"]}}
        documents.append({"id": cand["doc_id"], **where, "kind": cand["kind"], "title": title, "citation": citation, "authority": 2, "dates": {"decided_on": cand["date"]} if cand["date"] else {}, "selection": record})
    missing = [{"id": guide_id, "title": entry["title"], "why": entry.get("why", ""), "where": entry.get("page", "")} for guide_id, entry in state["guides"].items() if entry.get("status") != "ok"]
    missing += [{"case": f"{c['court']} {c['no']}".strip(), "wanted_for": sorted({f['by'] for f in c['found']})[:3], "why": (c.get("secured") or {}).get("why", "")} for c in selection.cands.values() if "trail" in c["purposes"] and (c.get("secured") or {}).get("status") != "ok"]
    return {"version": {"id": version_id(selection), "name": selection.version_name, "basis_date": str(selection.rules["basis_date"]), "rules": selection.rules.path.name, "rules_sha256": selection.rules.sha, "rubric": selection.rules.rubric_sha, "made_at": now_iso()}, "defaults": {"index": False}, "documents": documents, "missing": missing}


def report(selection: Selection) -> dict[str, Any]:
    """D13: what was collected and what is not known, counted. Not a check of search quality or legal accuracy."""
    state, cands = selection.state, list(selection.cands.values())
    questions = list(selection.rules["scope"]["questions"])
    need = {kind: int(v) for kind, v in selection.version["min_score"].items()}
    ok = [c for c in cands if (c.get("secured") or {}).get("status") == "ok"]
    mains = [c for c in cands if (c.get("adopted") or {}).get("main")]
    trails = [c for c in cands if "trail" in c["purposes"]]
    by_path: Counter[str] = Counter()
    for cand in cands:
        for path in {f["path"] for f in cand["found"]}:
            by_path[path] += 1
    reasons: Counter[str] = Counter()
    for cand in cands:
        if (cand.get("adopted") or {}).get("main"):
            continue
        status = (cand.get("secured") or {}).get("status")
        if not cand.get("serial"):
            reasons["원문 미확보: API에 없음"] += 1
        elif not cand["meta_rule"]["pass"]:
            reasons["규칙: " + cand["meta_rule"]["why"].split("(")[0].strip()] += 1
        elif status == "no_body":
            reasons["원문 미확보: API가 본문을 주지 않음"] += 1
        elif status == "failed":
            reasons["원문 미확보: 수집 실패"] += 1
        elif cand.get("duplicate_of"):
            reasons["중복: 같은 문서가 다른 일련번호로 한 번 더 실림"] += 1
        elif not cand.get("text_rule", {}).get("pass", True):
            reasons["규칙: 주제어가 원문에 없음"] += 1
        elif not cand.get("final"):
            reasons["채점 못 함" if "채점 못 함" in cand["flags"] else "채점 대상 아님(확인 · 경과용 원문)"] += 1
        else:
            why = cand["adopted"]["why"]
            reasons["보류" if why.startswith("보류") else "범위 밖" if why.startswith("범위 밖") else "영상 관련이 아님" if why.startswith("영상") else "점수 미달"] += 1
    # ④ has two halves: cases an adopted case cites (read from its text), and cases found by searching for its number
    forward = [(c, f) for c in cands for f in c["found"] if f["path"] == "④" and f["by"].endswith("을(를) 인용")]
    cited_path = {"cited_by_adopted": sum(1 for c in cands if any(f["path"] == "④" and not f["by"].endswith("을(를) 인용") for f in c["found"])), "search_hits": len({c["key"] for c, _ in forward}), "search_hits_confirmed": len({c["key"] for c, f in forward if f.get("confirmed")}), "search_hits_not_read": len({c["key"] for c, f in forward if "confirmed" not in f}), "searches_too_wide": sum(1 for c in cands if c.get("mentions_note"))}
    rows = [row for row in selection._calls if row["rubric"] == selection.rules.rubric_sha]
    spent = {model: {"calls": sum(1 for r in rows if r["model"] == model), "usd": round(sum(r["cost_usd"] for r in rows if r["model"] == model), 2)} for model in sorted({r["model"] for r in rows})}
    per_question = {}
    for question in questions:
        graded = [c for c in cands if c.get("final") and top_scores(c["final"]).get(question, 0) >= 1]
        adopted = [c for c in mains if c["adopted"]["questions"].get(question, 0) >= need[c["kind"]]]
        articles = [(norm_id, article) for norm_id, entry in state["norms"].items() for article, info in entry["articles"].items() if top_scores(info.get("grade")).get(question, 0) >= 3]
        guides = [guide_id for guide_id, entry in state["guides"].items() if top_scores(entry.get("grade")).get(question, 0) >= 2]
        per_question[question] = {"text": selection.rules["scope"]["questions"][question], "candidates": len(graded), "adopted": dict(Counter(c["kind"] for c in adopted)), "articles": len(articles), "guides": guides, "sides": state["balance"].get(question)}
    expect = []
    for item in (selection.rules.data.get("expect") or {}).get("cases", []):
        cand = next((c for c in cands if c["no"] == item["case"] and c.get("serial")), None)
        scores = top_scores((cand or {}).get("final"))
        expect.append({**item, "found": cand is not None, "adopted": bool(cand and (cand.get("adopted") or {}).get("main")), "score": scores.get(item["question"], 0), "top": sorted(scores.items(), key=lambda kv: -kv[1])[:3], "matches": bool(cand) and scores.get(item["question"], 0) >= 2})
    for item in (selection.rules.data.get("expect") or {}).get("articles", []):
        info = (state["norms"].get(item["norm"]) or {}).get("articles", {}).get(item["article"])
        scores = top_scores((info or {}).get("grade"))
        expect.append({**item, "found": info is not None, "score": scores.get(item["question"], 0), "top": sorted(scores.items(), key=lambda kv: -kv[1])[:3], "matches": scores.get(item["question"], 0) >= 2})
    compared = [c for c in cands if c.get("grade") and c.get("check")]
    relations = state["relations"]
    located = [a for c in mains for a in c["final"]["answers"] if a["score"] >= need[c["kind"]]]
    # the same document from another source (a court PDF next to the API text): kept apart, listed here
    by_citation: dict[str, list[str]] = {}
    for record in selection.store.records():
        if record.citation:
            by_citation.setdefault(record.citation, []).append(f"{record.doc_id} ({record.source} · {record.format})")
    duplicates = {citation: docs for citation, docs in by_citation.items() if len(docs) > 1 and any(selection.cite(c)[1] == citation for c in ok)}
    return {
        "version": version_id(selection),
        "made_at": now_iso(),
        "stages": {"candidates": len(cands), "by_path": dict(sorted(by_path.items())), "by_kind": dict(Counter(c["kind"] for c in cands)), "cited": cited_path, "secured": len(ok), "not_secured": len(cands) - len(ok), "no_body": sum(1 for c in cands if (c.get("secured") or {}).get("status") == "no_body"), "by_hand": sum(1 for c in cands if (c.get("secured") or {}).get("by_hand")), "duplicates": sum(1 for c in cands if c.get("duplicate_of")), "graded": sum(1 for c in cands if c.get("final")), "main": len(mains), "main_by_kind": dict(Counter(c["kind"] for c in mains)), "trail_wanted": len(trails), "trail_secured": sum(1 for c in trails if (c.get("secured") or {}).get("status") == "ok"), "not_adopted": dict(reasons.most_common())},
        "questions": per_question,
        "originals": {"other_sources": duplicates, "complete": sum(1 for c in ok if c["secured"]["complete"]), "partial": [{"doc": c["doc_id"], "missing": c["secured"]["missing"]} for c in ok if not c["secured"]["complete"] and c["purposes"]], "provenance_missing": [c["doc_id"] for c in ok if not all(c["secured"].get(k) for k in ("sha256", "fetched_at", "url"))]},
        "relations": {"by_type": {kind: dict(Counter(r["status"] for r in relations if r["type"] == kind)) for kind in sorted({r["type"] for r in relations})}, "outcomes": dict(Counter(r.get("outcome") or "표시 없음" for r in relations if r["type"] == "원심"))},
        "changes": dict(Counter((c.get("change") or {}).get("status", "검사 전") for c in mains)),
        "unconfirmed": dict(Counter(flag.split(":")[0] for c in cands if c["purposes"] for flag in c["flags"])),
        "levels": dict(Counter(level(c) or KIND_LABEL[c["kind"]] for c in mains)),
        "selection_records": {"main": len(mains), "with_rule_model_score": sum(1 for c in mains if c.get("meta_rule") and c.get("text_rule") and c["final"].get("model")), "quotes": len(located), "quotes_located": sum(1 for a in located if a.get("located"))},
        "grading": {"model": selection.rules["grading"]["model"], "check_model": selection.rules["grading"]["check_model"], "rubric": selection.rules.rubric_sha, "graded": sum(1 for c in cands if c.get("grade")), "checked": len(compared), "same_verdict": sum(1 for c in compared if selection._would_adopt(c, c["grade"]) == selection._would_adopt(c, c["check"])), **state["grading"], "spent": spent, "cost_usd": round(sum(v["usd"] for v in spent.values()), 2)},
        "expect": expect,
        "norms": {norm_id: {"name": e["name"], "status": e.get("status"), "articles": len(e["articles"]), "missing_articles": e.get("missing_articles", []), "dates": e.get("dates", {}), "decision": e.get("decision", "")} for norm_id, e in state["norms"].items()},
        "norm_refs": state["norm_refs"][:40],
        "guides": {guide_id: {"title": e["title"], "status": e.get("status"), "why": e.get("why", "")} for guide_id, e in state["guides"].items()},
        "follow": state.get("follow", []),
    }


def publish(selection: Selection, lists_dir: Path) -> tuple[Path, Path]:
    """Write the list file (named by its version id) and the check report beside the run."""
    entries = list_entries(selection)
    lists_dir.mkdir(parents=True, exist_ok=True)
    head = (
        f"# 법률 코퍼스 수집 목록 {entries['version']['id']} (M4-1)\n"
        "# 선정 규칙(configs/corpus/selection.yaml)으로 만든 결과다. 손으로 고치지 않는다: 규칙을 바꾸고 다시 만든다.\n"
        "#   uv run privframe-corpus fetch <이 파일>      # 같은 원문을 받는다 (색인하지 않는다: defaults.index = false)\n"
        "# selection: 왜 들어왔는지의 기록 (purpose: main 주요 근거 · trail 같은 사건의 경과 확인용). 수집기는 읽지 않는다.\n"
        "# missing: 찾았지만 원문을 확보하지 못한 것. 없다는 뜻이 아니다.\n"
    )
    list_file = lists_dir / f"{entries['version']['id']}.yaml"
    write_atomic(list_file, head + yaml.safe_dump(entries, allow_unicode=True, sort_keys=False, width=1000))
    summary = report(selection)
    write_json(selection.dir / "report.json", summary, indent=1)
    return list_file, selection.dir / "report.json"


def llm_grader(settings: Any, slot: str, candidate_id: str) -> Grader:
    """A grader on a catalog candidate (the same route and prices the pipeline uses for that model)."""
    from .llm import chat_model, cost_usd, usage
    from .pipeline import Part, load_catalog
    from .runtime import _json_block, strict_schema

    catalog = load_catalog(settings.pipeline_dir)
    slot_spec = catalog.slot(slot)
    candidate = next((c for c in slot_spec.candidates if c.id == candidate_id), None)
    if candidate is None:
        raise ValueError(f"카탈로그의 '{slot}'에 '{candidate_id}' 후보가 없습니다")
    part = Part(slot=slot_spec, candidate=candidate, kind=catalog.adapters[candidate.adapter].kind)
    model = chat_model(part, settings, max_tokens=4000, timeout_s=180)

    def grade(instructions: str, document: str) -> tuple[Grade, float]:
        messages = [("system", instructions), ("user", document)]
        if candidate.adapter == "anthropic":
            reply = model.with_structured_output(Grade, method="json_schema", include_raw=True).invoke(messages)
            raw, parsed = reply["raw"], reply.get("parsed")
        else:
            raw, parsed = model.bind(response_format={"type": "json_schema", "json_schema": {"name": "Grade", "strict": True, "schema": strict_schema(Grade)}}).invoke(messages), None
        tokens_in, tokens_out, cached, written = usage(raw)
        billed = raw.response_metadata.get("cost")
        cost = float(billed) if isinstance(billed, (int, float)) else cost_usd(part, tokens_in, tokens_out, cached, written)
        result = parsed if isinstance(parsed, Grade) else Grade.model_validate(json.loads(_json_block(raw.text)))
        return result, cost

    return grade


__all__ = ["Answer", "Grade", "Grader", "Rules", "Selection", "list_entries", "llm_grader", "publish", "report", "scrub", "top_scores", "version_id"]
