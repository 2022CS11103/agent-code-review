"""Review pipeline.

Deterministic steps — diff parsing, policy, database writes, and test execution —
stay in this module. LangGraph only coordinates specialist commentary and patch intents.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path

from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from app.agents.graph import (
    merge_static_and_agents,
    merge_static_and_model,
    static_findings_after_provider_failure,
)
from app.clock import utcnow
from app.config import get_settings
from app.intelligence.analyzer import analyze_files, analyze_source, coverage_gap
from app.intelligence.diffutil import changed_new_lines, unified_diff
from app.intelligence.paths import is_excluded, safe_relative
from app.intelligence.remediation import issues_for_rule, repair_from_failure
from app.intelligence.ruff_scan import run_ruff
from app.models import (
    AgentTrace,
    Evidence,
    Finding,
    PatchProposal,
    PullRequest,
    PullRequestFile,
    Repository,
    ReviewEvent,
    ReviewJob,
    ReviewPolicy,
    TestResult,
    ValidationRun,
)
from app.providers.base import FileSlice, ReviewContext
from app.providers.registry import build_provider
from app.redact import redact_text
from app.services.sandbox import SuiteResult, run_pytest, write_tree
from app.versions import ANALYZER_VERSION, PROMPT_VERSION, PROVIDER_PROMPT_VERSION

log = logging.getLogger("prism.pipeline")

DISCLAIMER = (
    "Targeted tests and static re-scans are reported separately. "
    "A passing test is not proof that the change is production-safe. "
    "PRISM does not merge code and does not apply a patch without an explicit human decision."
)
HARNESS_ROOT = Path(__file__).resolve().parents[1] / "demo" / "harness"
SUITES = {"sql_injection": ["tests/test_delivery.py"]}


def process_job(job_id: str, session_factory: sessionmaker) -> None:
    db = session_factory()
    try:
        job = db.get(ReviewJob, job_id)
        if job is None:
            return
        if job.status == "queued":
            result = db.execute(
                update(ReviewJob)
                .where(ReviewJob.id == job_id, ReviewJob.status == "queued")
                .values(status="running", started_at=utcnow(), stage="ingest")
            )
            db.commit()
            if result.rowcount != 1:
                return
            db.refresh(job)
        elif job.status != "running":
            return
        _run(db, job)
    except Exception as exc:
        log.exception("review job failed")
        db.rollback()
        job = db.get(ReviewJob, job_id)
        if job is not None and job.status == "running":
            job.status = "failed"
            job.error = redact_text(f"{type(exc).__name__}: {exc}", limit=500)
            job.finished_at = utcnow()
            job.stage = "failed"
            db.commit()
            _emit(db, job, "failed", job.error or "Review failed.", level="error")
    finally:
        db.close()


def _run(db: Session, job: ReviewJob) -> None:
    settings = get_settings()
    pr = db.get(PullRequest, job.pull_request_id)
    if pr is None:
        raise RuntimeError("Pull request disappeared before the job ran")
    repo = db.get(Repository, pr.repository_id)
    if repo.provider == "github":
        from app.services.github_ingest import refresh_pull_from_github

        refresh_pull_from_github(db, repo, pr)
        db.refresh(pr)
    policy = db.query(ReviewPolicy).filter_by(repository_id=repo.id).one()
    files = list(db.query(PullRequestFile).filter_by(pull_request_id=pr.id).all())
    _pause(settings)
    _emit(
        db,
        job,
        "ingest",
        f"Loaded pull request #{pr.number} at {pr.head_sha[:12]} with {len(files)} changed files.",
        payload={"head_sha": pr.head_sha, "files": [item.path for item in files]},
    )

    prepared = []
    for item in files:
        old = item.base_content or ""
        new = item.head_content or ""
        changed = changed_new_lines(old, new) if item.status != "deleted" else set()
        if item.status == "added":
            changed = set(range(1, len(new.splitlines()) + 1))
        prepared.append(
            {
                "file": item,
                "path": item.path,
                "language": item.language,
                "content": new,
                "changed_lines": changed,
            }
        )
    _pause(settings)
    _emit(db, job, "parse", f"Parsed diffs for {len(prepared)} files. Deleted files are not executed.")

    cap = min(policy.max_repair_attempts, settings.repair_cap, 3)
    report = analyze_files(
        prepared,
        exclude_globs=list(policy.exclude_globs or []),
        token_budget=min(policy.token_budget, settings.token_budget),
    )
    python_sources = {
        item["path"]: item["content"]
        for item in prepared
        if item["language"] == "python" and item["content"] and not is_excluded(item["path"], policy.exclude_globs or [])
    }
    ruff_findings, ruff_note = run_ruff(python_sources)
    report.scanner_notes.append(ruff_note)
    report.scanner_notes.append(
        "Semgrep CLI is not required. When the binary is absent, builtin rules remain the source of deterministic findings."
    )
    occupied = {(item.file_path, item.start_line) for item in report.findings}
    for extra in ruff_findings:
        if (extra.file_path, extra.start_line) not in occupied:
            report.findings.append(extra)
    gap = coverage_gap(
        [{"path": item["path"], "content": item["content"]} for item in prepared],
        report.findings,
    )
    if gap is not None:
        report.findings.append(gap)
    for item in files:
        match = next((row for row in report.files if row.path == item.path), None)
        if match and match.skipped_reason:
            item.skipped_reason = match.skipped_reason
    job.coverage = {
        "skipped": report.skipped,
        "markers": report.markers,
        "scanner_notes": report.scanner_notes,
        "analyzer_version": ANALYZER_VERSION,
    }
    provider = build_provider(settings)
    job.analyzer_version = ANALYZER_VERSION
    job.prompt_version = PROMPT_VERSION if provider.kind == "mock" else PROVIDER_PROMPT_VERSION
    job.model_provider = provider.label
    db.commit()
    _pause(settings)
    _emit(
        db,
        job,
        "analyze",
        f"Static analysis reported {len(report.findings)} findings. {len(report.skipped)} files were skipped and recorded.",
        payload={"rules": sorted({item.rule_id for item in report.findings}), "skipped": report.skipped},
    )
    if report.markers:
        _emit(
            db,
            job,
            "analyze",
            "Prompt-injection markers were observed in repository content and did not change policy.",
            level="warn",
            payload={"markers": report.markers},
        )

    sources = {item["path"]: item["content"] for item in prepared if item["content"]}
    context = ReviewContext(
        title=pr.title,
        body=pr.body or "",
        files=[
            FileSlice(
                path=item["path"],
                content=item["content"],
                language=item["language"],
                changed_lines=set(item["changed_lines"]),
            )
            for item in prepared
            if item["content"] and not item["file"].skipped_reason and not is_excluded(item["path"], policy.exclude_globs or [])
        ],
        sources=sources,
        markers=report.markers,
        static_findings=report.findings,
    )
    result = provider.review(context)
    if provider.kind == "mock" and result.status == "completed":
        merged = merge_static_and_agents(report.findings, result.findings)
    elif result.status == "completed":
        merged = merge_static_and_model(report.findings, result.findings)
    else:
        merged = static_findings_after_provider_failure(report.findings)
    job.plan = result.plan
    traces = result.traces
    usage = result.usage
    job.usage = usage
    for trace in traces:
        db.add(
            AgentTrace(
                job_id=job.id,
                agent=trace["agent"],
                node=trace["node"],
                status=trace["status"],
                duration_ms=trace["duration_ms"],
                input_summary=trace["input_summary"],
                output_json=trace["output"],
                tool_calls=trace["tool_calls"],
                token_input=trace["token_input"],
                token_output=trace["token_output"],
                estimated_cost_usd=trace["estimated_cost_usd"],
                billed=bool(trace.get("billed")),
                retry_count=trace["retry_count"],
                error=trace["error"],
                correlation_id=job.correlation_id,
                started_at=utcnow(),
                finished_at=utcnow(),
            )
        )
    finding_rows: dict[str, Finding] = {}
    for item in merged:
        row = Finding(
            job_id=job.id,
            stable_key=item["stable_key"],
            category=item["category"],
            severity=item["severity"],
            confidence=item["confidence"],
            claim_level=item["claim_level"],
            status="open",
            validation_state="not_applicable",
            title=item["title"],
            explanation=item["explanation"],
            impact=item["impact"],
            remediation=item["remediation"],
            file_path=item["file_path"],
            start_line=item["start_line"],
            end_line=item["end_line"],
            source=item.get("source") or "static",
            rule_id=item.get("rule_id"),
            agent=item.get("agent"),
            observed=item["observed"],
            uncertain_reason=item.get("uncertain_reason"),
            enclosing_symbol=next(
                (found.enclosing_symbol for found in report.findings if found.stable_key == item["stable_key"]),
                None,
            ),
            agent_note=item.get("agent_note"),
        )
        db.add(row)
        db.flush()
        static = next((found for found in report.findings if found.stable_key == item["stable_key"]), None)
        if static is not None:
            snippet = redact_text(static.snippet, limit=500)
            kind = "static_rule" if row.source != "ruff" else "scanner"
            detail = f"{ANALYZER_VERSION}:{row.rule_id}"
        else:
            snippet = redact_text(item.get("snippet") or "", limit=500)
            kind = "model_citation"
            detail = "Cited lines were checked against the fetched file. The model did not execute the code."
        db.add(
            Evidence(
                finding_id=row.id,
                kind=kind,
                file_path=row.file_path,
                start_line=row.start_line,
                end_line=row.end_line,
                snippet=snippet,
                detail=detail,
                trusted=True,
            )
        )
        finding_rows[row.stable_key] = row
    db.commit()
    _pause(settings)
    if provider.kind == "mock":
        agent_message = f"Specialists adopted {len(merged)} findings. Commentary is labeled separately from static evidence."
    elif result.status != "completed":
        agent_message = result.error or "Model provider did not complete a validated review."
    else:
        agent_message = (
            f"Validated {len(result.findings)} model findings. {len(result.rejected)} were rejected. "
            "Remediation was not run."
        )
    _emit(
        db,
        job,
        "agents",
        agent_message,
        agent="supervisor",
        level="error" if result.status != "completed" else "info",
        payload={
            "provider": job.model_provider,
            "billed": bool(usage.get("billed")),
            "calls": result.calls,
            "status": result.status,
            "usage": usage,
        },
    )
    if result.status != "completed":
        job.status = "budget_exhausted" if result.status == "budget_exhausted" else "failed"
        job.stage = result.status
        job.error = redact_text(result.error or "Model provider failed.", limit=500)
        job.finished_at = utcnow()
        db.commit()
        _emit(db, job, result.status, job.error or "Model provider failed.", level="error", agent="provider")
        return

    if provider.kind != "mock":
        _emit(
            db,
            job,
            "remediate",
            "Model findings were stored. Automatic patch proposals and sandbox execution were not run.",
            agent="remediation",
        )
        _emit(
            db,
            job,
            "validate",
            "Validation was skipped because this review used a hosted model. Untrusted code was not executed.",
            agent="sandbox",
        )
        job.status = "completed"
        job.stage = "ready"
        job.finished_at = utcnow()
        job.error = None
        db.commit()
        _emit(db, job, "ready", "Review is ready for a human decision. Nothing was merged.", payload={"job_id": job.id})
        return

    proposals = result.proposals
    declines = list(result.declines)
    patch_rows: list[tuple[PatchProposal, dict]] = []
    allowed_paths = {item.path for item in files if item.status != "deleted"}
    for proposal in proposals:
        path = proposal["path"]
        content = proposal["content"]
        original = sources.get(path, "")
        try:
            _assert_patch_allowed(path, content, original, allowed_paths, list(policy.exclude_globs or []))
        except ValueError as exc:
            declines.append({"rule_id": proposal.get("rule_id"), "reason": str(exc), "stable_key": proposal.get("stable_key")})
            continue
        row = PatchProposal(
            job_id=job.id,
            finding_id=finding_rows[proposal["stable_key"]].id,
            status="validating",
            title=proposal["title"],
            diff_text=proposal["diff"],
            patched_files={path: content},
            target_paths=[path],
            attempt=0,
            strategy=proposal["strategy"],
            disclaimer=DISCLAIMER,
        )
        db.add(row)
        db.flush()
        finding_rows[proposal["stable_key"]].validation_state = "validating"
        patch_rows.append((row, proposal))
    for decline in declines:
        _emit(
            db,
            job,
            "remediate",
            decline["reason"],
            agent="remediation",
            payload={"rule_id": decline.get("rule_id")},
        )
    db.commit()
    _pause(settings)
    _emit(
        db,
        job,
        "remediate",
        f"Remediation proposed {len(patch_rows)} patches and declined {len(declines)}. None were applied to the repository.",
        agent="remediation",
    )

    for row, proposal in patch_rows:
        patch_id = row.id
        finding_id = row.finding_id
        rule_id = str(proposal.get("rule_id") or "")
        job_id = job.id
        try:
            _validate_patch(
                db,
                job,
                row,
                proposal,
                files,
                sources,
                finding_rows[proposal["stable_key"]],
                repo,
                policy,
                cap,
            )
        except Exception as exc:
            log.exception("patch validation failed for %s", rule_id)
            _record_validation_crash(db, job_id, patch_id, finding_id, rule_id, exc)
    job.status = "completed"
    job.stage = "ready"
    job.finished_at = utcnow()
    job.error = None
    db.commit()
    _emit(
        db,
        job,
        "ready",
        "Review is ready for a human decision. Nothing was merged.",
        payload={"job_id": job.id},
    )


def _record_validation_crash(db, job_id: str, patch_id: str, finding_id: str | None, rule_id: str, exc: Exception) -> None:
    """Store the failed launch as its own attempt and leave the rest of the review runnable.

    A sandbox crash used to abort the job before ``attempt`` left zero, which hid
    every later patch and made finding decisions look tied to validation.
    """
    db.rollback()
    job = db.get(ReviewJob, job_id)
    patch = db.get(PatchProposal, patch_id)
    if job is None or patch is None:
        return
    finding = db.get(Finding, finding_id) if finding_id else None
    message = redact_text(f"{type(exc).__name__}: {exc}", limit=500)
    latest = (
        db.query(ValidationRun)
        .filter(ValidationRun.patch_id == patch.id)
        .order_by(ValidationRun.attempt.desc())
        .first()
    )
    if latest is not None and latest.status in {"passed", "needs_review"}:
        _emit(
            db,
            job,
            "validate",
            f"Validation for {rule_id} kept the recorded {latest.status} result after a later error. {message}",
            level="warn",
            agent="sandbox",
        )
        return
    attempt = int(patch.attempt or 0) + 1
    notes = f"Validation did not produce a sandbox result. {message}"
    _store_run(
        db,
        patch,
        attempt=attempt,
        status="error",
        notes=notes,
        security_status="not_run",
        lint_status="not_run",
        suite=None,
        baseline=None,
        tests_selected=SUITES.get(rule_id, []),
    )
    patch.attempt = attempt
    patch.status = "error"
    patch.proof_scope = "not_executed"
    patch.policy_notes = notes
    if finding is not None:
        finding.validation_state = "error"
    db.commit()
    _emit(
        db,
        job,
        "validate",
        f"Validation for {rule_id} stopped before a sandbox result. {message} Finding decisions were not changed.",
        level="error",
        agent="sandbox",
        payload={"attempt": attempt, "status": "error"},
    )


def _validate_patch(db, job, patch, proposal, files, sources, finding, repo, policy, cap: int) -> None:
    settings = get_settings()
    path = proposal["path"]
    content = proposal["content"]
    rule_id = proposal["rule_id"]
    allowed, reason = _execution_allowed(repo.trust_tier, settings.sandbox_driver)
    test_paths = SUITES.get(rule_id, [])
    harness = _harness_files(rule_id)
    if not allowed:
        _store_run(
            db,
            patch,
            attempt=1,
            status="inconclusive",
            notes=reason,
            security_status="not_run",
            lint_status="not_run",
            suite=None,
            baseline=None,
            tests_selected=test_paths,
        )
        patch.status = "needs_review"
        patch.attempt = 1
        patch.proof_scope = "not_executed"
        patch.policy_notes = reason
        finding.validation_state = "needs_review"
        db.commit()
        _emit(db, job, "validate", reason, level="warn", agent="sandbox")
        return

    baseline = None
    if test_paths:
        baseline = _execute(files, sources, path, sources.get(path, ""), harness, test_paths, policy.validation_timeout_seconds)
        for case in baseline.tests:
            case.phase = "baseline"
    for attempt in range(1, cap + 1):
        issues = issues_for_rule(rule_id, content)
        rescanned = {item.rule_id for item in analyze_source(path, content)}
        rule_remains = rule_id in rescanned
        suite = None
        if test_paths:
            suite = _execute(files, sources, path, content, harness, test_paths, policy.validation_timeout_seconds)
        status, security_status, scope, notes = _judge(rule_id, rule_remains, issues, suite, test_paths)
        run = _store_run(
            db,
            patch,
            attempt=attempt,
            status=status,
            notes=notes,
            security_status=security_status,
            lint_status="not_run",
            suite=suite,
            baseline=baseline,
            tests_selected=test_paths,
        )
        patch.attempt = attempt
        patch.proof_scope = scope
        patch.patched_files = {path: content}
        patch.diff_text = unified_diff(path, sources.get(path, ""), content)
        patch.policy_notes = notes
        patch.status = status
        finding.validation_state = status
        db.commit()
        _emit(
            db,
            job,
            "validate",
            f"Validation attempt {attempt} for {rule_id} is {status}. {notes}",
            agent="sandbox",
            payload={"attempt": attempt, "status": status, "proof_scope": scope},
        )
        if status in {"passed", "needs_review"}:
            if status == "passed" and baseline is not None and suite is not None:
                _maybe_reproduce(db, finding, baseline, suite, run)
            break
        if attempt >= cap:
            break
        repaired, strategy = repair_from_failure(content, f"{notes}\n{suite.stderr if suite else ''}")
        if repaired is None or repaired == content:
            _emit(db, job, "validate", f"Repair stopped: {strategy}", agent="sandbox")
            break
        try:
            _assert_patch_allowed(path, repaired, sources.get(path, ""), {path}, list(policy.exclude_globs or []))
        except ValueError as exc:
            _emit(db, job, "validate", f"Repair rejected by policy: {exc}", level="warn", agent="sandbox")
            break
        content = repaired
        _emit(db, job, "validate", f"Repair attempt used strategy {strategy}.", agent="sandbox", payload={"strategy": strategy})
        _pause(settings)


def _maybe_reproduce(db, finding: Finding, baseline: SuiteResult, suite: SuiteResult, run: ValidationRun) -> None:
    baseline_failed = {case.name for case in baseline.tests if case.outcome in {"failed", "error"}}
    patched_failed = {case.name for case in suite.tests if case.outcome in {"failed", "error"}}
    reproduced = [name for name in baseline_failed if name not in patched_failed and "malicious" in name]
    if not reproduced:
        return
    finding.claim_level = "reproduced"
    db.add(
        Evidence(
            finding_id=finding.id,
            kind="test",
            file_path=finding.file_path,
            start_line=finding.start_line,
            end_line=finding.end_line,
            snippet="",
            detail=(
                "Baseline sandbox test failed and the patched workspace passed: "
                + ", ".join(reproduced)
                + ". This reproduces the exercised behavior. It does not prove production safety."
            ),
            trusted=True,
        )
    )
    run.notes = (run.notes + " Reproduced by baseline-to-patched test transition.").strip()
    db.commit()


def _judge(rule_id: str, rule_remains: bool, issues: list[str], suite: SuiteResult | None, test_paths: list[str]):
    security = "failed" if rule_remains or issues else "passed"
    issue_text = ", ".join(issues) if issues else "no policy violations in the patched file"
    if suite is None:
        if not test_paths and security == "passed":
            return (
                "needs_review",
                security,
                "static_rescan_only",
                f"No targeted behavioral test is registered for {rule_id}. Static re-scan: {issue_text}. A human still has to review this patch.",
            )
        if security == "failed":
            return ("failed", security, "static_rescan_only", f"Static re-scan did not clear the patch ({issue_text}).")
        return ("needs_review", security, "static_rescan_only", issue_text)
    if suite.timed_out:
        return ("timeout", security, "targeted_tests", "Sandbox timed out. The patch was not treated as successful.")
    if suite.failed == 0 and suite.passed > 0 and security == "passed":
        return (
            "passed",
            security,
            "targeted_tests_and_static_rescan",
            "Targeted tests passed and the static rule is absent. This is not proof of production safety.",
        )
    detail = suite.stderr or suite.stdout or "targeted tests failed"
    return ("failed", security, "targeted_tests_and_static_rescan", redact_text(detail, limit=1000))


def _execute(files, sources, path, content, harness, test_paths, timeout: int) -> SuiteResult:
    tree = {}
    for item in files:
        if item.head_content is None or not str(item.path).endswith(".py"):
            continue
        if item.skipped_reason:
            continue
        tree[item.path] = item.head_content
    tree[path] = content
    tree.update(harness)
    with tempfile.TemporaryDirectory(prefix="prism-sbx-") as tmp:
        root = Path(tmp)
        write_tree(root, tree)
        return run_pytest(root, test_paths, timeout)


def _store_run(db, patch, attempt, status, notes, security_status, lint_status, suite, baseline, tests_selected):
    run = ValidationRun(
        patch_id=patch.id,
        attempt=attempt,
        status=status,
        tests_selected=tests_selected,
        tests_passed=suite.passed if suite else 0,
        tests_failed=suite.failed if suite else 0,
        baseline_passed=baseline.passed if baseline else 0,
        baseline_failed=baseline.failed if baseline else 0,
        lint_status=lint_status,
        security_status=security_status,
        stdout=suite.stdout if suite else "",
        stderr=suite.stderr if suite else "",
        duration_ms=(suite.duration_ms if suite else 0) + (baseline.duration_ms if baseline and attempt == 1 else 0),
        exit_code=suite.exit_code if suite else None,
        timed_out=bool(suite and suite.timed_out),
        notes=notes,
    )
    db.add(run)
    db.flush()
    if baseline and attempt == 1:
        for case in baseline.tests:
            db.add(
                TestResult(
                    validation_run_id=run.id,
                    name=case.name,
                    outcome=case.outcome,
                    duration_ms=case.duration_ms,
                    output=case.output,
                    phase="baseline",
                )
            )
    if suite:
        for case in suite.tests:
            db.add(
                TestResult(
                    validation_run_id=run.id,
                    name=case.name,
                    outcome=case.outcome,
                    duration_ms=case.duration_ms,
                    output=case.output,
                    phase="patched",
                )
            )
    return run


def _assert_patch_allowed(path: str, content: str, original: str, allowed: set[str], globs: list[str]) -> None:
    if not safe_relative(path):
        raise ValueError("Patch path is not a safe relative path")
    if path not in allowed:
        raise ValueError("Patch path is outside the pull request")
    if is_excluded(path, globs):
        raise ValueError("Patch path is excluded by policy")
    if "eval(" in content and "eval(" not in original:
        raise ValueError("Patch introduces eval")
    if len(content) > 200_000:
        raise ValueError("Patch exceeds the size limit")


def _execution_allowed(trust_tier: str, driver: str) -> tuple[bool, str]:
    if driver == "docker":
        if shutil.which("docker") is None:
            return False, "Docker sandbox was requested but docker is not available. Untrusted code was not executed."
        return True, ""
    if trust_tier == "demo_fixture":
        return True, ""
    return False, "Refusing to execute non-demo repository code outside the Docker sandbox."


def _harness_files(rule_id: str) -> dict[str, str]:
    if rule_id != "sql_injection":
        return {}
    path = HARNESS_ROOT / "billing" / "tests" / "test_delivery.py"
    return {"tests/test_delivery.py": path.read_text()}


def _emit(db: Session, job: ReviewJob, stage: str, message: str, level: str = "info", agent: str | None = None, payload: dict | None = None) -> None:
    job.stage = stage
    current = db.query(func.max(ReviewEvent.sequence)).filter(ReviewEvent.job_id == job.id).scalar()
    db.add(
        ReviewEvent(
            job_id=job.id,
            sequence=int(current or 0) + 1,
            stage=stage,
            level=level,
            agent=agent,
            message=redact_text(message, limit=2000),
            payload=payload,
        )
    )
    db.commit()


def _pause(settings) -> None:
    if settings.stage_delay_ms > 0:
        time.sleep(settings.stage_delay_ms / 1000)


def fail_interrupted(db: Session) -> int:
    rows = db.query(ReviewJob).filter(ReviewJob.status == "running").all()
    for job in rows:
        job.status = "failed"
        job.stage = "failed"
        job.error = "Worker restarted before the review completed. Retry the job."
        job.finished_at = utcnow()
        db.commit()
        _emit(db, job, "failed", job.error, level="error")
    return len(rows)


def claim_next_id(session_factory: sessionmaker) -> str | None:
    db = session_factory()
    try:
        job = (
            db.query(ReviewJob)
            .filter(ReviewJob.status == "queued")
            .order_by(ReviewJob.created_at.asc())
            .first()
        )
        if job is None:
            return None
        result = db.execute(
            update(ReviewJob)
            .where(ReviewJob.id == job.id, ReviewJob.status == "queued")
            .values(status="running", started_at=utcnow(), stage="ingest")
        )
        db.commit()
        if result.rowcount != 1:
            return None
        return job.id
    finally:
        db.close()
