from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import TracebackType
from typing import Any

from .errors import TranscribeError
from .models import Chunk, ChunkTranscript
from .util import atomic_write_json


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class JobLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> "JobLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    value = json.loads(self.path.read_text(encoding="utf-8"))
                    pid = int(value.get("pid", 0))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pid = 0
                if _process_alive(pid):
                    raise TranscribeError("job_locked", f"the same transcription job is already running as PID {pid}", 8)
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "created_at": time.time()}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            self.acquired = True
            return self
        raise TranscribeError("job_locked", "could not acquire transcription job lock", 8)

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False


class StateStore:
    def __init__(self, root: Path, job_key: str) -> None:
        self.directory = root / job_key
        self.chunks_directory = self.directory / "chunks"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.chunks_directory.mkdir(parents=True, exist_ok=True)
        self.lock = JobLock(self.directory / "lock")

    @property
    def manifest_path(self) -> Path:
        return self.directory / "manifest.json"

    @property
    def result_path(self) -> Path:
        return self.directory / "result.json"

    def load_manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.exists():
            return None
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TranscribeError("state_corrupt", f"cannot read state manifest {self.manifest_path}", 8) from exc
        if not isinstance(value, dict):
            raise TranscribeError("state_corrupt", f"state manifest {self.manifest_path} is not an object", 8)
        return value

    def chunk_response_path(self, chunk: Chunk) -> Path:
        return self.chunks_directory / f"{chunk.cache_key}.response.json"

    def chunk_error_path(self, chunk: Chunk) -> Path:
        return self.chunks_directory / f"{chunk.cache_key}.error.json"

    def load_chunk(self, chunk: Chunk) -> ChunkTranscript | None:
        path = self.chunk_response_path(chunk)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_text = payload["raw_text"]
            attempts = int(payload.get("attempts", 1))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(raw_text, str):
            return None
        return ChunkTranscript(
            index=chunk.index,
            logical_start_seconds=chunk.logical_start_seconds,
            logical_end_seconds=chunk.logical_end_seconds,
            media_start_seconds=chunk.media_start_seconds,
            media_end_seconds=chunk.media_end_seconds,
            raw_text=raw_text,
            attempts=attempts,
            warning="empty_transcript" if not raw_text.strip() else None,
            cached=True,
        )

    def save_chunk(self, chunk: Chunk, transcript: ChunkTranscript) -> None:
        atomic_write_json(
            self.chunk_response_path(chunk),
            {
                "raw_text": transcript.raw_text,
                "attempts": transcript.attempts,
                "saved_at": time.time(),
            },
        )
        self.chunk_error_path(chunk).unlink(missing_ok=True)

    def save_chunk_error(self, chunk: Chunk, code: str, message: str) -> None:
        atomic_write_json(
            self.chunk_error_path(chunk),
            {"code": code, "message": message, "saved_at": time.time()},
        )

    def save_manifest(self, payload: dict[str, Any]) -> None:
        value = dict(payload)
        value["updated_at"] = time.time()
        atomic_write_json(self.manifest_path, value)

    def save_result(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.result_path, payload)

