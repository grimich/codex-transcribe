from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence

from platformdirs import user_state_path

from . import __version__
from .errors import TranscribeError
from .models import Options
from .pipeline import transcribe
from .redaction import redact
from .render import render_json, render_text
from .util import atomic_write_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-transcribe",
        description="Transcribe audio through the ChatGPT backend used by Codex.",
    )
    parser.add_argument("input", nargs="?", help="audio file, or - to read audio from stdin")
    parser.add_argument("-o", "--output", type=Path, help="write the result atomically to this file")
    parser.add_argument("--format", choices=("text", "json"), default="text", dest="output_format")
    parser.add_argument("--language", default="ru", help="language hint sent to the transcription backend")
    parser.add_argument("--timestamps", action="store_true", help="include chunk intervals in text output")
    parser.add_argument("--max-upload-bytes", type=int, default=20 * 1024 * 1024)
    parser.add_argument("--chunk-seconds", type=float, default=600.0)
    parser.add_argument("--overlap-seconds", type=float, default=1.5)
    parser.add_argument("-j", "--jobs", type=int, default=3)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=900.0, dest="timeout_seconds")
    parser.add_argument("--auth-file", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--no-resume", action="store_false", dest="resume")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--ffprobe", type=Path)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.input is None:
        parser.error("INPUT is required")
    if args.timestamps and args.output_format != "text":
        parser.error("--timestamps is only valid with --format text")
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be between 1 and 16")
    if args.retries < 0:
        parser.error("--retries must not be negative")
    if args.timeout_seconds <= 0:
        parser.error("--timeout must be positive")
    if args.max_upload_bytes < 1024 * 1024:
        parser.error("--max-upload-bytes must be at least 1048576")
    if args.chunk_seconds <= 0:
        parser.error("--chunk-seconds must be positive")
    if args.overlap_seconds < 0 or args.chunk_seconds <= args.overlap_seconds * 2:
        parser.error("--chunk-seconds must be greater than twice --overlap-seconds")


async def _run(args: argparse.Namespace) -> int:
    state_root = (args.state_dir or user_state_path("codex-transcribe")).expanduser().resolve()
    options = Options(
        language=args.language,
        max_upload_bytes=args.max_upload_bytes,
        chunk_seconds=args.chunk_seconds,
        overlap_seconds=args.overlap_seconds,
        jobs=args.jobs,
        retries=args.retries,
        timeout_seconds=args.timeout_seconds,
        resume=args.resume,
        keep_work=args.keep_work,
        allow_partial=args.allow_partial,
    )

    def progress(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr, flush=True)

    result = await transcribe(
        input_value=args.input,
        options=options,
        state_root=state_root,
        auth_file=args.auth_file,
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
        progress=progress,
    )
    payload = render_json(result) if args.output_format == "json" else render_text(result, args.timestamps)
    if args.output:
        atomic_write_bytes(args.output.expanduser().resolve(), payload)
    else:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
    return 7 if result.status == "partial" else 0


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate(parser, args)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("codex-transcribe: cancelled: interrupted by user", file=sys.stderr)
        return 130
    except BrokenPipeError:
        try:
            sys.stdout.close()
        finally:
            return 0
    except TranscribeError as exc:
        print(f"codex-transcribe: {exc.code}: {redact(exc.message)}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        print(f"codex-transcribe: io_error: {redact(exc)}", file=sys.stderr)
        return 8
    except Exception as exc:
        message = redact(exc)
        if os.environ.get("CODEX_TRANSCRIBE_DEBUG") == "1":
            import traceback

            message = redact(traceback.format_exc())
        print(f"codex-transcribe: internal_error: {message}", file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(run())

