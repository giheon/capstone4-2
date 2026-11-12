---
status: accepted
date: 2026-11-05
---
# core / harness / service 3층으로 나누고 core는 나머지를 import하지 않는다

검증된 핵심을 실험 환경과 분리해 일반 사용자 서비스로 옮길 수 있어야 한다. 도메인·그래프·에이전트·도구는 `core`에 두고, 모델·프롬프트·도구 구현은 프로필(YAML)과 LangGraph `Runtime[Context]`로 주입한다. `harness`(CLI·데이터셋·평가기·LangSmith)와 `service`(FastAPI·Celery·React)는 core를 호출만 한다. 실행 골격은 기존 FastAPI + Celery를 유지한다. LangGraph Agent Server의 셀프호스트는 Enterprise·Elastic-2.0 조건이어서 채택하지 않되, thread run·command.resume·durability 같은 계약은 미러링해 나중에 옮길 수 있게 한다.

## Consequences
- 모델 선택이 상태(state)에 들어가지 않으므로 같은 체크포인트에서 "다른 모델"로 재실행하는 실험이 가능하다.
- 하네스와 서비스가 같은 core를 쓰므로 하네스에서 통과한 프로필을 그대로 서비스에 승격할 수 있다.
