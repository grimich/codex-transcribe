from __future__ import annotations

import asyncio
import platform
import random
from pathlib import Path

import httpx

from .auth import load_credentials
from .errors import AuthenticationError, BackendError, PayloadTooLargeError
from .models import Credentials
from .redaction import redact


ENDPOINT = "https://chatgpt.com/backend-api/transcribe"
DESKTOP_VERSION = "26.429.30905"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_RETRIES = 3


def _delay(attempt: int, response: httpx.Response | None = None) -> float:
    if response is not None and response.status_code == 429:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return max(0.0, min(120.0, float(value)))
            except ValueError:
                pass
    return (2.0, 5.0, 15.0)[min(attempt, 2)] + random.random()


class BackendClient:
    def __init__(self, auth_file: Path | None, endpoint: str = ENDPOINT) -> None:
        self.auth_file = auth_file
        self.endpoint = endpoint
        system = platform.system().lower() or "unknown"
        machine = platform.machine() or "unknown"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(900.0, connect=15.0),
            follow_redirects=False,
            trust_env=True,
            headers={
                "originator": "Codex Desktop",
                "user-agent": f"Codex Desktop/{DESKTOP_VERSION} ({system}; {machine})",
            },
        )

    async def __aenter__(self) -> "BackendClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self._client.aclose()

    @staticmethod
    def _headers(credentials: Credentials) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {credentials.access_token}",
            "ChatGPT-Account-Id": credentials.account_id,
        }

    async def transcribe(self, path: Path, language: str | None) -> str:
        retries = 0
        auth_retry_used = False
        while True:
            credentials = load_credentials(self.auth_file)
            try:
                with path.open("rb") as audio:
                    response = await self._client.post(
                        self.endpoint,
                        headers=self._headers(credentials),
                        files={"file": (path.name, audio, "audio/mp4")},
                        data={"language": language} if language else {},
                    )
            except httpx.RequestError as exc:
                if retries >= MAX_RETRIES:
                    raise BackendError("backend_network", f"transcribe request failed: {redact(exc)}") from exc
                await asyncio.sleep(_delay(retries))
                retries += 1
                continue
            except OSError as exc:
                raise BackendError("audio_unreadable", f"cannot read audio chunk: {exc}") from exc

            if len(response.content) > MAX_RESPONSE_BYTES:
                raise BackendError("response_too_large", "transcribe response exceeded 16 MiB")

            if response.status_code == 401:
                updated = load_credentials(self.auth_file)
                if not auth_retry_used and updated.access_token != credentials.access_token:
                    auth_retry_used = True
                    continue
                raise AuthenticationError("auth_rejected", "Codex login was rejected by transcription backend")

            if response.status_code == 413:
                raise PayloadTooLargeError()

            if response.status_code == 408 or response.status_code == 429 or 500 <= response.status_code < 600:
                if retries < MAX_RETRIES:
                    await asyncio.sleep(_delay(retries, response))
                    retries += 1
                    continue

            if not 200 <= response.status_code < 300:
                code = "auth_rejected" if response.status_code == 403 else "backend_http"
                detail = redact(response.text[:1000], credentials.access_token)
                message = f"HTTP {response.status_code}: {detail}"
                if response.status_code == 403:
                    raise AuthenticationError(code, message)
                raise BackendError(code, message, response.status_code)

            try:
                payload = response.json()
            except ValueError as exc:
                raise BackendError("backend_invalid_json", "transcribe response is not valid JSON") from exc
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str):
                raise BackendError("backend_missing_text", "transcribe response has no text string")
            return text
