"""Read models. Metrics are aggregates over stored rows."""

from __future__ import annotations

import statistics

from sqlalchemy.orm import Session

from app.intelligence.diffutil import split_rows
from app.models import (
    AgentTrace,
    AuditEvent,
    EvaluationRun,
    Evidence,
    Finding,
    Membership,
    PatchProposal,
    PullRequest,
    PullRequestFile,
    Repository,
    ReviewEvent,
    ReviewJob,
    ReviewPolicy,
    ReviewReport,
    TestResult,
    User,
    UserDecision,
    ValidationRun,
    Workspace,
)


def membership_for(db: Session, user: User) -> Membership:
    return db.query(Membership).filter(Membership.user_id == user.id).one()


def command_center(db: Session, workspace: Workspace, origin: str | None) -> dict:
    pulls = (
        db.query(PullRequest)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .filter(Repository.workspace_id == workspace.id)
        .all()
    )
    jobs_query = db.query(ReviewJob).filter(ReviewJob.workspace_id == workspace.id)
    if origin in {"seed", "live", "webhook"}:
        jobs_query = jobs_query.filter(ReviewJob.origin == origin)
    jobs = jobs_query.all()
    job_ids = [job.id for job in jobs] or ["__none__"]
    findings = db.query(Finding).filter(Finding.job_id.in_(job_ids)).all()
    decisions = (
        db.query(UserDecision)
        .filter(UserDecision.finding_id.in_([item.id for item in findings] or ["__none__"]))
        .all()
    )
    validations = (
        db.query(ValidationRun)
        .join(PatchProposal, PatchProposal.id == ValidationRun.patch_id)
        .filter(PatchProposal.job_id.in_(job_ids))
        .all()
    )
    durations = []
    for job in jobs:
        if job.status == "completed" and job.started_at and job.finished_at:
            durations.append((job.finished_at - job.started_at).total_seconds())
    decided = [item for item in decisions if item.action in {"confirm", "dismiss"}]
    confirmed = [item for item in decided if item.action == "confirm"]
    passed = [item for item in validations if item.status == "passed"]
    severity = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for finding in findings:
        if finding.status == "open":
            severity[finding.severity] = severity.get(finding.severity, 0) + 1
    queue = []
    for pull in sorted(pulls, key=lambda item: item.number, reverse=True):
        repo = db.get(Repository, pull.repository_id)
        related = [job for job in jobs if job.pull_request_id == pull.id]
        latest = max(related, key=lambda item: item.created_at) if related else None
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        if latest:
            for finding in findings:
                if finding.job_id == latest.id and finding.status == "open":
                    counts[finding.severity] = counts.get(finding.severity, 0) + 1
        queue.append(
            {
                "pull_request_id": pull.id,
                "number": pull.number,
                "title": pull.title,
                "author": pull.author,
                "head_ref": pull.head_ref,
                "head_sha": pull.head_sha,
                "state": pull.state,
                "repository": {"id": repo.id, "owner": repo.owner, "name": repo.name, "trust_tier": repo.trust_tier},
                "job": _job_brief(latest),
                "open_findings": counts,
            }
        )
    events = (
        db.query(ReviewEvent)
        .join(ReviewJob, ReviewJob.id == ReviewEvent.job_id)
        .filter(ReviewJob.workspace_id == workspace.id)
        .order_by(ReviewEvent.id.desc())
        .limit(20)
        .all()
    )
    repos = db.query(Repository).filter(Repository.workspace_id == workspace.id).all()
    return {
        "workspace": {"id": workspace.id, "name": workspace.name, "slug": workspace.slug, "demo": workspace.demo},
        "metrics": {
            "median_review_seconds": statistics.median(durations) if durations else None,
            "completed_reviews": sum(1 for job in jobs if job.status == "completed"),
            "failed_reviews": sum(1 for job in jobs if job.status == "failed"),
            "running_reviews": sum(1 for job in jobs if job.status in {"queued", "running"}),
            "confirmed_rate": (len(confirmed) / len(decided)) if decided else None,
            "decisions": len(decided),
            "confirmed": len(confirmed),
            "validation_success_rate": (len(passed) / len(validations)) if validations else None,
            "validation_runs": len(validations),
            "validation_passed": len(passed),
            "sample_size": len(jobs),
            "origin": origin or "all",
            "note": "Rates are computed from stored jobs. A null rate means the denominator is zero.",
        },
        "severity": severity,
        "queue": queue,
        "activity": [_event(item) for item in events],
        "repositories": [_repo_summary(db, repo) for repo in repos],
    }


def review_detail(db: Session, job: ReviewJob) -> dict:
    pull = db.get(PullRequest, job.pull_request_id)
    repo = db.get(Repository, pull.repository_id)
    files = db.query(PullRequestFile).filter(PullRequestFile.pull_request_id == pull.id).all()
    findings = db.query(Finding).filter(Finding.job_id == job.id).order_by(Finding.severity, Finding.file_path).all()
    by_file: dict[str, int] = {}
    for finding in findings:
        by_file[finding.file_path] = by_file.get(finding.file_path, 0) + 1
    patches = db.query(PatchProposal).filter(PatchProposal.job_id == job.id).all()
    report = db.query(ReviewReport).filter(ReviewReport.job_id == job.id).order_by(ReviewReport.created_at.desc()).first()
    return {
        "job": _job_full(job),
        "pull_request": _pull(pull),
        "repository": _repo_summary(db, repo),
        "files": [
            {
                "id": item.id,
                "path": item.path,
                "status": item.status,
                "language": item.language,
                "additions": item.additions,
                "deletions": item.deletions,
                "skipped_reason": item.skipped_reason,
                "finding_count": by_file.get(item.path, 0),
            }
            for item in sorted(files, key=lambda row: row.path)
        ],
        "findings": [_finding(db, item) for item in findings],
        "patches": [_patch(db, item) for item in patches],
        "report": _report(report),
    }


def file_diff(file: PullRequestFile) -> dict:
    old = file.base_content or ""
    new = file.head_content or ""
    rows, note = split_rows(old, new)
    return {
        "file_id": file.id,
        "path": file.path,
        "language": file.language,
        "status": file.status,
        "skipped_reason": file.skipped_reason,
        "patch": file.patch,
        "base_content": old,
        "head_content": new,
        "split": rows,
        "coverage_note": note,
    }


def _job_brief(job: ReviewJob | None) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "origin": job.origin,
        "trigger": job.trigger,
        "head_sha": job.head_sha,
        "error": job.error,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }


def _job_full(job: ReviewJob) -> dict:
    brief = _job_brief(job) or {}
    brief.update(
        {
            "correlation_id": job.correlation_id,
            "plan": job.plan,
            "coverage": job.coverage,
            "usage": job.usage,
            "prompt_version": job.prompt_version,
            "analyzer_version": job.analyzer_version,
            "model_provider": job.model_provider,
        }
    )
    return brief


def _pull(pull: PullRequest) -> dict:
    return {
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
        "html_url": pull.html_url,
    }


def _repo_summary(db: Session, repo: Repository) -> dict:
    policy = db.query(ReviewPolicy).filter_by(repository_id=repo.id).one_or_none()
    return {
        "id": repo.id,
        "owner": repo.owner,
        "name": repo.name,
        "provider": repo.provider,
        "connection_status": repo.connection_status,
        "trust_tier": repo.trust_tier,
        "primary_language": repo.primary_language,
        "html_url": repo.html_url,
        "policy": None
        if policy is None
        else {
            "severity_threshold": policy.severity_threshold,
            "github_min_severity": policy.github_min_severity,
            "required_checks": policy.required_checks,
            "exclude_globs": policy.exclude_globs,
            "model_provider": policy.model_provider,
            "token_budget": policy.token_budget,
            "max_repair_attempts": policy.max_repair_attempts,
            "validation_timeout_seconds": policy.validation_timeout_seconds,
            "post_github_comments": policy.post_github_comments,
        },
    }


def _finding(db: Session, finding: Finding) -> dict:
    evidence = db.query(Evidence).filter(Evidence.finding_id == finding.id).all()
    return {
        "id": finding.id,
        "stable_key": finding.stable_key,
        "category": finding.category,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "claim_level": finding.claim_level,
        "status": finding.status,
        "validation_state": finding.validation_state,
        "title": finding.title,
        "explanation": finding.explanation,
        "impact": finding.impact,
        "remediation": finding.remediation,
        "file_path": finding.file_path,
        "start_line": finding.start_line,
        "end_line": finding.end_line,
        "source": finding.source,
        "rule_id": finding.rule_id,
        "agent": finding.agent,
        "observed": finding.observed,
        "uncertain_reason": finding.uncertain_reason,
        "enclosing_symbol": finding.enclosing_symbol,
        "agent_note": finding.agent_note,
        "evidence": [
            {
                "id": item.id,
                "kind": item.kind,
                "file_path": item.file_path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "snippet": item.snippet,
                "detail": item.detail,
                "trusted": item.trusted,
            }
            for item in evidence
        ],
    }


def _patch(db: Session, patch: PatchProposal) -> dict:
    runs = (
        db.query(ValidationRun)
        .filter(ValidationRun.patch_id == patch.id)
        .order_by(ValidationRun.attempt.asc())
        .all()
    )
    return {
        "id": patch.id,
        "finding_id": patch.finding_id,
        "status": patch.status,
        "title": patch.title,
        "diff_text": patch.diff_text,
        "patched_files": patch.patched_files,
        "target_paths": patch.target_paths,
        "attempt": patch.attempt,
        "strategy": patch.strategy,
        "proof_scope": patch.proof_scope,
        "policy_notes": patch.policy_notes,
        "disclaimer": patch.disclaimer,
        "validations": [
            {
                "id": run.id,
                "attempt": run.attempt,
                "status": run.status,
                "tests_selected": run.tests_selected,
                "tests_passed": run.tests_passed,
                "tests_failed": run.tests_failed,
                "baseline_passed": run.baseline_passed,
                "baseline_failed": run.baseline_failed,
                "security_status": run.security_status,
                "lint_status": run.lint_status,
                "stdout": run.stdout,
                "stderr": run.stderr,
                "duration_ms": run.duration_ms,
                "exit_code": run.exit_code,
                "timed_out": run.timed_out,
                "notes": run.notes,
                "tests": [
                    {
                        "name": case.name,
                        "outcome": case.outcome,
                        "duration_ms": case.duration_ms,
                        "output": case.output,
                        "phase": case.phase,
                    }
                    for case in db.query(TestResult).filter(TestResult.validation_run_id == run.id).all()
                ],
            }
            for run in runs
        ],
    }


def _event(event: ReviewEvent) -> dict:
    return {
        "id": event.id,
        "sequence": event.sequence,
        "job_id": event.job_id,
        "stage": event.stage,
        "level": event.level,
        "agent": event.agent,
        "message": event.message,
        "payload": event.payload,
        "created_at": _iso(event.created_at),
    }


def _report(report: ReviewReport | None) -> dict | None:
    if report is None:
        return None
    return {
        "id": report.id,
        "body": report.body,
        "destination": report.destination,
        "delivery_status": report.delivery_status,
        "redacted": report.redacted,
        "created_at": _iso(report.created_at),
    }


def traces_for(db: Session, job_id: str) -> list[dict]:
    rows = db.query(AgentTrace).filter(AgentTrace.job_id == job_id).all()
    return [
        {
            "id": row.id,
            "agent": row.agent,
            "node": row.node,
            "status": row.status,
            "duration_ms": row.duration_ms,
            "input_summary": row.input_summary,
            "output": row.output_json,
            "tool_calls": row.tool_calls,
            "token_input": row.token_input,
            "token_output": row.token_output,
            "estimated_cost_usd": row.estimated_cost_usd,
            "billed": row.billed,
            "retry_count": row.retry_count,
            "error": row.error,
            "correlation_id": row.correlation_id,
        }
        for row in rows
    ]


def audit_page(db: Session, workspace_id: str, limit: int, offset: int) -> dict:
    query = db.query(AuditEvent).filter(AuditEvent.workspace_id == workspace_id).order_by(AuditEvent.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "details": row.details,
                "created_at": _iso(row.created_at),
                "actor_id": row.actor_id,
            }
            for row in rows
        ],
    }


def evaluation_page(db: Session, workspace_id: str) -> list[dict]:
    rows = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.workspace_id == workspace_id)
        .order_by(EvaluationRun.created_at.desc())
        .all()
    )
    return [_evaluation(row) for row in rows]


def _evaluation(row: EvaluationRun) -> dict:
    return {
        "id": row.id,
        "fixture_set": row.fixture_set,
        "analyzer_version": row.analyzer_version,
        "model_provider": row.model_provider,
        "prompt_version": row.prompt_version,
        "precision": row.precision,
        "recall": row.recall,
        "f1": row.f1,
        "false_positive_rate": row.false_positive_rate,
        "true_positive": row.true_positive,
        "false_positive": row.false_positive,
        "false_negative": row.false_negative,
        "duration_ms": row.duration_ms,
        "details": row.details,
        "residual_risks": row.residual_risks,
        "created_at": _iso(row.created_at),
    }


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()
