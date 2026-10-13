from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Credentials:
    access_token: str
    account_id: str
    auth_file: Path


@dataclass(frozen=True)
class SourceIdentity:
    path: Path
    original_path: Path | None
    sha256: str
    size_bytes: int
    temporary: bool = False


@dataclass(frozen=True)
class MediaInfo:
    format_name: str
    codec_name: str
    duration_seconds: float
    size_bytes: int
    bit_rate: int
    sample_rate: int | None
    channels: int | None


@dataclass(frozen=True)
class Chunk:
    index: int
    logical_start_seconds: float
    logical_end_seconds: float
    media_start_seconds: float
    media_end_seconds: float
    path: Path
    temporary: bool

    @property
    def cache_key(self) -> str:
        start = round(self.logical_start_seconds * 1000)
        end = round(self.logical_end_seconds * 1000)
        return f"{self.index:04d}-{start}-{end}"


@dataclass
class ChunkTranscript:
    index: int
    logical_start_seconds: float
    logical_end_seconds: float
    media_start_seconds: float
    media_end_seconds: float
    raw_text: str
    merged_text: str = ""
    attempts: int = 1
    warning: str | None = None
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptionResult:
    status: str
    source_path: str | None
    source_sha256: str
    source_size_bytes: int
    media: MediaInfo
    language: str
    text: str
    chunks: list[ChunkTranscript]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "source": {
                "path": self.source_path,
                "sha256": self.source_sha256,
                "size_bytes": self.source_size_bytes,
                "duration_seconds": self.media.duration_seconds,
                "format": self.media.format_name,
                "codec": self.media.codec_name,
            },
            "language": self.language,
            "text": self.text,
            "chunks": [chunk.as_dict() for chunk in self.chunks],
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class Options:
    language: str = "ru"
    max_upload_bytes: int = 20 * 1024 * 1024
    chunk_seconds: float = 600.0
    overlap_seconds: float = 1.5
    jobs: int = 3
    retries: int = 3
    timeout_seconds: float = 900.0
    resume: bool = True
    keep_work: bool = False
    allow_partial: bool = False

