import json

from jinja2 import Environment

from privframe.domain.models import RunSnapshot
from privframe.domain.ports import ArtifactStorage


TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>PrivFrame 실행 보고서 · {{ run.id }}</title>
<style>body{font:15px/1.7 system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 24px;color:#18312e}
h1{font-size:30px}h2{margin-top:32px;font-size:21px}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:10px;border-bottom:1px solid #d5dfdc;text-align:left;vertical-align:top}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f4;padding:16px;font-size:12px}
.badge{display:inline-block;padding:5px 12px;background:#e8eee9;border-radius:20px}.warning{background:#fff4d8;padding:16px}
.evidence{padding:14px 0;border-bottom:1px solid #d5dfdc}.evidence p{white-space:pre-wrap}
@media print{body{margin:0;max-width:none}button{display:none}pre{break-inside:auto}h2{break-after:avoid}}
</style></head><body>
<button onclick="window.print()">인쇄 / PDF 저장</button>
<h1>PrivFrame 실행 보고서</h1>
<p><span class="badge">{{ run.status }}</span> <span class="badge">{{ run.config.mode }}</span>
 · 실행 {{ run.id }} · {{ run.created_at }}</p>
{% if run.config.mode == 'fixture' %}<p class="warning">테스트용 fixture 실행입니다. 실제 법률 판단이나 OpenAI API 검증 결과가 아닙니다.</p>{% endif %}
{% if run.manual_output %}<p class="warning">사용자가 처리를 지정한 실험 출력입니다. 원래 검토 사유는 아래에 유지됩니다.</p>{% endif %}
{% if run.reused_stages %}<p>분석과 판단은 이전 실행 {{ run.parent_id }}의 저장된 결과를 재사용합니다. 수정한 처리 설정과 영상 출력은 이 실행에 별도로 기록합니다.</p>{% endif %}
{% if run.error %}<p class="warning">실패: {{ run.error }}</p>{% endif %}
<h2>사용자 상황과 입력</h2><p>{{ run.scenario.context }}</p><pre>{{ scenario_json }}</pre>
<h2>분석과 처리 결과</h2>
{% if run.observation %}<p>{{ run.observation.width }} × {{ run.observation.height }} · {{ run.observation.fps }} fps ·
{{ run.observation.duration }}초 · 검출 트랙 {{ run.observation.tracks|length }}개</p>{% endif %}
{% if run.no_faces_confirmed %}<p>얼굴 0개 검출 결과를 사용자가 확인하고 원본 실험 출력을 선택했습니다.</p>{% endif %}
{% if run.review_reasons %}<ul>{% for reason in run.review_reasons %}<li>{{ reason }}</li>{% endfor %}</ul>{% endif %}
{% if run.decision and run.decision.plan %}<p>{{ run.decision.plan.summary }}</p>{% endif %}
<table><thead><tr><th>트랙 / 구간</th><th>LLM 제안</th><th>사용자 수정</th><th>실제 적용</th><th>이유 / 근거 ID</th></tr></thead><tbody>
{% if run.observation %}{% for track in run.observation.tracks %}
{% set proposal = proposals.get(track.id) %}
<tr><td>{{ track.id }}<br>{{ track.start }} ~ {{ track.end }}초<br>{{ run.scenario.track_labels.get(track.id,'unknown') }}</td>
<td>{{ proposal.treatment if proposal else '없음' }}</td><td>{{ run.overrides.get(track.id,'없음') }}</td>
<td>{{ run.effective_treatments.get(track.id,'미출력') }}</td><td>{% if proposal %}{{ proposal.reason }}<br>{{ proposal.evidence_ids|join(', ') }}{% endif %}</td></tr>
{% endfor %}{% endif %}</tbody></table>
<h2>실제 LLM 입력 근거</h2>
{% if run.evidence %}{% for item in run.evidence.provided %}<div class="evidence"><strong>{{ item.id }} · {{ item.metadata.title }}</strong>
<div>{{ item.article or '' }} {% if item.page %}{{ item.page }}쪽{% endif %} · 문서 버전 {{ item.metadata.version or item.metadata.edition or item.content_hash[:12] }}
{% if item.metadata.effective_from %} · 시행 {{ item.metadata.effective_from }}{% endif %}</div>
{% if item.metadata.source_url %}<a href="{{ item.metadata.source_url }}" rel="noreferrer">공식/원문 출처</a>{% endif %}
<p>{{ item.text }}</p></div>{% endfor %}{% endif %}
<h2>검색 후보와 인용 연결</h2><pre>{{ evidence_json }}</pre>
<h2>설정·모델·실제 렌더 파라미터</h2><pre>{{ config_json }}</pre><pre>{{ render_json }}</pre>
<h2>실행 단계와 실패 이력</h2><table><tr><th>단계 / 시도</th><th>상태</th><th>시간(초)</th><th>오류</th></tr>
{% for item in run.stages %}<tr><td>{{ item.stage }}<br>{{ item.id }}</td><td>{{ item.status }}</td>
<td>{{ item.elapsed_seconds }}</td><td>{{ item.error or '' }}</td></tr>{% endfor %}</table>
<h2>LLM 호출 기록과 사용량</h2><pre>{{ decision_json }}</pre>
<p>이 보고서는 저장된 실행 데이터로 생성되었습니다. 보고서 재생성 시 검색·모델 판단을 다시 수행하지 않습니다.</p>
</body></html>"""


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
        def pretty(value):
            return json.dumps(value, ensure_ascii=False, indent=2)
        proposals = {item.track_id: item for item in run.decision.plan.decisions} if (
            run.decision and run.decision.plan) else {}
        html = self.template.render(run=snapshot, proposals=proposals,
                                    scenario_json=pretty(data["scenario"]), evidence_json=pretty(data["evidence"]),
                                    config_json=pretty(data["config"]), render_json=pretty(data["render"]),
                                    decision_json=pretty(data["decision"]))
        self.artifacts.put_bytes(paths["json"], pretty(data).encode())
        self.artifacts.put_bytes(paths["html"], html.encode())
        return paths
