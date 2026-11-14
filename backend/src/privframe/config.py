import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain.models import Contract

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRIVFRAME_", env_file=ROOT / ".env", extra="ignore")
    data_dir: Path = ROOT / "data"
    model_path: Path = ROOT / "models/face_detection_yunet_2023mar.onnx"
    identity_model_path: Path = ROOT / "models/w600k_r50.onnx"
    profiles_dir: Path = ROOT / "configs"
    redis_url: str = "redis://127.0.0.1:6379/0"
    frontend_origin: str = "http://localhost:5173"
    upload_limit_mb: int = 500
    network_timeout_seconds: float = 60


class PipelineConfig(Contract):
    name: str = "baseline"
    mode: Literal["real", "fixture"] = "real"
    extractor: Literal["pdfplumber-html-v1"] = "pdfplumber-html-v1"
    chunker: Literal["recursive"] = "recursive"
    chunk_size: int = Field(default=1200, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0)
    embedding_timeout: int = Field(default=60, ge=5, le=300)
    embedding_max_retries: int = Field(default=2, ge=0, le=5)
    retriever: Literal["chroma"] = "chroma"
    top_k: int = Field(default=8, ge=1, le=50)
    evidence_char_budget: int = Field(default=14000, ge=100, le=100000)
    reranker: Literal["none"] = "none"
    decider: Literal["openai", "fixture"] = "openai"
    llm_model: str = "gpt-5.4-mini-2026-03-17"
    llm_timeout: int = Field(default=120, ge=5, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    detector: Literal["yunet", "haar"] = "yunet"
    detector_threshold: float = Field(default=0.8, gt=0, le=1)
    detector_max_side: int = Field(default=640, ge=160, le=640)
    tracker: Literal["iou"] = "iou"
    tracker_iou_threshold: float = Field(default=0.3, gt=0, le=1)
    tracker_max_gap: int = Field(default=2, ge=0, le=2)
    scene_cut_threshold: float = Field(default=0.45, gt=0, le=1)
    scene_adaptive_threshold: float = Field(default=3.0, gt=0)
    scene_min_length: int = Field(default=15, ge=1)
    scene_window_width: int = Field(default=2, ge=1)
    scene_min_content: float = Field(default=15.0, ge=0)
    identity_grouper: Literal["none", "arcface"] = "arcface"
    identity_similarity_threshold: float = Field(default=0.60, ge=-1, le=1)
    identity_candidate_threshold: float = Field(default=0.40, ge=-1, le=1)
    identity_consistency_threshold: float = Field(default=0.35, ge=-1, le=1)
    identity_min_face_size: int = Field(default=48, ge=8)
    identity_max_samples: int = Field(default=5, ge=2, le=20)
    normalization_fps: int = Field(default=30, ge=1, le=60)
    video_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    renderer: Literal["opencv-ffmpeg-v1"] = "opencv-ffmpeg-v1"

    @model_validator(mode="after")
    def consistent(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.identity_candidate_threshold > self.identity_similarity_threshold:
            raise ValueError("Identity candidate threshold must not exceed the merge threshold")
        if self.mode == "fixture" and (self.decider != "fixture" or not self.embedding_model.startswith("fixture-")):
            raise ValueError("fixture mode requires explicit fixture adapters")
        if self.mode == "real" and (self.decider == "fixture" or self.embedding_model.startswith("fixture-")):
            raise ValueError("fixture adapters cannot be used in real mode")
        return self


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def load_profile(name: str, settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid profile name")

    def read(current: str, seen: set[str]) -> dict:
        if current in seen:
            raise ValueError("Profile inheritance cycle")
        seen.add(current)
        path = settings.profiles_dir / f"{current}.yaml"
        if not path.is_file():
            raise ValueError(f"Unknown profile: {current}")
        data = yaml.safe_load(path.read_text())
        parent = data.pop("extends", None)
        return {**(read(parent, seen) if parent else {}), **data}

    return PipelineConfig.model_validate(read(name, set())).model_dump(mode="json")


def index_config(config: dict) -> dict:
    keys = ("mode", "extractor", "chunker", "chunk_size", "chunk_overlap", "embedding_model",
            "embedding_dimensions", "retriever")
    return {key: config[key] for key in keys}
