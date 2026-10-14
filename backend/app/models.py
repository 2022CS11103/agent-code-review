"""Normalized persistence model for reviews, evidence, patches, and audit history."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workspace: Mapped[Workspace] = relationship()
    user: Mapped[User] = relationship()


class GithubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    account_login: Mapped[str] = mapped_column(String(200))
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="demo")
    status: Mapped[str] = mapped_column(String(32), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("workspace_id", "owner", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str | None] = mapped_column(
        ForeignKey("github_installations.id", ondelete="SET NULL"), nullable=True
    )
    owner: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    provider: Mapped[str] = mapped_column(String(32), default="demo")
    html_url: Mapped[str] = mapped_column(String(500), default="")
    connection_status: Mapped[str] = mapped_column(String(32), default="demo")
    primary_language: Mapped[str] = mapped_column(String(64), default="python")
    trust_tier: Mapped[str] = mapped_column(String(32), default="untrusted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    policy: Mapped[ReviewPolicy | None] = relationship(back_populates="repository", uselist=False)
    pulls: Mapped[list[PullRequest]] = relationship(back_populates="repository")


class ReviewPolicy(Base):
    __tablename__ = "review_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), unique=True
    )
    severity_threshold: Mapped[str] = mapped_column(String(16), default="low")
    github_min_severity: Mapped[str] = mapped_column(String(16), default="high")
    required_checks: Mapped[list] = mapped_column(JSON, default=list)
    exclude_globs: Mapped[list] = mapped_column(JSON, default=list)
    model_provider: Mapped[str] = mapped_column(String(64), default="mock")
    token_budget: Mapped[int] = mapped_column(Integer, default=24_000)
    max_repair_attempts: Mapped[int] = mapped_column(Integer, default=3)
    validation_timeout_seconds: Mapped[int] = mapped_column(Integer, default=20)
    post_github_comments: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    repository: Mapped[Repository] = relationship(back_populates="policy")


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(200))
    base_ref: Mapped[str] = mapped_column(String(200))
    head_ref: Mapped[str] = mapped_column(String(200))
    base_sha: Mapped[str] = mapped_column(String(64))
    head_sha: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="open")
    html_url: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    repository: Mapped[Repository] = relationship(back_populates="pulls")
    files: Mapped[list[PullRequestFile]] = relationship(back_populates="pull_request", cascade="all, delete-orphan")
    jobs: Mapped[list[ReviewJob]] = relationship(back_populates="pull_request")


class PullRequestFile(Base):
    __tablename__ = "pull_request_files"
    __table_args__ = (Index("ix_pr_files_pr_path", "pull_request_id", "path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pull_request_id: Mapped[str] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(500))
    previous_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(64), default="text")
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    patch: Mapped[str] = mapped_column(Text, default="")
    base_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    pull_request: Mapped[PullRequest] = relationship(back_populates="files")


class ReviewJob(Base):
    __tablename__ = "review_jobs"
    __table_args__ = (
        Index("ix_review_jobs_status", "status"),
        Index("ix_review_jobs_pr", "pull_request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    pull_request_id: Mapped[str] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    origin: Mapped[str] = mapped_column(String(32), default="live")
    head_sha: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(36), default=new_id, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    coverage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    analyzer_version: Mapped[str] = mapped_column(String(64), default="")
    model_provider: Mapped[str] = mapped_column(String(64), default="mock")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    pull_request: Mapped[PullRequest] = relationship(back_populates="jobs")
    events: Mapped[list[ReviewEvent]] = relationship(back_populates="job", cascade="all, delete-orphan")
    findings: Mapped[list[Finding]] = relationship(back_populates="job", cascade="all, delete-orphan")
    patches: Mapped[list[PatchProposal]] = relationship(back_populates="job", cascade="all, delete-orphan")
    traces: Mapped[list[AgentTrace]] = relationship(back_populates="job", cascade="all, delete-orphan")


class ReviewEvent(Base):
    __tablename__ = "review_events"
    __table_args__ = (UniqueConstraint("job_id", "sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("review_jobs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="info")
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[ReviewJob] = relationship(back_populates="events")


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_job", "job_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("review_jobs.id", ondelete="CASCADE"))
    stable_key: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    claim_level: Mapped[str] = mapped_column(String(32), default="pattern")
    status: Mapped[str] = mapped_column(String(32), default="open")
    validation_state: Mapped[str] = mapped_column(String(32), default="not_applicable")
    title: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(Text, default="")
    remediation: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(500))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="static")
    rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed: Mapped[bool] = mapped_column(Boolean, default=True)
    uncertain_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    enclosing_symbol: Mapped[str | None] = mapped_column(String(200), nullable=True)
    agent_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[ReviewJob] = relationship(back_populates="findings")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="finding", cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snippet: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    trusted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    finding: Mapped[Finding] = relationship(back_populates="evidence")


class PatchProposal(Base):
    __tablename__ = "patch_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("review_jobs.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("findings.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    title: Mapped[str] = mapped_column(String(200))
    diff_text: Mapped[str] = mapped_column(Text, default="")
    patched_files: Mapped[dict] = mapped_column(JSON, default=dict)
    target_paths: Mapped[list] = mapped_column(JSON, default=list)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    proof_scope: Mapped[str | None] = mapped_column(String(80), nullable=True)
    policy_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    disclaimer: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[ReviewJob] = relationship(back_populates="patches")
    validations: Mapped[list[ValidationRun]] = relationship(
        back_populates="patch", cascade="all, delete-orphan"
    )


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patch_id: Mapped[str] = mapped_column(ForeignKey("patch_proposals.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    tests_selected: Mapped[list] = mapped_column(JSON, default=list)
    tests_passed: Mapped[int] = mapped_column(Integer, default=0)
    tests_failed: Mapped[int] = mapped_column(Integer, default=0)
    baseline_passed: Mapped[int] = mapped_column(Integer, default=0)
    baseline_failed: Mapped[int] = mapped_column(Integer, default=0)
    lint_status: Mapped[str] = mapped_column(String(32), default="not_run")
    security_status: Mapped[str] = mapped_column(String(32), default="not_run")
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    patch: Mapped[PatchProposal] = relationship(back_populates="validations")
    tests: Mapped[list[TestResult]] = relationship(back_populates="validation", cascade="all, delete-orphan")


class TestResult(Base):
    __tablename__ = "test_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    validation_run_id: Mapped[str] = mapped_column(
        ForeignKey("validation_runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    outcome: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase: Mapped[str] = mapped_column(String(32), default="patched")

    validation: Mapped[ValidationRun] = relationship(back_populates="tests")


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("review_jobs.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    node: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    token_input: Mapped[int] = mapped_column(Integer, default=0)
    token_output: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    billed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    correlation_id: Mapped[str] = mapped_column(String(36), default="")

    job: Mapped[ReviewJob] = relationship(back_populates="traces")


class UserDecision(Base):
    __tablename__ = "user_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), nullable=True)
    patch_id: Mapped[str | None] = mapped_column(
        ForeignKey("patch_proposals.id", ondelete="CASCADE"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_workspace_created", "workspace_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    fixture_set: Mapped[str] = mapped_column(String(80))
    analyzer_version: Mapped[str] = mapped_column(String(64))
    model_provider: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    f1: Mapped[float | None] = mapped_column(Float, nullable=True)
    false_positive_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    true_positive: Mapped[int] = mapped_column(Integer, default=0)
    false_positive: Mapped[int] = mapped_column(Integer, default=0)
    false_negative: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    residual_risks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GithubAppCredential(Base):
    """One GitHub App created from the in-product manifest. The key never leaves the API."""

    __tablename__ = "github_app_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    app_id: Mapped[str] = mapped_column(String(32), unique=True)
    slug: Mapped[str] = mapped_column(String(80))
    private_key: Mapped[str] = mapped_column(Text)
    webhook_secret: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    delivery_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    event_name: Mapped[str] = mapped_column(String(80))
    action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    outcome: Mapped[str] = mapped_column(String(80))
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewReport(Base):
    __tablename__ = "review_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("review_jobs.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    destination: Mapped[str] = mapped_column(String(32), default="local")
    delivery_status: Mapped[str] = mapped_column(String(64), default="stored_locally")
    redacted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
