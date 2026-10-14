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
_stored: dict[str, str] | None = None


def remember_stored_app(*, app_id: str, slug: str, private_key: str, webhook_secret: str) -> None:
    global _stored
    _stored = {
        "app_id": str(app_id),
        "slug": slug,
        "private_key": private_key,
        "webhook_secret": webhook_secret,
    }
    clear_token_cache()


def clear_stored_app() -> None:
    global _stored
    _stored = None
    clear_token_cache()


def load_stored_app(db) -> None:
    from app.models import GithubAppCredential

    row = db.query(GithubAppCredential).order_by(GithubAppCredential.created_at.desc()).first()
    if row is None:
        clear_stored_app()
        return
    remember_stored_app(
        app_id=row.app_id,
        slug=row.slug,
        private_key=row.private_key,
        webhook_secret=row.webhook_secret,
    )


def resolved_credentials() -> tuple[str | None, str | None, str | None, str | None]:
    """App id, private key, slug, and webhook secret. A saved manifest wins over the environment."""
    if _stored is not None:
        return _stored.get("app_id"), _stored.get("private_key"), _stored.get("slug"), _stored.get("webhook_secret")
    settings = get_settings()
    key = _env_private_key(settings)
    return settings.github_app_id, key, settings.github_app_slug, None


def _env_private_key(settings) -> str | None:
    if settings.github_app_private_key:
        return settings.github_app_private_key.replace("\\n", "\n")
    if settings.github_app_private_key_path:
        path = Path(settings.github_app_private_key_path)
        if path.is_file():
            return path.read_text()
    return None


def private_key() -> str | None:
    return resolved_credentials()[1]


def resolved_app_id() -> str | None:
    return resolved_credentials()[0]


def app_slug() -> str | None:
    slug = resolved_credentials()[2]
    if isinstance(slug, str) and slug.replace("-", "").isalnum():
        return slug
    return None


def webhook_secret() -> str:
    stored = resolved_credentials()[3]
    if stored:
        return stored
    return get_settings().github_webhook_secret


def credential_source() -> str:
    if _stored is not None and _stored.get("private_key") and _stored.get("app_id"):
        return "saved"
    if get_settings().github_app_id and _env_private_key(get_settings()):
        return "environment"
    return "none"


def app_configured() -> bool:
    return bool(resolved_app_id() and private_key())


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
    identifier = resolved_app_id()
    if not identifier or not key:
        raise GithubClientError("GitHub App credentials are not configured")
    app_jwt = build_app_jwt(identifier, key)
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
