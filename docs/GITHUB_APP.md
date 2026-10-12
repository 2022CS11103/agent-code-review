# GitHub App setup

The demo does not need this. Use it when you want a real installation. Until `PRISM_GITHUB_TOKEN` is set and a repository row exists for the installation, webhooks for unknown repos are acknowledged and ignored.

## App

Create a GitHub App owned by the user or organization that will install it.

Permissions, least privilege:

- Pull requests: read and write (review comments and the summary).
- Checks: read and write, if you later turn on check runs.
- Contents: read (diff and file content).
- Metadata: read (mandatory).

Subscribe to:

- `pull_request` (opened, synchronize)
- `installation`
- `installation_repositories`

Set the webhook URL to the tunnel below. Set a long random webhook secret and the same value in `PRISM_GITHUB_WEBHOOK_SECRET`. Do not leave the dev default in place.

Generate a private key if you add an installation-token client later. This build’s HTTP client uses `PRISM_GITHUB_TOKEN` as a bearer token and only talks to `https://api.github.com`. Installation-token minting from the private key is not implemented.

## Local tunnel

The API listens on port 8741.

```bash
cloudflared tunnel --url http://127.0.0.1:8741
```

or

```bash
ngrok http 8741
```

Point the App webhook at `https://<tunnel>/api/v1/webhooks/github`.

A signed delivery looks like:

```bash
BODY='{"action":"opened","pull_request":{"number":1},"repository":{"full_name":"acme/billing-service"}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$PRISM_GITHUB_WEBHOOK_SECRET" | awk '{print $2}')
curl -sS -D- -o /tmp/prism-hook.json \
  -X POST http://127.0.0.1:8741/api/v1/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-GitHub-Delivery: local-1" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  --data "$BODY"
```

The handler returns 202 even when the event is ignored. A second request with the same delivery id does not create another job.

## What posting a review does today

`POST /api/v1/reviews/{id}/report` writes a redacted report with `delivery_status=stored_locally_demo` and `github_called=false`. Inline comments are not published. When a GitHub publisher is added, it must check that the line still exists on the pinned diff and fall back to a summary comment if it does not. It must not include secret snippets or sandbox paths.
