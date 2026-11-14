import json

import pytest

from privframe.core.corpus import CorpusStore, DocRecord
from privframe.core.parsers import OCR_REF, ParseError, law_html, lawgokr_json, parse_all, parser_for, pdf_ocr, units_from_pages


def record(doc_id: str = "d", source: str = "lawgokr", fmt: str = "json") -> DocRecord:
    return DocRecord(doc_id=doc_id, kind="law", title="t", source=source, format=fmt, sha256="0", size=1, fetched_at="2026-11-11T00:00:00+00:00")


def test_statute_page_splits_into_articles():
    html = """<html><body><script>ignored()</script>
      <div class="lawcon">제1조(목적) 이 법은 개인정보의 처리에 관한 사항을 정한다.</div>
      <div class="lawcon">제25조의2(이동형 영상정보처리기기의 운영 제한) ① 업무를 목적으로 …</div>
      <div class="lawcon">부칙 내용은 조문이 아니다</div></body></html>""".encode()
    units = law_html(record(source="law_html", fmt="html"), html)
    assert [u.unit_id for u in units] == ["제1조", "제25조의2"]
    assert units[1].label == "제25조의2(이동형 영상정보처리기기의 운영 제한)" and "업무를 목적으로" in units[1].text


def test_statute_api_keeps_paragraphs_and_items_in_order():
    body = {"법령": {"기본정보": {"법령명_한글": "개인정보 보호법"}, "조문": {"조문단위": [
        {"조문번호": "1", "조문여부": "전문", "조문내용": "제1장 총칙"},
        {"조문번호": "25", "조문가지번호": "2", "조문여부": "조문", "조문제목": "이동형 영상정보처리기기의 운영 제한",
         "조문내용": "제25조의2(이동형 영상정보처리기기의 운영 제한)", "조문시행일자": "20260911", "조문참고자료": "[본조신설 2023.3.14]",
         "항": [{"항번호": "①", "항내용": "① 촬영하여서는 아니 된다.", "호": [{"호번호": "1.", "호내용": "1. 제15조제1항 각 호"}, {"호번호": "2.", "호내용": "2. 촬영 사실을 표시한 경우"}]},
               {"항번호": "②", "항내용": "② 사생활을 침해할 우려가 있는 장소"}]},
    ]}}}
    units = lawgokr_json(record(), json.dumps(body).encode())
    assert [u.unit_id for u in units] == ["제25조의2"]  # the chapter heading is not an article
    lines = units[0].text.split("\n")
    assert lines == ["제25조의2(이동형 영상정보처리기기의 운영 제한)", "① 촬영하여서는 아니 된다.", "1. 제15조제1항 각 호", "2. 촬영 사실을 표시한 경우", "② 사생활을 침해할 우려가 있는 장소", "[본조신설 2023.3.14]"]
    assert units[0].meta["조문시행일자"] == "20260911"


def test_administrative_rule_articles():
    body = {"AdmRulService": {"행정규칙기본정보": {"행정규칙명": "표준 개인정보 보호지침"}, "조문내용": ["제1장 총칙", "제1조(목적) 이 지침은 …", "제2조(용어의 정의) 뜻은 다음과 같다.", "1. \"처리\"란 …"]}}
    units = lawgokr_json(record(), json.dumps(body).encode())
    assert [u.unit_id for u in units] == ["제1조", "제2조"] and units[1].text.endswith("1. \"처리\"란 …")


def test_judgment_fields_become_units_and_identifiers_do_not():
    body = {"PrecService": {"사건번호": "2021다219116", "법원명": "대법원", "사건명": "초상권침해금지및방해예방청구" * 3,
                            "판결요지": "촬영에 관한 동의를 받았다 하더라도 허용 범위를 벗어나 공표하려면 그에 관하여도 동의를 받아야 한다.",
                            "판례내용": "【주    문】<br/>원심판결을 파기하고, 사건을 서울고등법원에 환송한다.<br/><br/>【이    유】상고이유를 판단한다."}}
    units = lawgokr_json(record(), json.dumps(body).encode())
    assert [u.unit_id for u in units] == ["판결요지", "판례내용"]
    assert "<br" not in units[1].text and "환송한다.\n" in units[1].text


def test_empty_pdf_pages_are_dropped():
    units = units_from_pages(["첫 쪽", "", "셋째 쪽"])
    assert [(u.unit_id, u.label) for u in units] == [("p1", "1쪽"), ("p3", "3쪽")]


ONE_BLANK_PAGE = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n164\n%%EOF\n"


def test_a_pdf_without_a_text_layer_is_read_page_by_page_as_upright_lines(monkeypatch):
    asked = []

    class Reader:  # stands in for the OCR models: boxes (x, y, width, height) in any order, as the real reader gives them
        def read(self, image, *, upright=False):
            asked.append((image.shape[2], upright))
            return [
                ((300.0, 100.0, 200.0, 30.0), "2014가합508066 판결", 0.9),
                ((20.0, 160.0, 480.0, 30.0), "개인정보에 해당한다.", 1.0),
                ((20.0, 102.0, 260.0, 30.0), "서울중앙지방법원 2017. 9. 11. 선고", 0.8),
                ((530.0, 60.0, 20.0, 200.0), "&리Y", 0.5),  # the chapter tab printed sideways in the margin
            ]

    monkeypatch.setattr("privframe.core.perception.models._rapidocr", lambda ref: Reader())
    (unit,) = pdf_ocr(record(source="local_file", fmt="pdf"), ONE_BLANK_PAGE)
    assert asked == [(3, True)]  # a rendered page stands upright: no line is checked for being upside down
    assert unit.text == "서울중앙지방법원 2017. 9. 11. 선고 2014가합508066 판결\n개인정보에 해당한다."
    assert (unit.unit_id, unit.meta) == ("p1", {"page": 1, "ocr": OCR_REF, "cls": "off", "score": 0.9})  # a reading, and it says so

    monkeypatch.setattr(Reader, "read", lambda self, image, *, upright=False: [])
    with pytest.raises(ParseError, match="OCR로도"):
        pdf_ocr(record(source="local_file", fmt="pdf"), ONE_BLANK_PAGE)


def test_parser_choice_and_reparse_only_when_needed(tmp_path):
    store = CorpusStore(tmp_path)
    store.put_raw(doc_id="note", kind="memo", title="메모", source="local_file", format="txt", content="본문입니다".encode())
    store.put_raw(doc_id="bad", kind="law", title="빈 법령", source="law_html", format="html", content=b"<html></html>")
    assert parser_for(store.get("note")) == "text" and parser_for(store.get("bad")) == "law_html"
    first = dict((r.doc_id, s) for r, s in parse_all(store))
    assert first["note"] == "parsed 1" and first["bad"].startswith("error")
    assert dict((r.doc_id, s) for r, s in parse_all(store))["note"] == "current"


def test_unknown_format_is_reported():
    with pytest.raises(ParseError, match="파서가 없습니다"):
        parser_for(record(source="http_file", fmt="hwp"))
