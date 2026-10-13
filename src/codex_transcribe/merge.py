from __future__ import annotations

import re
from dataclasses import replace

from .models import ChunkTranscript


_WORD = re.compile(r"\w+", re.UNICODE)


def _words(text: str) -> tuple[list[str], list[re.Match[str]]]:
    matches = list(_WORD.finditer(text))
    return [match.group(0).casefold() for match in matches], matches


def deduplicate_boundary(previous: str, current: str) -> str:
    previous_words, _ = _words(previous)
    current_words, current_matches = _words(current)
    maximum = min(40, len(previous_words), len(current_words))
    for size in range(maximum, 2, -1):
        if previous_words[-size:] == current_words[:size]:
            cut = current_matches[size - 1].end()
            return current[cut:].lstrip(" \t\r\n,.;:—–-")
    return current


def merge_chunks(chunks: list[ChunkTranscript]) -> tuple[list[ChunkTranscript], str]:
    merged: list[ChunkTranscript] = []
    previous_raw = ""
    previous_index: int | None = None
    paragraphs: list[str] = []
    for chunk in sorted(chunks, key=lambda item: item.index):
        merged_text = chunk.raw_text.strip()
        if previous_raw and merged_text and previous_index is not None and chunk.index == previous_index + 1:
            merged_text = deduplicate_boundary(previous_raw, merged_text)
        updated = replace(chunk, merged_text=merged_text)
        merged.append(updated)
        if merged_text:
            paragraphs.append(merged_text)
        previous_raw = chunk.raw_text.strip()
        previous_index = chunk.index
    return merged, "\n\n".join(paragraphs)

