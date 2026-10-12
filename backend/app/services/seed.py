"""Demo workspace. Historical auth findings come from the real pipeline, not hand-written counters."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.demo.loader import language_for, load_side, tree_sha
from app.intelligence.diffutil import line_stats, unified_diff
from app.models import (
    GithubInstallation,
    Membership,
    PullRequest,
    PullRequestFile,
    Repository,
    ReviewPolicy,
    User,
    Workspace,
)
from app.services.actions import audit, create_review
from app.services.pipeline import process_job

REPOS = {
    "billing": {
        "owner": "acme",
        "name": "billing-service",
        "number": 142,
        "title": "Retry failed invoice webhooks and fix tax rounding",
        "body": "Retries webhook delivery, but the new path builds SQL and shell commands from the payload.",
        "author": "devon",
        "head_ref": "billing-webhook-retry",
        "run_history": False,
    },
    "auth": {
        "owner": "acme",
        "name": "auth-gateway",
        "number": 87,
        "title": "Reject pickled session payloads",
        "body": "Switches session loading to pickle so the admin worker can round-trip objects.",
        "author": "mina",
        "head_ref": "pickle-sessions",
        "run_history": True,
    },
}


def seed_demo(db: Session, session_factory, *, process_history: bool = True) -> None:
    if db.query(Workspace).filter(Workspace.slug == "prism-demo").one_or_none():
        return
    workspace = Workspace(name="Prism Demo", slug="prism-demo", demo=True)
    user = User(email="demo@prism.local", name="Avery Chen")
    db.add_all([workspace, user])
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    installation = GithubInstallation(
        workspace_id=workspace.id,
        account_login="acme",
        installation_id=None,
        provider="demo",
        status="demo",
    )
    db.add(installation)
    db.flush()
    for key, spec in REPOS.items():
        base = load_side(key, "base")
        head = load_side(key, "head")
        repo = Repository(
            workspace_id=workspace.id,
            installation_id=installation.id,
            owner=spec["owner"],
            name=spec["name"],
            default_branch="main",
            provider="demo",
            html_url=f"https://github.com/{spec['owner']}/{spec['name']}",
            connection_status="demo",
            primary_language="python",
            trust_tier="demo_fixture",
        )
        db.add(repo)
        db.flush()
        db.add(
            ReviewPolicy(
                repository_id=repo.id,
                severity_threshold="low",
                github_min_severity="high",
                required_checks=["static-analysis", "sandbox-validation"],
                exclude_globs=["**/vendor/**", "vendor/**", "**/.git/**", "**/dist/**"],
                model_provider="mock",
                token_budget=24_000,
                max_repair_attempts=3,
                validation_timeout_seconds=20,
                post_github_comments=False,
            )
        )
        pull = PullRequest(
            repository_id=repo.id,
            number=spec["number"],
            title=spec["title"],
            body=spec["body"],
            author=spec["author"],
            base_ref="main",
            head_ref=spec["head_ref"],
            base_sha=tree_sha(base),
            head_sha=tree_sha(head),
            state="open",
            html_url=f"https://github.com/{spec['owner']}/{spec['name']}/pull/{spec['number']}",
        )
        db.add(pull)
        db.flush()
        for path in sorted(set(base) | set(head)):
            old = base.get(path)
            new = head.get(path)
            if old == new:
                continue
            if old is None:
                status = "added"
            elif new is None:
                status = "deleted"
            else:
                status = "modified"
            additions, deletions = line_stats(old or "", new or "")
            db.add(
                PullRequestFile(
                    pull_request_id=pull.id,
                    path=path,
                    status=status,
                    language=language_for(path),
                    additions=additions,
                    deletions=deletions,
                    patch=unified_diff(path, old or "", new or ""),
                    base_content=old,
                    head_content=new,
                )
            )
        db.commit()
        if process_history and spec["run_history"]:
            job, _created = create_review(
                db,
                pull,
                workspace_id=workspace.id,
                trigger="manual",
                origin="seed",
                idempotency_key=f"seed:{repo.owner}/{repo.name}#{pull.number}",
            )
            process_job(job.id, session_factory)
    audit(db, workspace.id, user.id, "demo_seeded", "workspace", workspace.id, {"mode": "demo"})
    db.commit()
