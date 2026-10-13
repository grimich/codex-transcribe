from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

from .auth import load_credentials
from .backend import BackendClient
from .errors import PayloadTooLargeError, TranscribeError
from .media import (
    MAX_AUDIO_BYTES,
    materialize_segment,
    normalize,
    plan_chunks,
    probe,
    resolve_ffmpeg,
    split_segment,
)
from .merge import merge_chunks
from .models import TranscriptionResult


Progress = Callable[[str], None]


def _reject_output_alias(input_value: str, output_path: Path | None) -> None:
    if output_path is None:
        return
    output = output_path.expanduser().absolute()
    if output.exists() and output.is_dir():
        raise TranscribeError("output_invalid", "output path is a directory", 8)
    if input_value == "-":
        return
    source = Path(input_value).expanduser().absolute()
    if os.path.normcase(str(source)) == os.path.normcase(str(output)):
        raise TranscribeError("output_is_input", "output path is the input file", 3)
    try:
        if output.exists() and source.samefile(output):
            raise TranscribeError("output_is_input", "output path is the input file", 3)
    except OSError as exc:
        raise TranscribeError("output_invalid", f"cannot inspect output path: {exc}", 8) from exc


def _copy_stream(source: BinaryIO, destination: BinaryIO) -> int:
    total = 0
    while True:
        block = source.read(1024 * 1024)
        if not block:
            return total
        destination.write(block)
        total += len(block)


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino)


def _snapshot_input(input_value: str, destination: Path) -> None:
    try:
        if input_value == "-":
            with destination.open("wb") as target:
                size = _copy_stream(sys.stdin.buffer, target)
        else:
            source = Path(input_value).expanduser()
            if not source.is_file():
                raise TranscribeError("input_missing", f"input file does not exist: {source}", 3)
            before = _identity(source)
            with source.open("rb") as original, destination.open("wb") as target:
                size = _copy_stream(original, target)
            if before != _identity(source) or size != before[0]:
                raise TranscribeError("input_changed", "input changed while being copied", 3)
    except TranscribeError:
        raise
    except OSError as exc:
        raise TranscribeError("input_unreadable", f"cannot copy input: {exc}", 3) from exc
    if size == 0:
        raise TranscribeError("input_empty", "input contains no data", 3)


async def transcribe(
    input_value: str,
    output_path: Path | None,
    language: str | None,
    auth_file: Path | None,
    progress: Progress,
) -> TranscriptionResult:
    _reject_output_alias(input_value, output_path)
    load_credentials(auth_file)
    ffmpeg = resolve_ffmpeg()

    with tempfile.TemporaryDirectory(prefix="codex-transcribe-") as temporary:
        work = Path(temporary)
        source = work / "source.media"
        normalized = work / "normalized.m4a"
        progress("Copying input")
        _snapshot_input(input_value, source)
        progress("Normalizing audio")
        await normalize(source, normalized, ffmpeg)
        media = probe(normalized)
        segments = plan_chunks(media.duration_seconds)
        if len(segments) == 1:
            segments[0] = replace(segments[0], path=normalized)

        transcripts: list[str] = []
        index = 0
        async with BackendClient(auth_file) as backend:
            while index < len(segments):
                segment = segments[index]
                if segment.path is None:
                    path = await materialize_segment(normalized, segment, work, ffmpeg)
                    segment = replace(segment, path=path)
                    segments[index] = segment
                assert segment.path is not None

                if segment.path.stat().st_size > MAX_AUDIO_BYTES:
                    left, right = split_segment(segment, media.duration_seconds)
                    if segment.path != normalized:
                        segment.path.unlink(missing_ok=True)
                    segments[index:index + 1] = [left, right]
                    continue

                progress(f"Transcribing part {index + 1}/{len(segments)}")
                try:
                    text = await backend.transcribe(segment.path, language)
                except PayloadTooLargeError:
                    left, right = split_segment(segment, media.duration_seconds)
                    if segment.path != normalized:
                        segment.path.unlink(missing_ok=True)
                    segments[index:index + 1] = [left, right]
                    continue

                transcripts.append(text)
                if segment.path != normalized:
                    segment.path.unlink(missing_ok=True)
                index += 1

        progress("Completed")
        return TranscriptionResult(
            text=merge_chunks(transcripts),
            duration_seconds=media.duration_seconds,
        )
