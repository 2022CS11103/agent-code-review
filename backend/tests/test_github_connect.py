"""GitHub App manifest and repository sync. The GitHub API is mocked."""

import hashlib
import hmac
import json

import httpx
import pytest
from app.config import get_settings
from app.db import SessionLocal
from app.models import GithubAppCredential, PullRequest, Repository, ReviewJob
from app.services.github_app import clear_stored_app, remember_stored_app
from app.services.github_client import GithubClientError
from app.services.github_connect import accept_manifest, connect_view, sync_installations
from app.services.seed import seed_demo
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HEAD = "c" * 40
BASE = "d" * 40
SECRET = "manifest-webhook-secret-value"


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_connect_view_builds_a_user_app_manifest_without_a_localhost_webhook():
    view = connect_view("https://evil.example")
    assert view["form_action"] == "https://github.com/settings/apps/new"
    assert "evil.example" not in view["manifest"]
    manifest = json.loads(view["manifest"])
    assert manifest["default_permissions"] == {"contents": "read", "pull_requests": "write", "checks": "write"}
    assert manifest["redirect_url"].startswith("http://127.0.0.1:43123/api/v1/github/manifest/callback?return_to=")
    assert "hook_attributes" not in manifest
    assert view["webhook_url"] is None
    assert "PRIVATE KEY" not in json.dumps(view)


def test_public_https_origin_is_the_only_webhook_target(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "http://127.0.0.1:8741")
    assert connect_view("http://127.0.0.1:43123")["webhook_url"] is None
    monkeypatch.setattr(settings, "public_base_url", "https://review.example.com")
    view = connect_view("http://127.0.0.1:43123")
    assert view["webhook_url"] == "https://review.example.com/api/v1/webhooks/github"
    assert json.loads(view["manifest"])["hook_attributes"]["url"] == view["webhook_url"]


def test_manifest_callback_stores_the_app_and_hides_the_key(client, session, monkeypatch):
    seed_demo(session, SessionLocal, process_history=False)
    pem = _pem()

    def exchange(code: str, transport=None) -> dict:
        assert code == "manifest-code"
        assert transport is None
        return {"id": 4242, "slug": "prism-review", "pem": pem, "webhook_secret": SECRET}

    monkeypatch.setattr("app.services.github_connect._exchange", exchange)
    response = client.get(
        "/api/v1/github/manifest/callback?code=manifest-code&return_to=http://127.0.0.1:43123",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "http://127.0.0.1:43123/github?github=connected"
    stored = session.query(GithubAppCredential).one()
    assert stored.app_id == "4242"
    assert stored.slug == "prism-review"
    settings_view = client.get("/api/v1/settings")
    assert settings_view.status_code == 200
    text = settings_view.text
    assert "BEGIN PRIVATE KEY" not in text
    assert SECRET not in text
    assert settings_view.json()["github"]["install_url"] == "https://github.com/apps/prism-review/installations/new"
    assert settings_view.json()["github"]["credential_source"] == "saved"
    clear_stored_app()


def test_bad_manifest_code_redirects_without_storing(client, session):
    response = client.get(
        "/api/v1/github/manifest/callback?code=short&return_to=https://evil.example",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "http://127.0.0.1:43123/github?github=failed"
    assert session.query(GithubAppCredential).count() == 0


def test_saved_webhook_secret_replaces_the_dev_default(client, session):
    pem = _pem()
    remember_stored_app(app_id="7", slug="prism-review", private_key=pem, webhook_secret=SECRET)
    session.add(GithubAppCredential(app_id="7", slug="prism-review", private_key=pem, webhook_secret=SECRET))
    session.commit()
    body = b'{"zen":"pong"}'
    signed = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    ok = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": f"sha256={signed}",
            "X-GitHub-Delivery": "delivery-manifest",
            "X-GitHub-Event": "ping",
            "Content-Type": "application/json",
        },
    )
    assert ok.status_code == 202
    old = hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    rejected = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": f"sha256={old}",
            "X-GitHub-Delivery": "delivery-old-secret",
            "X-GitHub-Event": "ping",
            "Content-Type": "application/json",
        },
    )
    assert rejected.status_code == 401
    clear_stored_app()


def test_sync_imports_installations_and_queues_open_pulls(session):
    seed_demo(session, SessionLocal, process_history=False)
    pem = _pem()
    remember_stored_app(app_id="12345", slug="prism-review", private_key=pem, webhook_secret=SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/app/installations":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 99,
                        "account": {"login": "octo"},
                        "permissions": {
                            "contents": "read",
                            "pull_requests": "write",
                            "checks": "write",
                            "metadata": "read",
                        },
                    }
                ],
            )
        if path == "/app/installations/99/repositories":
            return httpx.Response(200, json={"total_count": 1, "repositories": [{"full_name": "octo/widget"}]})
        if path == "/app/installations/99/access_tokens":
            return httpx.Response(201, json={"token": "ghs_mockedinstallationtoken", "expires_at": "2099-01-01T00:00:00Z"})
        if path == "/repos/octo/widget/pulls":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 3,
                        "title": "Widget",
                        "html_url": "https://github.com/octo/widget/pull/3",
                        "user": {"login": "octo"},
                        "head": {"sha": HEAD, "ref": "feature"},
                        "base": {"sha": BASE, "ref": "main"},
                    }
                ],
            )
        return httpx.Response(404, json={"message": "missing"})

    result = sync_installations(session, transport=httpx.MockTransport(handler), sleeper=lambda _seconds: None)
    assert result["installations"] == 1
    assert result["repositories"] == 1
    assert result["pulls_queued"] == 1
    assert "ghs_" not in json.dumps(result)
    repo = session.query(Repository).filter(Repository.name == "widget").one()
    assert repo.owner == "octo"
    assert repo.trust_tier == "untrusted"
    assert repo.provider == "github"
    pull = session.query(PullRequest).filter(PullRequest.repository_id == repo.id).one()
    assert pull.number == 3
    assert pull.head_sha == HEAD
    job = session.query(ReviewJob).filter(ReviewJob.pull_request_id == pull.id).one()
    assert job.status == "queued"
    assert job.origin == "webhook"
    clear_stored_app()


def test_manifest_exchange_rejects_a_bad_code_without_calling_github(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network")

    monkeypatch.setattr(httpx, "Client", boom)
    with pytest.raises(GithubClientError):
        accept_manifest(None, "bad")
