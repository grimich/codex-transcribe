from __future__ import annotations

import re


_WORD = re.compile(r"\w+", re.UNICODE)


def deduplicate_boundary(previous: str, current: str) -> str:
    previous_words = [match.group(0).casefold() for match in _WORD.finditer(previous)]
    current_matches = list(_WORD.finditer(current))
    current_words = [match.group(0).casefold() for match in current_matches]
    maximum = min(40, len(previous_words), len(current_words))
    for size in range(maximum, 2, -1):
        if previous_words[-size:] == current_words[:size]:
            return current[current_matches[size - 1].end():].lstrip(" \t\r\n,.;:—–-")
    return current


def merge_chunks(chunks: list[str]) -> str:
    paragraphs: list[str] = []
    previous = ""
    for raw in chunks:
        text = raw.strip()
        if previous and text:
            text = deduplicate_boundary(previous, text)
        if text:
            paragraphs.append(text)
        previous = raw.strip()
    return "\n\n".join(paragraphs)
