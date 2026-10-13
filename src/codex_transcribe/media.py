from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path

import av
import imageio_ffmpeg

from .errors import TranscribeError
from .models import Chunk, MediaInfo


SUPPORTED_SUFFIXES = {".m4a", ".mp4", ".mp3", ".wav", ".wave", ".flac", ".ogg", ".oga", ".webm"}


def resolve_ffmpeg(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise TranscribeError("dependency_missing", f"FFmpeg was not found at {candidate}", 4)
    from_env = os.environ.get("CODEX_TRANSCRIBE_FFMPEG")
    if from_env:
        candidate = Path(from_env).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise TranscribeError("dependency_missing", f"FFmpeg was not found at {candidate}", 4)
    try:
        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    except Exception as exc:
        raise TranscribeError("dependency_missing", f"bundled FFmpeg is unavailable: {exc}", 4) from exc
    if not candidate.is_file():
        raise TranscribeError("dependency_missing", f"bundled FFmpeg was not found at {candidate}", 4)
    return candidate


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


def probe(path: Path) -> MediaInfo:
    try:
        size = path.stat().st_size
        with av.open(str(path), mode="r") as container:
            audio_stream = next((stream for stream in container.streams if stream.type == "audio"), None)
            if audio_stream is None:
                raise TranscribeError("media_no_audio", "input does not contain an audio stream", 4)

            duration: float | None = None
            if container.duration is not None:
                duration = float(container.duration / av.time_base)
            elif audio_stream.duration is not None and audio_stream.time_base is not None:
                duration = float(audio_stream.duration * audio_stream.time_base)

            codec = audio_stream.codec_context
            format_name = getattr(container.format, "name", None) or "unknown"
            codec_name = getattr(codec, "name", None) or "unknown"
            bit_rate_value = container.bit_rate or getattr(audio_stream, "bit_rate", None)
            sample_rate = getattr(codec, "sample_rate", None) or getattr(audio_stream, "rate", None)
            layout = getattr(codec, "layout", None)
            channels = getattr(layout, "nb_channels", None)
    except TranscribeError:
        raise
    except (OSError, av.error.FFmpegError) as exc:
        raise TranscribeError("media_probe_invalid", f"could not inspect input media: {exc}", 4) from exc

    if duration is None:
        raise TranscribeError("media_probe_invalid", "input duration or size is unavailable", 4)
    if not math.isfinite(duration) or duration <= 0:
        raise TranscribeError("media_probe_invalid", "input duration must be finite and positive", 4)

    try:
        bit_rate = int(bit_rate_value)
    except (TypeError, ValueError):
        bit_rate = max(1, round(size * 8 / duration))

    return MediaInfo(
        format_name=str(format_name),
        codec_name=str(codec_name),
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

