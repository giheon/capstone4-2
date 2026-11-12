---
status: accepted
date: 2026-11-06
---
# 화면은 서비스·실험실 두 영역이며, 실험실은 정규화한 실행 이벤트(SSE)만으로 그린다. M2는 같은 계약의 모의 백엔드로 구현한다

실험실의 토폴로지·에이전트 메모리·태스크 흐름은 LangGraph 스트림(tasks · checkpoints · updates · messages · custom)을 그대로 받지 않고, 백엔드가 정규화한 이벤트(`node.start/end`, `edge`, `llm`, `tool.call/result`, `message`, `state`, `interrupt/resume`, `checkpoint`, `budget`, `validator`, `activity`)를 순번과 함께 SSE로 받는다. 화면은 이 로그를 접어(fold) 상태를 만들기 때문에 실시간 보기와 재생이 같은 코드이고, LangGraph 버전이 바뀌어도 어댑터만 고치면 된다. 백엔드 구현(M4) 전에 화면을 실제 웹으로 검수하기 위해, 같은 REST·SSE 계약을 Vite 플러그인 모의 백엔드가 제공한다.

## Considered Options
- LangSmith/Studio 화면을 그대로 사용: 케이스·요건·사람 대기 같은 도메인 개념이 없고 시연·서비스와 연결되지 않는다.
- LangGraph 스트림을 웹에서 직접 해석: 네임스페이스·내부 형식이 화면에 새어 들어와 결합도가 커진다.
- 정적 목업(캔버스): 실시간·재생을 보여 줄 수 없어 사용자가 반려했다.

## Consequences
- M4의 FastAPI는 `frontend/src/api/contract.ts`와 같은 모양의 REST·SSE를 구현하고, 그래프 실행을 이벤트 로그로 바꾸는 어댑터를 둔다(가정 U11).
- 모의 백엔드 코드(`frontend/mock/`)는 M4 이후에도 오프라인 시연·화면 테스트용으로 남는다.
