from __future__ import annotations

import asyncio
import math
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import av
import imageio_ffmpeg

from .errors import TranscribeError
from .models import MediaInfo, Segment


MAX_REQUEST_BYTES = 20 * 1024 * 1024
MAX_AUDIO_BYTES = MAX_REQUEST_BYTES - 64 * 1024
CHUNK_SECONDS = 600.0
OVERLAP_SECONDS = 1.5
MAX_SPLIT_DEPTH = 12


def resolve_ffmpeg() -> Path:
    try:
        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    except Exception as exc:
        raise TranscribeError("dependency_missing", "bundled FFmpeg is unavailable", 4) from exc
    if not candidate.is_file():
        raise TranscribeError("dependency_missing", "bundled FFmpeg was not found", 4)
    return candidate


async def _run_ffmpeg(ffmpeg: Path, arguments: list[str], label: str) -> None:
    with tempfile.TemporaryFile() as errors:
        try:
            process = await asyncio.create_subprocess_exec(
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-xerror",
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=errors,
            )
        except OSError as exc:
            raise TranscribeError("media_tool_failed", f"{label}: cannot start FFmpeg: {exc}", 4) from exc

        try:
            await process.wait()
        except asyncio.CancelledError:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise

        if process.returncode != 0:
            errors.seek(max(0, errors.seek(0, os.SEEK_END) - 2000))
            detail = errors.read().decode("utf-8", errors="replace").strip()
            raise TranscribeError("media_tool_failed", f"{label}: {detail or 'FFmpeg failed'}", 4)


async def normalize(source: Path, destination: Path, ffmpeg: Path) -> None:
    await _run_ffmpeg(
        ffmpeg,
        [
            "-i", str(source),
            "-map", "0:a:0",
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "aac",
            "-b:a", "64k",
            "-movflags", "+faststart",
            "-y", str(destination),
        ],
        "audio normalization",
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise TranscribeError("media_empty", "normalization produced no audio", 4)


def probe(path: Path) -> MediaInfo:
    try:
        with av.open(str(path), mode="r") as container:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise TranscribeError("media_no_audio", "input has no audio stream", 4)
            if stream.duration is not None and stream.time_base is not None:
                duration = float(stream.duration * stream.time_base)
            elif container.duration is not None:
                duration = float(container.duration / av.time_base)
            else:
                raise TranscribeError("media_duration", "audio duration is unavailable", 4)
    except TranscribeError:
        raise
    except (OSError, av.error.FFmpegError) as exc:
        raise TranscribeError("media_invalid", f"cannot inspect audio: {exc}", 4) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise TranscribeError("media_duration", "audio duration is invalid", 4)
    return MediaInfo(duration_seconds=duration)


def plan_chunks(duration: float) -> list[Segment]:
    count = max(1, math.ceil(duration / CHUNK_SECONDS))
    if count > 1 and duration - (count - 1) * CHUNK_SECONDS < 1.0:
        count -= 1
    chunks: list[Segment] = []
    for index in range(count):
        logical_start = index * CHUNK_SECONDS
        logical_end = min(duration, (index + 1) * CHUNK_SECONDS)
        chunks.append(
            Segment(
                logical_start=logical_start,
                logical_end=logical_end,
                media_start=max(0.0, logical_start - (OVERLAP_SECONDS if index else 0.0)),
                media_end=min(duration, logical_end + (OVERLAP_SECONDS if index + 1 < count else 0.0)),
            )
        )
    return chunks


def split_segment(segment: Segment, total_duration: float) -> tuple[Segment, Segment]:
    if segment.depth >= MAX_SPLIT_DEPTH or segment.logical_end - segment.logical_start <= 1.0:
        raise TranscribeError("chunk_too_large", "audio cannot be split below the upload limit", 4)
    midpoint = (segment.logical_start + segment.logical_end) / 2
    depth = segment.depth + 1
    return (
        Segment(
            segment.logical_start,
            midpoint,
            max(0.0, segment.logical_start - OVERLAP_SECONDS),
            min(total_duration, midpoint + OVERLAP_SECONDS),
            depth=depth,
        ),
        Segment(
            midpoint,
            segment.logical_end,
            max(0.0, midpoint - OVERLAP_SECONDS),
            min(total_duration, segment.logical_end + OVERLAP_SECONDS),
            depth=depth,
        ),
    )


async def materialize_segment(
    normalized: Path, segment: Segment, directory: Path, ffmpeg: Path
) -> Path:
    destination = directory / f"chunk-{uuid4().hex}.m4a"
    await _run_ffmpeg(
        ffmpeg,
        [
            "-ss", f"{segment.media_start:.6f}",
            "-i", str(normalized),
            "-t", f"{segment.media_end - segment.media_start:.6f}",
            "-map", "0:a:0",
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "aac",
            "-b:a", "64k",
            "-movflags", "+faststart",
            "-y", str(destination),
        ],
        "audio chunking",
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise TranscribeError("media_chunk_empty", "FFmpeg produced an empty audio chunk", 4)
    return destination
