"""Video datasets for experiments: named sets of videos, each with the uploader's memo and, when known, what
the pipeline should find in it. A dataset holds no answers to the questions a run asks: an experiment never
answers for the user (service/experiments.py).

    data/datasets/<id>/dataset.json     name, description, whether frames may leave this machine, items
    data/datasets/<id>/videos/<item>.*  the videos (hard links or copies of what was added)

The synthetic example video is always available as the built-in dataset `sample`. A dataset made from the
team's own footage should be created with `sendable: false`: experiments then refuse combinations that would
send its frames to a cloud model.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.util import now_iso, write_json
from ..core.artifacts import place
from ..core.video import VideoError, probe
from .errors import ServiceError

SAMPLE = "sample"


class DatasetError(ServiceError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32]


class DatasetStore:
    def __init__(self, root: Path, sample: Callable[[], tuple[Path, dict[str, Any]] | None]):
        self.root = root
        self._sample = sample  # the example video and its notes, when it exists
        self._facts: dict[tuple[str, float], dict[str, Any]] = {}  # the sample's basic facts, by path and modification time

    # ---------- reading ----------

    def _builtin(self) -> dict[str, Any] | None:
        found = self._sample()
        if found is None:
            return None
        path, notes = found
        item = {"id": "sample", "title": notes.get("title") or "예시 영상", "filename": path.name, "video": str(path), "memo": notes.get("memo", ""), "expected": notes.get("expected") or None}
        stamp = (str(path), path.stat().st_mtime)
        if stamp not in self._facts:
            self._facts = {stamp: {k: v for k, v in self._described(dict(item)).items() if k not in item}}
        item.update(self._facts[stamp])
        return {"id": SAMPLE, "name": "예시 영상 (합성)", "description": "실제 인물이 없는 합성 영상 1건. 무엇이 들어 있는지 알고 있어 결과를 정답과 비교할 수 있다.", "builtin": True, "sendable": True, "createdAt": "", "items": [item]}

    @staticmethod
    def _described(item: dict[str, Any]) -> dict[str, Any]:
        """The item with the video's basic facts, read once and kept."""
        if "durationSec" not in item:
            try:
                meta, _ = probe(Path(item["video"]), item.get("filename"))
                item.update(durationSec=round(meta.duration_s, 2), width=meta.display_width, height=meta.display_height, frames=meta.frame_count, sha256=meta.sha256)
            except (VideoError, OSError):
                item.update(durationSec=0.0, missing=True)
        return item

    def list(self) -> list[dict[str, Any]]:
        out = [d for d in [self._builtin()] if d]
        for file in sorted(self.root.glob("*/dataset.json")):
            try:
                out.append(json.loads(file.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def get(self, dataset_id: str) -> dict[str, Any]:
        found = next((d for d in self.list() if d["id"] == dataset_id), None)
        if found is None:
            raise DatasetError(404, "데이터셋을 찾을 수 없습니다.")
        return found

    @staticmethod
    def public(dataset: dict[str, Any]) -> dict[str, Any]:
        """The dataset as the web app sees it: without where the videos are on this machine."""
        return {**dataset, "items": [DatasetStore.public_item(item) for item in dataset["items"]]}

    @staticmethod
    def public_item(item: dict[str, Any]) -> dict[str, Any]:
        """Without the file's place and hash."""
        return {k: v for k, v in item.items() if k not in ("video", "sha256")}

    def items(self, dataset_ids: list[str]) -> list[dict[str, Any]]:
        """Every item of the datasets, each tagged with its dataset and whether its frames may leave the machine."""
        out = []
        for dataset_id in dataset_ids:
            dataset = self.get(dataset_id)
            out += [{**item, "dataset": dataset["id"], "datasetName": dataset["name"], "sendable": dataset["sendable"], "key": f"{dataset['id']}/{item['id']}"} for item in dataset["items"] if not item.get("missing")]
        return out

    # ---------- writing ----------

    def _save(self, dataset: dict[str, Any]) -> dict[str, Any]:
        write_json(self.root / dataset["id"] / "dataset.json", dataset, indent=1)
        return dataset

    def create(self, name: str, description: str = "", sendable: bool = False) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise DatasetError(422, "데이터셋 이름을 적어 주세요.")
        dataset_id = f"{_slug(name) or 'set'}-{uuid.uuid4().hex[:4]}"
        return self._save({"id": dataset_id, "name": name, "description": description.strip(), "builtin": False, "sendable": bool(sendable), "createdAt": now_iso(), "items": []})

    def add_item(self, dataset_id: str, source: Path, filename: str, *, title: str = "", memo: str = "", expected: dict[str, Any] | None = None, origin: str | None = None) -> dict[str, Any]:
        dataset = self.get(dataset_id)
        if dataset["builtin"]:
            raise DatasetError(409, "기본 데이터셋에는 영상을 추가할 수 없습니다.")
        try:
            meta, _ = probe(source, filename)
        except VideoError as error:
            raise DatasetError(400, "영상 파일이 아니거나 손상된 파일입니다.") from error
        if any(item.get("sha256") == meta.sha256 for item in dataset["items"]):
            raise DatasetError(409, "같은 영상이 이미 이 데이터셋에 있습니다.")
        item_id = f"v{len(dataset['items']) + 1}-{uuid.uuid4().hex[:4]}"
        target = self.root / dataset_id / "videos" / f"{item_id}{Path(filename).suffix.lower() or '.mp4'}"
        place(source, target)
        item = {"id": item_id, "title": title.strip() or filename, "filename": filename, "video": str(target), "memo": memo, "expected": expected, "origin": origin,
                "durationSec": round(meta.duration_s, 2), "width": meta.display_width, "height": meta.display_height, "frames": meta.frame_count, "sha256": meta.sha256}  # fmt: skip
        dataset["items"].append(item)
        self._save(dataset)
        return item

    def _editable(self, dataset_id: str) -> dict[str, Any]:
        dataset = self.get(dataset_id)
        if dataset["builtin"]:
            raise DatasetError(409, "기본 데이터셋은 고칠 수 없습니다.")
        return dataset

    def update(self, dataset_id: str, *, name: str | None = None, description: str | None = None, sendable: bool | None = None) -> dict[str, Any]:
        dataset = self._editable(dataset_id)
        if name is not None:
            if not name.strip():
                raise DatasetError(422, "데이터셋 이름을 적어 주세요.")
            dataset["name"] = name.strip()
        if description is not None:
            dataset["description"] = description.strip()
        if sendable is not None:
            dataset["sendable"] = bool(sendable)
        return self._save(dataset)

    def update_item(self, dataset_id: str, item_id: str, *, title: str | None = None, memo: str | None = None, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        dataset = self._editable(dataset_id)
        item = next((i for i in dataset["items"] if i["id"] == item_id), None)
        if item is None:
            raise DatasetError(404, "그 영상은 이 데이터셋에 없습니다.")
        if title is not None and title.strip():
            item["title"] = title.strip()
        if memo is not None:
            item["memo"] = memo
        if expected is not None:
            item["expected"] = expected or None  # an empty object clears it
        self._save(dataset)
        return item

    def remove_item(self, dataset_id: str, item_id: str) -> None:
        dataset = self._editable(dataset_id)
        item = next((i for i in dataset["items"] if i["id"] == item_id), None)
        if item is None:
            raise DatasetError(404, "그 영상은 이 데이터셋에 없습니다.")
        Path(item["video"]).unlink(missing_ok=True)
        dataset["items"] = [i for i in dataset["items"] if i["id"] != item_id]
        self._save(dataset)

    def delete(self, dataset_id: str) -> None:
        dataset = self.get(dataset_id)
        if dataset["builtin"]:
            raise DatasetError(409, "기본 데이터셋은 지울 수 없습니다.")
        shutil.rmtree(self.root / dataset_id, ignore_errors=True)
