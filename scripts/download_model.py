#!/usr/bin/env python3
"""Download pinned YuNet and ArcFace assets without application dependencies."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import runpy
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = runpy.run_path(str(ROOT / "backend/src/privframe/adapters/model_manifest.py"))
MODEL = MANIFEST["YUNET_MODEL"]  # Preserve the original importable downloader API.
MODELS = {"yunet": MODEL, "arcface": MANIFEST["ARCFACE_MODEL"]}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(destination: Path, timeout: float = 30.0, *, model: dict | None = None) -> Path:
    model = model or MODEL
    destination = destination.resolve()
    if destination.exists():
        if destination.stat().st_size != model["size"] or _digest(destination) != model["sha256"]:
            raise ValueError(f"Existing model checksum mismatch: {destination}; remove it before downloading again")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    extracted_path = None
    try:
        request = urllib.request.Request(model["url"], headers={"User-Agent": "PrivFrame-model-download/1"})
        expected_size = model.get("archive_size", model["size"])
        expected_hash = model.get("archive_sha256", model["sha256"])
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".download", delete=False) as output:
                temporary_path = Path(output.name)
                digest = hashlib.sha256()
                size = 0
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > expected_size:
                        raise ValueError("Model response exceeds the pinned size (possibly an incorrect asset)")
                    digest.update(chunk)
                    output.write(chunk)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise ValueError("Downloaded model does not match the pinned SHA-256 and size")
        if "archive_member" in model:
            # Read one explicitly named file; never extract archive-controlled paths.
            with zipfile.ZipFile(temporary_path) as archive:
                member = archive.getinfo(model["archive_member"])
                if member.file_size != model["size"]:
                    raise ValueError("Archived model has an unexpected size")
                with archive.open(member) as source:
                    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".onnx.download",
                                                     delete=False) as output:
                        extracted_path = Path(output.name)
                        while chunk := source.read(64 * 1024):
                            output.write(chunk)
            if _digest(extracted_path) != model["sha256"]:
                raise ValueError("Archived model does not match the pinned SHA-256")
        # A hard link fails if another downloader already created the destination;
        # neither a local model nor an in-flight download is ever overwritten.
        try:
            os.link(extracted_path or temporary_path, destination)
        except FileExistsError:
            if _digest(destination) != model["sha256"]:
                raise ValueError("Another process created a model with a different checksum") from None
        return destination
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if extracted_path is not None:
            extracted_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["all", *MODELS], default="all")
    parser.add_argument("--output", type=Path,
                        help="Single-model destination; with no --model, preserves legacy YuNet behavior")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    selected = ["yunet"] if args.output and args.model == "all" else (
        list(MODELS) if args.model == "all" else [args.model]
    )
    try:
        for name in selected:
            model = MODELS[name]
            result = download(args.output or ROOT / "models" / model["name"], args.timeout, model=model)
            print(f"{name} ready: {result}\nSHA-256: {model['sha256']}\nVersion: {model['version']}")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, urllib.error.URLError) as exc:
        print(f"Model download failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
