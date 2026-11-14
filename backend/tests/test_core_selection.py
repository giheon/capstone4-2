"""Corpus selection (M4-1) on a scripted law API and a scripted grader: no keys, no network.

The cases below are made up. What is real: the search paths, the rules, the stored originals, the relations
read from the texts, the list file and the check report.
"""
import copy
import json

import pytest
import yaml

from privframe.core import casetext, parsers
from privframe.core.corpus import CorpusStore
from privframe.core.lawgokr import LawApiUnavailable
from privframe.core.selection import Answer, Grade, Halted, Rules, Selection, list_entries, publish, report
from privframe.core.sources import FetchContext, fetch_all

SECRET = "TOPSECRETOC"
SUPREME, LOWER = "400201", "400202"


def judgment(number, court, date, body, *, name="손해배상(기)", kind="판결", code=SUPREME, refs="", cited="", reviewed="", order="상고를 기각한다."):
    head = f"【원고, 상고인】 원고\n{f'【원심판결】 {reviewed}' if reviewed else ''}\n【주    문】\n{order}\n\n【이    유】 {body}"
    return {"사건번호": number, "법원명": court, "법원종류코드": code, "선고일자": date, "사건명": name, "사건종류명": "민사", "판결유형": kind, "판시사항": "", "판결요지": "", "참조조문": refs, "참조판례": cited, "판례내용": head.replace("\n", "<br/>")}


CASES = {
    # adopted: answers Q6 for the filmed person; cites 100 and names the judgment it reviews (not in the API)
    "100": judgment("2020다100", "대법원", "2021.03.04", "방송사가 동의 없이 얼굴을 내보낸 것은 초상권을 침해하였다. 대법원 2006. 10. 13. 선고 2004다200 판결 참조.", refs="헌법 제10조, 민법 제750조", cited="대법원 2006. 10. 13. 선고 2004다200 판결", reviewed="서울고법 2020. 1. 9. 선고 2019나300 판결", order="원심판결을 파기하고, 사건을 서울고등법원에 환송한다."),
    # found only by following 100's citation; the other side of Q6
    "200": judgment("2004다200", "대법원", "2006.10.13", "증거를 확보하려고 얼굴 사진을 찍은 행위는 위법성이 조각된다.", refs="민법 제750조"),
    # the search matched loosely: none of the topic words is in the text
    "400": judgment("2019다400", "대법원", "2022.05.05", "임대차 보증금 반환에 관한 사건이다."),
    # a lower court: not a main source of v1
    "500": judgment("2018가합500", "서울중앙지방법원", "2018.02.02", "식당 폐쇄회로 영상의 초상권 침해를 인정하였다.", code=LOWER),
    # a later full-bench judgment that changes 2004다200
    "600": judgment("2022다600", "대법원", "2023.06.01", "초상권에 관한 종전 견해를 바꾼다. 대법원 2006. 10. 13. 선고 2004다200 판결은 이 판결의 견해에 배치되는 범위에서 이를 변경하기로 한다.", kind="전원합의체 판결"),
    # the judgment after 100 was remanded
    "700": judgment("2021나700", "서울고등법원", "2021.11.11", "환송판결인 대법원 2021. 3. 4. 선고 2020다100 판결의 취지에 따라 초상권 침해를 다시 심리한다.", code=LOWER),
    # only about how a fine is computed
    "800": judgment("2021두800", "대법원", "2022.01.01", "영상정보처리기기 위반에 대한 과징금 산정 방식만을 다툰다."),
}
LOOSE = {"초상권": ["400"], "2020다100": ["400"]}  # hits the API returns although the word (or the number) is not in the text
NO_BODY = {"사건번호": "2020두950", "법원명": "대법원", "법원종류코드": SUPREME, "선고일자": "2020.02.02", "사건명": "과세처분취소", "사건종류명": "민사", "판결유형": "판결", "판례일련번호": "950"}
DECISION = {"안건번호": "제2024-001-001호", "의결일자": "2024.01.10", "안건명": "통합관제센터 영상정보 제공 관련 질의", "주문": "영상정보처리기기 영상의 제공은 허용되지 아니한다.", "이유": "별지와 같다", "별지": "통합관제센터가 영상정보처리기기로 촬영한 영상을 제3자에게 제공하는 것은 목적 외 제공에 해당한다. " * 6}
STATUTE = {"법령": {"기본정보": {"법령명_한글": "개인정보 보호법"}, "조문": {"조문단위": [
    {"조문여부": "조문", "조문번호": "25", "조문제목": "영상정보처리기기", "조문내용": "제25조(영상정보처리기기) 공개된 장소에 영상정보처리기기를 설치하여서는 아니 된다."},
    {"조문여부": "조문", "조문번호": "64", "조문제목": "과징금", "조문내용": "제64조(과징금) 과징금 산정 기준은 대통령령으로 정한다."},
]}}}  # fmt: skip


class FakeLaw:
    """The slice of LawGoKr the selection uses, over CASES. Every hit carries a link with the credential, as the real API does."""

    def __init__(self):
        self.calls = []

    def _hit(self, serial):
        case = CASES[serial]
        return {**{k: case[k] for k in ("사건번호", "법원명", "법원종류코드", "선고일자", "사건명", "사건종류명", "판결유형")}, "판례일련번호": serial, "판례상세링크": f"/DRF/lawService.do?OC={SECRET}&target=prec&ID={serial}"}

    def search(self, target, display=20, page=1, **params):
        self.calls.append(("search", target, params))
        if target == "law":
            return {"LawSearch": {"totalCnt": "1", "law": [{"법령명한글": "개인정보 보호법", "법령일련번호": "9001", "시행일자": "20260911", "공포일자": "20260310", "법령상세링크": f"/DRF/lawService.do?OC={SECRET}"}]}}
        if target == "ppc":  # the same decision listed twice, under two serial numbers, with empty list fields
            return {"Ppc": {"totalCnt": "2", "ppc": [{"결정문일련번호": serial, "안건명": "", "의안번호": "", "의결일": ""} for serial in ("31", "32")]}}
        if target != "prec":
            return {"Search": {"totalCnt": "0"}}
        serials = list(CASES)
        if "nb" in params:
            serials = [s for s in serials if CASES[s]["사건번호"] == params["nb"]]
        if "JO" in params:
            serials = [s for s in serials if params["JO"].replace(" ", "") in CASES[s]["참조조문"].replace(" ", "")]
        if "query" in params:
            words = params["query"].split()
            serials = [s for s in serials if all(w in CASES[s]["판례내용"] for w in words) or s in LOOSE.get(params["query"], [])]
        if params.get("org"):
            serials = [s for s in serials if CASES[s]["법원종류코드"] == params["org"]]
        hits = [self._hit(s) for s in serials] + ([NO_BODY] if params.get("query") == "초상권" else [])
        return {"PrecSearch": {"totalCnt": str(len(hits)), "prec": hits[(page - 1) * display : page * display]}}

    def service(self, target, **params):
        self.calls.append(("service", target, params))
        if target == "law":
            return STATUTE
        if target == "ppc":
            return {"PpcService": {**DECISION, "결정문일련번호": params["ID"]}}
        if params["ID"] == NO_BODY["판례일련번호"]:
            return {"Law": "일치하는 판례가 없습니다.  판례명을 확인하여 주십시오."}
        return {"PrecService": {**CASES[params["ID"]], "판례정보일련번호": params["ID"]}}


def scripted(calls):
    """A grader that reads the text the way the made-up cases are written, and quotes it verbatim."""

    def grade(instructions, document):
        calls.append(document.split("\n")[1])
        body = "\n".join(line for line in document.split("\n\n", 1)[1].split("\n") if not line.startswith("["))  # without the unit labels
        if "과징금 산정" in body:
            return Grade(scope="out", scope_reason="과징금 · 벌칙의 산정만 다루는 것", about_images=False, answers=[]), 0.001
        topics = ("초상권", "영상정보처리기기", "위법성이 조각")
        if any(topic in body for topic in topics):
            sentence = next(s for s in body.replace("\n", " ").split(". ") if any(topic in s for topic in topics)).strip().rstrip(".") + "."
            side = "against" if "조각" in body else "protect" if "침해" in body else "none"
            question = "Q2" if "설치" in body else "Q6"
            return Grade(scope="in", scope_reason="", about_images=True, answers=[Answer(question=question, score=3, direction=side, quote=sentence, why="직접 판단")]), 0.001
        return Grade(scope="out", scope_reason="무관", about_images=False, answers=[]), 0.001

    return grade


RULES = {
    "basis_date": "2026-11-13",
    "scope": {"criterion": "가림 결정을 바꿀 수 있으면 범위 안", "questions": {f"Q{n}": f"질문 {n}" for n in range(9)}, "out": ["과징금 · 벌칙의 산정만 다루는 것"], "hold": ["유명인의 초상의 상업적 이용"]},
    "norms": [{"id": "pipa", "target": "law", "name": "개인정보 보호법", "authority": 1}],
    "guides": [
        {"id": "guide-a", "kind": "guideline", "title": "안내서 A", "path": "inbox/guide-a.txt"},
        {"id": "guide-b", "kind": "guideline", "title": "안내서 B", "path": "inbox/guide-b.txt", "page": "https://example.org/b"},
    ],
    "paths": {"institution": {"from_kinds": ["guideline"]}, "statute_refs": {"prec": ["헌법"]}, "keywords": {"prec": ["초상권", "영상정보처리기기"], "ppc": ["영상정보처리기기"]}, "citations": {"rounds": 2, "max_mentions": 300}, "widen": {}},
    "screen": {"case_types": ["민사"], "words": ["초상", "영상정보처리기기", "폐쇄회로", "사진"]},
    "grading": {"scale": {3: "직접", 2: "유추", 1: "낱말만"}, "model": "light", "check_model": "strong", "slot": "llm.analyst", "check_sample": 0.0, "seed": 1, "escalate_above": 0.5, "parallel": 2, "cost_cap_usd": 5},
    "versions": {
        "v1": {"courts": ["대법원"], "min_score": {"prec": 3, "detc": 3, "expc": 3, "ppc": 3}, "about_images_only": ["expc", "ppc"], "trail": True},
        "v2": {"courts": "all", "min_score": {"prec": 2, "detc": 2, "expc": 2, "ppc": 2}, "about_images_only": ["expc", "ppc"], "trail": True},
    },
    "expect": {"cases": [{"case": "2020다100", "question": "Q6"}], "articles": [{"norm": "pipa", "article": "제25조", "question": "Q2"}]},
}


@pytest.fixture
def lab(tmp_path):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "guide-a.txt").write_text("폐쇄회로 영상의 제공에 관하여는 서울중앙지법 2018. 2. 2. 선고 2018가합500 판결을 본다.", encoding="utf-8")
    rules_file = tmp_path / "selection.yaml"
    rules_file.write_text(yaml.safe_dump(RULES, allow_unicode=True), encoding="utf-8")
    store = CorpusStore(tmp_path / "corpus")

    def make(version="v1", calls=None, law=None, change=None):
        """A selection over the scripted API. `change(rules)` edits a copy of the rules, which then get their own run folder."""
        calls = [] if calls is None else calls
        graders = {"model": scripted(calls), "check_model": scripted(calls)}
        file, name = rules_file, version
        if change:
            rules = copy.deepcopy(RULES)
            change(rules)
            name = f"{version}-{len(list(tmp_path.glob('selection-*.yaml')))}"
            file = tmp_path / f"selection-{name}.yaml"
            file.write_text(yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8")
        return Selection(Rules(file), store, law or FakeLaw(), tmp_path / "runs" / name, version=version, graders=graders, root=tmp_path, say=lambda _: None, pause=0, grade_cache=tmp_path / "grades.jsonl")

    return make, store, tmp_path


def by_no(selection, number):
    return next(c for c in selection.cands.values() if c["no"] == number)


def test_the_text_of_a_judgment_says_what_it_reviews_cites_and_orders():
    text = casetext.plain(CASES["100"]["판례내용"])
    assert casetext.reviewed(text) == {"no": "2019나300", "court": "서울고등법원", "date": "2020-01-09", "as_written": "서울고법 2020. 1. 9. 선고 2019나300 판결"}
    assert casetext.disposition(casetext.sections(text)["주문"]) == "파기환송"
    assert {"no": "2004다200", "court": "대법원", "date": "2006-10-13"} in casetext.citations(text)
    # a year and a count are not a case number; a number followed by "등" is
    assert [c["no"] for c in casetext.citations("2020년 3월 원고 3명. 헌재 2005. 5. 26. 99헌마513등")] == ["99헌마513"]
    assert casetext.statute_refs("[1] 헌법 제10조, 민법 제750조 / [2] 구 개인정보 보호법(2020. 2. 4. 개정 전) 제2조 제1호, 제17조") == [("헌법", "제10조"), ("민법", "제750조"), ("구 개인정보 보호법(2020. 2. 4. 개정 전)", "제2조"), ("구 개인정보 보호법(2020. 2. 4. 개정 전)", "제17조")]


def test_candidates_go_from_search_to_adoption_and_every_step_is_recorded(lab):
    make, store, tmp = lab
    selection = make()
    selection.run()
    # 600 is the later full-bench judgment: it was read to check for a change, and it answers Q6 itself
    assert {c["no"] for c in selection.cands.values() if (c.get("adopted") or {}).get("main")} == {"2020다100", "2004다200", "2022다600", "제2024-001-001호"}

    main = by_no(selection, "2020다100")
    assert {f["path"] for f in main["found"]} >= {"②", "③"} and main["text_rule"]["pass"] and main["final"]["answers"][0]["located"]["unit"] == "판례내용"
    # 2004다200 was not in any search result: it came from the citation, in the first round of following
    cited = by_no(selection, "2004다200")
    assert cited["round"] == 1 and [f["path"] for f in cited["found"]][0] == "④"
    # a loose match fails the text rule and is never sent to the model; a lower court is not fetched for v1
    loose = by_no(selection, "2019다400")
    assert loose["text_rule"] == {"pass": False, "why": "주제어가 원문에 없음(검색이 느슨하게 맞춘 결과)", "words": []} and "final" not in loose
    # the search for 100's number returned it too, and 100's number is not in its text: that is not a citation
    assert [f["confirmed"] for f in loose["found"] if f["path"] == "④"] == [False]
    assert ["confirmed" in f for f in by_no(selection, "2021나700")["found"] if f["path"] == "④"] == [False]  # a lower court: the rule is not applied to it in v1
    # listed by the API, but it has no text for it: not stored
    assert by_no(selection, "2020두950")["secured"]["status"] == "no_body" and store.get("prec-950") is None
    # one decision under two serial numbers: read once, adopted once; its name and number come from the body
    first, second = selection.cands["ppc:31"], selection.cands["ppc:32"]
    assert first["adopted"]["main"] and second["duplicate_of"] == "ppc:31" and second["adopted"]["why"].startswith("중복") and "grade" not in second
    assert (first["no"], first["date"], store.get("ppc-31").citation) == ("제2024-001-001호", "2024-01-10", "개인정보보호위원회 제2024-001-001호")
    lower = by_no(selection, "2018가합500")
    assert not lower["meta_rule"]["pass"] and lower.get("doc_id") is None and "①" in {f["path"] for f in lower["found"]}
    assert by_no(selection, "2021두800")["adopted"] == {"main": False, "why": "범위 밖: 과징금 · 벌칙의 산정만 다루는 것", "questions": {}, "version": "v1"}

    # originals are stored unindexed, with where they came from; no credential anywhere
    record = store.get(main["doc_id"])
    assert (record.index, record.citation, record.dates, record.url) == (False, "대법원 2020다100", {"decided_on": "2021-03-04"}, "https://www.law.go.kr/DRF/lawService.do?type=JSON&target=prec&ID=100")
    assert store.get("pipa").index is False and store.get("guide-a").index is False
    for file in tmp.rglob("*"):
        assert not file.is_file() or SECRET.encode() not in file.read_bytes(), file
    searches = [json.loads(line) for line in (selection.dir / "searches.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {s["path"] for s in searches} >= {"규범", "②", "③", "①", "④", "경과"} and all("at" in s and "total" in s for s in searches)


def test_the_same_case_in_other_instances_and_later_changes_are_looked_for_and_never_assumed(lab):
    make, _, _ = lab
    selection = make()
    selection.run()
    relations = {(r["type"], r["from"].split(":")[-1]): r for r in selection.state["relations"]}
    # the judgment 100 reviews is named in its text, and the API does not have it: wanted, not secured, outcome known
    reviewed = relations[("원심", "2020다100")]
    assert (reviewed["status"], reviewed["outcome"], reviewed["evidence"]["quote"]) == ("원문 미확보", "파기환송", "서울고법 2020. 1. 9. 선고 2019나300 판결")
    assert reviewed["evidence"]["located"]["unit"] == "판례내용"
    assert by_no(selection, "2019나300")["secured"]["status"] == "not_found" and "trail" in by_no(selection, "2019나300")["purposes"]
    # the judgment after the remand says so itself
    after = relations[("환송 후", "2020다100")]
    assert after["status"] == "원문 확보" and after["to"].endswith("2021나700") and "환송판결인 대법원" in after["evidence"]["quote"]
    assert "trail" in by_no(selection, "2021나700")["purposes"] and "final" not in by_no(selection, "2021나700")
    # 200 was upheld below (no remand to look for); its doctrine was changed by a later full bench
    assert relations[("원심", "2004다200")]["evidence"] == {"note": "원문에 원심 표시가 없음"}
    change = by_no(selection, "2004다200")["change"]
    assert change["status"] == "변경 문구 발견" and change["by"].endswith("2022다600") and "변경하기로 한다" in change["quote"]
    assert by_no(selection, "2020다100")["change"]["status"] == "발견 못 함" and "이후 전원합의체 0건" in by_no(selection, "2020다100")["change"]["scope"]
    # both sides of Q6 are among the adopted cases; what the adopted cases cite is set against what was collected
    assert selection.state["balance"]["Q6"]["status"] == "양쪽 사례 있음" and selection.state["balance"]["Q0"]["status"] == "채택 사례 없음"
    refs = {(r["law"], r["article"]): r["status"] for r in selection.state["norm_refs"]}
    assert refs[("민법", "제750조")] == "법령 미수집"


def test_the_list_reproduces_the_collection_and_the_report_counts_what_is_not_known(lab):
    make, store, tmp = lab
    selection = make()
    selection.run()
    list_file, report_file = publish(selection, tmp / "lists")
    listed = yaml.safe_load(list_file.read_text(encoding="utf-8"))
    assert list_file.stem == listed["version"]["id"] and listed["version"]["id"].startswith("v1-20261113-") and listed["defaults"] == {"index": False}
    by_id = {d["id"]: d for d in listed["documents"]}
    assert by_id["pipa"]["selection"]["units"] == "all" and by_id["guide-a"]["source"] == "local_file"
    assert by_id["prec-100"]["selection"]["purpose"] == "main" and by_id["prec-100"]["selection"]["questions"] == {"Q6": 3} and by_id["prec-100"]["params"] == {"ID": "100"}
    assert by_id["prec-700"]["selection"]["purpose"] == "trail" and "prec-400" not in by_id and "prec-500" not in by_id
    assert {m.get("id") or m["case"] for m in listed["missing"]} == {"guide-b", "서울고등법원 2019나300"}

    summary = json.loads(report_file.read_text(encoding="utf-8"))
    assert summary["version"] == listed["version"]["id"] == report(selection)["version"]
    assert summary["stages"]["main_by_kind"] == {"prec": 3, "ppc": 1} and summary["stages"]["not_adopted"]["규칙: 주제어가 원문에 없음"] == 1
    assert (summary["stages"]["duplicates"], summary["stages"]["no_body"]) == (1, 1) and summary["stages"]["not_adopted"]["원문 미확보: API가 본문을 주지 않음"] == 1
    cited = summary["stages"]["cited"]  # 100 and 600 do cite 2004다200; 400 only matched the search; 700 is not read for v1's main sources
    assert (cited["search_hits"], cited["search_hits_confirmed"], cited["search_hits_not_read"]) == (4, 2, 1)
    assert summary["selection_records"]["quotes"] == summary["selection_records"]["quotes_located"] == 4
    assert summary["grading"]["spent"]["light"]["calls"] > 0 and summary["grading"]["cost_usd"] > 0
    paid_twice = (tmp / "grades.jsonl").read_text(encoding="utf-8").splitlines()[0]  # the same text graded twice at once: both calls were paid for
    with (tmp / "grades.jsonl").open("a", encoding="utf-8") as file:
        file.write(paid_twice + "\n")
    spent = report(make())["grading"]["spent"][json.loads(paid_twice)["model"]]
    assert spent["calls"] == summary["grading"]["spent"][json.loads(paid_twice)["model"]]["calls"] + 1
    assert [(e.get("case") or e["article"], e["matches"]) for e in summary["expect"]] == [("2020다100", True), ("제25조", True)]
    assert summary["questions"]["Q2"]["articles"] == 1 and summary["guides"]["guide-b"]["status"] == "missing"

    # another machine: the list alone brings the same originals, unindexed
    other = CorpusStore(tmp / "other")
    outcomes = fetch_all(listed["documents"], other, FetchContext(root=tmp, http=None, law=FakeLaw()), listed["defaults"]["index"])
    assert {o.status for o in outcomes} == {"new"}
    assert other.get("prec-100").sha256 == store.get("prec-100").sha256 and other.get("prec-100").index is False


def test_a_second_run_asks_no_model_again_and_a_wider_version_takes_the_lower_court(lab):
    make, store, _ = lab
    calls = []
    first = make(calls=calls)
    first.run()
    asked = len(calls)
    assert asked > 0
    again = make(calls=calls)  # the same folder: nothing is fetched or graded twice
    again.run()
    assert len(calls) == asked and list_entries(again)["version"]["id"] == list_entries(first)["version"]["id"]

    wide = make("v2", calls=calls)  # a new folder; grades of the same texts come from the shared cache
    wide.run()
    lower = by_no(wide, "2018가합500")
    assert lower["adopted"]["main"] and store.get(lower["doc_id"]).index is False
    assert all("2020다100" not in label and "2004다200" not in label for label in calls[asked:])  # graded once, in the first run
    assert {c["no"] for c in wide.cands.values() if (c.get("adopted") or {}).get("main")} >= {"2020다100", "2004다200", "2018가합500", "2021나700"}


def test_a_question_answered_one_way_only_gets_one_wider_search(lab):
    make, _, _ = lab

    def narrow(rules):  # without following citations the other side of Q6 (2004다200) is never found …
        rules["paths"]["citations"]["rounds"] = 0
        rules["paths"]["widen"] = {"Q6": ["위법성이"]}  # … until the question is searched again with a wider word

    selection = make(change=narrow)
    selection.run()
    balance = selection.state["balance"]["Q6"]
    assert (balance["before"]["against"], balance["against"], balance["widened"], balance["status"]) == (0, 1, True, "양쪽 사례 있음")
    assert [f["by"] for f in by_no(selection, "2004다200")["found"]] == ["넓힌 주제어 위법성이 (Q6)"]
    assert selection.state["balance"]["Q2"]["widened"] is False  # nothing to widen with: recorded as it is


def test_a_citation_search_that_matches_far_too_much_is_not_used(lab):
    make, _, _ = lab

    def tight(rules):
        rules["paths"]["citations"]["max_mentions"] = 0

    selection = make(change=tight)
    selection.run()
    main = by_no(selection, "2020다100")
    assert main["mentions"] == [] and "사건번호와 무관한" in main["mentions_note"]
    assert main["change"]["status"] == "검사 못 함" and "2021나700" not in {c["no"] for c in selection.cands.values()}
    after = next(r for r in selection.state["relations"] if r["type"] == "환송 후" and r["from"].endswith("2020다100"))
    assert after["status"] == "미확인" and after["evidence"]["note"].endswith("없다는 뜻이 아니다")


class GoesDown(FakeLaw):
    """The API until the site closes for maintenance: after `searches` searches, no request is answered."""

    def __init__(self, searches):
        super().__init__()
        self.left = searches

    def _answering(self, searching):
        if self.left <= 0:
            raise LawApiUnavailable(f"HTTP 302 → https://www.law.go.kr/sorry/sorry.html (점검 안내로 보냄) · https://www.law.go.kr/DRF/lawSearch.do?OC={SECRET}&target=prec")
        self.left -= searching

    def search(self, target, display=20, page=1, **params):
        self._answering(1)
        return super().search(target, display=display, page=page, **params)

    def service(self, target, **params):
        self._answering(0)
        return super().service(target, **params)


def outcome(selection):
    return {key: ((c.get("secured") or {}).get("status"), (c.get("adopted") or {}).get("main"), sorted(c["purposes"]), (c.get("change") or {}).get("status")) for key, c in selection.cands.items()}


@pytest.mark.parametrize("share", [0.0, 0.2, 0.5, 0.8, 0.97])
def test_an_api_that_stops_answering_halts_the_run_and_nothing_is_recorded_as_not_found(lab, share):
    make, _, tmp = lab
    counting = FakeLaw()
    whole = make(law=counting, change=lambda rules: rules.update(note="the same rules in a run of their own"))
    whole.run()
    searches = sum(1 for call in counting.calls if call[0] == "search")

    down = make(law=GoesDown(int(searches * share)))
    with pytest.raises(Halted, match="응답하지 않습니다"):
        down.run()
    assert "norm_refs" not in down.state["done"]  # the run did not end, so the command publishes no list
    log = (down.dir / "searches.jsonl").read_text(encoding="utf-8")
    assert '"error"' in log.splitlines()[-1] and SECRET not in log  # the search that got no answer is on record, without the credential

    back = make()  # the same folder once the API answers again
    back.run()
    assert outcome(back) == outcome(whole) and len(back.state["relations"]) == len(whole.state["relations"])
    assert {c["no"] for c in back.cands.values() if (c.get("adopted") or {}).get("main")} == {"2020다100", "2004다200", "2022다600", "제2024-001-001호"}


def test_judgments_a_person_obtained_are_picked_up_from_the_inbox(lab):
    make, store, tmp = lab
    first = make()
    first.run()
    assert by_no(first, "2019나300")["secured"]["status"] == "not_found"

    inbox = tmp / "corpus" / "inbox" / "judgments"
    inbox.mkdir(parents=True)
    # the judgment 100 reviewed, as the court's reading service gives it: no brackets, letters spaced out
    (inbox / "서울고등법원_2019나300.txt").write_text("서 울 고 등 법 원\n판 결\n제 1 심 판 결 서울중앙지방법원 2019. 5. 1. 선고 2018가합900 판결\n주 문\n피고의 항소를 기각한다.\n이 유\n방송사가 얼굴을 내보낸 것은 초상권 침해이다.", encoding="utf-8")
    # the judgment after the remand: nobody knew its number; it names 100 as the remanding judgment, across a line break
    (inbox / "서울고등법원_2021나701.txt").write_text("주 문\n원고의 청구를 기각한다.\n이 유\n환송판결인 대법원 2021. 3. 4. 선고\n2020다100 판결의 취지에 따라 다시 판단한다.", encoding="utf-8")
    # a later judgment of another court that cites 100 and mentions a remand: not this case's continuation
    (inbox / "부산고등법원_2022나55.txt").write_text("이 유\n대법원 2020다100 판결은 원심을 파기환송하였다.", encoding="utf-8")

    again = make()  # the same run, continued
    again.run()
    reviewed = by_no(again, "2019나300")
    assert reviewed["secured"]["status"] == "ok" and reviewed["secured"]["by_hand"] and reviewed["secured"]["complete"]
    record = store.get(reviewed["doc_id"])
    assert (record.source, record.index, record.citation, record.extra["file"]) == ("local_file", False, "서울고등법원 2019나300", "서울고등법원_2019나300.txt")
    relations = {(r["type"], (r["to"] or "").split(":")[-1]): r for r in again.state["relations"] if r["from"].endswith("2020다100")}
    assert relations[("원심", "2019나300")]["status"] == "원문 확보"
    assert relations[("제1심", "2018가합900")]["status"] == "원문 미확보"  # named in the judgment that was obtained: the next one to look for
    assert relations[("환송 후", "2021나701")]["status"] == "원문 확보" and "환송판결인 대법원 2021. 3. 4. 선고 2020다100 판결" in relations[("환송 후", "2021나701")]["evidence"]["quote"]
    assert ("환송 후", "2022나55") not in relations and ("환송 후", "") not in relations
    listed = {d["id"]: d for d in list_entries(again)["documents"]}
    assert listed[reviewed["doc_id"]]["source"] == "local_file" and listed[reviewed["doc_id"]]["path"] == "corpus/inbox/judgments/서울고등법원_2019나300.txt"
    assert listed[reviewed["doc_id"]]["selection"]["purpose"] == "trail"
    assert "서울고등법원 2019나300" not in {m.get("case") for m in list_entries(again)["missing"]} and "서울중앙지방법원 2018가합900" in {m.get("case") for m in list_entries(again)["missing"]}


BLANK_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n164\n%%EOF\n"


def test_a_guide_added_later_is_read_by_ocr_when_its_pdf_has_no_text_and_its_citations_are_followed(lab, monkeypatch):
    make, store, tmp = lab
    first = make()
    first.run()
    assert first.state["guides"]["guide-b"]["status"] == "missing" and "2004다200" in {c["no"] for c in first.cands.values()}

    (tmp / "inbox" / "guide-b.pdf").write_bytes(BLANK_PDF)  # typeset with its letters turned into shapes: no text layer
    read = []

    def reading(record, content):  # stands in for the OCR models
        read.append(record.doc_id)
        return [parsers.Unit(unit_id="p1", label="1쪽", text="공개 범위에 관하여 대법원 2021. 3. 4. 선고 2020다100 판결과 대법원 2022. 1. 1. 선고 2021두800 판결 참조", meta={"page": 1, "ocr": "test"})]

    monkeypatch.setitem(parsers.PARSERS, "pdf_ocr", reading)

    def with_pdf(rules):
        rules["guides"][1]["path"] = "inbox/guide-b.pdf"

    again = make(change=with_pdf)
    again.run()
    guide = again.state["guides"]["guide-b"]
    assert (guide["status"], guide["read_by"], read) == ("ok", "ocr", ["guide-b"]) and (store.get("guide-b").parser, store.get("guide-b").extra["read_by"]) == ("pdf_ocr", "ocr")
    assert "①" in {f["path"] for f in by_no(again, "2021두800")["found"]} and "①" in {f["path"] for f in by_no(again, "2020다100")["found"]}
    make(change=with_pdf).run()  # another run over the same store: nothing is read again, and the record keeps saying how it was read
    assert read == ["guide-b"] and store.get("guide-b").parser == "pdf_ocr"
