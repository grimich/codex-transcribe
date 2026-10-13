from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .errors import TranscribeError
from .pipeline import transcribe
from .redaction import redact
from .render import render_json, render_text
from .util import atomic_write_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-transcribe",
        description="Transcribe audio with the existing Codex ChatGPT login.",
    )
    parser.add_argument("input", help="media file, or - for binary stdin")
    parser.add_argument("-o", "--output", type=Path, help="write the complete result atomically")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--language", help="optional language hint")
    parser.add_argument("--auth-file", type=Path, help="Codex auth.json path")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


async def _run(args: argparse.Namespace) -> int:
    task = asyncio.current_task()
    previous_term_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: task.cancel() if task else None)
    try:
        result = await transcribe(
            input_value=args.input,
            output_path=args.output,
            language=args.language,
            auth_file=args.auth_file,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
        payload = render_json(result) if args.format == "json" else render_text(result)
        if args.output is None:
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
        else:
            atomic_write_bytes(args.output.expanduser().absolute(), payload)
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous_term_handler)


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("codex-transcribe: cancelled: interrupted", file=sys.stderr)
        return 130
    except TranscribeError as exc:
        print(f"codex-transcribe: {exc.code}: {redact(exc.message)}", file=sys.stderr)
        return exc.exit_code
    except (OSError, BrokenPipeError) as exc:
        print(f"codex-transcribe: io_error: {redact(exc)}", file=sys.stderr)
        return 8
    except Exception:
        print("codex-transcribe: internal_error: unexpected failure", file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(run())
