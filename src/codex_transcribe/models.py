from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    access_token: str
    account_id: str
    auth_file: Path


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float


@dataclass(frozen=True)
class Segment:
    logical_start: float
    logical_end: float
    media_start: float
    media_end: float
    path: Path | None = None
    depth: int = 0


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    duration_seconds: float
