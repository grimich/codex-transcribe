from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .backend import BackendClient
from .errors import AuthenticationError, PayloadTooLargeError, TranscribeError
from .media import (
    SUPPORTED_SUFFIXES,
    materialize_chunks,
    plan_chunks,
    probe,
    resolve_binary,
    target_chunk_seconds,
)
from .merge import merge_chunks
from .models import Chunk, ChunkTranscript, Options, SourceIdentity, TranscriptionResult
from .redaction import redact
from .state import StateStore
from .util import fingerprint_file, sha256_stream


Progress = Callable[[str], None]


def _prepare_input(input_value: str, state_root: Path) -> SourceIdentity:
    if input_value != "-":
        path = Path(input_value).expanduser().resolve()
        if not path.is_file():
            raise TranscribeError("input_missing", f"input file does not exist: {path}", 3)
        try:
            digest, size = fingerprint_file(path)
        except OSError as exc:
            raise TranscribeError("input_unreadable", f"cannot read input file {path}: {exc}", 3) from exc
        return SourceIdentity(path=path, original_path=path, sha256=digest, size_bytes=size)

    incoming = state_root / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="stdin-", suffix=".audio", dir=incoming)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as destination:
            digest, size = sha256_stream(sys.stdin.buffer, destination)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise TranscribeError("input_empty", "stdin did not contain audio bytes", 3)
    return SourceIdentity(path=path, original_path=None, sha256=digest, size_bytes=size, temporary=True)


def _job_key(source: SourceIdentity, options: Options) -> str:
    value = {
        "schema": 1,
        "sha256": source.sha256,
        "language": options.language,
        "chunk_seconds": options.chunk_seconds,
        "max_upload_bytes": options.max_upload_bytes,
        "overlap_seconds": options.overlap_seconds,
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_base(source: SourceIdentity, media_duration: float, options: Options, target: float) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": {
            "path": str(source.original_path) if source.original_path else None,
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "duration_seconds": media_duration,
        },
        "options": {
            "language": options.language,
            "chunk_seconds": options.chunk_seconds,
            "max_upload_bytes": options.max_upload_bytes,
            "overlap_seconds": options.overlap_seconds,
        },
        "effective_chunk_seconds": target,
        "status": "transcribing",
        "chunks": {},
    }


def _chunk_manifest(chunk: Chunk, status: str, attempts: int = 0, error: str | None = None) -> dict[str, object]:
    return {
        "index": chunk.index,
        "logical_start_seconds": chunk.logical_start_seconds,
        "logical_end_seconds": chunk.logical_end_seconds,
        "media_start_seconds": chunk.media_start_seconds,
        "media_end_seconds": chunk.media_end_seconds,
        "status": status,
        "attempts": attempts,
        "error": error,
    }


async def _transcribe_plan(
    chunks: list[Chunk],
    options: Options,
    auth_file: Path | None,
    state: StateStore,
    manifest: dict[str, object],
    progress: Progress,
) -> tuple[list[ChunkTranscript], list[tuple[Chunk, TranscribeError]]]:
    transcripts: dict[int, ChunkTranscript] = {}
    failures: list[tuple[Chunk, TranscribeError]] = []
    pending: list[Chunk] = []
    chunk_state = manifest["chunks"]
    assert isinstance(chunk_state, dict)

    for chunk in chunks:
        cached = state.load_chunk(chunk) if options.resume else None
        if cached is not None:
            transcripts[chunk.index] = cached
            chunk_state[chunk.cache_key] = _chunk_manifest(chunk, "completed", cached.attempts)
        else:
            pending.append(chunk)
            chunk_state[chunk.cache_key] = _chunk_manifest(chunk, "pending")
    state.save_manifest(manifest)

    completed = len(transcripts)
    if completed:
        progress(f"Resuming: {completed}/{len(chunks)} chunks cached")

    semaphore = asyncio.Semaphore(options.jobs)

    async with BackendClient(auth_file, options.timeout_seconds, options.retries) as backend:
        async def run_one(chunk: Chunk) -> tuple[Chunk, ChunkTranscript | None, TranscribeError | None]:
            async with semaphore:
                try:
                    text, attempts = await backend.transcribe(chunk.path, options.language)
                    transcript = ChunkTranscript(
                        index=chunk.index,
                        logical_start_seconds=chunk.logical_start_seconds,
                        logical_end_seconds=chunk.logical_end_seconds,
                        media_start_seconds=chunk.media_start_seconds,
                        media_end_seconds=chunk.media_end_seconds,
                        raw_text=text,
                        attempts=attempts,
                        warning="empty_transcript" if not text.strip() else None,
                    )
                    return chunk, transcript, None
                except TranscribeError as exc:
                    return chunk, None, exc
                except Exception as exc:  # normalize library failures without leaking credentials
                    return chunk, None, TranscribeError("unexpected_backend", redact(exc), 6)

        tasks = [asyncio.create_task(run_one(chunk)) for chunk in pending]
        try:
            for finished in asyncio.as_completed(tasks):
                chunk, transcript, error = await finished
                if transcript is not None:
                    transcripts[chunk.index] = transcript
                    state.save_chunk(chunk, transcript)
                    chunk_state[chunk.cache_key] = _chunk_manifest(chunk, "completed", transcript.attempts)
                    completed += 1
                    progress(f"Transcribing: {completed}/{len(chunks)}")
                else:
                    assert error is not None
                    failures.append((chunk, error))
                    state.save_chunk_error(chunk, error.code, error.message)
                    chunk_state[chunk.cache_key] = _chunk_manifest(chunk, "failed", error=error.code)
                    if isinstance(error, AuthenticationError):
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        state.save_manifest(manifest)
                        raise error
                state.save_manifest(manifest)
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    return [transcripts[index] for index in sorted(transcripts)], failures


async def transcribe(
    input_value: str,
    options: Options,
    state_root: Path,
    auth_file: Path | None,
    ffmpeg_path: Path | None,
    ffprobe_path: Path | None,
    progress: Progress,
) -> TranscriptionResult:
    state_root.mkdir(parents=True, exist_ok=True)
    source = _prepare_input(input_value, state_root)
    ffprobe = resolve_binary("ffprobe", ffprobe_path, "CODEX_TRANSCRIBE_FFPROBE")
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_path, "CODEX_TRANSCRIBE_FFMPEG")

    try:
        progress("Inspecting input")
        media = probe(source.path, ffprobe)
        key = _job_key(source, options)
        state = StateStore(state_root, key)

        with state.lock:
            target = target_chunk_seconds(media, options.chunk_seconds, options.max_upload_bytes)
            if options.resume:
                old_manifest = state.load_manifest()
                if old_manifest is not None:
                    previous_target = old_manifest.get("effective_chunk_seconds")
                    if isinstance(previous_target, (int, float)) and previous_target > 0:
                        target = min(target, float(previous_target))

            replan_count = 0
            while True:
                can_upload_source = (
                    media.size_bytes <= options.max_upload_bytes
                    and media.duration_seconds <= target
                    and source.path.suffix.lower() in SUPPORTED_SUFFIXES
                )
                if can_upload_source:
                    chunks = [
                        Chunk(
                            index=0,
                            logical_start_seconds=0.0,
                            logical_end_seconds=media.duration_seconds,
                            media_start_seconds=0.0,
                            media_end_seconds=media.duration_seconds,
                            path=source.path,
                            temporary=False,
                        )
                    ]
                else:
                    planned = plan_chunks(media.duration_seconds, target, options.overlap_seconds)
                    progress(f"Splitting: {len(planned)} chunks")
                    chunks = materialize_chunks(source.path, media, planned, state.chunks_directory, ffmpeg)

                oversized = [chunk for chunk in chunks if chunk.path.stat().st_size > options.max_upload_bytes]
                if oversized:
                    replan_count += 1
                    if replan_count > 8 or target <= 1.0:
                        raise TranscribeError("chunk_too_large", "could not split audio below the upload limit", 4)
                    target = max(1.0, target / 2.0)
                    continue

                manifest = _manifest_base(source, media.duration_seconds, options, target)
                transcripts, failures = await _transcribe_plan(
                    chunks, options, auth_file, state, manifest, progress
                )

                if any(isinstance(error, PayloadTooLargeError) for _, error in failures):
                    replan_count += 1
                    if replan_count > 8 or target <= 1.0:
                        raise PayloadTooLargeError("backend rejected even the smallest planned chunk")
                    target = max(1.0, target / 2.0)
                    continue
                break

            if failures and not options.allow_partial:
                first_chunk, first_error = failures[0]
                raise TranscribeError(
                    "incomplete",
                    f"chunk {first_chunk.index + 1}/{len(chunks)} failed: {first_error.message}",
                    7,
                )

            merged_chunks, full_text = merge_chunks(transcripts)
            missing = sorted({chunk.index for chunk, _ in failures})
            warnings = [f"missing_chunk:{index}" for index in missing]
            warnings.extend(
                f"empty_transcript:{chunk.index}" for chunk in merged_chunks if chunk.warning == "empty_transcript"
            )
            status = "partial" if failures else "completed"
            result = TranscriptionResult(
                status=status,
                source_path=str(source.original_path) if source.original_path else None,
                source_sha256=source.sha256,
                source_size_bytes=source.size_bytes,
                media=media,
                language=options.language,
                text=full_text,
                chunks=merged_chunks,
                warnings=warnings,
            )
            state.save_result(result.as_dict())
            manifest["status"] = status
            manifest["warnings"] = warnings
            state.save_manifest(manifest)

            if not options.keep_work:
                for path in state.chunks_directory.iterdir():
                    if path.is_file() and ".response." not in path.name and ".error." not in path.name:
                        path.unlink(missing_ok=True)
            progress("Completed" if status == "completed" else "Completed with missing chunks")
            return result
    finally:
        if source.temporary and not options.keep_work:
            source.path.unlink(missing_ok=True)

