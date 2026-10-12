"""GitHub App authentication.

Installation tokens are minted in process memory. They are not written to the
database, returned by the HTTP API, or placed in review prompts.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

import jwt

from app.config import get_settings
from app.services.github_client import GithubClientError, HttpGithubClient

_lock = threading.Lock()
_tokens: dict[int, tuple[str, float]] = {}


def private_key() -> str | None:
    settings = get_settings()
    if settings.github_app_private_key:
        return settings.github_app_private_key.replace("\\n", "\n")
    if settings.github_app_private_key_path:
        path = Path(settings.github_app_private_key_path)
        if not path.is_file():
            return None
        return path.read_text()
    return None


def app_configured() -> bool:
    settings = get_settings()
    return bool(settings.github_app_id and private_key())


def build_app_jwt(app_id: str, key: str, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    try:
        token = jwt.encode(
            {"iat": issued - 60, "exp": issued + 540, "iss": str(app_id)},
            key,
            algorithm="RS256",
        )
    except Exception as exc:
        raise GithubClientError("App private key could not sign a JWT") from exc
    if isinstance(token, bytes):
        return token.decode()
    return token


def clear_token_cache() -> None:
    with _lock:
        _tokens.clear()


def installation_token(installation_id: int, *, transport=None, sleeper=None, now: float | None = None) -> str:
    clock = time.time() if now is None else now
    with _lock:
        cached = _tokens.get(installation_id)
        if cached and cached[1] > clock + 30:
            return cached[0]
    settings = get_settings()
    key = private_key()
    if not settings.github_app_id or not key:
        raise GithubClientError("GitHub App credentials are not configured")
    app_jwt = build_app_jwt(settings.github_app_id, key)
    client = HttpGithubClient(
        app_jwt,
        base_url=settings.github_api_base,
        transport=transport,
        sleeper=sleeper or time.sleep,
    )
    try:
        payload = client.create_installation_token(installation_id)
    finally:
        client.close()
    token = payload["token"]
    if not token.startswith("ghs_") or len(token) < 12:
        raise GithubClientError("Installation token response was rejected")
    expires = _expiry(payload.get("expires_at"), clock)
    with _lock:
        _tokens[installation_id] = (token, expires)
    return token


def github_client_for_installation(installation_id: int, *, transport=None, sleeper=None) -> HttpGithubClient:
    token = installation_token(installation_id, transport=transport, sleeper=sleeper)
    settings = get_settings()
    return HttpGithubClient(
        token,
        base_url=settings.github_api_base,
        transport=transport,
        sleeper=sleeper or time.sleep,
    )


def _expiry(value, now: float) -> float:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp()
        except ValueError:
            return now + 50 * 60
    return now + 50 * 60
