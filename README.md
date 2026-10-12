# PRISM

Production-grade code review and remediation. Catch the bug. Prove the fix. Show the evidence.

A review job parses a pull-request diff, runs deterministic static checks, asks specialist agents for structured commentary, proposes patches, and validates those patches in a bounded sandbox. Test results, lint, and security checks are reported separately. A passing test is not treated as proof that a change is production-safe. Nothing is merged, and a patch is not applied to the repository without an explicit human decision — even then, accept only records the decision.

This build runs without a GitHub App and without a paid model. Demo repositories are local fixtures. The default model provider is a deterministic mock. Set `PRISM_LLM_PROVIDER=openai` and `PRISM_OPENAI_API_KEY` only when you want the validated hosted-model path. That path does not apply patches or execute pull-request code.

## Run locally

Requirements: Python 3.12, Node.js 22.

```bash
./scripts/dev.sh
```

- App: http://127.0.0.1:43123
- API: http://127.0.0.1:8741/api/v1/health
- OpenAPI: http://127.0.0.1:8741/docs

The script uses SQLite at `backend/data/prism.db`, seeds the demo workspace, and runs the worker inside the API process. Copy `.env.example` if you want to override settings. The prefix is `PRISM_`.

### Demo path

1. Open the review queue. `acme/auth-gateway` #87 already has a seeded review produced by the pipeline. `acme/billing-service` #142 is waiting.
2. Open billing #142 and choose **Run review**.
3. Stay on the review. Stages move from ingest through ready. Findings show file, line, severity, and observed evidence separately from agent commentary.
4. Open **Validation** to see baseline versus patched tests, the repair attempt, and lint/security status.
5. Confirm or dismiss a finding (`c` / `d`, or `j` / `k` to move). Accepting a patch is refused unless the latest validation passed or needs review, and the repository is not modified.
6. **Publish report** stores a redacted local report. GitHub is not called.
7. **Evaluation → Run fixture suite** records precision and recall for the builtin rules. **Settings** shows the live provider, sandbox driver, and whether credentials are configured.

`Ctrl` or `Cmd`+`K` jumps to a pull request or screen. The header switches light and dark.

## Tests

```bash
cd backend && .venv/bin/pytest && .venv/bin/ruff check app tests
cd frontend && npx tsc --noEmit --incremental false && npm run lint
```

Backend tests set `PRISM_AUTO_MIGRATE=0` and build the schema with SQLAlchemy. A separate test upgrades and downgrades the Alembic revision on a temporary SQLite file.

## Layout

| Path | Role |
| --- | --- |
| `backend/app` | FastAPI, analysis, LangGraph agents, sandbox, demo fixtures |
| `frontend` | Next.js review product. `/api/v1` is proxied to the API |
| `docs` | Architecture, security, GitHub App setup, implementation status |
| `docker-compose.yml` | Postgres, Redis, API, worker, frontend. Not executed in the environment that first ran the demo |

## What this build does not do

- The default provider does not call OpenAI. `PRISM_LLM_PROVIDER=mock` fills agent notes from structured fields. `PRISM_LLM_PROVIDER=openai` is an OpenAI-compatible client with a schema, a timeout, and a call budget. Tests mock that HTTP API. A live model call was not made in this environment. A failed model call cannot be published as a completed review.
- GitHub App delivery is implemented and covered with mocked API tests. A live install was not verified in this environment, because no App credentials were available. Demo mode still runs with no GitHub credentials. Publishing a check uses conclusion `action_required` and a `COMMENT` review. It never approves or merges. Turn on **Allow GitHub comments** in the repository policy before Publish will call GitHub. Untrusted pull-request code is still not executed in the subprocess sandbox.
- It does not run untrusted repository code in the API process. The subprocess sandbox is limited to `trust_tier=demo_fixture`.
- Docker Compose is the documented production-shaped path. It was not run here, and this machine has no Docker daemon.
- Redis is in Compose for a later queue. The worker polls the database.
- Semgrep is optional. If the CLI is absent, the scan is recorded as unavailable.
- OpenTelemetry packages are installed. Export is not wired; `PRISM_OTLP_ENDPOINT` is reserved.
- PostgreSQL was not started in the environment that verified the demo. `psycopg` 3.3.6 is pinned so the Compose URL can connect. The verified path is SQLite.

See `docs/IMPLEMENTATION.md` for the phase checklist and `docs/SECURITY.md` for sandbox limits.
