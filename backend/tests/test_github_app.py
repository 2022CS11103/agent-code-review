"""GitHub App flow with a mocked API. No live GitHub call is made."""

import hashlib
import hmac
import json

import httpx
import jwt
import pytest
from app.config import get_settings
from app.db import SessionLocal
from app.models import Finding, PullRequest, Repository, ReviewJob, ReviewPolicy
from app.services.github_app import clear_token_cache, installation_token
from app.services.github_client import GithubClientError, GithubRateLimit, HttpGithubClient
from app.services.github_ingest import commentable_new_lines, plan_review_comments
from app.services.pipeline import process_job
from app.services.seed import seed_demo
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HEAD = "a" * 40
BASE = "b" * 40
SOURCE = "def record(db, name):\n    db.execute(f\"SELECT status FROM invoices WHERE id = '{name}'\")\n"
PATCH = (
    "@@ -0,0 +1,2 @@\n"
    "+def record(db, name):\n"
    "+    db.execute(f\"SELECT status FROM invoices WHERE id = '{name}'\")\n"
)
PERMISSIONS = {"contents": "read", "pull_requests": "write", "checks": "write"}


def _pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public


def _sign(raw: bytes) -> dict[str, str]:
    digest = hmac.new(b"test-webhook-secret", raw, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Delivery": "delivery-fixed",
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }


def test_comment_lines_follow_the_new_file_side_of_the_diff():
    lines = commentable_new_lines(PATCH)
    assert lines == {1, 2}
    assert commentable_new_lines("@@ -4,1 +4,1 @@\n-old\n+new\n") == {4}


class _Finding:
    def __init__(self, line: int):
        self.status = "open"
        self.severity = "high"
        self.file_path = "src/pay.py"
        self.start_line = line
        self.title = "SQL injection"
        self.claim_level = "pattern"
        self.explanation = "The query interpolates name."


def test_off_diff_finding_stays_in_the_summary():
    comments, omitted = plan_review_comments([_Finding(50)], {"src/pay.py": PATCH}, "high")
    assert comments == []
    assert "src/pay.py:50" in omitted[0]
    anchored, skipped = plan_review_comments([_Finding(2)], {"src/pay.py": PATCH}, "high")
    assert skipped == []
    assert anchored[0]["line"] == 2
    assert anchored[0]["side"] == "RIGHT"
    assert "merge" in anchored[0]["body"]


def test_client_retries_rate_limits_and_refuses_foreign_pagination():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"message": "slow down"})
        return httpx.Response(200, json={"number": 7})

    client = HttpGithubClient("ghs_mockedinstallationtoken", transport=httpx.MockTransport(handler), sleeper=lambda _seconds: None)
    assert client.get_pull("octo", "widget", 7)["number"] == 7
    assert calls["n"] == 2

    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"link": '<http://169.254.169.254/latest>; rel="next"'})

    hostile = HttpGithubClient("ghs_mockedinstallationtoken", transport=httpx.MockTransport(blocked), sleeper=lambda _seconds: None)
    with pytest.raises(GithubClientError):
        hostile.get_files("octo", "widget", 7)


def test_app_jwt_exchanges_for_an_installation_token_without_leaking_it(monkeypatch):
    private, public = _pem()
    settings = get_settings()
    monkeypatch.setattr(settings, "github_app_id", "12345")
    monkeypatch.setattr(settings, "github_app_private_key", private)
    clear_token_cache()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        seen["path"] = request.url.path
        return httpx.Response(201, json={"token": "ghs_mockedinstallationtoken", "expires_at": "2099-01-01T00:00:00Z"})

    token = installation_token(42, transport=httpx.MockTransport(handler), sleeper=lambda _seconds: None)
    assert token == "ghs_mockedinstallationtoken"
    assert seen["path"] == "/app/installations/42/access_tokens"
    bearer = seen["authorization"].removeprefix("Bearer ")
    decoded = jwt.decode(bearer, public, algorithms=["RS256"], options={"verify_aud": False})
    assert decoded["iss"] == "12345"
    assert "ghs_" not in bearer
    clear_token_cache()


def test_installation_and_pull_request_are_fetched_reviewed_and_published(client, session, monkeypatch):
    private, _public = _pem()
    settings = get_settings()
    monkeypatch.setattr(settings, "github_app_id", "12345")
    monkeypatch.setattr(settings, "github_app_private_key", private)
    monkeypatch.setattr(settings, "github_app_slug", "prism-review")
    seed_demo(session, SessionLocal, process_history=False)
    clear_token_cache()
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_mockedinstallationtoken", "expires_at": "2099-01-01T00:00:00Z"})
        if path == "/repos/octo/widget/pulls/7":
            assert request.headers["authorization"] == "Bearer ghs_mockedinstallationtoken"
            return httpx.Response(
                200,
                json={
                    "number": 7,
                    "title": "Charge retries",
                    "body": "review this",
                    "user": {"login": "octocat"},
                    "head": {"sha": HEAD, "ref": "retries"},
                    "base": {"sha": BASE, "ref": "main"},
                },
            )
        if path == "/repos/octo/widget/pulls/7/files":
            return httpx.Response(
                200,
                json=[{"filename": "src/pay.py", "status": "modified", "additions": 1, "deletions": 1, "patch": PATCH}],
            )
        if path == "/repos/octo/widget/contents/src/pay.py":
            text = SOURCE if request.url.params["ref"] == HEAD else "def record(db, name):\n    return name\n"
            return httpx.Response(200, text=text)
        if path == "/repos/octo/widget/check-runs":
            body = json.loads(request.content)
            assert body["conclusion"] == "action_required"
            assert body["head_sha"] == HEAD
            assert "ghs_" not in request.content.decode()
            return httpx.Response(201, json={"id": 1})
        if path == "/repos/octo/widget/pulls/7/reviews":
            body = json.loads(request.content)
            assert body["event"] == "COMMENT"
            assert body["comments"][0]["line"] == 2
            assert body["comments"][0]["path"] == "src/pay.py"
            return httpx.Response(200, json={"id": 9})
        raise AssertionError(path)

    transport = httpx.MockTransport(handler)

    def factory(installation_id: int, **_kwargs):
        assert installation_id == 42
        return HttpGithubClient("ghs_mockedinstallationtoken", transport=transport, sleeper=lambda _seconds: None)

    monkeypatch.setattr("app.services.github_ingest.github_client_for_installation", factory)
    monkeypatch.setattr("app.services.github_app.github_client_for_installation", factory)

    def post(event: str, payload: dict, delivery: str):
        raw = json.dumps(payload).encode()
        headers = _sign(raw)
        headers["X-GitHub-Event"] = event
        headers["X-GitHub-Delivery"] = delivery
        response = client.post("/api/v1/webhooks/github", content=raw, headers=headers)
        assert response.status_code == 202, response.text
        return response

    installed = post(
        "installation",
        {
            "action": "created",
            "installation": {"id": 42, "account": {"login": "octo"}, "permissions": PERMISSIONS},
            "repositories": [{"full_name": "octo/widget"}],
        },
        "inst-1",
    )
    assert installed.json()["outcome"] == "installation_recorded"
    opened = post(
        "pull_request",
        {
            "action": "opened",
            "installation": {"id": 42},
            "repository": {"full_name": "octo/widget"},
            "pull_request": {
                "number": 7,
                "title": "Charge retries",
                "user": {"login": "octocat"},
                "head": {"sha": HEAD, "ref": "retries"},
                "base": {"sha": BASE, "ref": "main"},
            },
        },
        "pr-1",
    )
    assert opened.json()["outcome"] == "queued"
    again = post(
        "pull_request",
        {
            "action": "opened",
            "installation": {"id": 42},
            "repository": {"full_name": "octo/widget"},
            "pull_request": {"number": 7, "title": "Charge retries", "user": {"login": "octocat"}, "head": {"sha": HEAD, "ref": "retries"}, "base": {"sha": BASE, "ref": "main"}},
        },
        "pr-1",
    )
    assert again.json()["duplicate"] is True
    job_id = opened.json()["job_id"]
    process_job(job_id, SessionLocal)
    session.expire_all()
    job = session.get(ReviewJob, job_id)
    assert job.status == "completed", job.error
    finding = session.query(Finding).filter(Finding.job_id == job.id, Finding.rule_id == "sql_injection").one()
    assert finding.start_line == 2
    assert finding.claim_level == "pattern"
    pull = session.query(PullRequest).filter(PullRequest.number == 7).one()
    assert pull.head_sha == HEAD
    repo = session.get(Repository, pull.repository_id)
    assert repo.trust_tier == "untrusted"
    assert repo.connection_status == "installed"
    detail = client.get(f"/api/v1/reviews/{job.id}")
    assert detail.status_code == 200
    assert any(item["rule_id"] == "sql_injection" for item in detail.json()["findings"])
    policy = session.query(ReviewPolicy).filter_by(repository_id=repo.id).one()
    policy.post_github_comments = True
    session.commit()
    published = client.post(f"/api/v1/reviews/{job.id}/report")
    assert published.status_code == 200, published.text
    assert published.json()["github_called"] is True
    assert published.json()["delivery_status"] == "delivered_check_and_summary"
    assert "ghs_" not in published.json()["body"]
    posted = [path for _method, path in calls]
    assert "/repos/octo/widget/check-runs" in posted
    assert "/repos/octo/widget/pulls/7/reviews" in posted
    assert not any("merge" in path for path in posted)
    settings_view = client.get("/api/v1/settings")
    text = settings_view.text
    assert "BEGIN PRIVATE KEY" not in text
    assert "ghs_" not in text
    assert settings_view.json()["github"]["install_url"] == "https://github.com/apps/prism-review/installations/new"
    assert settings_view.json()["github"]["note"]
    clear_token_cache()


def test_incomplete_permissions_do_not_enqueue_a_review(client, session):
    seed_demo(session, SessionLocal, process_history=False)

    def post(event: str, payload: dict, delivery: str):
        raw = json.dumps(payload).encode()
        headers = _sign(raw)
        headers["X-GitHub-Event"] = event
        headers["X-GitHub-Delivery"] = delivery
        return client.post("/api/v1/webhooks/github", content=raw, headers=headers)

    installed = post(
        "installation",
        {
            "action": "created",
            "installation": {"id": 99, "account": {"login": "octo"}, "permissions": {"contents": "read", "metadata": "read"}},
            "repositories": [{"full_name": "octo/locked"}],
        },
        "inst-locked",
    )
    assert installed.json()["outcome"] == "installation_permissions_incomplete"
    opened = post(
        "pull_request",
        {
            "action": "synchronize",
            "installation": {"id": 99},
            "repository": {"full_name": "octo/locked"},
            "pull_request": {"number": 3, "head": {"sha": HEAD}, "base": {"sha": BASE}},
        },
        "pr-locked",
    )
    assert opened.json()["outcome"] == "installation_not_ready"
    assert opened.json()["job_id"] is None


def test_publish_without_app_credentials_does_not_call_github(client, session, monkeypatch):
    seed_demo(session, SessionLocal, process_history=False)
    settings = get_settings()
    monkeypatch.setattr(settings, "github_app_id", None)
    monkeypatch.setattr(settings, "github_app_private_key", None)

    def forbid(*_args, **_kwargs):
        raise AssertionError("GitHub was called")

    monkeypatch.setattr("app.services.github_app.github_client_for_installation", forbid)
    raw = json.dumps(
        {
            "action": "created",
            "installation": {"id": 7, "account": {"login": "octo"}, "permissions": PERMISSIONS},
            "repositories": [{"full_name": "octo/plain"}],
        }
    ).encode()
    headers = _sign(raw)
    headers["X-GitHub-Event"] = "installation"
    headers["X-GitHub-Delivery"] = "plain-inst"
    assert client.post("/api/v1/webhooks/github", content=raw, headers=headers).json()["outcome"] == "installation_recorded"
    pull_raw = json.dumps(
        {
            "action": "opened",
            "installation": {"id": 7},
            "repository": {"full_name": "octo/plain"},
            "pull_request": {"number": 4, "title": "Plain", "user": {"login": "octocat"}, "head": {"sha": HEAD, "ref": "plain"}, "base": {"sha": BASE, "ref": "main"}},
        }
    ).encode()
    pull_headers = _sign(pull_raw)
    pull_headers["X-GitHub-Delivery"] = "plain-pr"
    pull_headers["X-GitHub-Event"] = "pull_request"
    created = client.post("/api/v1/webhooks/github", content=pull_raw, headers=pull_headers)
    assert created.json()["outcome"] == "queued"
    # The worker would try to fetch. Publish is the credential gate under test, so finish the job locally.
    job = session.get(ReviewJob, created.json()["job_id"])
    job.status = "completed"
    job.stage = "ready"
    session.commit()
    repo = session.query(Repository).filter(Repository.name == "plain").one()
    policy = session.query(ReviewPolicy).filter_by(repository_id=repo.id).one()
    policy.post_github_comments = True
    session.commit()
    published = client.post(f"/api/v1/reviews/{job.id}/report")
    assert published.status_code == 200
    assert published.json()["github_called"] is False
    assert published.json()["delivery_status"] == "not_delivered_app_not_configured"


def test_exhausted_rate_limit_is_distinct():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "30"}, json={"message": "slow down"})

    client = HttpGithubClient("ghs_mockedinstallationtoken", transport=httpx.MockTransport(handler), sleeper=lambda _seconds: None, max_retry_wait=2)
    with pytest.raises(GithubRateLimit):
        client.get_pull("octo", "widget", 1)
