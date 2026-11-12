"""Local models behind the perception slots: loaded on first use, one instance per process.

The service runs cases in worker threads. Every wrapper serializes inference with its own lock, because
OpenCV's DNN classes keep per-call state and the detector libraries do not promise thread safety. ONNX Runtime
is pinned to the CPU provider (it would otherwise pick CoreML on macOS and results would differ by machine).
Nothing here touches the network except the one-time weight downloads done by the libraries and the OpenCV Zoo files (`_weights`).
"""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from privframe.core.domain import Box
from privframe.core.pipeline import ResolvedProfile
from privframe.core.settings import ROOT
from privframe.core.util import fetch_file

from .common import RFDETR_CLASSES, PerceptionError, candidate, require

MODELS_DIR = ROOT / "models"
SFACE_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
# pinned to a commit of the OpenCV Zoo (MIT licence)
YUNET = {
    "name": "face_detection_yunet_2023mar.onnx",
    "url": "https://media.githubusercontent.com/media/opencv/opencv_zoo/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
}
CPU = ["CPUExecutionProvider"]
Det = tuple[Box, float]  # box in pixels of the image that was passed in, detection score

_build_lock = threading.Lock()
_shared: dict[tuple[str, ...], Any] = {}


def _once(key: tuple[str, ...], build: Callable[[], Any]) -> Any:
    with _build_lock:
        if key not in _shared:
            _shared[key] = build()
        return _shared[key]


def _load(what: str, build: Callable[[], Any]) -> Any:
    try:
        return build()
    except PerceptionError:
        raise
    except Exception as error:  # noqa: BLE001 — a missing package, a failed download and a bad weight file all end here
        raise PerceptionError(f"{what} 모델을 불러오지 못했습니다 ({type(error).__name__}: {error}). `uv sync --group models`와 네트워크(첫 실행의 가중치 내려받기)를 확인하세요.") from error


# ---------- faces ----------


class FaceDetector:
    """YuNet. Rows are `x, y, w, h, 5 landmarks (x, y), score` in pixels of the given image."""

    def __init__(self, path: Path) -> None:
        self._net = cv2.FaceDetectorYN.create(str(path), "", (320, 320), 0.5, 0.3, 5000)
        self._lock = threading.Lock()

    def rows(self, image: np.ndarray, min_score: float) -> np.ndarray:
        with self._lock:
            self._net.setInputSize((image.shape[1], image.shape[0]))
            self._net.setScoreThreshold(float(min_score))
            _, faces = self._net.detect(image)
        return np.empty((0, 15), np.float32) if faces is None else faces

    def detect(self, image: np.ndarray, min_score: float) -> list[Det]:
        height, width = image.shape[:2]
        out: list[Det] = []
        for row in self.rows(image, min_score):
            x0, y0 = max(0.0, float(row[0])), max(0.0, float(row[1]))
            x1, y1 = min(float(width), float(row[0] + row[2])), min(float(height), float(row[1] + row[3]))
            if x1 - x0 >= 4 and y1 - y0 >= 4:
                out.append(((x0, y0, x1 - x0, y1 - y0), float(row[14])))
        return out


def face_detector(profile: ResolvedProfile) -> FaceDetector:
    require(profile, "perception.face_detector")
    chosen = candidate(profile, "perception.face_detector")
    path = MODELS_DIR / str(chosen.ref)
    if not path.is_file():
        if chosen.ref != YUNET["name"]:
            raise PerceptionError(f"얼굴 검출 모델 파일이 없습니다: models/{chosen.ref}")
        path = _weights(YUNET["name"], YUNET["url"], "YuNet 얼굴 검출", sha256=YUNET["sha256"])
    return _once(("yunet", str(path)), lambda: _load("YuNet 얼굴 검출", lambda: FaceDetector(path)))


class FaceEmbedder:
    """SFace. `feature` takes one YuNet row (box + landmarks) on the same image and returns a unit vector.
    The vector may be biometric data: callers keep it in memory only."""

    def __init__(self, path: Path) -> None:
        self._net = cv2.FaceRecognizerSF.create(str(path), "")
        self._lock = threading.Lock()

    def feature(self, image: np.ndarray, row: np.ndarray) -> np.ndarray:
        with self._lock:
            vector = self._net.feature(self._net.alignCrop(image, row)).reshape(-1).astype(np.float64)
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm < 1e-9:
            raise PerceptionError("얼굴 특징 벡터를 계산하지 못했습니다.")
        return vector / norm


def _weights(name: str, url: str, what: str, *, sha256: str | None = None, min_bytes: int = 1) -> Path:
    """A weight file in models/ (git-ignored), fetched once from the OpenCV Zoo."""
    try:
        return fetch_file(url, MODELS_DIR / name, sha256=sha256, min_bytes=min_bytes)
    except Exception as error:  # noqa: BLE001
        raise PerceptionError(f"{what} 모델을 내려받지 못했습니다 ({type(error).__name__}: {error}). {url} 파일을 직접 받아 models/{name} 로 저장한 뒤 다시 실행하세요.") from error


def sface_path(name: str = "face_recognition_sface_2021dec.onnx") -> Path:
    return _weights(name, SFACE_URL, "SFace 얼굴 특징", min_bytes=1_000_000)


def face_embedder(profile: ResolvedProfile) -> FaceEmbedder:
    require(profile, "perception.face_embedding")
    name = str(candidate(profile, "perception.face_embedding").ref or "face_recognition_sface_2021dec.onnx")
    return _once(("sface", name), lambda: _load("SFace 얼굴 특징", lambda: FaceEmbedder(sface_path(name))))


# ---------- people and plates ----------


class BoxDetector:
    def __init__(self, predict: Callable[[np.ndarray], list[Det]]) -> None:
        self._predict = predict
        self._lock = threading.Lock()

    def detect(self, image: np.ndarray, min_score: float) -> list[Det]:
        with self._lock:
            found = self._predict(image)
        return [d for d in found if d[1] >= min_score and d[0][2] >= 2 and d[0][3] >= 2]


def _open_image_models(ref: str, labels: set[str] | None) -> BoxDetector:
    from open_image_models import create_detector

    model = create_detector(ref, conf_thresh=0.2, providers=CPU)

    def predict(image: np.ndarray) -> list[Det]:
        out: list[Det] = []
        for found in model.predict(image):
            if labels is None or found.label in labels:
                b = found.bounding_box
                out.append(((float(b.x1), float(b.y1), float(b.x2 - b.x1), float(b.y2 - b.y1)), float(found.confidence)))
        return out

    return BoxDetector(predict)


def _rfdetr(ref: str) -> BoxDetector:
    import rfdetr

    model = getattr(rfdetr, RFDETR_CLASSES[ref])(device="cpu")

    def predict(image: np.ndarray) -> list[Det]:
        found = model.predict(np.ascontiguousarray(image[:, :, ::-1]), threshold=0.2)  # the model takes RGB
        names = found.data.get("class_name")
        out: list[Det] = []
        for i, (x0, y0, x1, y1) in enumerate(found.xyxy.tolist()):
            if (names[i] == "person") if names is not None else (int(found.class_id[i]) == 1):  # COCO id 1
                out.append(((x0, y0, x1 - x0, y1 - y0), float(found.confidence[i])))
        return out

    return BoxDetector(predict)


def person_detector(profile: ResolvedProfile) -> BoxDetector:
    """COCO class `person` only."""
    require(profile, "perception.object_detector")
    chosen = candidate(profile, "perception.object_detector")
    ref = str(chosen.ref)
    if chosen.adapter == "rfdetr":
        return _once(("rfdetr", ref), lambda: _load("사람 검출", lambda: _rfdetr(ref)))
    return _once(("oim", ref), lambda: _load("사람 검출", lambda: _open_image_models(ref, {"person"})))


def plate_detector(profile: ResolvedProfile) -> BoxDetector | None:
    """None when the profile turns plate detection off."""
    require(profile, "perception.plate_detector")
    chosen = candidate(profile, "perception.plate_detector")
    if chosen.adapter == "none":
        return None
    ref = str(chosen.ref)
    return _once(("oim", ref), lambda: _load("번호판 검출", lambda: _open_image_models(ref, None)))


# ---------- text ----------

Reading = tuple[Box, str, float]  # box in pixels of the image, text, score


class TextReader:
    """RapidOCR. The engine remembers the `use_det/use_cls/use_rec` flags of its last call, so every call
    states them and runs under the lock."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._lock = threading.Lock()

    def read(self, image: np.ndarray) -> list[Reading]:
        """Find and read every text of an image."""
        with self._lock:
            result = self._engine(image, use_det=True, use_cls=True, use_rec=True)
        if result.boxes is None or result.txts is None:
            return []
        out: list[Reading] = []
        for quad, text, score in zip(result.boxes, result.txts, result.scores):
            xs, ys = [float(p[0]) for p in quad], [float(p[1]) for p in quad]
            if text and text.strip():
                out.append(((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)), text.strip(), float(score)))
        return out

    def read_line(self, image: np.ndarray) -> tuple[str, float]:
        """Read an image that is one line of text (a text or plate box): recognition only, about 10 ms."""
        with self._lock:
            result = self._engine(image, use_det=False, use_cls=False, use_rec=True)
        if not result.txts:
            return "", 0.0
        return str(result.txts[0]).strip(), float(result.scores[0])


def _rapidocr(ref: str) -> TextReader:
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    det = re.search(r"det=(PP-OCRv\d)_det_(\w+)", ref)
    rec = re.search(r"rec=([a-z_]+?)_(PP-OCRv\d)_rec_(\w+)", ref)
    if not det or not rec:
        raise PerceptionError(f"RapidOCR 모델 표기를 해석할 수 없습니다: '{ref}' (예: det=PP-OCRv6_det_tiny rec=korean_PP-OCRv5_rec_mobile)")
    engine = RapidOCR(
        params={
            "Global.log_level": "error",
            "Det.ocr_version": OCRVersion(det.group(1)),
            "Det.model_type": ModelType(det.group(2)),
            "Rec.lang_type": LangRec(rec.group(1)),
            "Rec.ocr_version": OCRVersion(rec.group(2)),
            "Rec.model_type": ModelType(rec.group(3)),
        }
    )
    return TextReader(engine)


def text_reader(profile: ResolvedProfile) -> TextReader:
    require(profile, "perception.ocr")
    ref = str(candidate(profile, "perception.ocr").ref or "")
    return _once(("rapidocr", ref), lambda: _load("RapidOCR 글자 인식", lambda: _rapidocr(ref)))
