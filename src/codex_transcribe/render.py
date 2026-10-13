from __future__ import annotations

import json

from .models import TranscriptionResult


def render_text(result: TranscriptionResult) -> bytes:
    return (result.text.strip() + "\n").encode("utf-8")


def render_json(result: TranscriptionResult) -> bytes:
    payload = {
        "schema_version": 1,
        "status": "completed",
        "text": result.text,
        "duration_seconds": result.duration_seconds,
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
