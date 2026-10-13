from __future__ import annotations

import asyncio
import json
import platform
import random
from pathlib import Path

import httpx

from .auth import load_credentials
from .errors import AuthenticationError, BackendError, PayloadTooLargeError
from .models import Credentials
from .redaction import redact
from .util import safe_filename


DEFAULT_ENDPOINT = "https://chatgpt.com/backend-api/transcribe"
DEFAULT_DESKTOP_VERSION = "26.429.30905"
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def content_type_for(path: Path) -> str:
    return {
        ".wav": "audio/wav",
        ".wave": "audio/wav",
        ".webm": "audio/webm",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".flac": "audio/flac",
    }.get(path.suffix.lower(), "application/octet-stream")


def _delay_for(attempt: int, response: httpx.Response | None = None) -> float:
    if response is not None and response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(120.0, float(retry_after)))
            except ValueError:
                pass
    schedule = (2.0, 5.0, 15.0)
    return schedule[min(attempt, len(schedule) - 1)] + random.random()


class BackendClient:
    def __init__(
        self,
        auth_file: Path | None,
        timeout_seconds: float,
        retries: int,
        endpoint: str = DEFAULT_ENDPOINT,
    ) -> None:
        self.auth_file = auth_file
        self.retries = retries
        self.endpoint = endpoint
        system = platform.system().lower() or "unknown"
        machine = platform.machine() or "unknown"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds)),
            follow_redirects=False,
            trust_env=True,
            headers={
                "originator": "Codex Desktop",
                "user-agent": f"Codex Desktop/{DEFAULT_DESKTOP_VERSION} ({system}; {machine})",
            },
        )

    async def __aenter__(self) -> "BackendClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self._client.aclose()

    def _headers(self, credentials: Credentials) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {credentials.access_token}",
            "ChatGPT-Account-Id": credentials.account_id,
        }

    async def transcribe(self, path: Path, language: str) -> tuple[str, int]:
        auth_refreshed = False
        last_error: Exception | None = None
        total_attempts = max(1, self.retries + 1)

        for attempt in range(total_attempts):
            credentials = load_credentials(self.auth_file)
            try:
                with path.open("rb") as audio:
                    response = await self._client.post(
                        self.endpoint,
                        headers=self._headers(credentials),
                        files={
                            "file": (
                                safe_filename(path.name),
                                audio,
                                content_type_for(path),
                            )
                        },
                        data={"language": language} if language else {},
                    )
            except AuthenticationError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt + 1 >= total_attempts:
                    raise BackendError("backend_network", f"transcribe request failed: {redact(exc)}") from exc
                await asyncio.sleep(_delay_for(attempt))
                continue
            except OSError as exc:
                raise BackendError("audio_unreadable", f"cannot read audio chunk {path}: {exc}") from exc

            if len(response.content) > MAX_RESPONSE_BYTES:
                raise BackendError("response_too_large", "transcribe response exceeded 16 MiB", response.status_code)

            if response.status_code == 401 and not auth_refreshed:
                auth_refreshed = True
                if attempt + 1 < total_attempts:
                    continue

            if response.status_code == 413:
                raise PayloadTooLargeError()

            if response.status_code in RETRYABLE_STATUSES and attempt + 1 < total_attempts:
                await asyncio.sleep(_delay_for(attempt, response))
                continue

            if response.status_code < 200 or response.status_code >= 300:
                body = redact(response.text[:2000], credentials.access_token)
                code = "auth_rejected" if response.status_code in {401, 403} else "backend_http"
                message = f"transcribe request failed with HTTP {response.status_code}: {body}"
                if response.status_code in {401, 403}:
                    raise AuthenticationError(code, message)
                raise BackendError(code, message, response.status_code)

            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                last_error = exc
                if attempt + 1 < total_attempts:
                    await asyncio.sleep(_delay_for(attempt))
                    continue
                raise BackendError("backend_invalid_json", "transcribe response was not valid JSON") from exc

            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str):
                raise BackendError("backend_missing_text", "transcribe response did not contain a text string")
            return text, attempt + 1

        raise BackendError("backend_failed", f"transcribe request failed: {redact(last_error)}")
