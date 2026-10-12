"""Turn signed GitHub events into stored repositories, pulls, and review jobs.

File contents come from the GitHub API at the pinned SHA. Webhook fields are
validated and are not executed. Clone URLs are ignored.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.demo.loader import language_for
from app.models import (
    GithubInstallation,
    PullRequest,
    PullRequestFile,
    Repository,
    ReviewPolicy,
    Workspace,
)
from app.services.actions import audit, create_review
from app.services.github_app import github_client_for_installation
from app.services.github_client import GithubClientError, HttpGithubClient

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
REQUIRED_PERMISSIONS = {
    "contents": "read",
    "pull_requests": "write",
    "checks": "write",
    "metadata": "read",
}
FILE_CAP = 100


def handle_event(db: Session, event_name: str, payload: dict) -> tuple[str | None, str | None, str]:
    if event_name == "ping":
        return None, None, "pong"
    if event_name == "installation":
        return None, *_installation(db, payload)
    if event_name == "installation_repositories":
        return None, *_installation_repositories(db, payload)
    if event_name == "pull_request" and payload.get("action") in {"opened", "synchronize", "reopened"}:
        return enqueue_pull_request(db, payload)
    return None, None, "ignored"


def enqueue_pull_request(db: Session, payload: dict) -> tuple[str | None, str | None, str]:
    owner, name = _repository_name(payload.get("repository"))
    if owner is None or name is None:
        return None, None, "invalid_repository"
    number = _pull_number(payload.get("pull_request"))
    sha = _head_sha(payload.get("pull_request"))
    if number is None:
        return None, None, "invalid_pull_request"
    if sha is None:
        return None, None, "invalid_sha"
    repo = db.query(Repository).filter(Repository.owner == owner, Repository.name == name).one_or_none()
    if repo is not None and repo.trust_tier == "demo_fixture":
        return _enqueue_demo(db, repo, number, sha)
    if not isinstance(payload.get("installation"), dict):
        audit(db, None, None, "webhook_ignored", "repository", None, {"reason": "unknown_repository", "name": f"{owner}/{name}"})
        return None, None, "unknown_repository"
    installation = _active_installation(db, payload.get("installation"))
    if isinstance(installation, str):
        return None, None, installation
    if repo is None:
        repo = _create_repo(db, installation, owner, name)
    elif repo.installation_id != installation.id:
        audit(db, repo.workspace_id, None, "webhook_ignored", "repository", repo.id, {"reason": "installation_mismatch"})
        return None, repo.workspace_id, "installation_mismatch"
    pull = _upsert_pull_stub(db, repo, payload.get("pull_request"), number, sha)
    job, _created = create_review(db, pull, workspace_id=repo.workspace_id, trigger="webhook", origin="webhook")
    return job.id, repo.workspace_id, "queued"


def refresh_pull_from_github(db: Session, repo: Repository, pull: PullRequest, client: HttpGithubClient | None = None) -> None:
    if repo.provider != "github":
        return
    installation = db.get(GithubInstallation, repo.installation_id) if repo.installation_id else None
    if installation is None or installation.installation_id is None or installation.status != "active":
        raise GithubClientError("GitHub installation is not active")
    owned = client or github_client_for_installation(installation.installation_id)
    try:
        remote = owned.get_pull(repo.owner, repo.name, pull.number)
        head = ((remote.get("head") or {}) if isinstance(remote.get("head"), dict) else {}).get("sha")
        base = ((remote.get("base") or {}) if isinstance(remote.get("base"), dict) else {}).get("sha")
        if not isinstance(head, str) or not SHA_RE.match(head) or head != pull.head_sha:
            raise GithubClientError("GitHub head SHA does not match the pinned review")
        if not isinstance(base, str) or not SHA_RE.match(base):
            raise GithubClientError("GitHub base SHA was rejected")
        files = owned.get_files(repo.owner, repo.name, pull.number)
        _replace_files(db, owned, repo, pull, files, head, base)
        pull.base_sha = base
        pull.title = _text(remote.get("title"), 300) or pull.title
        pull.body = _text(remote.get("body"), 20_000)
        user = remote.get("user") if isinstance(remote.get("user"), dict) else {}
        login = user.get("login")
        if isinstance(login, str) and NAME_RE.match(login):
            pull.author = login
        db.commit()
    finally:
        if client is None:
            owned.close()


def commentable_new_lines(patch: str) -> set[int]:
    lines: set[int] = set()
    new_line: int | None = None
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            new_line = int(match.group(1)) if match else None
            continue
        if new_line is None or raw.startswith("\\"):
            continue
        if raw.startswith("-"):
            continue
        lines.add(new_line)
        new_line += 1
    return lines


def plan_review_comments(findings: list, files: dict[str, str], min_severity: str) -> tuple[list[dict], list[str]]:
    floor = SEVERITY_RANK.get(min_severity, 3)
    comments: list[dict] = []
    omitted: list[str] = []
    seen: set[tuple[str, int]] = set()
    for finding in findings:
        if finding.status == "dismissed":
            continue
        if SEVERITY_RANK.get(finding.severity, 0) < floor:
            continue
        patch = files.get(finding.file_path) or ""
        anchor = finding.start_line if finding.start_line in commentable_new_lines(patch) else None
        label = f"{finding.file_path}:{finding.start_line} {finding.title}"
        if anchor is None or (finding.file_path, anchor) in seen:
            omitted.append(label)
            continue
        seen.add((finding.file_path, anchor))
        comments.append(
            {
                "path": finding.file_path,
                "line": anchor,
                "side": "RIGHT",
                "body": (
                    f"**{finding.severity} · {finding.claim_level}** {finding.title}\n\n"
                    f"{finding.explanation}\n\n"
                    "This comment is not an approval and does not merge the pull request."
                )[:6000],
            }
        )
    return comments, omitted


def _enqueue_demo(db: Session, repo: Repository, number: int, sha: str) -> tuple[str | None, str | None, str]:
    stored = (
        db.query(PullRequest)
        .filter(PullRequest.repository_id == repo.id, PullRequest.number == number)
        .one_or_none()
    )
    if stored is None:
        audit(db, repo.workspace_id, None, "webhook_ignored", "pull_request", None, {"reason": "pull_not_ingested", "number": number})
        return None, repo.workspace_id, "pull_not_ingested"
    if stored.head_sha != sha:
        audit(
            db,
            repo.workspace_id,
            None,
            "webhook_ignored",
            "pull_request",
            stored.id,
            {"reason": "sha_not_pinned_locally", "payload_sha": sha, "stored_sha": stored.head_sha},
        )
        return None, repo.workspace_id, "sha_not_pinned_locally"
    job, _created = create_review(db, stored, workspace_id=repo.workspace_id, trigger="webhook", origin="webhook")
    return job.id, repo.workspace_id, "queued"


def _installation(db: Session, payload: dict) -> tuple[str | None, str]:
    parsed = _installation_record(payload.get("installation"))
    if parsed is None:
        return None, "invalid_installation"
    inst_id, login, permissions = parsed
    action = payload.get("action")
    workspace = _demo_workspace(db)
    if workspace is None:
        return None, "no_workspace"
    row = db.query(GithubInstallation).filter(GithubInstallation.installation_id == inst_id).one_or_none()
    if action == "deleted":
        if row is not None:
            row.status = "deleted"
            _mark_repos(db, row, "removed")
        audit(db, workspace.id, None, "github_installation_deleted", "github_installation", str(inst_id), {})
        return workspace.id, "installation_deleted"
    if action == "suspend":
        if row is not None:
            row.status = "suspended"
        return workspace.id, "installation_suspended"
    if action not in {"created", "unsuspend", "new_permissions_accepted"}:
        return workspace.id, "ignored"
    status, excess = _permission_status(permissions)
    if row is None:
        row = GithubInstallation(
            workspace_id=workspace.id,
            account_login=login,
            installation_id=inst_id,
            provider="github",
            status=status,
        )
        db.add(row)
        db.flush()
    else:
        row.account_login = login
        row.provider = "github"
        row.status = status
    repositories = payload.get("repositories")
    for item in repositories if isinstance(repositories, list) else []:
        _repo_from_installation_payload(db, row, item)
    audit(
        db,
        workspace.id,
        None,
        "github_installation_recorded",
        "github_installation",
        str(inst_id),
        {"status": status, "excess_permissions": excess},
    )
    return workspace.id, "installation_recorded" if status == "active" else "installation_permissions_incomplete"


def _installation_repositories(db: Session, payload: dict) -> tuple[str | None, str]:
    parsed = _installation_record(payload.get("installation"))
    if parsed is None:
        return None, "invalid_installation"
    inst_id, _login, _permissions = parsed
    row = db.query(GithubInstallation).filter(GithubInstallation.installation_id == inst_id).one_or_none()
    if row is None or row.status == "deleted":
        return None, "unknown_installation"
    action = payload.get("action")
    if action == "added":
        added = payload.get("repositories_added")
        for item in added if isinstance(added, list) else []:
            _repo_from_installation_payload(db, row, item)
        return row.workspace_id, "repositories_added"
    if action == "removed":
        removed = payload.get("repositories_removed")
        for item in removed if isinstance(removed, list) else []:
            owner, name = _full_name(item)
            if owner is None or name is None:
                continue
            repo = (
                db.query(Repository)
                .filter(Repository.installation_id == row.id, Repository.owner == owner, Repository.name == name)
                .one_or_none()
            )
            if repo is not None:
                repo.connection_status = "removed"
        return row.workspace_id, "repositories_removed"
    return row.workspace_id, "ignored"


def _replace_files(db: Session, client: HttpGithubClient, repo: Repository, pull: PullRequest, files: list, head: str, base: str) -> None:
    db.query(PullRequestFile).filter(PullRequestFile.pull_request_id == pull.id).delete()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            continue
        path = item.get("filename")
        if not isinstance(path, str) or not _safe_path(path):
            continue
        if index >= FILE_CAP:
            db.add(
                PullRequestFile(
                    pull_request_id=pull.id,
                    path=path[:500],
                    status="skipped",
                    language=language_for(path),
                    patch="",
                    skipped_reason="Changed-file list was truncated at 100 files and this file was not fetched.",
                )
            )
            continue
        status = _file_status(item.get("status"))
        patch = item.get("patch") if isinstance(item.get("patch"), str) else ""
        head_content = None
        base_content = None
        skipped = None
        try:
            if status != "deleted":
                head_content = client.get_text(repo.owner, repo.name, path, head)
            previous = item.get("previous_filename") if isinstance(item.get("previous_filename"), str) else path
            if status != "added" and _safe_path(previous):
                base_content = client.get_text(repo.owner, repo.name, previous, base)
        except GithubClientError as exc:
            skipped = str(exc)[:300]
        db.add(
            PullRequestFile(
                pull_request_id=pull.id,
                path=path[:500],
                previous_path=item.get("previous_filename") if isinstance(item.get("previous_filename"), str) else None,
                status=status,
                language=language_for(path),
                additions=item.get("additions") if isinstance(item.get("additions"), int) else 0,
                deletions=item.get("deletions") if isinstance(item.get("deletions"), int) else 0,
                patch=patch[:200_000],
                base_content=base_content,
                head_content=head_content,
                skipped_reason=skipped,
            )
        )


def _upsert_pull_stub(db: Session, repo: Repository, pull_payload: dict, number: int, sha: str) -> PullRequest:
    stored = (
        db.query(PullRequest)
        .filter(PullRequest.repository_id == repo.id, PullRequest.number == number)
        .one_or_none()
    )
    head = pull_payload.get("head") if isinstance(pull_payload.get("head"), dict) else {}
    base = pull_payload.get("base") if isinstance(pull_payload.get("base"), dict) else {}
    base_sha = base.get("sha") if isinstance(base.get("sha"), str) and SHA_RE.match(base.get("sha")) else sha
    author = pull_payload.get("user") if isinstance(pull_payload.get("user"), dict) else {}
    login = author.get("login") if isinstance(author.get("login"), str) and NAME_RE.match(author.get("login")) else "unknown"
    if stored is None:
        stored = PullRequest(
            repository_id=repo.id,
            number=number,
            title=_text(pull_payload.get("title"), 300) or f"Pull request {number}",
            body=_text(pull_payload.get("body"), 20_000),
            author=login,
            base_ref=_ref(base.get("ref")) or repo.default_branch,
            head_ref=_ref(head.get("ref")) or "unknown",
            base_sha=base_sha,
            head_sha=sha,
            state="open",
            html_url=_html(pull_payload.get("html_url"), repo, number),
        )
        db.add(stored)
        db.flush()
        return stored
    if stored.head_sha != sha:
        db.query(PullRequestFile).filter(PullRequestFile.pull_request_id == stored.id).delete()
        stored.head_sha = sha
    stored.title = _text(pull_payload.get("title"), 300) or stored.title
    stored.base_ref = _ref(base.get("ref")) or stored.base_ref
    stored.head_ref = _ref(head.get("ref")) or stored.head_ref
    stored.base_sha = base_sha
    db.flush()
    return stored


def _create_repo(db: Session, installation: GithubInstallation, owner: str, name: str) -> Repository:
    repo = Repository(
        workspace_id=installation.workspace_id,
        installation_id=installation.id,
        owner=owner,
        name=name,
        provider="github",
        html_url=f"https://github.com/{owner}/{name}",
        connection_status="installed",
        trust_tier="untrusted",
        primary_language="unknown",
    )
    db.add(repo)
    db.flush()
    db.add(
        ReviewPolicy(
            repository_id=repo.id,
            severity_threshold="low",
            github_min_severity="high",
            required_checks=["static-analysis"],
            exclude_globs=["**/vendor/**", "vendor/**", "**/.git/**", "**/dist/**"],
            model_provider="mock",
            token_budget=24_000,
            max_repair_attempts=3,
            validation_timeout_seconds=20,
            post_github_comments=False,
        )
    )
    db.flush()
    return repo


def _repo_from_installation_payload(db: Session, installation: GithubInstallation, item: dict) -> None:
    if not isinstance(item, dict):
        return
    owner, name = _full_name(item)
    if owner is None or name is None:
        return
    existing = (
        db.query(Repository)
        .filter(Repository.workspace_id == installation.workspace_id, Repository.owner == owner, Repository.name == name)
        .one_or_none()
    )
    if existing is None:
        _create_repo(db, installation, owner, name)
        return
    if existing.trust_tier == "demo_fixture":
        return
    existing.installation_id = installation.id
    existing.provider = "github"
    existing.connection_status = "installed" if installation.status == "active" else installation.status
    existing.trust_tier = "untrusted"


def _active_installation(db: Session, payload) -> GithubInstallation | str:
    if not isinstance(payload, dict) or isinstance(payload.get("id"), bool) or not isinstance(payload.get("id"), int):
        return "unknown_installation"
    row = db.query(GithubInstallation).filter(GithubInstallation.installation_id == payload["id"]).one_or_none()
    if row is None:
        return "unknown_installation"
    if row.status == "permissions_incomplete":
        return "installation_not_ready"
    if row.status != "active":
        return "installation_not_ready"
    return row


def _installation_record(payload) -> tuple[int, str, dict] | None:
    if not isinstance(payload, dict) or isinstance(payload.get("id"), bool) or not isinstance(payload.get("id"), int):
        return None
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    login = account.get("login")
    if not isinstance(login, str) or not NAME_RE.match(login):
        return None
    permissions = payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {}
    return payload["id"], login, permissions


def _permission_status(permissions: dict) -> tuple[str, list[str]]:
    missing = []
    for name, level in REQUIRED_PERMISSIONS.items():
        granted = permissions.get(name)
        if name == "metadata" and granted is None:
            continue
        if granted not in {level, "write"}:
            missing.append(name)
    excess = []
    if permissions.get("contents") == "write":
        excess.append("contents:write")
    if permissions.get("administration"):
        excess.append("administration")
    if missing:
        return "permissions_incomplete", excess
    return "active", excess


def _repository_name(payload) -> tuple[str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    return _full_name(payload)


def _full_name(payload: dict) -> tuple[str | None, str | None]:
    full = payload.get("full_name")
    if isinstance(full, str) and full.count("/") == 1:
        owner, name = full.split("/", 1)
        if NAME_RE.match(owner) and NAME_RE.match(name):
            return owner, name
    return None, None


def _pull_number(payload) -> int | None:
    if not isinstance(payload, dict):
        return None
    number = payload.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        return None
    return number


def _head_sha(payload) -> str | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("head"), dict):
        return None
    sha = payload["head"].get("sha")
    if isinstance(sha, str) and SHA_RE.match(sha):
        return sha
    return None


def _demo_workspace(db: Session) -> Workspace | None:
    return db.query(Workspace).filter(Workspace.slug == "prism-demo").one_or_none()


def _mark_repos(db: Session, installation: GithubInstallation, status: str) -> None:
    repos = db.query(Repository).filter(Repository.installation_id == installation.id, Repository.trust_tier != "demo_fixture").all()
    for repo in repos:
        repo.connection_status = status


def _file_status(value) -> str:
    if value == "removed":
        return "deleted"
    if value in {"added", "modified", "renamed", "deleted"}:
        return value
    return "modified"


def _safe_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    return ".." not in path.split("/")


def _text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _ref(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        return ""
    if any(char in value for char in "\x00\n\r") or value.startswith("-") or ".." in value:
        return ""
    return value


def _html(value, repo: Repository, number: int) -> str:
    if isinstance(value, str) and value.startswith(f"https://github.com/{repo.owner}/{repo.name}/"):
        return value[:500]
    return f"https://github.com/{repo.owner}/{repo.name}/pull/{number}"
