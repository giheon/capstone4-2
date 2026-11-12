# R6 — 한국 법률 코퍼스·접근 경로·법적 프레임·테스트 설계

> **정정 (2026-11-11)**: 아래 본문의 2020다253423 요약("blur alone does not de-identify …"와 시나리오 5 · 9의 활용)은 판결의 결론과 맞지 않는다. 대법원은 위법성 조각 여지를 들어 원심을 파기환송했고, 인용된 문장은 얼굴 공개로 인한 추가 피해가 크지 않다는 이유였다. 정정된 정리는 [docs/design/12-corpus-plan-draft.md](../design/12-corpus-plan-draft.md) §A. 본문은 조사 당시 기록으로 남긴다.


- 조사일: 2026-11-05 · 방식: 백그라운드 리서치 에이전트가 1차 출처(공식 문서·소스·모델 카드·논문·가격 페이지)만 인용해 작성한 보고서 원문 · 반영 위치: 설계 §9, §10.6, §11
- 상태: 설계·PRD v2 승인(2026-11-05) 시점의 스냅샷. 수치·가격·버전은 조사일 기준이며, 재검증 없이는 갱신되지 않는다. `UNVERIFIED` 표시는 1차 출처로 확인하지 못한 항목이다.

<details><summary>리서치 브리프(에이전트에 전달한 요청 전문)</summary>

```text
You are a research agent. Today is 2026-11-05. Do NOT write any files; return your findings in your final message as Markdown with tables and a URL for every claim. Use WebSearch (mode "extended" when needed) and WebFetch on primary sources only: law.go.kr (국가법령정보센터, incl. Open API docs at open.law.go.kr), pipc.go.kr (개인정보보호위원회), privacy.go.kr (개인정보 포털), kopico.go.kr (분쟁조정위원회), glaw.scourt.go.kr / 대법원 종합법률정보, AI Hub (aihub.or.kr), Hugging Face dataset cards, arXiv papers. Korean sources are expected; quote article numbers exactly. Mark anything unverifiable as UNVERIFIED.

## Project context
PrivFrame (Korean capstone): LangGraph multi-agent system that de-identifies personal information in user-uploaded videos (faces, full bodies, license plates, name tags, on-screen text) and grounds every redaction decision in Korean privacy law via RAG with an explainable report. The professor demanded: (1) a firm definition of what counts as 개인정보 in video for this system, (2) retrieval of statutes + precedents + similar cases (not just statutes), (3) retrieved evidence must actually change what/where/how we redact, (4) demo with test cases on 2026-11-20. Current corpus: only 개인정보 보호법 (law.go.kr HTML) and two PIPC guideline PDFs.

## Questions (Track R6: Korean legal corpus, access, structure, framework, test-case design)
1. Data access: 국가법령정보센터 Open API (open.law.go.kr / law.go.kr/DRF/lawService.do & lawSearch.do): endpoints for 법령 본문(조문 단위), 시행령/시행규칙, 판례, 헌재결정례, 법령해석례, 행정규칙; auth (OC parameter / 신청 절차), output formats (XML/JSON/HTML), rate limits, terms of use; 대법원 종합법률정보 / 판례 검색 access and scraping policy; PIPC: 결정문·의결서 공개 위치, 분쟁조정 사례집 (KOPICO), 개인정보 보호법 표준 해석/해설서, 표준 개인정보 보호지침, 영상정보처리기기 관련 안내서 (고정형 CCTV 가이드라인, "이동형 영상정보처리기기 개인영상정보 보호·활용 안내서" — latest edition/date), 가명정보 처리 가이드라인 (latest edition/date, incl. any 2026 edition on 영상/비정형 데이터), AI 관련 가이드라인 (e.g., "AI 프라이버시 리스크 관리 모델", "비정형데이터 가명처리 기준"). Datasets: AI Hub 법률 데이터 (판례, 법률 QA, 약관), LBox Open (precedent corpus, casename classification, summarization), KLAID, KBL benchmark (Korean Benchmark for Legal language understanding) incl. its statute/precedent corpus, other HF Korean legal datasets. For each: URL, size, license, access method.
2. Document structure for chunking/citation: 법령 (편·장·절·조·항·호·목, 부칙, 시행일, 개정 이력, 법령ID/조문ID in the API), 판례 (사건번호, 선고일, 법원, 판시사항, 판결요지, 참조조문, 참조판례, 전문/이유), PIPC 결정문/의결서 (의결번호, 안건명, 사건개요, 관련 법령, 판단, 결정), 가이드라인 (목차/섹션/Q&A/사례). Give the canonical Korean citation forms (e.g., "개인정보 보호법 제25조의2 제1항 제2호", "대법원 2006. 10. 13. 선고 2004다16280 판결", "개인정보보호위원회 2023. x. x. 의결 제2023-xxx-xxx호").
3. Legal framework (with exact 조문 and current 시행일 as of Nov 2026, noting 2023–2026 amendments): 개인정보 보호법 제2조(개인정보·가명처리·영상정보처리기기 정의, 결합 용이성 기준), 제3조, 제15조, 제17조, 제22조, 제22조의2(아동), 제23조(민감정보), 제25조(고정형 영상정보처리기기), 제25조의2(이동형 영상정보처리기기), 제26조(위탁), 제28조의2~7(가명정보), 제28조의8(국외이전), 제58조(적용 제외) and 제58조의2(익명정보), 시행령 relevant 조문 (e.g., 제3조, 제22조, 제24조~27조, 제29조의13 등 확인), 표준 개인정보 보호지침 relevant articles; 초상권 case law (민법 제750·751조; 대법원 2006. 10. 13. 선고 2004다16280 판결; 대법원 2013다? on 공개장소 촬영; 2021/2023 recent 초상권/유튜브 판결), 언론·보도 예외, 사생활 비밀 (헌법 제17조), 정보통신망법 items, 아동·청소년 관련 (아동복지법 등) if relevant to 영상. Summarize what counts as 개인정보 in video: 얼굴, 전신·복장·소지품 결합(간접 식별), 차량번호, 명찰/이름, 음성; and the distinction 가명처리 vs 익명처리 for video (PIPC 가명정보 가이드라인 on 영상·비정형 data: 얼굴 블러 기준 등). Cite each.
4. Test-case design and counterfactual corpus: list 10–15 representative scenario templates (e.g., 거리 브이로그 유튜브 업로드, 학교 행사 영상 학부모 공유, 병원 로비 CCTV 제공, 집회 현장 뉴스 보도, 사내 교육용 영상 공개, 블랙박스 사고 영상 보험사 제출, 아동 포함 영상 SNS 공개, 명찰 착용 직원 등) and, for each, which 조문/판례/가이드라인 should drive the decision and what the expected redaction difference is. Suggest how a "counterfactual corpus" (swap guideline version, remove 제25조의2, insert a stricter fictional rule) could test whether retrieval actually changes decisions.
5. Data-protection constraints for the prototype itself: sending frames/descriptions of people to foreign LLM APIs — applicability of 제26조(처리위탁) and 제28조의8(국외이전), consent/notice requirements, and the practical mitigation for a research prototype (consented/synthetic test videos, zero-data-retention, minimization). Cite 조문 and PIPC guidance.
Keep under ~4000 words; prefer tables.
```

</details>

---

## PrivFrame R6 — Korean legal corpus, access, structure, framework, test design

Scope notes: all facts below were checked against the cited page/PDF on 2026-11-05; items I could not confirm from a primary source are marked **UNVERIFIED**. `law.go.kr` article bodies are JS-rendered (only headers were fetchable), `glaw.scourt.go.kr` did not resolve from this environment, and my web-search budget ran out near the end, so the 표준지침 고시 번호 and a few verbatim statute texts are flagged rather than quoted. Primary PDFs I pulled are on disk for reuse: 고정형 안내서 2024.12 (`/Users/limgiheon/.claude/projects/-Users-limgiheon-Desktop-----capstone4-2/2e0551b7-db18-4479-8dbd-202fb69bff44/tool-results/webfetch-1793863342033-rubuiy.pdf`), 가명정보 처리 가이드라인 2024.2 (`.../tool-results/webfetch-1793863476348-6unli2.pdf`), and 판결문 PDFs for 2020다227455 (`.../webfetch-1793863010430-p2srqn.pdf`), 2020다253423 (`.../webfetch-1793863010411-7bxuu0.pdf`), 2021다219116 (`.../webfetch-1793863461682-gjnmbq.pdf`).

### 1. Data access

**1a. 국가법령정보 공동활용 Open API (open.law.go.kr → www.law.go.kr/DRF)**

| Item | Verified detail | Source |
|---|---|---|
| Auth | `OC` = "신청한 API 인증값"; calls from an unregistered host fail with `사용자 정보 검증에 실패하였습니다 / OPEN API 호출 시 사용자 검증을 위하여 정확한 서버장비의 IP주소 및 도메인주소를 등록해 주세요` — i.e., key **and** server IP/domain must be registered. Third-party wrappers say OC = e-mail ID before `@` (UNVERIFIED). Apply via 사용자 가입 → OPEN API 신청; contact 02-2109-6446. | [guide index](https://open.law.go.kr/LSO/openApi/guideList.do), [신청](https://open.law.go.kr/LSO/openApi/cuAskList.do), [error response](https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=XML&query=개인정보보호법) |
| Formats | `type=HTML|XML|JSON` on every endpoint | [법령 목록 guide](https://open.law.go.kr/LSO/openApi/guideResult.do) |
| 법령 목록 | `lawSearch.do?target=law` params: `search`(1 법령명/2 본문), `query`, `display`(≤100), `page`, `sort`, `efYd`, `ancYd`, `ancNo`, `rrClsCd`, `nb`, `org`, `knd`, `lsChapNo`, `gana`. Fields: 법령일련번호, 현행연혁코드, 법령명한글, 법령약칭명, 법령ID, 공포일자, 공포번호, 제개정구분명, 소관부처, 법령구분명, 시행일자, 법령상세링크 | same |
| 법령 조문 단위 | `lawService.do?target=lawjosub` with `ID`(법령ID) or `MST`(법령일련번호 = lsi_seq), `JO` 6-digit (제2조→`000200`, 제10조의2→`001002`), optional `HANG`, `HO`, `MOK`(URL-encoded 가/나/다). Response carries 법령키/ID, 공포일자·번호, 시행일자, 편장절관 코드, 조문번호·제목·시행일자·이동전후·변경여부·내용, 항/호/목 번호·내용 | [lawjosub guide](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lawJosubInfoGuide) |
| 법령 본문 전체 / 시행령·시행규칙 | Same `lawService.do?target=law&MST=…` (same ID scheme; 시행령·규칙 are just other 법령ID). The 법령 본문 guide page name is JS-routed and I could not open it — param list beyond ID/MST/JO is **UNVERIFIED** | [guide index](https://open.law.go.kr/LSO/openApi/guideList.do) |
| 연혁 | `lawService.do?target=lsHistory&MST=…&type=HTML` (`chrClsCd` 010202 한글) | [lsHistory guide](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsHstInfoGuide) |
| 판례 목록 | `lawSearch.do?target=prec`: `search`(1 사건명/2 본문/3 참조판례), `query`, `org`(대법원 400201/하위법원 400202), `curt`, `JO`(참조조문 법령명), `prncYd`(YYYYMMDD~YYYYMMDD), `nb`(사건번호, comma list), `datSrcNm`. Fields: 판례일련번호, 사건명, 사건번호, 선고일자, 법원명, 사건종류명, 판결유형, 선고, 데이터출처명, 판례상세링크 | [prec list guide](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=precListGuide) |
| 판례 본문 | `lawService.do?target=prec&ID=…`: 판례정보일련번호, 사건명, 사건번호, 선고일자, 선고, 법원명, 법원종류코드, 사건종류명/코드, 판결유형, 판시사항, 판결요지, 참조조문, 참조판례, 판례내용 | [prec info guide](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=precInfoGuide) |
| 헌재결정례 | `target=detc&ID=…`: 종국일자, 사건번호, 사건명, 재판부구분코드, 판시사항, 결정요지, 전문, 참조조문, 참조판례, 심판대상조문 | [detc guide](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=detcInfoGuide) |
| 법령해석례 | `target=expc&ID=…`: 안건명, 안건번호, 해석일자, 해석기관, 질의기관, 질의요지, 회답, 이유 | [expc guide](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=expcInfoGuide) |
| 행정규칙 (표준지침 등) | `target=admrul&ID|LID|LM=…`: 행정규칙명, 종류, 발령일자, 발령번호, 현행여부, 시행일자, 조문내용, 부칙, 별표, 첨부파일링크 | [admrul guide](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=admrulInfoGuide) |
| 개인정보보호위원회 결정문 | `target=ppc&ID=…`: 기관명, 결정, 회의종류, 안건번호, 안건명, 신청인, 의결연월일, 주문, 이유, 배경, 주요내용, 의결일자, 결정요지, 별지 | [ppc guide](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=ppcInfoGuide) |
| Terms / rate limit | Guide pages state no quota; data.go.kr mirror lists "이용허락범위 제한 없음, 무료". **No published rate limit found (UNVERIFIED)** | [data.go.kr](https://www.data.go.kr/data/15057041/openapi.do) |

**1b. Courts and PIPC**

| Source | What / where | Access & policy | Source |
|---|---|---|---|
| 대법원 종합법률정보 | `glaw.scourt.go.kr` — DNS failed from here; no 약관/robots verified (**UNVERIFIED**). `www.scourt.go.kr/robots.txt` disallows AhrefsBot/SemrushBot entirely and ~45 paths for `*`; `/sjudge/` (판결문 PDF) is not disallowed. Full-text PDFs of 중요판결 are served under `/sjudge/…pdf`. Practical route for the corpus: law.go.kr `target=prec` (same 대법원 판례 data, `datSrcNm=대법원`) | [robots](https://www.scourt.go.kr/robots.txt), [2020다227455 PDF](https://scourt.go.kr/sjudge/1620265689791_104809.pdf) |
| PIPC 의결서 | Board 심의·의결 > 위원회 결정문: columns 번호/회의구분(위원회·1소위·2소위)/제목/의결일/미리보기(HTML)/조회수; ~281 pages; detail `agenda.do?op=view&mCode=E030010000&idxId=2026-0800`; attachments 심의의결서 PDF+HWP. Policy: 의결서 송부 후 홈페이지 공개; "2025년 의결 안건부터 피심인명 원칙적으로 공개"; 위원회 월 2회 | [board](https://www.pipc.go.kr/np/default/agenda.do?mCode=E030010000), [procedure](https://pipc.go.kr/np/default/page.do?mCode=E010020000), [example](https://www.pipc.go.kr/np/default/agenda.do?op=view&mCode=E030010000&idxId=2026-0800) |
| PIPC 결정문·사례집 | 법령해석 심의·의결 결정문 모음집 2021–2025 (nttId 8004/9071/10033/11095/12013), 개인정보보호 판례집 (7940), 2019·2020 결정문 모음집 | [BS231](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS231&mCode=D070010010) |
| 분쟁조정 사례집 (KOPICO) | PIPC board BS232 lists 2016–2025 사례집 (2025년: nttId 12104, posted 2026-05-26, PDF). KOPICO site menus: 분쟁조정 사례 `/mng/exammngMain.do` (JS list), 자료실 `/home/hbbs/homeBoardMain.do?bbs_id=BBS_00000003` | [BS232](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS232&mCode=D070010020), [2025 post](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS232&mCode=D070010020&nttId=12104), [kopico](https://www.kopico.go.kr/main/main.do) |

**1c. PIPC guidelines — current editions (board "안내서", 129 posts; master list post nttId 12605, '26.10.30 기준)**

| Guide | Edition | nttId / URL |
|---|---|---|
| 가명정보 처리 가이드라인 | **2026.3. 개정** (current; 2024.2. now "과거 안내서"). 2026.3 TOC: 본권 제3장 "비정형데이터 가명처리 기준 (57p)", 별권 PART 6 위험도 판단 예시 (저/중/고) | [11931](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=11931), [2024.2 = 9900](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS217&mCode=G010030000&searchCnd=1&searchWrd=가명정보), [2026.3 TOC mirror](https://www.cela.kr/4/?bmode=view&idx=170625969) |
| 이동형 영상정보처리기기를 위한 개인영상정보 보호·활용 안내서 | 2024.9. (posted 2024-10-14; no newer edition found) | [10679](https://pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=10679), [보도자료](https://pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS074&mCode=C020010000&nttId=10678) |
| 고정형 영상정보처리기기 설치·운영 안내서 (공공·민간 통합본) | 2024.12. (51 pp; 관계법령 "법 제25조, 시행령 제22조~26조 및 제27조의3"; 공공누리 1유형 on data.go.kr) | [10901](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=10901), [PDF](https://www.goe.go.kr/resource/goe/na/bbs_2675/2025/11/dc125513-0545-45c2-98b1-c4a62ead810a.pdf), [data.go.kr](https://www.data.go.kr/data/15142230/fileData.do) |
| 생성형 AI 개발·활용을 위한 개인정보 처리 안내서 | 2025.8. | [11439](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=11439) |
| AI 프라이버시 리스크 관리 모델 | posted 2025-02-21 (model dated 2024.12 per press) | [11014](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=11014) |
| 인공지능 개발·서비스를 위한 공개된 개인정보 처리 안내서 | 2024.7. | [10375](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS217&mCode=G010030000&pageIndex=5) |
| 개인정보 처리 통합 안내서 (동의·위수탁 통합) | 2025.7. | [11352](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=11352) |
| 생체정보 보호 안내서 / 아동·청소년 개인정보 보호 안내서 / 합성데이터 생성·활용 안내서 | 2024.12. | [10900](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=10900), [10896 / 10915](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS217&mCode=G010030000&pageIndex=4) |
| 개인정보의 안전성 확보조치 기준 안내서 | 2026.11. (posted 2026-11-05) | [12611](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS217&mCode=G010030000) |
| 표준 개인정보 보호지침 (고시) | Current 고시 번호/시행일 **UNVERIFIED** (law.go.kr page JS-only). Article map used by PIPC 2024.12: 제2조제9호 개인영상정보, 제10호 운영자, 제11호 공개된 장소; 제36조 방침, 제37조 관리책임자, 제38조 의견수렴, 제39조 안내판, 제40조 목적외, 제41조 보관·파기(제2항 30일), 제42조 파기기록, 제43조 위탁, 제44~46조 열람·제3자 보호조치, 제47조 소상공인, 제48조 공공기관 등록 | [고정형 안내서 PDF](https://www.goe.go.kr/resource/goe/na/bbs_2675/2025/11/dc125513-0545-45c2-98b1-c4a62ead810a.pdf) |

**1d. Datasets**

| Dataset | Size | License | Access | Source |
|---|---|---|---|---|
| LBox Open | 301,328 rows: precedent_corpus 150k, casename 11.3k(+31.3k), statute 3.3k(+17.8k), ljp_criminal 11.4k, ljp_civil 5.08k, summarization 20k(+51.1k) | CC BY-NC 4.0 | `load_dataset("lbox/lbox_open", …)` | [HF](https://huggingface.co/datasets/lbox/lbox_open), [arXiv 2206.05224](https://arxiv.org/abs/2206.05224?context=cs) |
| KBL | 67 subsets ≈3.4k items (bar exam 2,510 + knowledge 510 + reasoning 288); RAG eval uses a statute+precedent corpus (size not stated) | cc-by-nc-4.0 | HF | [HF](https://huggingface.co/datasets/lbox/kbl), [arXiv 2410.08731](https://arxiv.org/abs/2410.08731) |
| KCL | 452 bar-exam items with 1,653 supporting precedents (RAG-oriented) | CC BY-NC 4.0 | HF | [HF](https://huggingface.co/datasets/lbox/kcl) |
| KLAID | 161,192 fact→statute rows, 177 labels | CC-BY-NC-ND-4.0 | HF | [HF](https://huggingface.co/datasets/lawcompany/KLAID) |
| korean_law_open_data_precedents | 171,599 판례 (대법원 89,687 …), fields = law.go.kr API fields incl. 판시사항/판결요지/참조조문/참조판례/전문; rebuilt Oct 2026 | openrail | HF parquet | [HF](https://huggingface.co/datasets/joonhok-exo-ai/korean_law_open_data_precedents) |
| MiniLex (wellsa-ai) | law-kr 5,589 법령 (daily cron from 법제처 DRF) + sibling sets precedent-kr, regulate-kr, interpretation-kr, constitution-kr … (~397K docs total) | MIT | HF | [HF](https://huggingface.co/datasets/wellsa-ai/law-kr) |
| AI Hub 법률 | 580: 판결문 1만 + 약관 1만 (2021); 71723: 상황별 판례 66,511 + QA 20,160; 71722: 법률 지식 SPO 39,035; **71968: 판결서 익명처리 데이터 21,000건 (2025)** — useful as a de-identification-label reference | NIA policy: 영리·비영리 R&D 허용, "인공지능 학습모델의 학습용으로만", 재배포 금지, 출처표시; "내국인만 신청" | approval → API/분할압축 | [580](https://www.aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100&dataSetSn=580), [71968](https://www.aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100&dataSetSn=71968), [71723](https://www.aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100&dataSetSn=71723), [policy](https://www.aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105) |

### 2. Document structure and citation forms

| Doc type | Structural units (chunk keys) | Canonical citation |
|---|---|---|
| 법령 | 법령ID/법령일련번호(MST) → 편·장·절·관 코드 → 조문번호(+가지번호) → 항 → 호 → 목; per-article 시행일자, 이동전후, 변경여부; 부칙; 연혁 via `lsHistory`. Header string: `개인정보 보호법 [시행 2026. 9. 11.] [법률 제21445호, 2026. 3. 10., 일부개정]` | `「개인정보 보호법」 제25조의2제1항제2호` (PIPC style: `법 제25조제1항제3･4･5호`, `시행령 제22조제1항`, `표준지침 제41조제2항`) — [lawjosub](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lawJosubInfoGuide), [header](https://www.law.go.kr/LSW/lsInfoP.do?lsNm=개인정보보호법&efYd=&chrClsCd=010202) |
| 판례 | 사건번호, 사건명, 선고일자, 법원명, 사건종류, 판결유형, 선고(주문), 판시사항, 판결요지, 참조조문, 참조판례, 판례내용(전문: 주문/이유 numbered 1.,2.,가.,나.) | `대법원 2021. 4. 29. 선고 2020다227455 판결` — exactly as the Court cites: "대법원 2006. 10. 13. 선고 2004다16280 판결, 대법원 2021. 4. 29. 선고 2020다227455 판결 등 참조" — [2020다253423 PDF](https://www.scourt.go.kr/sjudge/1682031331262_075531.pdf) |
| 헌재결정례 | 사건번호, 종국일자, 재판부, 판시사항, 결정요지, 심판대상조문, 전문 | `헌법재판소 2017. 12. 28. 선고 2015헌마994 결정` (PIPC cites "(2015헌마994, '17.12.28.)") — [고정형 안내서](https://www.goe.go.kr/resource/goe/na/bbs_2675/2025/11/dc125513-0545-45c2-98b1-c4a62ead810a.pdf) |
| PIPC 의결서 | 회의구분, 의안번호 (e.g., `제2026-220-441~443호`), 안건명 (with 사건번호 `2026-조이-0065-01`), 의결일, 피심인, 주문, 이유, 배경/주요내용, 결정요지, 별지 | `개인정보보호위원회 2026. 10. 14. 의결 제2026-220-441호` (format = 연도-회차-안건) — [example](https://www.pipc.go.kr/np/default/agenda.do?op=view&mCode=E030010000&idxId=2026-0800), [API fields](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=ppcInfoGuide) |
| 법령해석례 | 안건번호, 해석일자, 질의요지, 회답, 이유 | `법제처 YYYY. M. D. 회신 안건번호 …` — [expc](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=expcInfoGuide) |
| 분쟁조정 사례 | 침해유형 → 사건개요 / 조정의견 / 합의·조정 결과 (사건번호 형식 **UNVERIFIED**) | `개인정보 분쟁조정위원회, 2025년 개인정보 분쟁조정 사례집, 사례 n` — [post](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS232&mCode=D070010020&nttId=12104) |
| 안내서 | 장(Ⅰ~)/항목/Q&A/관련조항 박스/적용예시; PIPC prescribes its own citation: `출처 : 개인정보보호위원회 「고정형 영상정보처리기기 설치･운영 안내서(공공 및 민간분야 통합본)」 2024.12.` | [PDF p.3](https://www.goe.go.kr/resource/goe/na/bbs_2675/2025/11/dc125513-0545-45c2-98b1-c4a62ead810a.pdf) |

### 3. Legal framework (as of 2026-11-05; demo 2026-11-20)

Amendment timeline from law.go.kr: 법률 제16930호(2020.2.4, 시행 2020.8.5) → **제19234호(2023.3.14, 시행 2023.9.15; 제25조의2·제28조의8·제58조의2 introduced)** → 제20897호(2025.4.1, 시행 2025.10.2; content UNVERIFIED) → **제21445호(2026.3.10, 시행 2026.9.11; current)** → 제21910호(2026.9.8, **시행 2027.3.9**, AI 학습 특례 제28조의12~15 — not yet in force on demo day). 시행령 current: 대통령령 제36671호(2026.9.10, 시행 2026.9.11). Sources: [개정문 목록](https://www.law.go.kr/lsRvsDocListP.do?lsId=011357), [시행령 header](https://www.law.go.kr/LSW/lsInfoP.do?lsNm=개인정보보호법시행령&chrClsCd=010202), [AI 특례 summary (secondary)](https://datalaw.kr/guides/pipa-2026-amendment/). The 2026 amendment (제21445호) changed 제23조제2항 "유출등", 제30조의3, 제31조, 제34조, 제64조의2 과징금(전체 매출액 10%), 제75조 — not the video/가명/국외이전 articles ([신구조문 대조표, secondary](https://datalaw.kr/posts/pipa-2026-amendment-comparison/)).

| Provision | Content relevant to video (verbatim where a primary quote was obtained) | Source |
|---|---|---|
| 제2조제1호 | "살아있는 개인에 관한 정보로서 … 성명, 주민등록번호 및 영상 등을 통하여 개인을 알아볼 수 있는 정보"(가목); 나목 "해당 정보만으로는 특정 개인을 알아볼 수 없더라도 다른 정보와 쉽게 결합하여 알아볼 수 있는 정보. 이 경우 쉽게 결합할 수 있는지 여부는 다른 정보의 입수 가능성 등 개인을 알아보는 데 소요되는 시간, 비용, 기술 등을 합리적으로 고려하여야 한다"; 다목 가명정보 | [가명정보 가이드라인 2024.2 PDF p.9,15](https://data.gangwon.kr/images/pdf/%EA%B0%80%EB%AA%85%EC%A0%95%EB%B3%B4_%EC%B2%98%EB%A6%AC_%EA%B0%80%EC%9D%B4%EB%93%9C%EB%9D%BC%EC%9D%B8(2024_2_%EA%B0%9C%EC%A0%95).pdf) |
| 제2조제1호의2 가명처리 | "개인정보의 일부를 삭제하거나 일부 또는 전부를 대체하는 등의 방법으로 추가 정보가 없이는 특정 개인을 알아볼 수 없도록 처리하는 것" (tail UNVERIFIED) | same |
| 제2조제7호 고정형 / 제7호의2 이동형 | 고정형: "일정한 공간에 설치되어 지속적 또는 주기적으로 사람 또는 사물의 영상 등을 촬영하거나 이를 유･무선망을 통하여 전송하는 장치" (시행령 제3조제1항: CCTV, 네트워크 카메라); 이동형: 신체 착용·휴대 또는 이동 가능한 물체에 부착·거치 (시행령 제3조제2항) — dashcam filming outside = 이동형 | [고정형 안내서 p.6,9](https://www.goe.go.kr/resource/goe/na/bbs_2675/2025/11/dc125513-0545-45c2-98b1-c4a62ead810a.pdf) |
| 제3조 원칙 / 제15조·제17조 | 비공개 장소 영상 → 제25조 미적용, 제15조 적용 (동의 등); 제3자 제공 제17조제1항; 목적외 제18조제2항 (가명처리 시 제4호) | 고정형 안내서 p.8–9, 27–29 (same PDF) |
| 제22조의2 아동 | 만 14세 미만 법정대리인 동의 (verbatim UNVERIFIED); PIPC 아동·청소년 개인정보 보호 안내서 2024.12 | [10896](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS217&mCode=G010030000&pageIndex=4) |
| 제23조 민감정보 + 시행령 제18조제3호 | 생체인식정보 = "개인을 알아볼 목적으로 … 특징에 관한 정보로서 … 기술적 수단을 통해 생성한 정보" — raw face pixels ≠ 민감정보, face embeddings used for identification = 민감정보 (verbatim UNVERIFIED; see 생체정보 보호 안내서) | [10900](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=10900) |
| 제25조 고정형 | 제1항 각 호 (법령 허용, 범죄예방, 시설안전, 교통단속, 교통정보, 촬영 영상 저장 없는 일시 처리=제6호/시행령 제22조제1항); 제2항 사생활 침해 장소 금지; 제3항 의견수렴; 제4항 안내판(시행령 제24조); 제5항 임의조작·녹음 금지; 제7항 운영·관리 방침(시행령 제25조); 제8항 공공기관 위탁(시행령 제26조) | 고정형 안내서 pp.14–26 |
| 제25조의2 이동형 | Structure per PIPC: 공개된 장소에서 업무 목적 촬영은 (1) 제15조제1항 각 호 사유, (2) 촬영 사실 명확히 표시 + 거부 의사 없음 (정당한 이익·합리적 범위), (3) 대통령령 사유에만 허용; 사생활 침해 장소 금지; 불빛·소리·안내판 등 촬영 사실 표시 (시행령 제27조); 드론 등은 PIPC 인터넷 공지 가능 — **verbatim UNVERIFIED** | [보도자료](https://pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS074&mCode=C020010000&nttId=10678), [안내서 post](https://pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=10679) |
| 제26조 위탁 | 문서(목적 외 처리 금지, 보호조치, 재위탁 제한, 감독, 손해배상); 제2항 공개(시행령 제28조제2항·제3항); 제4항 교육·감독; 제5항 수탁자 범위 초과 이용·제공 금지; 2026 amendment applies CPO duties to 수탁자 via 제26조제8항 | 고정형 안내서 pp.34–35; [datalaw](https://datalaw.kr/guides/pipa-2026-amendment/) |
| 제28조의2~7 가명정보 | 제28조의2① "통계작성, 과학적 연구, 공익적 기록보존 등을 위하여 정보주체의 동의 없이 가명정보를 처리할 수 있다"; 제28조의3 결합 전문기관; 제28조의4 추가정보 분리·기록 3년; 제28조의5 재식별 금지·즉시 중지·회수·파기; 시행령 제29조의5 안전조치 | 가명정보 가이드라인 2024.2 PDF pp.9,15,77 |
| 제28조의8 국외이전 | 5 grounds: 별도 동의 / 법률·조약 / 계약 이행 위한 처리위탁·보관(처리방침 공개 또는 통지) / PIPC 인증 / 동등성 인정; 제28조의9 중지명령; 시행령 제29조의8·9·11·12; 「개인정보 국외 이전 운영 등에 관한 규정」 2023.10.16 | [PIPC 보도자료 2023.10.12](https://m.korea.kr/briefing/pressReleaseView.do?newsId=156594022) |
| 제58조·제58조의2 | 제58조제2항 (공개 장소 고정형 CCTV 영상 → 제15조·제22조·제22조의2·제27조①②·제34조·제37조 적용 제외; UNVERIFIED verbatim); 제58조의2 익명정보 "시간·비용·기술 등을 합리적으로 고려할 때 다른 정보를 사용하여도 더 이상 개인을 알아볼 수 없는 정보" → 법 적용 제외; 가이드라인: "불특정 제3자에게 제공하는 경우(공개 등) 익명정보로 처리하는 것을 원칙으로 함" | 가명정보 가이드라인 2024.2 PDF pp.9,20 |
| 헌법 제10조·제17조, 민법 제750·751조 | Court: "초상권은 헌법 제10조 제1문에 따라 헌법적으로도 보장 … 헌법 제10조는 헌법 제17조와 함께 사생활의 비밀과 자유를 보장"; 침해 = 불법행위 (민법 제750·751 위자료) | [2020다227455 PDF](https://scourt.go.kr/sjudge/1620265689791_104809.pdf) |

**Case law (verified from 판결문 PDFs)**

| Case | Holding to encode as a retrievable rule |
|---|---|
| 대법원 2006. 10. 13. 선고 2004다16280 판결 (cited) | 초상권 = "얼굴 그 밖에 사회통념상 특정인임을 식별할 수 있는 신체적 특징"; 공개된 장소 촬영·증거수집 목적만으로 정당화 안 됨 — [cited in 2020다227455](https://scourt.go.kr/sjudge/1620265689791_104809.pdf) |
| 대법원 2013. 6. 27. 선고 2012다31628 판결 (cited) | 위법성 조각: 공중의 정당한 관심사 + 공공의 이익 + 표현 내용·방법 상당성; 증명책임은 촬영·공표자 — same PDF |
| 대법원 2021. 4. 29. 선고 2020다227455 판결 | 이익형량 요소 (침해 측: 이익의 내용·중대성, 필요성·효과성, 보충성·긴급성, 방법의 상당성; 피해 측: 법익·피해 정도·보호가치); 폭행 증거 촬영 OK; 공적 논의에 나선 자의 묵시적 동의; 전송 범위 제한이 고려 요소 — [PDF](https://scourt.go.kr/sjudge/1620265689791_104809.pdf) |
| 대법원 2023. 4. 13. 선고 2020다253423 판결 | 언론보도 32초 얼굴 노출: 공적 인물·공적 관심사·스스로 관여 여부가 핵심; **"설사 … 얼굴을 식별할 수 없게 하였더라도 시청자들은 … 원고라는 사실을 충분히 알 수 있었다"** (자막 이름·다른 사진) → blur alone does not de-identify when captions/context identify — [PDF](https://www.scourt.go.kr/sjudge/1682031331262_075531.pdf) |
| 대법원 2021. 7. 21. 선고 2021다219116 판결 (citing 2013. 2. 14. 선고 2010다103185) | 촬영 동의 ≠ 공표 동의; 동의 범위 초과 공표는 별도 동의; 증명책임 촬영자·공표자 — [PDF](https://www.scourt.go.kr/sjudge/1627345568125_092608.pdf) |
| 헌재 2017. 12. 28. 2015헌마994 | 어린이집 CCTV 보호자 열람은 공익이 큼 — [고정형 안내서 p.38](https://www.goe.go.kr/resource/goe/na/bbs_2675/2025/11/dc125513-0545-45c2-98b1-c4a62ead810a.pdf) |

**What counts as 개인정보 in video (defensible definition for PrivFrame)** — 개인영상정보 = "개인의 초상, 행동 등과 관련된 영상으로서 해당 개인을 식별할 수 있는 정보" (표준지침 제2조제9호). PIPC's own 비정형 guidance: "눈·코·입을 알아볼 수 없는 거리에서 찍힌 CCTV 영상은 통상 개인식별 위험성이 낮지만, 흉터, 문신, 머리스타일 등 특이한 신체 특징이 있는 경우 개인식별 위험성이 높음"; object detectors miss faces at 90–98% accuracy; mosaic can be AI-restored; 자율주행 사례: "얼굴·차량번호판 영역을 사람과 컴퓨터가 식별 불가능한 수준으로 마스킹"; 다인승 사례: "탑승자가 누구인지는 판별 불가능하도록 블러링", "블러링 수준(1~10단계)별로 … 식별위험이 없으면서 AI 정밀도를 … 확보할 수 있는 블러링 수준을 결정"; "특이점이 있는 차량 이미지는 삭제" ([2024.2 PDF pp.40–45](https://data.gangwon.kr/images/pdf/%EA%B0%80%EB%AA%85%EC%A0%95%EB%B3%B4_%EC%B2%98%EB%A6%AC_%EA%B0%80%EC%9D%B4%EB%93%9C%EB%9D%BC%EC%9D%B8(2024_2_%EA%B0%9C%EC%A0%95).pdf)). So: 얼굴 (direct), 전신·복장·소지품·문신·걸음걸이 (제2조제1호 나목 간접 결합; context-dependent), 차량번호판 (indirect, strong), 명찰·자막 이름 (direct; 2020다253423), 음성 (voiceprint restorable; 2024.2 p.40). 가명처리 vs 익명처리 for video: blur/mask to "사람과 컴퓨터가 식별 불가능한 수준" + 특이정보 제거 + 메타데이터 삭제 = 가명정보 for 과학적 연구 (제28조의2); public release requires 익명정보 standard (제58조의2) per the guideline's 원칙.

### 4. Test-case templates and a counterfactual corpus

| # | Scenario | Driving sources | Expected redaction difference |
|---|---|---|---|
| 1 | 거리 브이로그 → 유튜브 공개 (업무 목적 이동형) | 제25조의2①2호 촬영 표시·거부권; 2004다16280; 2024.2 p.40 특이정보; 공개 = 익명 원칙 (p.20) | Blur all bystander faces + 문신/특이 복장 + plates; no exemption for "공개된 장소" |
| 2 | 사적 브이로그 (업무 아님) | 제58조/사적 목적 → 보호법 비적용이나 초상권 (민법 750) 적용 | Faces still blurred (초상권), but no 안내판/표시 findings |
| 3 | 학교 행사 영상 학부모 공유 (아동) | 제22조의2, 아동·청소년 안내서 2024.12, 제17조 | Children without 법정대리인 동의 blurred; teachers' 명찰 blurred; limited-audience sharing weighs lighter (2020다227455 전송 범위) |
| 4 | 병원 로비 CCTV → 보험사 제공 | 제25조①, 제18조②, 제58조②, 표준지침 제40·46조; 고정형 안내서 p.29,37 | Only requester's own image clear; 제3자 모자이크 "최소한으로 제공"; 민감정보 cues (환자 상태) masked |
| 5 | 집회 현장 뉴스 보도 | 2020다253423 (공적 관심사, 공적 인물), 2012다31628 | Public figures/speakers may stay clear; bystanders blurred; captions must not name blurred persons |
| 6 | 사내 교육용 영상 사외 공개 | 비공개 장소 → 제15조 동의; 명찰; 근참법 (고정형 안내서 p.8–9) | Employees without consent blurred incl. 명찰 text; internal-only version may keep faces |
| 7 | 블랙박스 사고 영상 보험사 제출 | 블랙박스 외부 = 이동형 (안내서 p.9); 2020다227455 증거보전 필요성·긴급성 | Keep accident parties' plates clear (증거), blur unrelated pedestrians/plates |
| 8 | 아동 포함 영상 SNS 공개 | 제22조의2; 익명 원칙 | Full-body blur for children (clothing/school uniform as 결합정보) |
| 9 | 명찰 착용 직원 매장 영상 | 제25조①, 명찰 = 직접 식별 (2020다253423 logic) | OCR-driven name-tag masking even when face blurred |
| 10 | 드론 항공촬영 공원 | 제25조의2③ 단서 (인터넷 공지), 시행령 제27조 | Distance-based: faces unresolvable → low risk; plates/문신 still masked |
| 11 | 자율주행·로봇 학습데이터 (가명처리) | 제28조의2, 2024.2 사례4·5, 2024.10 보도자료 (원본은 규제샌드박스) | Mask faces+plates "사람과 컴퓨터가 식별 불가능"; blur level chosen by utility test; delete 특이 차량 |
| 12 | 수사기관 CCTV 제공 | 제18조②7호(공공), 표준지침 제40조; 민간은 제18조②1~3호만 | Public body: provide with 기록; private: refuse/limit unless 제3호 |
| 13 | 음성 포함 인터뷰 공개 | 2024.2 p.40 voice restoration; 제25조⑤ 녹음 금지(고정형) | Voice transform + name bleep; 고정형 CCTV audio = violation flag |

Counterfactual corpus (tests "does retrieval change the decision"): (a) **version swap** — index 가명정보 가이드라인 2022.4 (no 비정형 chapter) vs 2024.2 vs 2026.3 and assert blur-level/특이정보 rules appear only with ≥2024.2 ([board history](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS217&mCode=G010030000&searchCnd=1&searchWrd=가명정보)); (b) **statute ablation** — remove 제25조의2 and 시행령 제27조 (pre-2023.9.15 world) → scenario 1/10 must fall back to 초상권 reasoning only, with no "촬영 사실 표시" finding; (c) **fictional stricter rule** — insert "제25조의3 (가상): 공개된 장소 촬영 영상은 전신을 식별 불가능하게 처리" and assert full-body masking in scenarios 1, 5; (d) **precedent flip** — replace 2020다253423 with a fictional affirmance → scenario 5 should blur the public figure; (e) **context identifier test** — same frame with/without caption naming the person (2020다253423) → caption masking must switch on. Score each as decision-diff rate vs. the unperturbed corpus; a retrieval-grounded system should show near-100% diffs on (b)–(e) and zero on irrelevant swaps.

### 5. Data-protection constraints for the prototype

| Issue | Rule | Mitigation |
|---|---|---|
| Sending frames to a foreign LLM API | Vendor = 수탁자 under 제26조 (문서·공개·교육·감독; 수탁자 범위 초과 금지) **and** 국외이전 under 제28조의8①3호 (계약 이행 위한 처리위탁·보관: 처리방침 공개 or 통지) — if the uploader is a 정보주체, 영상 속 제3자는 아니므로 그들에 대한 적법 근거가 별도로 필요 | [고정형 안내서 pp.34–35](https://www.goe.go.kr/resource/goe/na/bbs_2675/2025/11/dc125513-0545-45c2-98b1-c4a62ead810a.pdf), [국외이전 규정 보도자료](https://m.korea.kr/briefing/pressReleaseView.do?newsId=156594022) |
| Face embeddings | identification-purpose embeddings = 생체인식정보(민감정보, 제23조) → avoid persistent embeddings; detect-and-blur only | [생체정보 보호 안내서](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=10900) |
| Research framing | 가명처리 후 과학적 연구 (제28조의2) with 추가정보 분리·기록 (제28조의4, 시행령 제29조의5); PIPC AI guides: 외부 API 연동 시 접근통제·추가 안전조치 (생성형 AI 안내서 2025.8, summary via secondary) | [11439](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=11439), [AI 리스크 모델](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=G010030000&nttId=11014) |
| Practical | Use consented actors or synthetic video (PIPC 합성데이터 안내서 2024.12, nttId 10915); blur locally **before** any cloud call and send only bounding-box/text descriptions; zero-data-retention terms with the vendor; log 처리 내역; disclose 수탁자·국외이전 in a 처리방침 for the demo | [board p.3](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS217&mCode=G010030000&pageIndex=3) |

**Open items (UNVERIFIED, need follow-up with a registered Open API key or browser):** verbatim text of 제25조의2, 제26조, 제28조의8, 제58조②, 제22조의2, 시행령 제27조; 표준지침 current 고시 번호; content of 법률 제20897호; the 이동형 안내서 PDF body (PIPC download is JS-only; a mirror redirect to S3 was truncated); KOPICO case-number format. The web-search quota for this session is exhausted, so a follow-up message would be needed to continue searching.
