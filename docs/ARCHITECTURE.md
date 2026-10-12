# Architecture

PRISM separates deterministic work from model work.

Webhook verification, diff parsing, path policy, database writes, static analysis, and test execution are ordinary Python. LangGraph coordinates the specialist agents and the bounded remediation step. A repository file, comment, or `.prism.yml` cannot change those limits.

## Request path

1. The browser talks only to Next.js on port 43123.
2. `frontend/src/app/api/v1/[...path]/route.ts` proxies to the API and streams response bodies, including server-sent events.
3. Mutations from a foreign `Origin` are rejected by the API, except `/api/v1/webhooks/github`.
4. `POST /api/v1/reviews` inserts a job and returns `202`. The embedded worker claims it with a conditional `queued → running` update.
5. The UI refetches while a job is queued or running, and also listens to `GET /api/v1/reviews/{id}/stream`. Reconnects can pass `Last-Event-ID`.

## Review pipeline

`process_job` in `backend/app/services/pipeline.py`:

1. **Ingest / parse.** Load the pinned demo tree or, later, a fetched diff. Record skipped files (vendor, excluded globs, token budget). Skipping is persisted, not silent.
2. **Analyze.** Builtin rules plus Ruff S-rules. Assert-style `S101` is dropped. Diagnostics that land on the same path and line as a builtin finding are deduped.
3. **Agents.** Supervisor fans out to security, correctness, performance, and tests. Outputs are Pydantic models with `extra=forbid`. High and critical findings cannot be `claim_level=hypothesis`. Agents adopt static findings; they do not invent a confirmed vulnerability. Static findings an agent omits are kept.
4. **Remediate.** A patch is a file rewrite plus a unified diff, not a shell command. Some rules are declined because there is no mechanical patch.
5. **Validate.** Only `trust_tier=demo_fixture` may use the subprocess driver. The SQL patch is checked with a harness test: the vulnerable f-string fails the baseline, the parameterized form passes. Secret and shell patches are policy or static rescans and stay `needs_review`. Repair stops at three attempts.
6. **Ready.** A disclaimer is stored: passing tests are not production safety.

Claim levels are `pattern`, `hypothesis`, and `reproduced`. SQL injection becomes `reproduced` only after the baseline fails and the patched test passes.

## Agents

`backend/app/agents/graph.py` uses a LangGraph state with list reducers so parallel nodes append traces instead of overwriting them. The mock provider templates commentary from structured fields. Source text is not copied into the note, so a prompt-injection comment cannot rewrite the review.

Prompt version `prism-agents-2026.10.1` and analyzer version `builtin-0.4.0` are stored on the job and on evaluation runs.

## Persistence

SQLAlchemy 2 models and one Alembic revision cover users, workspaces, memberships, installations, repositories, policies, pull requests, files, jobs, events, findings, evidence, patches, validation runs, test results, traces, decisions, audit events, evaluation runs, webhook deliveries, and reports. Jobs survive process restarts. Interrupted `running` jobs are failed on startup. Webhook delivery ids are unique.

The local default is SQLite. Compose points at PostgreSQL. JSON columns are portable.

## Sandbox interface

`backend/app/services/sandbox.py` builds either a subprocess invocation or a `docker run` argument list. The Docker list drops capabilities, sets `no-new-privileges`, `--network=none`, a non-root user, a read-only root, a tmpfs `/tmp`, and CPU, memory, pid, and time limits. It does not mount the Docker socket or host credentials. The subprocess environment is an allowlist, with `RLIMIT_CPU`, `RLIMIT_AS`, and `RLIMIT_FSIZE`. Logs are scrubbed of host paths and secret-shaped tokens.

Docker is not a security boundary by itself. The interface is a function that returns a command, so a later microVM runner can replace it without changing the pipeline’s decision rules.

## GitHub

`POST /api/v1/webhooks/github` checks `X-Hub-Signature-256`, requires `X-GitHub-Delivery`, and acknowledges quickly. Duplicate deliveries are ignored. Unknown repositories are ignored. The client allows only `https://api.github.com`. Demo reviews pin the stored commit SHA and never clone a payload URL.

Publishing a report writes `delivery_status=stored_locally_demo` and does not call GitHub.
