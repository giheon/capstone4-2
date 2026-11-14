"""Reading what a Korean judgment says about itself: its number, the judgment it reviews, its order, the cases
it cites. Everything here is read from the text as written; nothing is inferred beyond what a marker states.
"""
from __future__ import annotations

import re
from typing import Any

# case-type marks. A bare "(\d+)[가-힣](\d+)" would also match dates ("2020년 3") and counts, so marks are listed.
_MARKS = (
    "헌[가나다라마바사아]|가합|가단|가소|고합|고단|고정|구합|구단|카합|카단|카기|드합|드단|느합|느단|재나|재다|재두|재도|초기|전도|전합"
    "|[다도두마므스그모후허추수우나노누르라로머버서어저처커터퍼]"
)
CASE_NO = re.compile(rf"(?<![\d가-힣])((?:19|20)?\d{{2}})\s?({_MARKS})\s?(\d{{1,7}})(?!\d)")
_COURT = re.compile(r"(대법원|헌법재판소|헌재|[가-힣]{2,8}(?:고등법원|지방법원|가정법원|행정법원|회생법원|고법|지법)(?:\s?[가-힣]{2,6}지원)?|특허법원)")
_DATE = re.compile(r"((?:19|20)\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})")
SUPREME_MARKS = set("다도두마므스그모후추")
_SHORT = {"고법": "고등법원", "지법": "지방법원"}
MARKER = re.compile(r"【\s*([^】]{1,20}?)\s*】")


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def plain(value: str) -> str:
    """API text without its markup."""
    return re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", value or ""))


def case_no(text: str) -> str | None:
    match = CASE_NO.search(text or "")
    return f"{match.group(1)}{match.group(2)}{match.group(3)}" if match else None


def court_of(number: str, named: str | None = None) -> str:
    """The court a case number belongs to: the one named next to it, or what its mark says."""
    if named:
        named = "헌법재판소" if named == "헌재" else named
        for short, full in _SHORT.items():
            if named.endswith(short) or f"{short} " in named:
                named = named.replace(short, full, 1)
        return re.sub(r"\s+", "", named)
    mark = CASE_NO.search(number)
    if mark and mark.group(2).startswith("헌"):
        return "헌법재판소"
    if mark and mark.group(2) in SUPREME_MARKS:
        return "대법원"
    return ""


def iso_date(value: str) -> str:
    """'2023.04.13' · '20230413' · '2023. 4. 13.' → '2023-04-13' ('' when it is not a date)."""
    digits = re.findall(r"\d+", value or "")
    if len(digits) == 1 and len(digits[0]) == 8:
        digits = [digits[0][:4], digits[0][4:6], digits[0][6:]]
    if len(digits) >= 3 and len(digits[0]) == 4:
        return f"{digits[0]}-{int(digits[1]):02d}-{int(digits[2]):02d}"
    return ""


def citations(text: str) -> list[dict[str, str]]:
    """Every case the text cites: number, and the court and date written in front of it when there are any.
    A list of numbers after one court ("대법원 … 2004다16280 판결, … 2015다33489 판결") keeps that court."""
    out: dict[str, dict[str, str]] = {}
    for match in CASE_NO.finditer(text or ""):
        number = f"{match.group(1)}{match.group(2)}{match.group(3)}"
        before = text[max(0, match.start() - 45) : match.start()]
        courts, dates = _COURT.findall(before), _DATE.findall(before)
        named = courts[-1] if courts else None
        entry = {"no": number, "court": court_of(number, named), "date": f"{dates[-1][0]}-{int(dates[-1][1]):02d}-{int(dates[-1][2]):02d}" if dates else ""}
        known = out.get(number)
        if known is None or (not known["date"] and entry["date"]):
            out[number] = entry
    return list(out.values())


def sections(text: str) -> dict[str, str]:
    """The 【…】 parts of a judgment's full text, by marker name without spaces (원심판결, 주문, 이유 …)."""
    marks = list(MARKER.finditer(text or ""))
    return {squeeze(mark.group(1)): text[mark.end() : marks[index + 1].start() if index + 1 < len(marks) else len(text)].strip() for index, mark in enumerate(marks)}


def reviewed(text: str, marker: str = "원심판결") -> dict[str, str] | None:
    """The judgment under review, as the text names it (【원심판결】 서울서부지법 2020. 7. 16. 선고 2019나38288 판결)."""
    line = sections(text).get(marker, "").split("\n")[0].strip()
    if not line:  # a court's own PDF: "원 심 판 결  서울고등법원 2020. 1. 9. 선고 2019나300 판결", no brackets
        plain_form = re.search(r"[ \t]*".join(marker) + r"[ \t]*[:：]?[ \t]*([^\n]+)", text or "")
        line = plain_form.group(1).strip() if plain_form else ""
    number = case_no(line)
    if not number:
        return None
    court = _COURT.search(line)
    date = _DATE.search(line)
    return {"no": number, "court": court_of(number, court.group(1) if court else None), "date": f"{date.group(1)}-{int(date.group(2)):02d}-{int(date.group(3)):02d}" if date else "", "as_written": line[:160]}


def disposition(order: str) -> str:
    """What the order does to the judgment under review, read from its own words: 유지 · 파기환송 · 파기자판 ·
    일부 파기 · 미확인. It says nothing about who was right on a question (that is not read from an order)."""
    text = squeeze(order or "")
    if not text:
        return "미확인"
    quashed = "파기" in text
    partly = bool(re.search(r"중[^.]{0,60}부분을파기", text)) or ("파기" in text and "나머지상고" in text and "기각" in text)
    if quashed and partly:
        return "일부 파기"
    if quashed and ("환송" in text or "이송" in text):
        return "파기환송"
    if quashed:
        return "파기자판"
    if "기각" in text or "각하" in text:
        return "유지"
    return "미확인"


def complete(kind: str, units: dict[str, str]) -> tuple[bool, list[str]]:
    """Whether the stored text is the whole document, and what is missing when it is not. `units` maps the
    API's passage fields to their text."""
    missing: list[str] = []
    if kind == "prec":
        parts = sections(units.get("판례내용", ""))
        missing = [name for name in ("주문", "이유") if not parts.get(name)]
        if not units.get("판례내용"):
            missing = ["전문"]
    elif kind == "detc":
        if len(units.get("전문", "")) < 300:
            missing = ["전문"]
    elif kind == "expc":
        missing = [name for name in ("질의요지", "회답", "이유") if not units.get(name)]
    elif kind == "ppc":
        if not units.get("주문"):
            missing.append("주문")
        if not any(len(units.get(name, "")) > 200 for name in ("이유", "별지", "결정요지", "주요내용")):
            missing.append("이유")
    return not missing, missing


def locate(units: list[Any], quote: str) -> dict[str, Any] | None:
    """Where a quoted sentence is in the document: the unit and the character offset in that unit's text.
    Spacing and line breaks are ignored; any other difference means the sentence is not in the text."""
    wanted = squeeze(quote or "").strip("\"'“”‘’")
    if len(wanted) < 8:
        return None
    for unit in units:
        positions = [index for index, char in enumerate(unit.text) if not char.isspace()]
        at = "".join(unit.text[index] for index in positions).find(wanted)
        if at >= 0:
            return {"unit": unit.unit_id, "at": positions[at], "length": positions[at + len(wanted) - 1] - positions[at] + 1}
    return None


def statute_refs(text: str) -> list[tuple[str, str]]:
    """(law name, article) pairs of a 참조조문 field: "[1] 헌법 제10조, 민법 제750조 / [2] 개인정보 보호법 제2조 제1호, 제17조".
    A bare article takes the law named before it. The law name is kept as written ("구 …" stays)."""
    out: list[tuple[str, str]] = []
    law = ""
    for piece in re.split(r"[,/\n]|\[\d+\]", plain(text)):
        piece = piece.strip()
        match = re.search(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", piece)
        if not match:
            continue
        name = piece[: match.start()].strip(" ·")
        if name and not re.match(r"제\s*\d|같은\s*법|동법", name):  # "제5호(현행 …" continues the article before it
            law = re.sub(r"\s+", " ", name)
        if law:
            out.append((law, f"제{match.group(1)}조" + (f"의{match.group(2)}" if match.group(2) else "")))
    return list(dict.fromkeys(out))
