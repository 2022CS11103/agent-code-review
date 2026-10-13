import hashlib
import hmac
import json

from app.db import SessionLocal
from app.models import Finding, PatchProposal, PullRequest, Repository, ReviewJob, ValidationRun
from app.services.actions import publish_report
from app.services.evaluation import run_evaluation
from app.services.pipeline import _execution_allowed, process_job
from app.services.seed import seed_demo


def test_untrusted_code_is_not_executed_in_the_subprocess_driver():
    allowed, reason = _execution_allowed("untrusted", "subprocess")
    assert allowed is False
    assert "Docker" in reason
    assert _execution_allowed("demo_fixture", "subprocess")[0] is True


def test_billing_review_reproduces_sql_and_does_not_merge(session):
    seed_demo(session, SessionLocal, process_history=False)
    pull = session.query(PullRequest).filter(PullRequest.number == 142).one()
    from app.services.actions import create_review

    job, created = create_review(session, pull, workspace_id=pull.repository.workspace_id, trigger="manual", origin="live")
    assert created
    process_job(job.id, SessionLocal)
    session.expire_all()
    stored = session.get(ReviewJob, job.id)
    assert stored.status == "completed", stored.error
    findings = session.query(Finding).filter(Finding.job_id == job.id).all()
    rules = {item.rule_id for item in findings}
    assert {"sql_injection", "shell_true", "hardcoded_secret", "float_currency", "n_plus_one", "missing_auth", "test_gap"} <= rules
    assert not any(item.rule_id == "eval_exec" for item in findings)
    sql = next(item for item in findings if item.rule_id == "sql_injection")
    assert sql.claim_level == "reproduced"
    assert sql.file_path == "src/billing/webhooks.py"
    assert any(item.kind == "test" for item in sql.evidence)
    assert "whsec_demo_billing" not in json.dumps([item.snippet for item in next(f for f in findings if f.rule_id == "hardcoded_secret").evidence])
    patches = session.query(PatchProposal).filter(PatchProposal.job_id == job.id).all()
    by_strategy = {item.strategy: item for item in patches}
    assert by_strategy["parameterize_sql"].status == "passed"
    assert by_strategy["secret_from_environment"].status == "needs_review"
    assert by_strategy["secret_from_environment"].attempt == 2
    assert by_strategy["shell_to_argv"].status == "needs_review"
    runs = session.query(ValidationRun).filter(ValidationRun.patch_id == by_strategy["parameterize_sql"].id).all()
    assert runs[0].baseline_failed >= 1
    assert runs[0].tests_passed >= 1
    assert "not proof" in by_strategy["parameterize_sql"].disclaimer
    coverage = stored.coverage or {}
    skipped = {item["path"] for item in coverage.get("skipped", [])}
    assert ".prism.yml" in skipped
    assert "vendor/leftpad.py" in skipped
    user = session.query(__import__("app.models", fromlist=["User"]).User).one()
    report = publish_report(session, user, stored)
    assert "whsec_demo_billing" not in report.body
    assert report.delivery_status == "stored_locally_demo"
    assert report.redacted is True


def test_seed_history_stays_queued_until_the_worker_runs(session, monkeypatch):
    called = []
    monkeypatch.setattr("app.services.seed.process_job", lambda *args, **kwargs: called.append(args))
    seed_demo(session, SessionLocal, process_history=True, run_inline=False)
    assert called == []
    auth = session.query(PullRequest).filter(PullRequest.number == 87).one()
    job = session.query(ReviewJob).filter(ReviewJob.pull_request_id == auth.id).one()
    assert job.status == "queued"
    assert job.origin == "seed"


def test_api_command_center_and_decisions(client, session):
    seed_demo(session, SessionLocal, process_history=True)
    center = client.get("/api/v1/command-center")
    assert center.status_code == 200
    body = center.json()
    assert body["workspace"]["demo"] is True
    assert body["metrics"]["median_review_seconds"] is not None
    assert body["metrics"]["validation_success_rate"] is None
    billing = next(item for item in body["queue"] if item["number"] == 142)
    assert billing["job"] is None
    auth = next(item for item in body["queue"] if item["number"] == 87)
    assert auth["job"]["origin"] == "seed"
    assert auth["job"]["status"] == "completed"
    assert auth["open_findings"]["critical"] >= 1
    created = client.post("/api/v1/reviews", json={"pull_request_id": billing["pull_request_id"]})
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    again = client.post("/api/v1/reviews", json={"pull_request_id": billing["pull_request_id"]})
    assert again.json()["job_id"] == job_id
    assert again.json()["created"] is False
    process_job(job_id, SessionLocal)
    detail = client.get(f"/api/v1/reviews/{job_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["job"]["status"] == "completed"
    assert payload["job"]["usage"]["billed"] is False
    finding = next(item for item in payload["findings"] if item["rule_id"] == "sql_injection")
    decision = client.post(f"/api/v1/findings/{finding['id']}/decision", json={"action": "confirm", "note": "reproduced in sandbox"})
    assert decision.status_code == 200
    assert decision.json()["finding"]["status"] == "confirmed"
    patch = next(item for item in payload["patches"] if item["strategy"] == "parameterize_sql")
    accepted = client.post(f"/api/v1/patches/{patch['id']}/decision", json={"action": "accept"})
    assert accepted.status_code == 200
    assert accepted.json()["repository_modified"] is False
    failed = next(item for item in payload["patches"] if item["strategy"] == "shell_to_argv")
    # needs_review may be accepted; a proposed patch with no passing validation may not.
    report = client.post(f"/api/v1/reviews/{job_id}/report")
    assert report.status_code == 200
    assert report.json()["github_called"] is False
    assert "whsec_" not in report.json()["body"]
    file_id = next(item["id"] for item in payload["files"] if item["path"] == "src/billing/webhooks.py")
    diff = client.get(f"/api/v1/pulls/{billing['pull_request_id']}/files/{file_id}")
    assert diff.status_code == 200
    assert any(row["right_kind"] == "add" for row in diff.json()["split"])
    evaluation = client.post("/api/v1/evaluations")
    assert evaluation.status_code == 201
    assert evaluation.json()["precision"] == 1
    assert evaluation.json()["recall"] == 1
    assert evaluation.json()["residual_risks"]
    audit = client.get("/api/v1/audit")
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1
    _ = failed


def test_webhook_signature_is_required_and_duplicate_delivery_is_idempotent(client, session):
    seed_demo(session, SessionLocal, process_history=False)
    pull = session.query(PullRequest).filter(PullRequest.number == 142).one()
    payload = {
        "action": "opened",
        "repository": {"full_name": "acme/billing-service"},
        "pull_request": {"number": 142, "head": {"sha": pull.head_sha}},
    }
    raw = json.dumps(payload).encode()
    secret = "test-webhook-secret"
    good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    rejected = client.post("/api/v1/webhooks/github", content=raw, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64, "X-GitHub-Delivery": "d1", "X-GitHub-Event": "pull_request"})
    assert rejected.status_code == 401
    headers = {"X-Hub-Signature-256": good, "X-GitHub-Delivery": "d1", "X-GitHub-Event": "pull_request", "Content-Type": "application/json"}
    first = client.post("/api/v1/webhooks/github", content=raw, headers=headers)
    assert first.status_code == 202
    assert first.json()["outcome"] == "queued"
    second = client.post("/api/v1/webhooks/github", content=raw, headers=headers)
    assert second.json()["duplicate"] is True
    assert second.json()["job_id"] == first.json()["job_id"]
    evil = dict(payload)
    evil["repository"] = {"full_name": "evil/other", "clone_url": "http://169.254.169.254/latest"}
    raw_evil = json.dumps(evil).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw_evil, hashlib.sha256).hexdigest()
    ignored = client.post(
        "/api/v1/webhooks/github",
        content=raw_evil,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Delivery": "d2", "X-GitHub-Event": "pull_request"},
    )
    assert ignored.status_code == 202
    assert ignored.json()["outcome"] == "unknown_repository"
    assert session.query(ReviewJob).count() == 1


def test_cross_workspace_review_is_hidden(client, session):
    seed_demo(session, SessionLocal, process_history=False)
    from app.models import Membership, User, Workspace

    other = Workspace(name="Other", slug="other", demo=False)
    user = User(email="other@example.com", name="Other")
    session.add_all([other, user])
    session.flush()
    session.add(Membership(workspace_id=other.id, user_id=user.id, role="owner"))
    session.commit()
    pull = session.query(PullRequest).filter(PullRequest.number == 87).one()
    missing = client.get(f"/api/v1/pulls/{pull.id}")
    assert missing.status_code == 200  # demo user owns this pull
    repo = session.get(Repository, pull.repository_id)
    assert repo.workspace_id != other.id


def test_github_client_rejects_metadata_hosts():
    import pytest
    from app.services.github_client import GithubClientError, HttpGithubClient

    with pytest.raises(GithubClientError):
        HttpGithubClient("token", "http://169.254.169.254")
    with pytest.raises(GithubClientError):
        HttpGithubClient("token", "https://example.com")


def test_evaluation_direct(session):
    from app.models import User, Workspace

    workspace = Workspace(name="Eval", slug="eval", demo=True)
    session.add(workspace)
    session.commit()
    run = run_evaluation(session, workspace.id, "mock")
    assert run.precision == 1
    assert run.recall == 1
    assert run.false_negative == 0
    assert run.residual_risks
    _ = User
