"""Human decisions and review creation. Nothing here merges a repository."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.clock import utcnow
from app.errors import APIError
from app.intelligence.diffutil import unified_diff
from app.intelligence.paths import is_excluded, safe_relative
from app.intelligence.remediation import policy_issues
from app.models import (
    AuditEvent,
    Finding,
    GithubInstallation,
    PatchProposal,
    PullRequest,
    PullRequestFile,
    Repository,
    ReviewJob,
    ReviewPolicy,
    ReviewReport,
    User,
    UserDecision,
    ValidationRun,
)
from app.redact import redact_text
from app.versions import ANALYZER_VERSION, PROMPT_VERSION

ACCEPTABLE_PATCH = {"passed", "needs_review"}


def audit(db: Session, workspace_id: str | None, actor_id: str | None, action: str, target_type: str, target_id: str | None, details: dict | None = None) -> None:
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
    )


def create_review(
    db: Session,
    pull: PullRequest,
    *,
    workspace_id: str,
    trigger: str,
    origin: str,
    idempotency_key: str | None = None,
) -> tuple[ReviewJob, bool]:
    if idempotency_key:
        existing = db.query(ReviewJob).filter(ReviewJob.idempotency_key == idempotency_key).one_or_none()
        if existing is not None:
            return existing, False
    active = (
        db.query(ReviewJob)
        .filter(
            ReviewJob.pull_request_id == pull.id,
            ReviewJob.head_sha == pull.head_sha,
            ReviewJob.status.in_(["queued", "running"]),
        )
        .order_by(ReviewJob.created_at.desc())
        .first()
    )
    if active is not None and trigger != "retry":
        return active, False
    policy = db.query(ReviewPolicy).filter_by(repository_id=pull.repository_id).one()
    job = ReviewJob(
        workspace_id=workspace_id,
        pull_request_id=pull.id,
        status="queued",
        trigger=trigger,
        origin=origin,
        head_sha=pull.head_sha,
        idempotency_key=idempotency_key,
        stage="queued",
        model_provider=policy.model_provider,
        analyzer_version=ANALYZER_VERSION,
        prompt_version=PROMPT_VERSION,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, True


def decide_finding(db: Session, user: User, finding: Finding, action: str, note: str | None) -> Finding:
    if action not in {"confirm", "dismiss", "request_reanalysis"}:
        raise APIError(422, "invalid_action", "Action must be confirm, dismiss, or request_reanalysis.")
    if action == "confirm":
        finding.status = "confirmed"
    elif action == "dismiss":
        finding.status = "dismissed"
    db.add(UserDecision(user_id=user.id, finding_id=finding.id, action=action, note=note))
    job = db.get(ReviewJob, finding.job_id)
    audit(
        db,
        job.workspace_id if job else None,
        user.id,
        f"finding_{action}",
        "finding",
        finding.id,
        {"note": note, "rule_id": finding.rule_id},
    )
    db.commit()
    db.refresh(finding)
    return finding


def edit_patch(db: Session, user: User, patch: PatchProposal, content: str) -> PatchProposal:
    if patch.status == "accepted":
        raise APIError(409, "patch_locked", "An accepted patch cannot be edited. Start a new review to propose another change.")
    paths = list(patch.target_paths or [])
    if len(paths) != 1:
        raise APIError(422, "patch_shape", "Only single-file patches can be edited in this version.")
    path = paths[0]
    if not safe_relative(path):
        raise APIError(422, "unsafe_path", "Patch path is not safe.")
    job = db.get(ReviewJob, patch.job_id)
    pull = db.get(PullRequest, job.pull_request_id)
    repo = db.get(Repository, pull.repository_id)
    policy = db.query(ReviewPolicy).filter_by(repository_id=repo.id).one()
    if is_excluded(path, list(policy.exclude_globs or [])):
        raise APIError(422, "excluded_path", "That path is excluded by repository policy.")
    original = (
        db.query(PullRequestFile)
        .filter(PullRequestFile.pull_request_id == pull.id, PullRequestFile.path == path)
        .one()
        .head_content
        or ""
    )
    if "eval(" in content and "eval(" not in original:
        raise APIError(422, "policy_rejected", "The edit introduces eval and was rejected.")
    if any(issue == "introduces_eval" for issue in policy_issues(content)) and "eval(" not in original:
        raise APIError(422, "policy_rejected", "The edit introduces eval and was rejected.")
    patch.patched_files = {path: content}
    patch.diff_text = unified_diff(path, original, content)
    patch.status = "proposed"
    patch.policy_notes = "Edited by a reviewer. Validation has not been re-run."
    patch.updated_at = utcnow()
    db.add(UserDecision(user_id=user.id, patch_id=patch.id, action="edit", note="Edited patched file"))
    audit(db, job.workspace_id, user.id, "patch_edited", "patch", patch.id, {"path": path})
    db.commit()
    db.refresh(patch)
    return patch


def decide_patch(db: Session, user: User, patch: PatchProposal, action: str, note: str | None) -> dict:
    if action not in {"accept", "reject"}:
        raise APIError(422, "invalid_action", "Action must be accept or reject.")
    latest = (
        db.query(ValidationRun)
        .filter(ValidationRun.patch_id == patch.id)
        .order_by(ValidationRun.attempt.desc())
        .first()
    )
    if action == "accept" and (latest is None or latest.status not in ACCEPTABLE_PATCH):
        raise APIError(
            409,
            "validation_required",
            "Refusing to accept a patch that has not passed or been marked needs review.",
        )
    patch.status = "accepted" if action == "accept" else "rejected"
    db.add(UserDecision(user_id=user.id, patch_id=patch.id, action=action, note=note))
    job = db.get(ReviewJob, patch.job_id)
    audit(db, job.workspace_id, user.id, f"patch_{action}", "patch", patch.id, {"note": note, "repository_modified": False})
    db.commit()
    return {
        "patch_id": patch.id,
        "status": patch.status,
        "repository_modified": False,
        "message": "Decision recorded. The repository was not modified and nothing was merged.",
    }


def publish_report(db: Session, user: User, job: ReviewJob) -> ReviewReport:
    if job.status != "completed":
        raise APIError(409, "review_incomplete", "Publish a report after the review completes.")
    pull = db.get(PullRequest, job.pull_request_id)
    repo = db.get(Repository, pull.repository_id)
    findings = db.query(Finding).filter(Finding.job_id == job.id).all()
    patches = db.query(PatchProposal).filter(PatchProposal.job_id == job.id).all()
    lines = [
        f"PRISM review for {repo.owner}/{repo.name}#{pull.number}",
        f"Commit: `{pull.head_sha}`",
        "",
        "Passing tests are not proof of production safety. Static results, tests, and human decisions are listed separately. This report does not merge the pull request.",
        "",
        "## Findings",
    ]
    for finding in findings:
        lines.append(
            f"- [{finding.severity}/{finding.claim_level}] {finding.title} "
            f"({finding.file_path}:{finding.start_line}) status={finding.status} validation={finding.validation_state}"
        )
        lines.append(f"  - {finding.explanation}")
    lines.append("")
    lines.append("## Patches")
    if not patches:
        lines.append("No patch was proposed.")
    for patch in patches:
        latest = (
            db.query(ValidationRun)
            .filter(ValidationRun.patch_id == patch.id)
            .order_by(ValidationRun.attempt.desc())
            .first()
        )
        lines.append(
            f"- {patch.title}: status={patch.status}, proof_scope={patch.proof_scope}, "
            f"attempts={patch.attempt}, latest_validation={latest.status if latest else 'none'}"
        )
    lines.append("")
    lines.append("Sandbox paths, host secrets, and credential literals are redacted in this report.")
    body = redact_text("\n".join(lines), limit=50_000)
    destination, delivery, github_called = _deliver(db, repo, pull, job, body, findings)
    report = ReviewReport(job_id=job.id, body=body, destination=destination, delivery_status=delivery, redacted=True)
    db.add(report)
    audit(
        db,
        job.workspace_id,
        user.id,
        "report_published",
        "review_job",
        job.id,
        {"delivery_status": delivery, "github_called": github_called},
    )
    db.commit()
    db.refresh(report)
    report.github_called = github_called  # type: ignore[attr-defined]
    return report


def _deliver(db: Session, repo: Repository, pull: PullRequest, job: ReviewJob, body: str, findings: list[Finding]) -> tuple[str, str, bool]:
    if repo.provider == "demo" or repo.trust_tier == "demo_fixture":
        return "local", "stored_locally_demo", False
    policy = db.query(ReviewPolicy).filter_by(repository_id=repo.id).one()
    if not policy.post_github_comments:
        return "local", "withheld_by_policy", False
    from app.services.github_app import app_configured, github_client_for_installation
    from app.services.github_client import GithubClientError
    from app.services.github_ingest import plan_review_comments

    if not app_configured():
        return "github", "not_delivered_app_not_configured", False
    installation = db.get(GithubInstallation, repo.installation_id) if repo.installation_id else None
    if installation is None or installation.status != "active" or installation.installation_id is None:
        return "github", "not_delivered_no_installation", False
    files = {
        item.path: item.patch or ""
        for item in db.query(PullRequestFile).filter(PullRequestFile.pull_request_id == pull.id).all()
    }
    comments, omitted = plan_review_comments(findings, files, policy.github_min_severity)
    summary = body
    if omitted:
        summary = summary + "\n\nFindings that were not on a diff line stayed in this summary:\n" + "\n".join(f"- {item}" for item in omitted)
    summary = redact_text(summary, limit=60_000)
    client = github_client_for_installation(installation.installation_id)
    try:
        client.create_check_run(repo.owner, repo.name, pull.head_sha, summary, summary)
        client.create_review(repo.owner, repo.name, pull.number, summary, pull.head_sha, comments)
    except GithubClientError:
        return "github", "github_failed", True
    finally:
        client.close()
    return "github", "delivered_check_and_summary", True
