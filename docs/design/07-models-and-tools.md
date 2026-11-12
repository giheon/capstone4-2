> PrivFrame 2.0 설계·PRD v2(승인본 2026-11-05)의 일부. 전체 목차와 다이어그램 검증 기록은 [README.md](./README.md).

## 10. 모델·도구 선정 (리서치 6트랙, 1차 출처)

> 상세 표·출처는 §10.1~10.6. 결론만 먼저.

| 단계 | 최종 추천 | 대안 | 비고 |
|---|---|---|---|
| 장면 설명·인물 속성(VLM) | Qwen3.6-35B-A3B via OpenRouter(video_url, `require_parameters`, ZDR) | Gemini 3.8 Flash 직접 API · 로컬 Qwen3-VL-8B | 입력 프라이버시는 §7.3 실험으로 확정 |
| `inspect_visual` · 한국어 OCR 폴백 | 위와 동일 모델 / Gemini 3.8 Flash | Qwen3.5-9B | — |
| 얼굴 검출·특징 | YuNet 유지 · (서비스 전) AdaFace IR-50(MIT) | RetinaFace-MNetV2 · SFace | ArcFace는 비상용 |
| 전신·번호판·텍스트 | RF-DETR Nano · open-image-models yolo-v9-t · RapidOCR(PP-OCRv6 det + 한국어 v5 rec) | D-FINE-S · RT-DETRv2 plate · CRAFT | 모두 허용 라이선스 |
| 추적·마스크 | roboflow/trackers BoT-SORT · RF-DETR-Seg-Nano · EfficientTAM-S(전파, 선택) | ByteTrack · EdgeTAM | — |
| 임베딩·리랭커·스토어 | KURE-v1(기본)/v3(실험) · KURE-Reranker-nano · Chroma + Kiwi-BM25 RRF | Upstage Embed 2 · Qwen3-Reranker-4B/Cohere v5 · Qdrant | 로컬 API 비용 0 |
| Legal Analyst(도구 사용) | Claude Sonnet 5.5 | Gemini 3.8 Flash · GPT-5.4 | 한국어 법률(KCL 근거 제공 설정 상위), 도구 호출 품질 |
| Planner | GPT-5.4(strict JSON) | Gemini 4 Argon | — |
| Review Guide · 질문 문구 | Claude Sonnet 5.5 | Haiku 5.5 | — |
| Auditor | Claude Opus 5.5 → **Planner와 다른 군** 유지 위해 Gemini 3.1 Pro도 후보 | Grok 4.7 | Analyst가 Sonnet이면 Auditor는 Gemini/OpenAI군으로 교차 |
| Report Writer | Solar Pro 4 | Haiku 5.5 | 한국어 네이티브 |
| LLM Judge | 채점 대상 생성 모델군을 제외한 3종 패널 다수결 | — | — |
| 오케스트레이션·LLMOps | LangGraph 1.2.20 · LangChain 1.4.9(`create_agent`+미들웨어) · LangSmith 0.14.17 · `langchain-openrouter` 0.2.9 | — | — |
| 영상 I/O | PyAV 19.0.2 단일 패스(영상 재인코딩 · 오디오/자막/데이터 패킷 복사 · PTS/time_base 보존) + ffprobe/ffmpeg 검증 | ffmpeg CLI `-fps_mode passthrough -enc_time_base demux` 경로 · GOP smart cut(M6) | R7 §10.7, 상세 §6.2·§6.4 |

### 10.1 영상 이해(VLM) — 리서치 R1 요약
- 네이티브 영상 입력: Gemini 3.x/4(1 FPS 정적, `MM:SS`, 60초 ≈ 4~18k 토큰, 3.8 Flash $0.75/$3.75), Qwen3.6-27B/35B-A3B(Apache-2.0, Video-MME 87.7/86.6, OCRBench v2 EN 68.3; OpenRouter `qwen/qwen3.6-35b-a3b` video $0.10/$0.90), GLM-4.6V(구조화 출력 미지원), InternVL3.5·MiniCPM-V(OR 미제공), VideoLLaMA3(비상용). GPT-5.x·Claude 5.5는 **이미지만**.
- 한국어 OCR(문서 WER): Gemini-3-Flash 4.85, Qwen3.5-4B 6.46, GPT-5.4-mini 8.23, Tesseract 40.1([arXiv 2607.21617](https://arxiv.org/html/2607.21617)). 한국어 장면 텍스트 벤치마크는 없음 → 자체 검증.
- OpenRouter 영상: `video_url`/`input_video`, Gemini는 AI Studio 경유 YouTube만·Vertex 경유 base64만; ZDR 시 AI Studio·OpenAI/Anthropic 1차 엔드포인트 제외([videos](https://openrouter.ai/docs/features/multimodal/videos), [ZDR](https://openrouter.ai/docs/guides/features/zdr)).
- 로컬: mlx-vlm Qwen3-VL-8B 4-bit 5.76 GB, Qwen3-VL-4B 10초 클립 16프레임 5.8 s(M4 Max, [arXiv 2601.19139](https://arxiv.org/html/2601.19139v2)); Ollama 영상 미지원.
- 비용(60초): Qwen3.6 via OR ≈$0.005, Gemini 3.8 Flash ≈$0.010, 프레임 30장 + Haiku 5.5 ≈$0.009, GPT-5.5 high ≈$0.41.
- 데이터 정책: Gemini 유료 티어 학습 미사용·무료 티어는 사람 검토 가능([약관](https://aistudio.google.com/docs/terms)); OpenAI 30일 남용 로그·ZDR 승인제; Anthropic 30일; Vertex 서울 리전은 2.5 Flash만.

### 10.2 지각 — 리서치 R2 요약
- 검출: RF-DETR Nano/Small(Apache, COCO 48.4/53.0, M3 Pro CoreML fp32 CPU 73.6 ms/fp16 CPU+NE 20.8 ms, [repo](https://github.com/roboflow/rf-detr)); YOLO26n은 최속(CPU ONNX 38.9 ms)이나 AGPL; DEIMv2 비상용; Objects365에 번호판·명찰·얼굴 클래스 없음.
- 분할·전파: RF-DETR-Seg-Nano(Apache), EfficientTAM-S(Apache, MPS 지원, iPhone NPU 80.6 ms/프레임, [repo](https://github.com/yformer/EfficientTAM)), SAM 2.1-tiny는 M4 CPU 23 s/장, SAM 3는 CUDA 필수·자체 라이선스.
- 추적: roboflow/trackers(Apache, BoT-SORT·C-BIoU, [repo](https://github.com/roboflow/trackers)); Re-ID OSNet x0_25(MIT).
- 얼굴: YuNet(MIT, WIDER Hard 0.708~0.750) 유지; SCRFD 가중치 비상용; 특징은 AdaFace IR-50(MIT, IJB-C 96.98) 또는 SFace(Apache); ArcFace w600k_r50 비상용.
- OCR: RapidOCR(PP-OCRv6 det + `korean_PP-OCRv5_mobile_rec` 한국어 88.0%, Apple M4 ORT det+rec 0.35 s/장, [문서](http://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html)).
- 번호판: open-image-models yolo-v9-t(MIT, mAP50 .92~.97, [repo](https://github.com/ankandrew/open-image-models)); EgoBlur(Apache, macOS 비권장) 검증용. 허용 라이선스 한글 번호판 OCR 없음(마스킹은 검출만 필요).
- 연령: faceage-onnx/SigLIP2 보조 신호 + VLM 크롭(미성년→성인 오분류율이 전용 모델보다 낮음, [arXiv 2602.07815](https://arxiv.org/abs/2602.07815)); 법적 판정 아님.

### 10.3 임베딩·리랭커·스토어 — 리서치 R3 요약
- MTEB(kor, v2) 9과제·LawIRKo(law.go.kr 조문 검색): KURE-v3 0.803/0.816, KURE-v1 0.762/0.743, Qwen3-Embedding-8B 0.783/0.817, bge-m3 0.751/0.717; OpenAI 3-large는 8과제 0.617(KURE-v1 0.695)([KURE 결과](https://github.com/nlpai-lab/KURE/blob/main/eval/dense/results/README.md), [KURE-v1 카드](https://huggingface.co/nlpai-lab/KURE-v1)). 상용 API(Gemini·Voyage·Cohere·Upstage·CLOVA)는 공개 한국어 점수 없음. Jina v4/v5·리랭커 v3는 비상용.
- 리랭커(BM25 top-50 재정렬): Qwen3-Reranker-4B 0.892/0.877, KURE-Reranker-nano(149M) 0.876/0.837·482 pairs/s, bge-reranker-v2-m3 0.852/0.791([표](https://github.com/nlpai-lab/KURE/blob/main/eval/cross-encoder/results/README.md)). LegalBench-RAG는 리랭커가 정밀도를 낮춘 사례 보고([논문](https://arxiv.org/html/2408.10343)).
- Chroma 1.x 하이브리드·sparse는 Cloud 전용([문서](https://docs.trychroma.com/cloud/search-api.md)) → 앱 레벨 BM25(`kiwipiepy`+`bm25s`)+`EnsembleRetriever`. 대안 Qdrant 로컬.
- 청킹 근거: SearchFireSafety(조·항·호 단위+제목 접두어, [arXiv 2604.06173](https://arxiv.org/html/2604.06173)), Contextual Retrieval([Anthropic](https://www.anthropic.com/news/contextual-retrieval)).
- 평가 자료: KBL(법령 22만 조문+판례 RAG 코퍼스), KCL, LawIRKo, LBox Open, MiniLex law-kr(MIT, 일일 갱신).

### 10.4 LLM·OpenRouter — 리서치 R4 요약
- 근거 제공(RAG) 설정의 한국어 법률 벤치마크 KCL: Gemini 2.5 Pro 89.3, Claude Sonnet 4.5 85.2, GPT-5 84.2 vs A.X 4.0 71.3·EXAONE 72.3 → 근거가 주어지면 프런티어 추론 모델이 한국어 네이티브 모델을 압도([arXiv 2512.24572](https://arxiv.org/html/2512.24572v2)). KMMLU-Pro 변호사 과목은 추론 모드만 합격선([arXiv 2507.08924](https://arxiv.org/html/2507.08924v2)).
- 가격(OR, $/1M): GPT-5.4 2.5/15, 5.4-mini 0.75/4.5; Sonnet 5.5 2/10, Opus 5.5 4/20, Haiku 5.5 0.1/0.5; Gemini 3.8 Flash 0.75/3.75, 4 Argon 2/10; Solar Pro 4 0.30/1.20(공식); GLM-5.3-flash 0.15/0.5.
- 연동: 공식 `langchain-openrouter` 0.2.9(`ChatOpenRouter`, Chat Completions, `with_structured_output(method="json_schema", strict=True)`, `openrouter_provider={"require_parameters": True, "data_collection": "deny", "zdr": True}`); `ChatOpenAI(base_url=…)`는 비권장([docs](https://docs.langchain.com/oss/python/integrations/chat/openrouter)). ZDR 목록: GPT는 Azure만, Claude는 Bedrock·Vertex만, Gemini는 Vertex, Solar는 Upstage([목록](https://openrouter.ai/api/v1/endpoints/zdr)).
- Judge: 자기선호 편향([arXiv 2404.13076](https://arxiv.org/abs/2404.13076)), 이종 소형 패널 우위([arXiv 2404.18796](https://arxiv.org/abs/2404.18796)), 한국어 메타평가 KUDGE 최고 judge pairwise ~89%([arXiv 2409.11239](https://arxiv.org/html/2409.11239v2)).
- 오프라인 fixture: `GenericFakeChatModel`, 역할별 fixture 재생(`sha256(model, prompt_version, input)`), `pytest-recording`, 야간 live 잡.

### 10.5 LangGraph·LangSmith — 리서치 R5 요약
- 버전: langgraph 1.2.20, langchain 1.4.9, langsmith 0.14.17, checkpoint-sqlite 3.1.3/postgres 3.2.0, cli 0.4.41, openevals 0.2.2(agentevals 0.0.9 정체).
- 채택 기능: `Runtime[Context]`(설정을 상태 밖으로), `durability="sync"`(하네스·interrupt), `interrupt(response_schema=)`, `Send`, `RetryPolicy`·`timeout`·`error_handler`, `CachePolicy`(InMemory만 문서화 → artifact 캐시 유지), 서브그래프 `checkpointer=None`, 시간 여행, `TracePolicy`. `langgraph dev`는 컴파일 시 체크포인터를 무시(개발 전용)([issue 5790](https://github.com/langchain-ai/langgraph/issues/5790)).
- LangSmith: Developer $0·1석·5k trace/월, 보존 14/180일, 메타데이터 필터, 커스텀 모델 단가(소급 불가), `evaluate`/`aevaluate`(experiment_prefix·metadata 예약 키 models/prompts), 쌍대 비교, 노드 단위 평가(`graph.nodes[...]`·child_runs), 온라인 평가·주석 큐, pytest 통합, Prompt Hub 커밋 고정([pricing](https://www.langchain.com/pricing), [evaluate](https://docs.langchain.com/langsmith/evaluate-llm-application)).
- 배포: 셀프호스트 Agent Server는 Enterprise·Elastic-2.0 → FastAPI+Celery 유지, Agent Server 계약(thread run·command.resume·durability) 미러링.

### 10.6 법률 코퍼스·프레임 — 리서치 R6 요약 (§9·§11에 반영)
- law.go.kr Open API: `lawjosub`(조문 단위, `JO` 6자리), `prec`(판시사항·판결요지·참조조문·전문), `detc`, `expc`, `admrul`, **`ppc`(PIPC 결정문)**; OC 인증값 + 서버 IP/도메인 등록 필수([가이드](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lawJosubInfoGuide)).
- 현행판 안내서: 가명정보 처리 가이드라인 **2026.3**(비정형데이터 가명처리 기준 장), 이동형 2024.9, 고정형 2024.12, 아동·생체 2024.12, 생성형 AI 2025.8([목록](https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS217&mCode=G010030000)).
- 현행 법률 제21445호(시행 2026.9.11); AI 특례 제21910호는 2027.3.9 시행(시연 시점 미적용).

### 10.7 영상 I/O(원본 품질 보존) — 리서치 R7 요약 (§6에 반영)
- 출처: FFmpeg 공식 texinfo(ffmpeg·ffprobe·muxers·demuxers·filters·codecs·encoders·bitstream_filters)와 소스(movenc.c·libx264.c·libx265.c·videotoolboxenc.c·h264_mp4toannexb.c), PyAV 19.0.2 문서(pyav.basswood.io; pyav.org/docs/stable은 구판 스냅샷)·master 소스·PyPI, OpenCV `cap_ffmpeg_impl.hpp`, x265 CLI 문서, Netflix VMAF 문서, LosslessCut·smartcut·avcut·Avidemux·deface·EgoBlur·DashcamCleaner 저장소, Apple ProRes 문서. Homebrew ffmpeg 9.0.2로 파이프 전용 왕복 테스트(파일 미생성). `trac.ffmpeg.org` 위키는 접속 불가라 위키 전용 주장(CRF 17~18 "시각적 무손실", `hvc1` 태그 근거)은 미검증으로 표시.
- 핵심 사실: `-fps_mode passthrough`는 "디먹서 타임스탬프를 그대로 먹서로"; `-vsync`는 9.x에서 제거; `-enc_time_base demux`가 없으면 인코더 타임베이스 1/framerate로 반올림; `-copyts`/`-copytb`는 보조; 입력 `-r`은 타임스탬프 폐기; mp4 먹서는 `AVFMT_VARIABLE_FPS`로 VFR 허용, `video_track_timescale 0`이면 `time_base.den`을 10000 이상이 될 때까지 2배; 전역·스트림·챕터 메타는 기본 복사되나 데이터·첨부 스트림은 `-map 0` 필요, QuickTime 키는 `use_metadata_tags`; 색 정보는 libx264·libx265·videotoolbox가 `avctx`에서 VUI로 전달; HDR10 패스스루는 7.0+(libx264·libx265·libsvtav1), Dolby Vision RPU는 libx265만; HEVC 기본 태그 `hev1`; ffprobe `pkt_pts`는 5.0에서 제거(`pts`/`best_effort_timestamp` 사용).
- PyAV 19.0.2(2026-10-21): Python ≥3.12, macOS 14+ arm64·manylinux_2_28 휠, FFmpeg 9.0.2 번들(x264·x265 4.3·libvmaf 3.2.1·dav1d·SVT-AV1, macOS videotoolbox). `add_stream(codec, time_base=…)`, `add_stream_from_template`(video/audio/subtitle/data/attachment), `set_display_rotation`, `VideoFrame.from_ndarray(yuv420p)`, `coded_side_data`/`decoded_side_data`(19.0+). 리먹스 규칙: `packet.dts is None` 검사 대신 `packet.size == 0` 건너뛰기.
- OpenCV: `CAP_PROP_POS_MSEC`는 프레임 pts(없으면 pkt_dts) 기반 ms 실수, `CAP_PROP_FPS`는 `avg_frame_rate`, 프레임 탐색은 `frame_number/fps`로 seek → VFR에서 부정확. 회전은 `CAP_PROP_ORIENTATION_AUTO`를 명시해야 함.
- 마스크 렌더 선례: deface(프레임 독립 처리 + mask-scale 1.3), DashcamCleaner(blur_memory), EgoBlur(scale 1.1, 고정 FPS 요구), Balaji et al. 2021(프레임별 익명화의 깜빡임, [arXiv 2106.02328](https://arxiv.org/abs/2106.02328)).
- 미검증·미발견: CRF별 1080p PSNR 1차 표, FFV1·x264 무손실의 용량 수치, videotoolbox HDR10 메타 처리, 엄격 `avc1` 플레이어의 smart-cut 접합 재생 → 모두 M4/M6 측정 항목.

