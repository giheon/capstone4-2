"""Versioned product questions and deterministic retrieval terms (not legal rules)."""

INPUT_VERSION = "minimal-input-v1"
QUERY_VERSION = "purpose-audience-issues-v1"
PURPOSES = {
    "personal_record": ("개인 기록·보관", "개인영상정보 보관 이용"),
    "social_publish": ("SNS·인터넷 게시", "영상 공개 개인정보 초상 비식별 처리"),
    "commercial": ("광고·홍보·상업적 이용", "영상 홍보 목적 이용 동의 범위"),
    "internal_use": ("업무·조직 내부 활용", "개인영상정보 업무 목적 이용 접근 제한"),
    "limited_share": ("특정 사람에게 보여주거나 제공", "개인영상정보 제공 제3자 보호"),
    "subject_access": ("본인 영상 열람 요청에 대한 제공", "정보주체 개인영상정보 열람 요구 제3자 보호"),
    "evidence_submission": ("신고·제보·수사기관·보험사 제출", "개인영상정보 제출 제공 목적 외 이용"),
    "news_public_interest": ("보도·공익적 정보 전달", "영상 공개 공익 개인정보 보호"),
    "research_ai": ("연구·AI 학습·개발", "개인영상정보 연구 학습 비식별 처리"),
    "other": ("기타", "개인영상정보 이용 목적"),
    "unknown": ("아직 정하지 않음·모름", "개인영상정보 이용 목적 확인"),
}
AUDIENCES = {
    "self_only": ("본인만", "개인 보관 열람"),
    "limited_persons": ("가족·지인 등 일부 사람", "제한된 대상 영상 공유"),
    "internal_members": ("조직 내부의 제한된 관계자", "내부 접근 제한 목적 내 이용"),
    "designated_external": ("특정 외부 사람·기관", "개인영상정보 제3자 제공"),
    "public": ("불특정 다수에게 공개", "영상 공개 제3자 개인정보 보호"),
    "unknown": ("아직 정하지 않음·모름", "공개 제공 범위 확인"),
}
RELATIONSHIPS = {"self": "본인(영상을 업로드한 사용자)", "other": "본인이 아닌 다른 사람", "unknown": "알 수 없음"}
CONSENTS = {"granted": "이번 이용·공개에 동의함", "not_obtained": "동의를 받지 않음",
            "refused": "동의를 명시적으로 거부함", "unknown": "동의 여부를 알 수 없음",
            "not_applicable": "해당 없음"}


def input_options():
    def choices(values):
        return [{"value": key, "label": value[0] if isinstance(value, tuple) else value}
                for key, value in values.items()]
    return {"version": INPUT_VERSION, "use_purpose": choices(PURPOSES), "audience_scope": choices(AUDIENCES),
            "relationship": choices(RELATIONSHIPS), "consent": choices(CONSENTS)}


def usage_scope(scenario):
    """All supplied video-level facts define the scope of person confirmations."""
    from privframe.config import fingerprint
    return fingerprint(scenario.model_dump(exclude={"persons", "track_labels"}))


class QueryBuilder:
    def build(self, scenario):
        issues = set()
        persons = list(scenario.persons.values())
        if any(p.relationship in {"other", "unknown"} for p in persons):
            issues.add("영상 속 제3자 개인정보 보호 비식별 처리")
        for consent, issue in {
            "granted": "동의한 이용 목적 및 공개 범위",
            "not_obtained": "동의 미취득 영상 이용 제공 근거",
            "refused": "명시적 동의 거부 영상 이용 제한",
            "unknown": "동의 여부 미확인 추가 확인",
        }.items():
            if any(p.consent == consent for p in persons):
                issues.add(issue)
        if scenario.use_purpose == "subject_access":
            issues.add("열람 요청 당사자와 함께 촬영된 제3자 보호")
            if any(p.access_requester == "yes" for p in persons):
                issues.add("열람 요청 당사자 확인")
        lines, terms = [], []
        if scenario.use_purpose is not None:
            label, term = PURPOSES[scenario.use_purpose]
            lines.append(f"이용 목적: {label}")
            terms.append(term)
            label, term = AUDIENCES[scenario.audience_scope]
            lines.append(f"공개·제공 범위: {label}")
            terms.append(term)
        else:
            # Legacy developer manifests retain their original descriptive input.
            lines = [scenario.context] + [f"{k}: {v}" for k, v in sorted(scenario.facts.items())]
        query = "\n".join([*lines, *dict.fromkeys(terms), *sorted(issues)])
        return {"version": QUERY_VERSION, "input_version": INPUT_VERSION, "query": query,
                "issues": sorted(issues)}
