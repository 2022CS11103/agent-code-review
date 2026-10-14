"""Create a GitHub App from the browser and import the repositories it can see.

The manifest exchange runs on the API. The private key and webhook secret are
stored in the database and are not returned to the browser. Sync uses the App
JWT and installation tokens. It does not merge, and it does not clone.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GithubAppCredential, GithubInstallation, Repository
from app.services.actions import audit
from app.services.github_app import (
    app_configured,
    app_slug,
    build_app_jwt,
    credential_source,
    github_client_for_installation,
    private_key,
    remember_stored_app,
    resolved_app_id,
)
from app.services.github_client import GithubClientError, HttpGithubClient
from app.services.github_ingest import enqueue_pull_request, handle_event

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9-]{1,80}$")
_MAX_INSTALLATIONS = 20


def connect_view(browser_origin: str | None) -> dict:
    settings = get_settings()
    origin = _browser_origin(browser_origin, settings)
    webhook = _public_webhook_url(settings.public_base_url)
    redirect = f"{origin}/api/v1/github/manifest/callback?return_to={quote(origin, safe='')}"
    manifest = {
        "name": "PRISM",
        "url": "https://github.com/2022CS11103/agent-code-review",
        "public": False,
        "redirect_url": redirect,
        "default_permissions": {"contents": "read", "pull_requests": "write", "checks": "write"},
        "default_events": ["pull_request", "installation", "installation_repositories"],
        "request_oauth_on_install": False,
    }
    if webhook:
        manifest["hook_attributes"] = {"url": webhook, "active": True}
    slug = app_slug() if app_configured() else None
    return {
        "app_configured": app_configured(),
        "credential_source": credential_source(),
        "install_url": f"https://github.com/apps/{slug}/installations/new" if slug else None,
        "form_action": "https://github.com/settings/apps/new",
        "manifest": json.dumps(manifest, separators=(",", ":")),
        "webhook_url": webhook,
        "delivery": "webhook_and_sync" if webhook else "sync",
        "note": (
            "Add GitHub opens GitHub so you can create the PRISM app on your account. "
            "Install it on the repositories you choose, then Sync. "
            "PRISM reads pull requests and can post a comment or a check. It does not merge. "
            "Untrusted repository code is not executed. "
            "Set PRISM_PUBLIC_BASE_URL to a public https origin before GitHub can deliver webhooks. "
            "Sync works without that origin."
        ),
    }


def accept_manifest(db: Session, code: str, *, transport=None) -> str:
    payload = _exchange(code, transport=transport)
    app_id = payload.get("id")
    slug = payload.get("slug")
    pem = payload.get("pem")
    secret = payload.get("webhook_secret")
    if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
        raise GithubClientError("Manifest response was rejected")
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise GithubClientError("Manifest response was rejected")
    if not isinstance(pem, str) or "BEGIN" not in pem or "PRIVATE KEY" not in pem or len(pem) > 20_000:
        raise GithubClientError("Manifest response was rejected")
    if not isinstance(secret, str) or not 8 <= len(secret) <= 200:
        raise GithubClientError("Manifest response was rejected")
    build_app_jwt(str(app_id), pem)
    db.query(GithubAppCredential).delete()
    db.add(GithubAppCredential(app_id=str(app_id), slug=slug, private_key=pem, webhook_secret=secret))
    audit(db, None, None, "github_app_connected", "github_app", slug, {})
    db.commit()
    remember_stored_app(app_id=str(app_id), slug=slug, private_key=pem, webhook_secret=secret)
    return slug


def browser_return(return_to: str | None) -> str:
    return _browser_origin(return_to, get_settings())


def sync_installations(db: Session, *, transport=None, sleeper=None) -> dict:
    if not app_configured():
        raise GithubClientError("GitHub App credentials are not configured")
    identifier = resolved_app_id()
    key = private_key()
    if not identifier or not key:
        raise GithubClientError("GitHub App credentials are not configured")
    settings = get_settings()
    app_client = HttpGithubClient(
        build_app_jwt(identifier, key),
        base_url=settings.github_api_base,
        transport=transport,
        sleeper=sleeper or time.sleep,
    )
    installations = 0
    repositories = 0
    queued = 0
    skipped: list[str] = []
    truncated = False
    try:
        listed = app_client.list_installations()
        if len(listed) > _MAX_INSTALLATIONS:
            truncated = True
            listed = listed[:_MAX_INSTALLATIONS]
        for item in listed:
            if not isinstance(item, dict) or isinstance(item.get("id"), bool) or not isinstance(item.get("id"), int):
                skipped.append("installation")
                continue
            inst_id = item["id"]
            login = ((item.get("account") or {}) if isinstance(item.get("account"), dict) else {}).get("login")
            label = login if isinstance(login, str) else str(inst_id)
            _job, _workspace_id, outcome = handle_event(
                db,
                "installation",
                {"action": "created", "installation": item, "repositories": []},
            )
            if outcome not in {"installation_recorded", "installation_permissions_incomplete"}:
                skipped.append(label)
                continue
            installations += 1
            repos, repo_truncated = app_client.list_installation_repositories(inst_id)
            truncated = truncated or repo_truncated
            handle_event(
                db,
                "installation_repositories",
                {
                    "action": "added",
                    "installation": item,
                    "repositories_added": repos,
                },
            )
            row = db.query(GithubInstallation).filter(GithubInstallation.installation_id == inst_id).one_or_none()
            if row is None or row.status != "active":
                skipped.append(label)
                continue
            for repo_item in repos:
                if not isinstance(repo_item, dict):
                    continue
                full = repo_item.get("full_name")
                if not isinstance(full, str) or full.count("/") != 1:
                    continue
                owner, name = full.split("/", 1)
                stored = (
                    db.query(Repository)
                    .filter(Repository.workspace_id == row.workspace_id, Repository.owner == owner, Repository.name == name)
                    .one_or_none()
                )
                if stored is None or stored.trust_tier == "demo_fixture" or stored.provider != "github":
                    continue
                repositories += 1
                install_client = github_client_for_installation(inst_id, transport=transport, sleeper=sleeper)
                try:
                    pulls = install_client.list_open_pulls(owner, name)
                finally:
                    install_client.close()
                for pull in pulls:
                    if not isinstance(pull, dict):
                        continue
                    job_id, _workspace, pull_outcome = enqueue_pull_request(
                        db,
                        {
                            "action": "opened",
                            "installation": {"id": inst_id},
                            "repository": {"full_name": full},
                            "pull_request": pull,
                        },
                    )
                    if pull_outcome == "queued" and job_id:
                        queued += 1
        db.commit()
    finally:
        app_client.close()
    return {
        "installations": installations,
        "repositories": repositories,
        "pulls_queued": queued,
        "truncated": truncated,
        "skipped": skipped[:20],
    }


def _exchange(code: str, *, transport=None) -> dict:
    if not isinstance(code, str) or not _CODE_RE.match(code):
        raise GithubClientError("Manifest code was rejected")
    with httpx.Client(transport=transport, timeout=20.0, follow_redirects=False) as client:
        response = client.post(
            f"https://api.github.com/app-manifests/{code}/conversions",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "prism-ai",
            },
        )
    if response.status_code != 201:
        raise GithubClientError("GitHub did not accept the manifest code")
    payload = response.json()
    if not isinstance(payload, dict):
        raise GithubClientError("Manifest response was rejected")
    return payload


def _browser_origin(value: str | None, settings) -> str:
    candidate = (value or "").strip().rstrip("/")
    if candidate and candidate in settings.origin_allowlist:
        return candidate
    for item in settings.allowed_origins.split(","):
        stripped = item.strip().rstrip("/")
        if stripped:
            return stripped
    return "http://127.0.0.1:43123"


def _public_webhook_url(value: str) -> str | None:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlparse(raw)
    host = parsed.hostname
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return None
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return None
    return raw + "/api/v1/webhooks/github"
