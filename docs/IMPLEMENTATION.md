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
- Review provider interface. The default deterministic provider makes no model call. Gemini and Groq use the same validated chat client, with their own API keys and default models. Tests use a mocked HTTP transport. A live model API was not called.
- Patch proposals for SQL, secret, and shell. Declines for auth, currency, N+1, test gap, and pickle. Repair cap of 3. Secret empty-default repair is a real second attempt.
- Subprocess validation for demo fixtures, including the SQL harness (baseline fails, patched test passes). Docker argument builder unit-tested. Untrusted repos are not executed.
- Human confirm, dismiss, reanalyze, patch edit, accept, reject, local report. Accept cannot modify the repo.
- Webhook HMAC, delivery idempotency, GitHub host allowlist.
- GitHub App JWT exchange, installation webhooks, pull-request fetch at the pinned SHA, check runs, and summary comments. Mocked API tests cover this. A live GitHub repository was not connected here.
- Evaluation fixture suite with precision, recall, and residual risks.
- Audit log, settings, command center metrics computed from stored jobs. Null rates when the denominator is zero.
- Next.js command center, pull-request workspace, diff, trace, validation studio, policy editor, evaluation, audit, settings. Command palette. Live refetch and SSE.
- Backend pytest, Ruff, frontend `tsc`, and ESLint.

## Not done

- A live GitHub App round trip. The code path is tested against a mock API only.
- A live model API call. The OpenAI-compatible client is implemented and tested against a mock transport. No API key was used against a hosted model.
- Redis/ARQ queue. The worker polls the database. Redis in Compose is unused.
- Postgres exercised in this environment. Compose was not started. `psycopg` 3.3.6 is pinned for that URL.
- Docker sandbox execution. No daemon here, and Compose does not pass the socket.
- Semgrep CLI, OpenTelemetry export, Kubernetes, multi-process rate limits, and real user authentication.
- Re-running validation after a manual patch edit.

## Phase order from here

1. Verify the GitHub App against a private test repository once App credentials exist. The mocked flow is in `backend/tests/test_github_app.py`. Provider work did not change delivery, dedupe, or the pinned-SHA check.
2. Call the OpenAI-compatible provider with a real key on a demo fixture and compare its validated findings with the scripted suite in `model-structured-v1`. Do not turn that on for automatic patches.
3. Run untrusted pull-request tests in disposable, resource-limited containers. The subprocess driver still refuses `trust_tier=untrusted`. Model findings do not execute code.
4. Extend the evaluation set beyond the builtin rule fixtures and the scripted model responses. Record patch success, latency, and cost only after findings are reliable.
5. Before more than the demo workspace can sign in, map each GitHub installation to an authorized workspace and repository. Installations currently share `prism-demo`.
