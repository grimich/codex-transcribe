from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

from .errors import TranscribeError
from .models import Chunk, MediaInfo


SUPPORTED_SUFFIXES = {".m4a", ".mp4", ".mp3", ".wav", ".wave", ".flac", ".ogg", ".oga", ".webm"}


def resolve_binary(name: str, explicit: Path | None, env_name: str) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise TranscribeError("dependency_missing", f"{name} was not found at {candidate}", 4)
    from_env = os.environ.get(env_name)
    if from_env:
        candidate = Path(from_env).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise TranscribeError("dependency_missing", f"{name} was not found at {candidate}", 4)
    resolved = shutil.which(name)
    if not resolved:
        raise TranscribeError(
            "dependency_missing",
            f"{name} is required but was not found in PATH; install FFmpeg or pass --{name}",
            4,
        )
    return Path(resolved).resolve()


def _run(command: list[str], label: str, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TranscribeError("media_tool_failed", f"{label} failed: {exc}", 4) from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-4000:]
        raise TranscribeError("media_tool_failed", f"{label} failed: {detail}", 4)
    return result


def probe(path: Path, ffprobe: Path) -> MediaInfo:
    result = _run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate,format_name",
            "-show_entries",
            "stream=index,codec_type,codec_name,sample_rate,channels,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        "ffprobe",
        timeout=120.0,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TranscribeError("media_probe_invalid", "ffprobe returned invalid JSON", 4) from exc

    streams = payload.get("streams") if isinstance(payload, dict) else None
    audio_stream = None
    if isinstance(streams, list):
        audio_stream = next(
            (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
            None,
        )
    if not audio_stream:
        raise TranscribeError("media_no_audio", "input does not contain an audio stream", 4)

    format_info = payload.get("format") if isinstance(payload, dict) else None
    if not isinstance(format_info, dict):
        raise TranscribeError("media_probe_invalid", "ffprobe response did not contain format metadata", 4)

    try:
        duration = float(format_info["duration"])
        size = int(format_info.get("size") or path.stat().st_size)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise TranscribeError("media_probe_invalid", "input duration or size is unavailable", 4) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise TranscribeError("media_probe_invalid", "input duration must be finite and positive", 4)

    bit_rate_value = format_info.get("bit_rate") or audio_stream.get("bit_rate")
    try:
        bit_rate = int(bit_rate_value)
    except (TypeError, ValueError):
        bit_rate = max(1, round(size * 8 / duration))

    sample_rate = audio_stream.get("sample_rate")
    channels = audio_stream.get("channels")
    return MediaInfo(
        format_name=str(format_info.get("format_name") or "unknown"),
        codec_name=str(audio_stream.get("codec_name") or "unknown"),
        duration_seconds=duration,
        size_bytes=size,
        bit_rate=bit_rate,
        sample_rate=int(sample_rate) if sample_rate is not None else None,
        channels=int(channels) if channels is not None else None,
    )


def target_chunk_seconds(media: MediaInfo, requested: float, max_upload_bytes: int) -> float:
    size_bound = math.floor(max_upload_bytes * 8 / max(1, media.bit_rate) * 0.80)
    return max(1.0, min(requested, float(max(1, size_bound))))


def plan_chunks(duration: float, target_seconds: float, overlap_seconds: float) -> list[Chunk]:
    count = max(1, math.ceil(duration / target_seconds))
    planned: list[Chunk] = []
    placeholder = Path(".")
    for index in range(count):
        logical_start = index * target_seconds
        logical_end = min(duration, (index + 1) * target_seconds)
        media_start = max(0.0, logical_start - (overlap_seconds if index > 0 else 0.0))
        media_end = min(duration, logical_end + (overlap_seconds if index + 1 < count else 0.0))
        planned.append(
            Chunk(
                index=index,
                logical_start_seconds=logical_start,
                logical_end_seconds=logical_end,
                media_start_seconds=media_start,
                media_end_seconds=media_end,
                path=placeholder,
                temporary=True,
            )
        )
    return planned


def _output_suffix(source: Path, media: MediaInfo) -> str:
    if source.suffix.lower() in SUPPORTED_SUFFIXES:
        return ".m4a" if source.suffix.lower() == ".mp4" else source.suffix.lower()
    if media.codec_name == "mp3":
        return ".mp3"
    if media.codec_name == "flac":
        return ".flac"
    if media.codec_name in {"vorbis", "opus"}:
        return ".ogg"
    if media.codec_name.startswith("pcm_"):
        return ".wav"
    return ".m4a"


def materialize_chunks(
    source: Path,
    media: MediaInfo,
    planned: list[Chunk],
    directory: Path,
    ffmpeg: Path,
) -> list[Chunk]:
    directory.mkdir(parents=True, exist_ok=True)
    suffix = _output_suffix(source, media)
    result: list[Chunk] = []
    for item in planned:
        path = directory / f"chunk-{item.cache_key}{suffix}"
        duration = item.media_end_seconds - item.media_start_seconds
        if not path.exists() or path.stat().st_size == 0:
            _run(
                [
                    str(ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{item.media_start_seconds:.6f}",
                    "-i",
                    str(source),
                    "-t",
                    f"{duration:.6f}",
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-c",
                    "copy",
                    "-avoid_negative_ts",
                    "make_zero",
                    str(path),
                ],
                f"ffmpeg chunk {item.index + 1}/{len(planned)}",
            )
        if not path.exists() or path.stat().st_size == 0:
            raise TranscribeError("media_chunk_empty", f"FFmpeg produced an empty chunk for part {item.index}", 4)
        result.append(
            Chunk(
                index=item.index,
                logical_start_seconds=item.logical_start_seconds,
                logical_end_seconds=item.logical_end_seconds,
                media_start_seconds=item.media_start_seconds,
                media_end_seconds=item.media_end_seconds,
                path=path,
                temporary=True,
            )
        )
    return result

