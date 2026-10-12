# Security

PRISM reviews untrusted diffs. The controls below are what this repository actually implements.

## Trust boundaries

- Pull-request source, comments, documentation, lockfiles, and tool output are untrusted data.
- A comment that says to ignore policy does not suppress findings. A fixture covers this.
- `.prism.yml` in a repository is ignored. It cannot set the sandbox image, disable checks, or raise the repair cap.
- Repair attempts are capped by `min(policy, settings, 3)` in application code, not in a prompt.
- High and critical findings cannot be stored as an ungrounded hypothesis. The schema rejects that shape.
- Accepting a patch requires the latest validation status to be `passed` or `needs_review`. The response includes `repository_modified: false`. There is no merge API.

## Webhooks and GitHub

- HMAC-SHA256 over the raw body, compared with `hmac.compare_digest`.
- Missing or bad signatures return 401 and write an audit event.
- Delivery ids are stored unique, so retries do not create a second job.
- Clone URLs and branch names from the payload are not passed to a shell.
- The HTTP client rejects any base URL other than `https://api.github.com`, which blocks link-local and metadata hosts. Pagination links are checked the same way and cannot carry an access token.
- GitHub App private keys sign a short-lived JWT. Installation tokens (`ghs_`) are kept in process memory. Settings reports only whether the key is configured.
- A published check uses conclusion `action_required`. The review event is `COMMENT`. Neither result approves or merges the pull request.
- Reports pass through redaction. Webhook secrets, `sk-` keys, `ghp_` tokens, `ghs_` installation tokens, `github_pat_` tokens, and bearer values are stripped. Snippets of hardcoded secrets keep a short prefix and then `[redacted]`.

The default webhook secret `dev-only-webhook-secret` logs a warning at startup. Replace it before exposing the endpoint. Settings reports `webhook_secret_is_default`.

## Sandbox

The subprocess driver runs only when `trust_tier` is `demo_fixture`. Any other repository is refused rather than executed beside the API.

Limits on that driver:

- Non-login environment allowlist: `PATH`, `HOME`, `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`, `LANG`.
- Address space, CPU, and file-size rlimits.
- Wall-clock timeout via the `timeout` binary.
- Workspace under a temporary directory, removed after the run.
- Log paths rewritten to `/sandbox`.

The Docker argument builder, used when `PRISM_SANDBOX_DRIVER=docker` and `docker` is on `PATH`, adds:

- `--network=none`
- `--cap-drop=ALL`
- `--security-opt no-new-privileges`
- `--pids-limit 64`
- `--memory 512m`
- `--cpus 1`
- `--user 65534:65534`
- `--read-only` and a tmpfs on `/tmp`
- no Docker socket and no host secret mounts

Docker still shares the host kernel. A container escape is outside this design. Public deployment should move execution to a microVM or gVisor-class runner behind the same interface. Compose does not mount `docker.sock` into the API or worker.

## Application controls

- Demo mode authenticates as the seeded user. With demo mode off, the API returns 401 because no session authenticator is configured. That is intentional: there is no anonymous fallback.
- Workspace checks sit on every job, pull request, and repository read.
- Request bodies are size-limited. An in-process rate limit returns 429. It does not coordinate across processes.
- Browser mutations with an `Origin` outside `PRISM_ALLOWED_ORIGINS` are rejected. Webhooks are exempt because GitHub does not send that header.
- Logs pass through the same redaction helper.
- Patch edits that introduce `eval(` are rejected. Edits mark the patch `proposed` and do not re-run validation by themselves.
- Vendor and excluded paths are recorded as skipped coverage, then left out of findings.

## What is not a control yet

- No organization SSO, CSRF cookie, or per-user GitHub token store.
- No dependency or container scan in CI beyond the tests in this repo.
- OpenTelemetry export is not connected.
- Semgrep is not installed in the default environment. Its absence is reported; results are not invented.
