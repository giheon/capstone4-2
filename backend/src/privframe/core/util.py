"""Small things every layer needs in one form: timestamps, ids, replacing a file, fetching a pinned file."""
from __future__ import annotations

import hashlib
import json
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(kind: str) -> str:
    """`<kind>_<month day hour minute second>_<4 hex>`: sorts by time, readable in a list."""
    return f"{kind}_{datetime.now(UTC).strftime('%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"


def write_atomic(path: Path, data: bytes | str) -> None:
    """Replace the file rather than write into it: a reader gets the old or the new content, never half, and
    a file another case shares by hard link is not changed for that case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    temp.replace(path)


def write_json(path: Path, value: Any, **dumps: Any) -> None:
    write_atomic(path, json.dumps(value, ensure_ascii=False, **dumps))


def fetch_file(url: str, path: Path, *, sha256: str | None = None, min_bytes: int = 1) -> Path:
    """Download a file once. With `sha256` the content must match; `min_bytes` catches an error page or an
    LFS pointer where no hash is pinned. Raises OSError when the download is not what was asked for."""
    if path.is_file() and path.stat().st_size >= min_bytes:
        return path
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "privframe"}), timeout=120) as response:  # noqa: S310 - fixed https URLs
        content = response.read()
    if len(content) < min_bytes:
        raise OSError(f"받은 파일이 {len(content)}바이트뿐입니다 (LFS 포인터이거나 오류 페이지)")
    if sha256 and hashlib.sha256(content).hexdigest() != sha256:
        raise OSError("받은 파일의 sha256이 고정한 값과 다릅니다")
    write_atomic(path, content)
    return path
