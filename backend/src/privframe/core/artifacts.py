"""Results of pipeline steps, kept by what they were computed from (design §4.2: artifact fingerprint cache).

A step's key is a hash of everything its result depends on: the video's content hash, the keys of the steps it
reads, and the parts and parameters it uses. A run that reaches a step whose key is already stored takes the
stored result instead of computing it, whatever case stored it. That is what lets an experiment change one
part of the pipeline and run only that step and the ones after it.

    data/artifacts/<step>/<key>/artifact.json    what was stored, how long it took to compute, which case made it
    data/artifacts/<step>/<key>/<files…>         the step's output files, at their path relative to a case directory

Stored files are never written again: a case gets hard links to large files and copies of small ones, and a
step that recomputes removes its old outputs first, so no run can change what another run reads.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from .util import now_iso

WORKDIR = "{workdir}"  # stands for the case directory inside a stored state update
LINK_FROM_BYTES = 1 << 20  # files at least this large are hard-linked instead of copied


def key(*parts: Any) -> str:
    """A stable key for a step result: 16 hex characters of the SHA-256 of its inputs."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]


def place(source: Path, target: Path) -> None:
    """Put `source` at `target` without sharing a writable file between them unless it is large and immutable."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    if source.stat().st_size >= LINK_FROM_BYTES:
        try:
            os.link(source, target)
            return
        except OSError:  # another filesystem, or one without hard links
            pass
    shutil.copy2(source, target)


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root

    def _dir(self, step: str, key: str) -> Path:
        return self.root / step / key

    def restore(self, step: str, key: str, workdir: Path) -> dict[str, Any] | None:
        """Put a stored result's files into the case directory and return its record (with the state update the
        step returned), or None when nothing is stored under the key."""
        directory = self._dir(step, key)
        record_file = directory / "artifact.json"
        if not record_file.is_file():
            return None
        try:
            text = record_file.read_text(encoding="utf-8")
            record = json.loads(text)
            files = [directory / name for name in record["files"]]
            if not all(file.is_file() for file in files):
                return None
            for name, file in zip(record["files"], files):
                place(file, workdir / name)
            record["update"] = json.loads(json.dumps(record["update"], ensure_ascii=False).replace(WORKDIR, _escaped(workdir)))
            return record
        except (OSError, ValueError, KeyError):
            return None  # an unreadable entry is the same as no entry: the step runs

    def store(self, step: str, key: str, workdir: Path, files: list[str], update: dict[str, Any], seconds: float, source: str, cost_usd: float = 0.0) -> None:
        """Keep a step's output files and state update. Storing the same key twice keeps the first."""
        directory = self._dir(step, key)
        if (directory / "artifact.json").is_file():
            return
        staging = directory.with_name(f".{directory.name}.{uuid.uuid4().hex[:8]}")
        try:
            for name in files:
                place(workdir / name, staging / name)
            record = {
                "step": step,
                "key": key,
                "files": files,
                "update": json.loads(json.dumps(update, ensure_ascii=False).replace(_escaped(workdir), WORKDIR)),
                "seconds": round(seconds, 3),
                "costUsd": round(max(0.0, cost_usd), 6),
                "source": source,
                "createdAt": now_iso(),
            }
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "artifact.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            directory.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(directory)
        except OSError:
            shutil.rmtree(staging, ignore_errors=True)  # another run stored it first, or the disk is full: the result is still in the case

    def stats(self) -> dict[str, Any]:
        """How much is stored, per step."""
        out: dict[str, Any] = {}
        if not self.root.is_dir():
            return out
        for step in sorted(p for p in self.root.iterdir() if p.is_dir()):
            entries = [e for e in step.iterdir() if (e / "artifact.json").is_file()]
            out[step.name] = {"entries": len(entries), "bytes": sum(f.stat().st_size for e in entries for f in e.rglob("*") if f.is_file())}
        return out


def _escaped(path: Path) -> str:
    """The path as it appears inside a JSON string."""
    return json.dumps(str(path), ensure_ascii=False)[1:-1]
