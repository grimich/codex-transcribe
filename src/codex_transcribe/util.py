from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, BinaryIO


def sha256_stream(stream: BinaryIO, destination: BinaryIO | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
        total += len(block)
        if destination is not None:
            destination.write(block)
    return digest.hexdigest(), total


def fingerprint_file(path: Path) -> tuple[str, int]:
    before = path.stat()
    with path.open("rb") as handle:
        digest, total = sha256_stream(handle)
    after = path.stat()
    before_identity = (before.st_size, before.st_mtime_ns, getattr(before, "st_ino", None))
    after_identity = (after.st_size, after.st_mtime_ns, getattr(after, "st_ino", None))
    if before_identity != after_identity:
        from .errors import TranscribeError

        raise TranscribeError("input_changed", "input file changed while it was being read", 3)
    return digest, total


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write_bytes(path, payload)


def format_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def safe_filename(value: str, fallback: str = "audio") -> str:
    name = Path(value).name.replace('"', "")
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name or fallback
