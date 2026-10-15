"""Readable product report plus a complete developer/audit JSON snapshot."""
import json

from jinja2 import Environment

from privframe.domain.inputs import AUDIENCES, CONSENTS, PURPOSES, RELATIONSHIPS
from privframe.domain.models import RunSnapshot
from privframe.domain.ports import ArtifactStorage

TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>PrivFrame 처리 보고서</title>
<style>body{font:15px/1.7 system-ui,sans-serif;max-width:1000px;margin:40px auto;padding:0 24px;color:#18312e}
h1{font-size:30px}h2{margin-top:32px;font-size:21px}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:10px;border-bottom:1px solid #d5dfdc;text-align:left;vertical-align:top}.notice{background:#fff4d8;padding:16px}
.evidence{padding:14px 0;border-bottom:1px solid #d5dfdc}.evidence p{white-space:pre-wrap;overflow-wrap:anywhere}
@media print{body{margin:0;max-width:none}button{display:none}h2{break-after:avoid}}</style></head><body>
<button onclick="window.print()">인쇄 / PDF 저장</button><h1>PrivFrame 처리 보고서</h1>
<p>{{ run.created_at }} · {{ status }}</p>
{% if run.config.mode == 'fixture' %}<p class="notice">테스트용 fixture 실행입니다. 실제 법률 판단이나 OpenAI API 검증 결과가 아닙니다.</p>{% endif %}
{% if run.manual_output %}<p class="notice">사용자가 직접 지정한 처리 방법을 적용했습니다.</p>{% endif %}
{% if run.error %}<p class="notice">처리를 완료하지 못했습니다. 작업 상태를 확인해 주세요.</p>{% endif %}
<h2>이용 정보</h2>
{% if run.scenario.use_purpose %}<p>이용 목적: {{ purpose }}<br>공개·제공 범위: {{ audience }}</p>{% endif %}
{% if run.scenario.context %}<p>{{ run.scenario.context }}</p>{% endif %}
{% if run.identity %}<h2>인물 그룹과 사용자 확인</h2><p>{{ run.identity.groups|length }}명 · 인물 확인 {{ '완료' if run.identity.reviewed else '대기' }}</p>
<table><tr><th>인물</th><th>등장 구간 수</th><th>관계</th><th>이번 이용·공개에 대한 동의</th>{% if run.scenario.use_purpose == 'subject_access' %}<th>열람 요청 당사자</th>{% endif %}</tr>
{% for group in run.identity.groups %}{% set person = run.scenario.persons.get(group.id) %}
<tr><td>인물 {{ loop.index }}</td><td>{{ group.track_ids|length }}</td><td>{{ relationships.get(person.relationship, '미확인') if person else '미확인' }}</td>
<td>{{ consents.get(person.consent, '미확인') if person else '미확인' }}</td>{% if run.scenario.use_purpose == 'subject_access' %}<td>{{ requesters.get(person.access_requester, '알 수 없음') if person else '알 수 없음' }}</td>{% endif %}</tr>{% endfor %}</table>{% endif %}
<h2>분석과 처리 결과</h2>
{% if run.observation %}<p>{{ run.observation.width }} × {{ run.observation.height }} · {{ run.observation.duration }}초</p>{% endif %}
{% if run.no_faces_confirmed %}<p>얼굴 미검출 결과를 사용자가 확인하고 원본 저장을 선택했습니다.</p>{% endif %}
{% if run.decision and run.decision.plan %}<p>{{ run.decision.plan.summary }}</p>{% endif %}
{% if run.review_reasons %}<ul>{% for reason in run.review_reasons %}<li>{{ reason.split(': ', 1)[-1] }}</li>{% endfor %}</ul>{% endif %}
{% macro treatment(value) %}{% if value %}{{ actions[value.action] }}{% if value.strength %} · 강도 {{ value.strength }}{% endif %}{% else %}없음{% endif %}{% endmacro %}
<table><tr><th>등장 구간</th><th>처리 제안</th><th>사용자 지정</th><th>실제 적용</th><th>판단 이유</th></tr>
{% if run.observation %}{% for track in run.observation.tracks %}{% set proposal = proposals.get(track.id) %}
<tr><td>{{ track.start }} ~ {{ track.end }}초</td><td>{{ treatment(proposal.treatment if proposal else none) }}</td>
<td>{{ treatment(run.overrides.get(track.id)) }}</td><td>{{ treatment(run.effective_treatments.get(track.id)) }}</td><td>{{ proposal.reason if proposal else '없음' }}</td></tr>{% endfor %}{% endif %}</table>
<h2>판단에 사용한 근거</h2>
{% for item in cited %}<div class="evidence"><strong>{{ item.metadata.title }}</strong><div>{{ item.article or '' }} {% if item.page %}{{ item.page }}쪽{% endif %}
 · {{ item.metadata.version or item.metadata.edition or '' }}</div>{% if item.metadata.source_url %}<a href="{{ item.metadata.source_url }}" rel="noreferrer">원문 출처</a>{% endif %}<p>{{ item.text }}</p></div>{% else %}<p>확인 가능한 인용 근거가 없습니다.</p>{% endfor %}
<p>이 보고서는 저장된 입력과 처리 결과로 생성되었습니다.</p></body></html>"""


class SnapshotReporter:
    def __init__(self, artifacts: ArtifactStorage):
        self.artifacts = artifacts
        self.template = Environment(autoescape=True).from_string(TEMPLATE)

    def generate(self, run: RunSnapshot) -> dict[str, str]:
        revision = run.stages[-1].id if run.stages else "initial"
        paths = {"json": f"runs/{run.id}/reports/{revision}/report.json",
                 "html": f"runs/{run.id}/reports/{revision}/report.html"}
        snapshot = run.model_copy(deep=True)
        snapshot.reports = paths
        data = snapshot.model_dump(mode="json")
        proposals = {item.track_id: item for item in run.decision.plan.decisions} if run.decision and run.decision.plan else {}
        cited_ids = {key for item in proposals.values() for key in item.evidence_ids}
        cited = [item for item in run.evidence.provided if item.id in cited_ids] if run.evidence else []
        html = self.template.render(
            run=snapshot, proposals=proposals, cited=cited,
            status={"COMPLETED": "출력 완료", "FAILED": "처리 실패",
                    "REVIEW_READY": "처리 검토", "REVIEW_REQUIRED": "사용자 검토 필요"}.get(run.status, "처리 중"),
            purpose=PURPOSES.get(run.scenario.use_purpose, ("미확인", ""))[0],
            audience=AUDIENCES.get(run.scenario.audience_scope, ("미확인", ""))[0],
            relationships=RELATIONSHIPS, consents=CONSENTS,
            requesters={"yes": "열람 요청 당사자", "no": "다른 사람", "unknown": "알 수 없음"},
            actions={"KEEP": "원본 유지", "BLUR": "블러", "MOSAIC": "모자이크", "MASK": "단색 가림"})
        self.artifacts.put_bytes(paths["json"], json.dumps(data, ensure_ascii=False, indent=2).encode())
        self.artifacts.put_bytes(paths["html"], html.encode())
        return paths
