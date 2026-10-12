"""Versioned HTTP API. Browser mutations are proxied by the Next.js server."""

from __future__ import annotations

import asyncio
import json

from app.config import get_settings
from app.db import SessionLocal, get_session
from app.errors import APIError
from app.models import (
    Finding,
    PatchProposal,
    PullRequest,
    PullRequestFile,
    Repository,
    ReviewJob,
    ReviewPolicy,
    User,
    Workspace,
)
from app.services.actions import (
    audit,
    create_review,
    decide_finding,
    decide_patch,
    edit_patch,
    publish_report,
)
from app.services.evaluation import run_evaluation, run_structured_provider_evaluation
from app.services.queries import (
    _evaluation,
    _event,
    _finding,
    _patch,
    _repo_summary,
    audit_page,
    command_center,
    evaluation_page,
    file_diff,
    membership_for,
    review_detail,
    traces_for,
)
from app.services.signatures import verify_github_signature
from app.versions import ANALYZER_VERSION, PROMPT_VERSION
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1")


def db_session():
    yield from get_session()


def current_user(db: Session = Depends(db_session)) -> User:
    settings = get_settings()
    if not settings.demo_mode:
        raise APIError(401, "auth_not_configured", "Demo mode is off and no session authenticator is configured.")
    user = db.query(User).filter(User.email == settings.demo_user_email).one_or_none()
    if user is None:
        raise APIError(503, "demo_not_seeded", "The demo user has not been seeded.")
    return user


def current_workspace(user: User = Depends(current_user), db: Session = Depends(db_session)) -> Workspace:
    membership = membership_for(db, user)
    workspace = db.get(Workspace, membership.workspace_id)
    if workspace is None:
        raise APIError(403, "no_workspace", "This user has no workspace.")
    return workspace


def _job_or_404(db: Session, workspace: Workspace, job_id: str) -> ReviewJob:
    job = db.get(ReviewJob, job_id)
    if job is None or job.workspace_id != workspace.id:
        raise APIError(404, "not_found", "Review job not found in this workspace.")
    return job


def _pull_or_404(db: Session, workspace: Workspace, pull_id: str) -> PullRequest:
    pull = db.get(PullRequest, pull_id)
    if pull is None:
        raise APIError(404, "not_found", "Pull request not found.")
    repo = db.get(Repository, pull.repository_id)
    if repo is None or repo.workspace_id != workspace.id:
        raise APIError(404, "not_found", "Pull request not found.")
    return pull


class ReviewCreate(BaseModel):
    pull_request_id: str


class DecisionBody(BaseModel):
    action: str
    note: str | None = Field(default=None, max_length=2000)


class PatchEdit(BaseModel):
    content: str = Field(min_length=0, max_length=200_000)


class PolicyUpdate(BaseModel):
    severity_threshold: str | None = None
    github_min_severity: str | None = None
    exclude_globs: list[str] | None = None
    token_budget: int | None = Field(default=None, ge=1_000, le=200_000)
    max_repair_attempts: int | None = Field(default=None, ge=0, le=3)
    validation_timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    post_github_comments: bool | None = None
    required_checks: list[str] | None = None


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(db_session)) -> dict:
    db.query(User).count()
    settings = get_settings()
    return {
        "status": "ready",
        "database": "ok",
        "worker_mode": settings.worker_mode,
        "demo_mode": settings.demo_mode,
    }


@router.get("/me")
def me(user: User = Depends(current_user), workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    membership = membership_for(db, user)
    return {
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "workspace": {"id": workspace.id, "name": workspace.name, "slug": workspace.slug, "demo": workspace.demo},
        "role": membership.role,
    }


@router.get("/command-center")
def center(origin: str | None = None, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    if origin not in {None, "all", "seed", "live", "webhook"}:
        raise APIError(422, "invalid_origin", "Origin filter must be all, seed, live, or webhook.")
    return command_center(db, workspace, None if origin in {None, "all"} else origin)


@router.get("/repositories")
def list_repos(workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    repos = db.query(Repository).filter(Repository.workspace_id == workspace.id).order_by(Repository.name).all()
    return {"items": [_repo_summary(db, repo) for repo in repos]}


@router.get("/repositories/{repository_id}")
def get_repo(repository_id: str, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    repo = _repo_or_404(db, workspace, repository_id)
    pulls = db.query(PullRequest).filter(PullRequest.repository_id == repo.id).all()
    return {**_repo_summary(db, repo), "pulls": [{"id": pull.id, "number": pull.number, "title": pull.title, "author": pull.author, "state": pull.state, "head_sha": pull.head_sha} for pull in pulls]}


@router.patch("/repositories/{repository_id}/policy")
def update_policy(
    repository_id: str,
    body: PolicyUpdate,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(db_session),
) -> dict:
    repo = _repo_or_404(db, workspace, repository_id)
    policy = db.query(ReviewPolicy).filter_by(repository_id=repo.id).one()
    allowed = {"low", "medium", "high", "critical"}
    data = body.model_dump(exclude_unset=True)
    if "severity_threshold" in data and data["severity_threshold"] not in allowed:
        raise APIError(422, "invalid_severity", "Unknown severity threshold.")
    if "github_min_severity" in data and data["github_min_severity"] not in allowed:
        raise APIError(422, "invalid_severity", "Unknown GitHub severity threshold.")
    for key, value in data.items():
        setattr(policy, key, value)
    if policy.max_repair_attempts > 3:
        policy.max_repair_attempts = 3
    audit(db, workspace.id, user.id, "policy_updated", "repository", repo.id, data)
    db.commit()
    return _repo_summary(db, repo)


@router.get("/pulls/{pull_id}")
def get_pull(pull_id: str, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    pull = _pull_or_404(db, workspace, pull_id)
    repo = db.get(Repository, pull.repository_id)
    files = db.query(PullRequestFile).filter(PullRequestFile.pull_request_id == pull.id).all()
    jobs = (
        db.query(ReviewJob)
        .filter(ReviewJob.pull_request_id == pull.id)
        .order_by(ReviewJob.created_at.desc())
        .all()
    )
    return {
        "pull_request": {
            "id": pull.id,
            "number": pull.number,
            "title": pull.title,
            "body": pull.body,
            "author": pull.author,
            "base_ref": pull.base_ref,
            "head_ref": pull.head_ref,
            "base_sha": pull.base_sha,
            "head_sha": pull.head_sha,
            "state": pull.state,
        },
        "repository": _repo_summary(db, repo),
        "files": [{"id": item.id, "path": item.path, "status": item.status, "additions": item.additions, "deletions": item.deletions, "language": item.language, "skipped_reason": item.skipped_reason} for item in files],
        "jobs": [{"id": job.id, "status": job.status, "stage": job.stage, "origin": job.origin, "created_at": job.created_at.isoformat()} for job in jobs],
    }


@router.get("/pulls/{pull_id}/files/{file_id}")
def get_file_diff(pull_id: str, file_id: str, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    pull = _pull_or_404(db, workspace, pull_id)
    item = db.get(PullRequestFile, file_id)
    if item is None or item.pull_request_id != pull.id:
        raise APIError(404, "not_found", "File not found on this pull request.")
    return file_diff(item)


@router.post("/reviews", status_code=202)
def start_review(
    body: ReviewCreate,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(db_session),
) -> dict:
    pull = _pull_or_404(db, workspace, body.pull_request_id)
    job, created = create_review(db, pull, workspace_id=workspace.id, trigger="manual", origin="live")
    audit(db, workspace.id, user.id, "review_requested" if created else "review_reused", "review_job", job.id, {"created": created})
    db.commit()
    return {"job_id": job.id, "status": job.status, "created": created}


@router.post("/reviews/{job_id}/retry", status_code=202)
def retry_review(
    job_id: str,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(db_session),
) -> dict:
    job = _job_or_404(db, workspace, job_id)
    if job.status not in {"failed", "completed", "budget_exhausted"}:
        raise APIError(409, "not_retryable", "Only a finished review can be retried.")
    pull = db.get(PullRequest, job.pull_request_id)
    fresh, created = create_review(db, pull, workspace_id=workspace.id, trigger="retry", origin="live")
    audit(db, workspace.id, user.id, "review_retried", "review_job", fresh.id, {"from": job.id, "created": created})
    db.commit()
    return {"job_id": fresh.id, "status": fresh.status, "created": created}


@router.get("/reviews/{job_id}")
def get_review(job_id: str, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    return review_detail(db, _job_or_404(db, workspace, job_id))


@router.get("/reviews/{job_id}/findings")
def get_findings(job_id: str, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    job = _job_or_404(db, workspace, job_id)
    rows = db.query(Finding).filter(Finding.job_id == job.id).all()
    return {"items": [_finding(db, row) for row in rows]}


@router.get("/reviews/{job_id}/events")
def get_events(job_id: str, after: int = 0, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    from app.models import ReviewEvent

    job = _job_or_404(db, workspace, job_id)
    rows = (
        db.query(ReviewEvent)
        .filter(ReviewEvent.job_id == job.id, ReviewEvent.sequence > after)
        .order_by(ReviewEvent.sequence.asc())
        .all()
    )
    return {"job_status": job.status, "stage": job.stage, "items": [_event(row) for row in rows]}


@router.get("/reviews/{job_id}/stream")
async def stream_review(job_id: str, request: Request, after: int = 0, workspace: Workspace = Depends(current_workspace)) -> StreamingResponse:
    header = request.headers.get("last-event-id")
    if header and header.isdigit():
        after = max(after, int(header))

    async def generate():
        cursor = after
        quiet = 0
        while True:
            if await request.is_disconnected():
                break
            db = SessionLocal()
            try:
                job = db.get(ReviewJob, job_id)
                if job is None or job.workspace_id != workspace.id:
                    yield 'event: error\ndata: {"message":"not found"}\n\n'
                    break
                from app.models import ReviewEvent

                rows = (
                    db.query(ReviewEvent)
                    .filter(ReviewEvent.job_id == job.id, ReviewEvent.sequence > cursor)
                    .order_by(ReviewEvent.sequence.asc())
                    .all()
                )
                terminal = job.status in {"completed", "failed", "cancelled"}
                payloads = [(row.sequence, _event(row)) for row in rows]
            finally:
                db.close()
            for sequence, payload in payloads:
                cursor = sequence
                yield f"id: {sequence}\ndata: {json.dumps(payload)}\n\n"
            if terminal and not payloads:
                yield 'event: end\ndata: {"status":"closed"}\n\n'
                break
            quiet = 0 if payloads else quiet + 1
            if quiet >= 15:
                yield ": keepalive\n\n"
                quiet = 0
            await asyncio.sleep(0.35)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/stream")
async def stream_workspace(request: Request, after: int = 0, workspace: Workspace = Depends(current_workspace)) -> StreamingResponse:
    header = request.headers.get("last-event-id")
    if header and header.isdigit():
        after = max(after, int(header))

    async def generate():
        cursor = after
        quiet = 0
        while True:
            if await request.is_disconnected():
                break
            db = SessionLocal()
            try:
                from app.models import ReviewEvent

                rows = (
                    db.query(ReviewEvent)
                    .join(ReviewJob, ReviewJob.id == ReviewEvent.job_id)
                    .filter(ReviewJob.workspace_id == workspace.id, ReviewEvent.id > cursor)
                    .order_by(ReviewEvent.id.asc())
                    .limit(50)
                    .all()
                )
                payloads = [(row.id, _event(row)) for row in rows]
            finally:
                db.close()
            for event_id, payload in payloads:
                cursor = event_id
                yield f"id: {event_id}\ndata: {json.dumps(payload)}\n\n"
            quiet = 0 if payloads else quiet + 1
            if quiet >= 20:
                yield ": keepalive\n\n"
                quiet = 0
            await asyncio.sleep(0.8)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/reviews/{job_id}/trace")
def get_trace(job_id: str, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    job = _job_or_404(db, workspace, job_id)
    return {"job_id": job.id, "correlation_id": job.correlation_id, "usage": job.usage, "plan": job.plan, "traces": traces_for(db, job.id)}


@router.get("/reviews/{job_id}/patches")
def get_patches(job_id: str, workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    job = _job_or_404(db, workspace, job_id)
    rows = db.query(PatchProposal).filter(PatchProposal.job_id == job.id).all()
    return {"items": [_patch(db, row) for row in rows], "disclaimer": "A passing validation is not proof of production safety."}


@router.post("/findings/{finding_id}/decision")
def post_finding_decision(
    finding_id: str,
    body: DecisionBody,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(db_session),
) -> dict:
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise APIError(404, "not_found", "Finding not found.")
    _job_or_404(db, workspace, finding.job_id)
    if body.action == "request_reanalysis":
        job = db.get(ReviewJob, finding.job_id)
        pull = db.get(PullRequest, job.pull_request_id)
        decide_finding(db, user, finding, body.action, body.note)
        fresh, created = create_review(db, pull, workspace_id=workspace.id, trigger="retry", origin="live")
        return {"finding": _finding(db, finding), "reanalysis_job_id": fresh.id, "created": created}
    updated = decide_finding(db, user, finding, body.action, body.note)
    return {"finding": _finding(db, updated)}


@router.patch("/patches/{patch_id}")
def patch_content(
    patch_id: str,
    body: PatchEdit,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(db_session),
) -> dict:
    patch = _patch_or_404(db, workspace, patch_id)
    updated = edit_patch(db, user, patch, body.content)
    return _patch(db, updated)


@router.post("/patches/{patch_id}/decision")
def post_patch_decision(
    patch_id: str,
    body: DecisionBody,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(db_session),
) -> dict:
    patch = _patch_or_404(db, workspace, patch_id)
    return decide_patch(db, user, patch, body.action, body.note)


@router.post("/reviews/{job_id}/report")
def post_report(
    job_id: str,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(db_session),
) -> dict:
    job = _job_or_404(db, workspace, job_id)
    report = publish_report(db, user, job)
    return {
        "id": report.id,
        "body": report.body,
        "destination": report.destination,
        "delivery_status": report.delivery_status,
        "redacted": report.redacted,
        "github_called": bool(getattr(report, "github_called", False)),
    }


@router.get("/evaluations")
def list_evaluations(workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    return {"items": evaluation_page(db, workspace.id)}


@router.post("/evaluations", status_code=201)
def create_evaluation(workspace: Workspace = Depends(current_workspace), db: Session = Depends(db_session)) -> dict:
    run = run_evaluation(db, workspace.id, get_settings().llm_provider)
    run_structured_provider_evaluation(db, workspace.id)
    return _evaluation(run)


@router.get("/audit")
def list_audit(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(db_session),
) -> dict:
    return audit_page(db, workspace.id, limit, offset)


@router.get("/settings")
def settings_view(workspace: Workspace = Depends(current_workspace)) -> dict:
    settings = get_settings()
    dialect = "postgresql" if settings.resolved_database_url.startswith("postgres") else "sqlite"
    return {
        "demo_mode": settings.demo_mode,
        "workspace": {"id": workspace.id, "name": workspace.name, "demo": workspace.demo},
        "llm": _llm_settings(settings),
        "sandbox": {
            "driver": settings.sandbox_driver,
            "image": settings.sandbox_image,
            "limitations": (
                "The subprocess driver is only used for first-party demo fixtures. "
                "It is not a security boundary for untrusted pull requests. "
                "Docker isolation, when selected, still shares a kernel and mounts a workspace. "
                "It is not a substitute for a microVM or gVisor-class sandbox."
            ),
        },
        "github": _github_settings(settings),
        "database_dialect": dialect,
        "worker_mode": settings.worker_mode,
        "analyzer_version": ANALYZER_VERSION,
        "prompt_version": PROMPT_VERSION,
        "repair_cap": settings.repair_cap,
    }


@router.post("/webhooks/github", status_code=202)
async def github_webhook(request: Request, db: Session = Depends(db_session)) -> dict:
    body = await request.body()
    settings = get_settings()
    if len(body) > settings.request_max_bytes:
        raise APIError(413, "payload_too_large", "Webhook body exceeds the size limit.")
    signature = request.headers.get("x-hub-signature-256")
    if not verify_github_signature(settings.github_webhook_secret, body, signature):
        audit(db, None, None, "webhook_rejected", "webhook", request.headers.get("x-github-delivery"), {"reason": "bad_signature"})
        db.commit()
        raise APIError(401, "bad_signature", "Webhook signature did not match.")
    delivery = request.headers.get("x-github-delivery") or ""
    if not delivery or len(delivery) > 200:
        raise APIError(400, "missing_delivery", "X-GitHub-Delivery is required.")
    event_name = request.headers.get("x-github-event") or ""
    try:
        payload = json.loads(body.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise APIError(400, "invalid_json", "Webhook body was not JSON.") from exc
    if not isinstance(payload, dict):
        raise APIError(400, "invalid_json", "Webhook body must be an object.")
    from app.models import WebhookDelivery

    existing = db.query(WebhookDelivery).filter(WebhookDelivery.delivery_id == delivery).one_or_none()
    if existing is not None:
        return {"ok": True, "duplicate": True, "job_id": existing.job_id, "outcome": existing.outcome}
    action = payload.get("action") if isinstance(payload.get("action"), str) else None
    outcome = "ignored"
    job_id = None
    workspace_id = None
    if event_name == "ping":
        outcome = "pong"
    else:
        from app.services.github_ingest import handle_event

        job_id, workspace_id, outcome = handle_event(db, event_name, payload)
    db.add(
        WebhookDelivery(
            delivery_id=delivery,
            event_name=event_name or "unknown",
            action=action,
            outcome=outcome,
            job_id=job_id,
            workspace_id=workspace_id,
        )
    )
    db.commit()
    return {"ok": True, "duplicate": False, "job_id": job_id, "outcome": outcome}


def _llm_settings(settings) -> dict:
    from app.providers.registry import llm_view

    view = llm_view(settings)
    encoded = str(view)
    for secret in (settings.openai_api_key, settings.groq_api_key, settings.gemini_api_key):
        if secret and secret in encoded:
            raise RuntimeError("Settings view included a model API key")
    return view


def _github_settings(settings) -> dict:
    from app.services.github_app import app_configured, private_key

    slug = settings.github_app_slug if isinstance(settings.github_app_slug, str) else None
    safe_slug = slug if slug and slug.replace("-", "").isalnum() else None
    return {
        "api_base": settings.github_api_base,
        "app_id_configured": bool(settings.github_app_id),
        "private_key_configured": bool(private_key()),
        "app_configured": app_configured(),
        "app_slug": safe_slug,
        "install_url": f"https://github.com/apps/{safe_slug}/installations/new" if safe_slug else None,
        "webhook_secret_is_default": settings.github_webhook_secret == "dev-only-webhook-secret",
        "permissions": ["metadata:read", "contents:read", "pull_requests:write", "checks:write"],
        "note": "Installation tokens stay in the API process. They are not sent to the browser or the model.",
    }


def _repo_or_404(db: Session, workspace: Workspace, repository_id: str) -> Repository:
    repo = db.get(Repository, repository_id)
    if repo is None or repo.workspace_id != workspace.id:
        raise APIError(404, "not_found", "Repository not found in this workspace.")
    return repo


def _patch_or_404(db: Session, workspace: Workspace, patch_id: str) -> PatchProposal:
    patch = db.get(PatchProposal, patch_id)
    if patch is None:
        raise APIError(404, "not_found", "Patch not found.")
    _job_or_404(db, workspace, patch.job_id)
    return patch

