> PrivFrame 2.0 설계·PRD v2(승인본 2026-11-05)의 일부. 전체 목차와 다이어그램 검증 기록은 [README.md](./README.md).

## 7. 영상 이해: 무엇을, 어느 세밀도로, 어떤 구조로

### 7.1 소비자에서 역산 (변경 없음, 요지)
렌더에는 객체별 프레임 영역(Track·마스크), 법률 분석에는 장면 맥락(장소·활동·인원·아동·민감 맥락·카메라 인지·텍스트)과 인물 속성(식별 단서·역할 단서·연령대·주요도), 보고서에는 시간축 투영이 필요하다. 줄거리 요약은 파생, 전용 행동 인식·관계 그래프·음성은 범위 외(음성은 보고서 고지).

### 7.2 산출 구조 `VideoUnderstanding` (원본 PTS 기준)
`video{meta, pts_map_ref}`, `segments[{id, start_pts, end_pts, keyframes, description{place_type, setting, activity, people_count, minors_present, sensitivity_flags[], camera_awareness, visible_text_summary, summary_ko, confidence}, provenance}]`, `tracks[{id, object_type, points(원본 좌표·PTS), masks?, quality}]`, `person_groups[{id, track_ids, attributes{age_band, role_cues[], identifiability_cues[], prominence}, provenance}]`, `text_items[{id, text, type, attached_person_id?, ranges}]`, `summary_ko`, `model_provenance`. 식별 단서는 **인물 단위로 전파**(채택안 A3). 얼굴 특징 벡터는 케이스 완료 시 삭제(§11.4).

### 7.3 클라우드 VLM 입력 프라이버시: 3안 비교 (v1의 "사전 블러 기본값" 재검토)

| 안 | 내용 | 장점 | 단점·실패 상황 | 법적·정책 |
|---|---|---|---|---|
| V-1 원본 프레임 + ZDR 제공자 | 축소 프레임을 그대로 전송 | 설명 품질 최고, 얼굴 기반 연령·표정·카메라 인지 판단 가능 | 얼굴 픽셀이 국외 수탁자에게 감 → 제26조·제28조의8 요건, 동의 없는 제3자 | 시연용 동의·합성 영상에 한정 가능 |
| V-2 축소 + 얼굴 익명화(블러/픽셀화) 후 전송 | 로컬 얼굴 검출로 얼굴만 익명화 | 얼굴 픽셀 비전송 원칙 유지, 장소·활동·복장·텍스트 판단은 유지 | 연령·표정 정확도 하락, 검출 누락 얼굴은 전송됨, VLM이 "블러된 얼굴"을 언급 | 위탁 범위 최소화 |
| V-3 로컬 VLM만 | Qwen3-VL-8B(mlx) | 데이터 비유출 | 품질 하락(Video-MME 71 vs 87), 클립당 수십 초, Mac 메모리 | 가장 보수적 |

**결정**: 기본 프로필 **V-2**(연령은 로컬 연령 모델 + 블러된 전신 단서로 보완, 익명화 검출기는 재현율 우선 저임계값), 대안 **V-3**(완전 로컬 프로필), **V-1**은 ZDR·동의 영상 조건에서 품질 상한 측정용. **U3 실험**(M4에서는 측정하지 않음, M5): 같은 테스트 영상에서 V-1/V-2/V-3의 장면 설명 루브릭·아동/민감맥락 탐지율·비용을 비교해 기본값을 확정한다. 사전 블러가 품질을 유의미하게 떨어뜨리면 "얼굴 영역만 저해상도 픽셀화(식별 불가 수준)"를 추가 실험한다.

### 7.4 도구 호출 설계
장면 설명은 장면당 1회(키프레임 ≤8장 또는 네이티브 클립), 인물 속성은 인물당 ≤2 크롭(얼굴 익명화된 전신), OCR은 키프레임·1 fps 로컬, `inspect_visual`은 에이전트 요청 시에만. 모든 산출물에 provenance.

