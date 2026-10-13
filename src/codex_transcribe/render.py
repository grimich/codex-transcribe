from __future__ import annotations

import json

from .models import TranscriptionResult
from .util import format_timestamp


def render_text(result: TranscriptionResult, timestamps: bool = False) -> bytes:
    if not timestamps:
        text = result.text.strip()
        return (text + "\n").encode("utf-8")

    sections: list[str] = []
    for chunk in sorted(result.chunks, key=lambda item: item.index):
        start = format_timestamp(chunk.logical_start_seconds)
        end = format_timestamp(chunk.logical_end_seconds)
        text = chunk.merged_text.strip() or "[Речь не обнаружена.]"
        sections.append(f"[{start}–{end}]\n{text}")
    return ("\n\n".join(sections).rstrip() + "\n").encode("utf-8")


def render_json(result: TranscriptionResult) -> bytes:
    return (json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")

