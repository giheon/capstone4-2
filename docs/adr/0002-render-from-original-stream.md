---
status: accepted
date: 2026-11-05
---
# 분석은 축소 프록시로, 렌더는 원본 스트림에서 한다

기존 파이프라인은 입력을 30 fps CFR·yuv420p·AAC로 재인코딩한 정규화본을 분석하고 그 정규화본을 다시 인코딩해 출력했다. 출력은 입력과 프레임 수·타이밍·픽셀 포맷·오디오·메타데이터가 달라져 "비식별 변경 외에는 입력과 동일"이라는 요구를 충족할 수 없었다. 그래서 분석용 프록시(축소만, 프레임 수·PTS 동일)와 최종 렌더(원본을 PyAV로 디코드해 결정 구간의 프레임만 수정, 같은 코덱군·픽셀 포맷·색 메타로 재인코딩, PTS·time_base 유지, 오디오·자막·데이터·메타·회전/HDR은 바이트 복사)를 분리했다.

## Considered Options
- 정규화 품질만 상향: fps·VFR·오디오·메타 손실은 그대로여서 탈락.
- ffmpeg rawvideo 파이프 유지 + `-fps_mode passthrough`: rawvideo에는 타임스탬프가 없어 VFR을 재현할 수 없음(ffmpeg 9.0.2 왕복 테스트, docs/research/R7).
- GOP 단위 선택 재인코딩(smart cut): 복사 구간은 bit-exact이지만 closed-GOP H.264에서만 안정 → 실험 기능(M6)으로 보류.

## Consequences
- 영상 픽셀은 가림 밖 영역까지 1세대 손실 인코딩을 피할 수 없다. 그래서 출력 검증(프레임 수·PTS·오디오 framemd5·메타 diff·마스크 외 PSNR/SSIM)이 결정적 노드로 들어가고 실패 시 케이스는 완료되지 않는다.
- OpenCV의 프레임 탐색(`CAP_PROP_POS_FRAMES`)은 CFR을 가정하므로 시간축 기준으로 쓰지 않는다. 모든 시간·좌표는 원본 PTS·원본 픽셀로 저장한다.
