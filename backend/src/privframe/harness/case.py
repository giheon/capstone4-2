"""Run a case from the command line, without the web app.

    uv run privframe-case make-sample                       # synthetic example video → data/samples/sample.mp4
    uv run privframe-case run data/samples/sample.mp4 --auto --answer purpose.publication_scope=public
    uv run privframe-case run <video> --memo "SNS에 올릴 카페 홍보 영상"      # asks you the questions here
    uv run privframe-case run <video> --fixture --auto      # scripted stand-ins: no keys, network or models
    uv run privframe-case list

The case is stored under data/cases like any other, so the web app shows it after the server starts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..core.settings import ROOT, CoreSettings
from ..core.util import fetch_file
from ..service.runner import TERMINAL, Case, RunManager

SAMPLE_NOTES = {
    "title": "예시 영상 (합성)",
    "memo": "카페를 소개하는 홍보 영상입니다. SNS에 올릴 예정입니다.",
    "answers": {"purpose.publication_scope": "public", "location.reveals_private_place": "no"},
    # what make_sample draws, so experiments can compare what a run finds with what is there
    "expected": {"scenes": 2, "people": 1, "plates": 1, "texts": ["김민지 바리스타", "하늘 카페", "12가 3456", "오늘의 추천 메뉴"]},
}
PORTRAIT = ROOT / "data" / "test-assets" / "astronaut.png"
# the public-domain NASA test portrait, pinned to a scikit-image release
PORTRAIT_URL = "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png"
PORTRAIT_SHA256 = "88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5"


def make_sample(out: Path, portrait: Path = PORTRAIT) -> Path:
    """A 10-second 720p clip with two shots, a name tag, a sign, a licence plate and a tone.

    The only face is the public-domain NASA test portrait (scikit-image's `astronaut`); the name and the
    plate are made up. It exists so demo runs and experiments have a video that needs no one's consent.
    """
    import av
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    if not portrait.exists():
        try:
            fetch_file(PORTRAIT_URL, portrait, sha256=PORTRAIT_SHA256)
        except OSError as error:
            raise SystemExit(f"초상 이미지를 내려받지 못했습니다 ({error}). {PORTRAIT_URL} 파일을 {portrait} 로 저장한 뒤 다시 실행하세요.") from error
    font_path = next((f for f in ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/AppleGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"] if Path(f).exists()), None)

    def font(size: int) -> Any:
        return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()

    face = Image.open(portrait).convert("RGB")
    width, height, fps, seconds, cut = 1280, 720, 30, 10, 6.0
    out.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(out), mode="w") as container:
        video = container.add_stream("libx264", rate=fps, options={"crf": "18", "preset": "medium"})
        video.width, video.height, video.pix_fmt = width, height, "yuv420p"
        audio = container.add_stream("aac", rate=44100)
        container.metadata["title"] = "PrivFrame 예시 영상 (합성)"
        # a shooting location, as a phone would record it (Seoul City Hall: a public place, not anyone's home)
        container.metadata["location"] = "+37.5665+126.9780/"
        for index in range(seconds * fps):
            t = index / fps
            first = t < cut
            image = Image.new("RGB", (width, height), (196, 168, 132) if first else (84, 104, 120))
            draw = ImageDraw.Draw(image)
            if first:
                draw.rectangle((0, 520, width, height), fill=(120, 92, 68))  # counter
                draw.rectangle((820, 60, 1200, 170), fill=(52, 44, 40))
                draw.text((856, 84), "하늘 카페", font=font(56), fill=(240, 226, 200))
                x = 150 + int(90 * t / cut)
                person = face.resize((460, 460))
                image.paste(person, (x, 150))
                draw.rectangle((x + 250, 470, x + 440, 524), fill=(250, 250, 250), outline=(60, 60, 60), width=2)
                draw.text((x + 262, 482), "김민지 바리스타", font=font(24), fill=(20, 20, 20))
                if t >= 3.0:
                    draw.rectangle((930, 580, 1190, 660), fill=(255, 255, 255), outline=(20, 20, 20), width=4)
                    draw.text((952, 594), "12가 3456", font=font(46), fill=(15, 15, 15))
            else:
                person = face.resize((600, 600))
                image.paste(person, (560 - int(40 * (t - cut)), 90))
                draw.rectangle((60, 600, 520, 670), fill=(30, 36, 42))
                draw.text((80, 614), "오늘의 추천 메뉴", font=font(40), fill=(236, 236, 236))
            for packet in video.encode(av.VideoFrame.from_ndarray(np.asarray(image), format="rgb24")):
                container.mux(packet)
        tone = (np.sin(np.arange(44100 * seconds) * 2 * np.pi * 330 / 44100) * 0.15).astype(np.float32)
        for start in range(0, len(tone), 1024):
            chunk = av.AudioFrame.from_ndarray(tone[start : start + 1024].reshape(1, -1), format="fltp", layout="mono")
            chunk.sample_rate, chunk.pts = 44100, start
            for packet in audio.encode(chunk):
                container.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode():
                container.mux(packet)
    out.with_suffix(".json").write_text(json.dumps(SAMPLE_NOTES, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _manager(settings: CoreSettings, fixture: bool) -> RunManager:
    if fixture:
        from ..service.app import fixture_manager

        return fixture_manager(settings)
    return RunManager(settings)


def _ask(prompt: str, options: list[dict[str, Any]], extra: dict[str, str]) -> str:
    print(f"\n  {prompt}")
    keys = [*[o["value"] for o in options], *extra]
    for number, option in enumerate(options, start=1):
        detail = f" — {option['detail']}" if option.get("detail") else ""
        print(f"    {number}. {option['label']}{detail}")
    for offset, (_, label) in enumerate(extra.items(), start=len(options) + 1):
        print(f"    {offset}. {label}")
    while True:
        raw = input("  번호: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]


def _interact(manager: RunManager, case: Case) -> None:
    """Answer the interrupt the case is waiting on from the terminal."""
    kind = case.waiting["payload"] if case.waiting else ""
    detail = case.detail
    if kind == "identity":
        question = detail["identity"]
        print(f"\n[대상 확인] {question['detail']}")
        value = _ask(question["prompt"], question["options"], {})
        manager.resume(case, kind, value, value)
    elif kind == "questions":
        answers = {}
        for question in [q for q in detail["questions"] if q["status"] == "pending"]:
            print(f"\n[질문 {question['id']}] 묻는 이유: {question['basis']}")
            for impact in question["impact"]:
                print(f"    · {impact['option']} → {impact['effect']}")
            answers[question["id"]] = _ask(question["prompt"], question["options"], {"unknown": "모름", "later": "나중에"})
        manager.resume(case, kind, answers, " · ".join(f"{k}={v}" for k, v in answers.items()))
    elif kind == "review":
        print("\n[계획 검토]")
        for decision in detail["plan"]["decisions"]:
            spans = ", ".join(f"{a:.1f}–{b:.1f}s" for a, b in decision["ranges"])
            print(f"  {decision['id']} {decision['targetLabel']}: {' · '.join(x for x in (decision['method'], decision.get('shape'), spans) if x)}\n      {decision['rationale']}")
        input("  Enter를 누르면 이 계획대로 진행합니다 (수정은 웹 화면에서). ")
        manager.resume(case, kind, {"action": "approve", "decisions": []}, "수정 없음")
    elif kind == "audit":
        print("\n[감사]")
        for finding in detail["findings"]:
            print(f"  {finding['id']} ({finding['severity']}) {finding['title']}\n      {finding['detail']}\n      권고: {finding['recommendation']}")
        input("  Enter를 누르면 권고를 모두 적용합니다. ")
        manager.resume(case, kind, {"resolutions": []}, "권고 모두 적용")


def run(args: argparse.Namespace) -> int:
    settings = CoreSettings()
    manager = _manager(settings, args.fixture)
    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"파일이 없습니다: {video}")
    answers = dict(pair.split("=", 1) for pair in args.answer or [])
    case = manager.create(video, video.name, memo=args.memo or "", mode=args.mode, profile_id=args.profile, autopilot=args.auto, autopilot_answers=answers, start=False)
    if args.cost_cap is not None:
        case.detail["costCapUsd"] = args.cost_cap
    shown = {"seq": 0}

    def progress() -> None:
        for event in case.events:
            if event["seq"] <= shown["seq"]:
                continue
            shown["seq"] = event["seq"]
            if event["kind"] == "activity":
                print(f"  [{event['t'] / 1000:6.1f}s] {event['text']}")
            elif event["kind"] == "run.status" and args.verbose:
                print(f"  [{event['t'] / 1000:6.1f}s] 상태: {event['status']}")

    print(f"케이스 {case.id} · 프로필 {case.meta['profileName']} ({case.meta['configHash']}) · {case.detail['video']['durationSec']}초")
    manager.begin(case)
    while True:
        try:
            status = manager.wait(case, timeout=2)
        except TimeoutError:
            progress()
            continue
        progress()
        if status in TERMINAL or status == "PAUSED":
            break
        _interact(manager, case)
    detail = case.detail
    print(f"\n상태 {detail['status']} · 비용 ${detail['costUsd']:.4f} / 상한 ${detail['costCapUsd']:.2f}" + (f" · {detail.get('error')}" if detail.get("error") else ""))
    if detail.get("report"):
        for row in detail["report"]["verification"]:
            print(f"  [{row['verdict']:>4}] {row['check']}: {row['output']}")
        print(f"출력: {next((case.dir / 'output').iterdir())}\n보고서: {case.dir / 'report.html'}")
    manager.close()
    return 0 if detail["status"] == "COMPLETED" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("make-sample", help="합성 예시 영상 만들기")
    p.add_argument("--out", type=Path, default=None)
    p = sub.add_parser("run", help="영상 하나를 처음부터 끝까지 처리")
    p.add_argument("video")
    p.add_argument("--profile", help="프로필 ID (기본: 서비스 프로필)")
    p.add_argument("--memo", help="상황 메모")
    p.add_argument("--mode", choices=["anonymized_upload", "local_only"], default="anonymized_upload")
    p.add_argument("--auto", action="store_true", help="질문에 자동으로 답하고(기본 '모름') 계획을 그대로 승인")
    p.add_argument("--answer", action="append", metavar="FACT_KEY=VALUE", help="--auto일 때 쓸 답")
    p.add_argument("--fixture", action="store_true", help="고정 응답으로 실행 (키 · 네트워크 · 모델 불필요)")
    p.add_argument("--cost-cap", type=float, help="비용 상한(USD)")
    p.add_argument("--verbose", action="store_true")
    sub.add_parser("list", help="케이스 목록")
    args = parser.parse_args(argv)
    if args.command == "make-sample":
        settings = CoreSettings()
        out = make_sample(args.out or settings.data_dir / "samples" / "sample.mp4")
        print(f"만들었습니다: {out} (+ {out.with_suffix('.json').name})")
        return 0
    if args.command == "list":
        manager = RunManager(CoreSettings())
        for case in manager.list():
            print(f"{case.id}  {case.status:<22} ${case.detail['costUsd']:.4f}  {case.meta['profileName']}  {case.detail['title']}")
        manager.close()
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
