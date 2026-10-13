from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from .errors import AuthenticationError
from .models import Credentials


SUPPORTED_AUTH_MODES = {"chatgpt", "chatgpt_auth_tokens"}


def default_auth_file() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def resolve_auth_file(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser()
    from_env = os.environ.get("CODEX_TRANSCRIBE_AUTH_FILE")
    if from_env:
        return Path(from_env).expanduser()
    return default_auth_file()


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        value = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _account_id_from_payload(payload: dict[str, Any]) -> str | None:
    auth_claim = payload.get("https://api.openai.com/auth")
    if not isinstance(auth_claim, dict):
        return None
    account_id = auth_claim.get("chatgpt_account_id")
    if isinstance(account_id, str) and account_id.strip():
        return account_id.strip()
    return None


def load_credentials(auth_file: Path | None = None) -> Credentials:
    path = resolve_auth_file(auth_file).resolve()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthenticationError("auth_unreadable", f"cannot read Codex auth file {path}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthenticationError("auth_invalid_json", f"invalid JSON in Codex auth file {path}") from exc

    if not isinstance(parsed, dict):
        raise AuthenticationError("auth_invalid_schema", f"Codex auth file {path} is not a JSON object")

    mode = parsed.get("auth_mode", parsed.get("authMode"))
    if mode is not None and mode not in SUPPORTED_AUTH_MODES:
        raise AuthenticationError("auth_unsupported_mode", f"Codex auth mode {mode!r} is not ChatGPT token auth")

    tokens = parsed.get("tokens")
    if not isinstance(tokens, dict):
        raise AuthenticationError("auth_missing_tokens", f"Codex auth file {path} does not contain tokens")

    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AuthenticationError("auth_missing_token", f"Codex auth file {path} does not contain an access token")
    access_token = access_token.strip()

    payload = _decode_jwt_payload(access_token)
    expires_at = payload.get("exp")
    if isinstance(expires_at, (int, float)) and expires_at <= time.time():
        raise AuthenticationError("auth_expired", "Codex OAuth token has expired; sign in to Codex again")

    account_id = tokens.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        account_id = _account_id_from_payload(payload)
    if not account_id:
        raise AuthenticationError("auth_missing_account", "Codex access token does not contain a ChatGPT account id")

    return Credentials(access_token=access_token, account_id=account_id.strip(), auth_file=path)

