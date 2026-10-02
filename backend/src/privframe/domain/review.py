from .models import DecisionResult, EvidenceBundle, RunSnapshot, Treatment, VideoObservation


def review_reasons(observation: VideoObservation, evidence: EvidenceBundle,
                   result: DecisionResult) -> list[str]:
    reasons: list[str] = []
    if not observation.tracks:
        reasons.append("NO_FACES_DETECTED: 얼굴이 검출되지 않았습니다. 검출 결과 확인이 필요합니다.")
    if result.error or result.plan is None:
        reasons.append(f"DECISION_ERROR: {result.error or '구조화된 판단 결과가 없습니다.'}")
        return reasons
    if not evidence.provided:
        reasons.append("NO_EVIDENCE: LLM에 전달된 검색 근거가 없습니다.")
    plan = result.plan
    if plan.needs_review:
        reasons.append("LLM_REVIEW: LLM이 추가 검토를 요청했습니다.")
    if plan.missing_facts:
        reasons.append("MISSING_FACTS: " + "; ".join(plan.missing_facts))
    expected = {track.id for track in observation.tracks}
    ids = [decision.track_id for decision in plan.decisions]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        reasons.append("TRACK_MAP_INVALID: 누락·중복·존재하지 않는 트랙 ID가 있습니다.")
    provided_ids = {item.id for item in evidence.provided}
    for decision in plan.decisions:
        if not set(decision.evidence_ids).issubset(provided_ids):
            reasons.append("EVIDENCE_ID_INVALID: 실제 LLM 입력에 없는 근거를 인용했습니다.")
        if not decision.evidence_ids:
            reasons.append(f"UNCITED_DECISION: {decision.track_id}의 근거가 없습니다.")
    return list(dict.fromkeys(reasons))


def effective_treatments(run: RunSnapshot) -> dict[str, Treatment]:
    if run.observation is None:
        raise ValueError("Video analysis is required")
    expected = {track.id for track in run.observation.tracks}
    if not set(run.overrides).issubset(expected):
        raise ValueError("Override contains an unknown track")
    if not expected:
        if not run.no_faces_confirmed:
            raise ValueError("얼굴 0개 결과를 확인하고 원본 실험 출력을 명시적으로 선택하세요.")
        return {}
    if run.review_reasons:
        if set(run.overrides) != expected:
            raise ValueError("검토 필요 실행은 모든 트랙의 처리를 직접 지정해야 합니다.")
        return dict(run.overrides)
    if run.decision is None or run.decision.plan is None:
        raise ValueError("Decision is required")
    result = {entry.track_id: entry.treatment for entry in run.decision.plan.decisions}
    result.update(run.overrides)
    if set(result) != expected:
        raise ValueError("Final treatment map must include every detected track")
    return result
