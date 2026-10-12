# Implementation checklist

Status is what has been run, not what the product spec hoped for.

## Done

- Domain model, Alembic revision, upgrade/downgrade test on SQLite.
- Demo seed: `acme/billing-service` #142 (not pre-reviewed) and `acme/auth-gateway` #87 (pipeline history).
- Review job creation, idempotent reuse of a queued job, retry, startup failure of interrupted jobs.
- Diff parse, changed-line filter, tree-sitter symbols with a regex fallback, exclusion and token-budget skips recorded.
- Builtin rules: SQL injection, `shell=True`, hardcoded secret, `eval`/`exec`, `pickle.loads`, float currency, query-in-loop, missing auth, test gap.
- Ruff S-rules where Ruff is installed. `S101` ignored. Semgrep status recorded, not faked.
- LangGraph fan-out: supervisor, security, correctness, performance, tests, remediation. Structured outputs. Deduped with static findings.
- Patch proposals for SQL, secret, and shell. Declines for auth, currency, N+1, test gap, and pickle. Repair cap of 3. Secret empty-default repair is a real second attempt.
- Subprocess validation for demo fixtures, including the SQL harness (baseline fails, patched test passes). Docker argument builder unit-tested. Untrusted repos are not executed.
- Human confirm, dismiss, reanalyze, patch edit, accept, reject, local report. Accept cannot modify the repo.
- Webhook HMAC, delivery idempotency, GitHub host allowlist.
- Evaluation fixture suite with precision, recall, and residual risks.
- Audit log, settings, command center metrics computed from stored jobs. Null rates when the denominator is zero.
- Next.js command center, pull-request workspace, diff, trace, validation studio, policy editor, evaluation, audit, settings. Command palette. Live refetch and SSE.
- Backend pytest, Ruff, frontend `tsc`, and ESLint.

## Not done

- GitHub App installation UI, check runs, and published review comments.
- Hosted LLM provider. The mock is the only executor.
- Redis/ARQ queue. The worker polls the database. Redis in Compose is unused.
- Postgres exercised in this environment. Compose was not started. `psycopg` 3.3.6 is pinned for that URL.
- Docker sandbox execution. No daemon here, and Compose does not pass the socket.
- Semgrep CLI, OpenTelemetry export, Kubernetes, multi-process rate limits, and real user authentication.
- Re-running validation after a manual patch edit.

## Phase order from here

1. Add `psycopg`, run Compose against Postgres, and keep SQLite for the default laptop path.
2. Publish a GitHub check and a summary comment from an approved report, with line validation.
3. Put a real provider behind the existing Pydantic schemas without letting it override policy.
4. Move sandbox execution to a stronger runner when a deployment target exists.
