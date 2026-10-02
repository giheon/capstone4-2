#!/usr/bin/env python3
"""Download the pinned YuNet asset without needing application dependencies."""

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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = runpy.run_path(str(ROOT / "backend/src/privframe/adapters/model_manifest.py"))["YUNET_MODEL"]


def download(destination: Path, timeout: float = 30.0) -> Path:
    destination = destination.resolve()
    if destination.exists():
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if digest != MODEL["sha256"]:
            raise ValueError(f"Existing model checksum mismatch: {destination}; remove it before downloading again")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        request = urllib.request.Request(MODEL["url"], headers={"User-Agent": "PrivFrame-model-download/1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".download", delete=False) as output:
                temporary_path = Path(output.name)
                digest = hashlib.sha256()
                size = 0
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > MODEL["size"]:
                        raise ValueError("Model response exceeds the pinned size (possibly an incorrect asset)")
                    digest.update(chunk)
                    output.write(chunk)
        if size != MODEL["size"] or digest.hexdigest() != MODEL["sha256"]:
            raise ValueError("Downloaded model does not match the pinned SHA-256 and size")
        # A hard link fails if another downloader already created the destination;
        # neither a local model nor an in-flight download is ever overwritten.
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            if hashlib.sha256(destination.read_bytes()).hexdigest() != MODEL["sha256"]:
                raise ValueError("Another process created a model with a different checksum") from None
        return destination
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "models" / MODEL["name"])
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    try:
        result = download(args.output, args.timeout)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f"YuNet download failed: {exc}", file=sys.stderr)
        return 1
    print(f"YuNet ready: {result}\nSHA-256: {MODEL['sha256']}\nVersion: {MODEL['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
