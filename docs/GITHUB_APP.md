# GitHub App

Demo mode does not need an App. With no App id and no private key, PRISM still reviews the seeded repositories and stores reports locally.

## From the Repositories page

**Add GitHub repository** posts a GitHub App manifest to `https://github.com/settings/apps/new`. After you confirm it, GitHub sends the browser back to PRISM. The API exchanges that code for the App id, private key, and webhook secret, and stores them in the database. Settings shows that the App is configured. It does not show the key or the secret.

**Install on a repository** opens GitHub’s install page for that App. Choose the repositories you want reviewed.

**Sync from GitHub** lists those installations and open pull requests through `api.github.com` and queues a review for each open pull request. Sync does not need a public webhook URL. A live call was not made from the environment that added this button. The tests mock `api.github.com`.

Set `PRISM_PUBLIC_BASE_URL` to a public `https` origin before creating the App if you want GitHub to deliver webhooks. A localhost value is ignored. Untrusted repository code is still not executed, and publish still cannot merge.

Manual environment variables below remain available when you would rather create the App yourself. A saved manifest is used instead of those variables.

A live installation was not exercised in the environment that added this code. The tests in `backend/tests/test_github_app.py` mock `api.github.com`. Treat a real repository check as still open until you have run the steps below against a repository you control.

## Permissions

Create the App with only:

- Metadata: read (GitHub requires this)
- Contents: read
- Pull requests: write (used to post a `COMMENT` review, not to merge)
- Checks: write

Do not grant contents write, administration, or workflows. If an installation arrives with contents write or administration, PRISM records the extra permission and still does not call a write-contents or merge API. If contents read, pull requests write, or checks write is missing, the installation is stored as `permissions_incomplete` and pull-request events do not enqueue a review.

Subscribe to:

- `pull_request` (opened and synchronize; reopened is handled the same way)
- `installation`
- `installation_repositories`

## Secrets

Set a long webhook secret in the App and in `PRISM_GITHUB_WEBHOOK_SECRET`. Leave the dev default only on a machine that is not reachable from GitHub.

```bash
PRISM_GITHUB_APP_ID=123456
PRISM_GITHUB_APP_SLUG=your-app-slug
PRISM_GITHUB_APP_PRIVATE_KEY_PATH=/secure/prism-github.pem
```

The private key may also be passed as `PRISM_GITHUB_APP_PRIVATE_KEY` with escaped newlines. Do not commit either value. A personal access token is not accepted.

The install link shown in Settings is `https://github.com/apps/<slug>/installations/new`. Installation tokens are minted by exchanging a short-lived App JWT at `/app/installations/{id}/access_tokens`. They stay in API memory.

## What a delivery does

1. The webhook signature is checked. A bad signature returns 401 and writes an audit event.
2. `X-GitHub-Delivery` is stored. The same delivery returns the original outcome and does not create a second job.
3. An installation event creates or updates repositories on the `prism-demo` workspace. This build does not map one GitHub account to many PRISM workspaces.
4. `pull_request.opened` and `pull_request.synchronize` validate the owner, name, number, and head SHA, store a stub, and return `202` with a job id.
5. The worker fetches the pull request, the changed-file list, and file text from the API. If the API head SHA differs from the pinned SHA, the job fails instead of reviewing a different commit.
6. Static analysis runs. Untrusted repository code is not executed by the subprocess sandbox, so patch validation is `inconclusive` until a container runner is available.
7. Publish, after the job is `completed` and the repository policy allows GitHub comments, posts:
   - a check run named `PRISM` with conclusion `action_required`
   - a review whose event is `COMMENT`
   - inline comments only when the finding line is on the new-file side of the fetched patch
8. Findings that are not on a diff line stay in the summary. Nothing is merged or pushed.

Rate limits and 5xx responses are retried up to three times, and only when `Retry-After` is at most two seconds. A longer limit fails the attempt so the process does not sleep through a large window. Publish then reports `github_failed` and `github_called: true`.

## Try it on a test repository

Use an empty private repository that you own. Install the App on that repository only.

Expose the API:

```bash
cloudflared tunnel --url http://127.0.0.1:8741
```

Set the App webhook URL to `https://<tunnel>/api/v1/webhooks/github`.

Open a pull request that changes a Python file. The command center should show the pull request after the webhook, and the review page should fill in once the worker finishes the fetch. In the repository policy, enable GitHub comments, then choose Publish report. GitHub should show a neutral-blocking check (`action_required`) and a comment review. It should not show an approval, and the pull request should remain unmerged.

Confirm Settings does not display the private key or an installation token.

## Local signature check

This checks the webhook handler without GitHub. It does not prove the App can fetch a repository.

```bash
BODY='{"action":"opened","repository":{"full_name":"acme/billing-service"},"pull_request":{"number":142,"head":{"sha":"<stored demo sha>"}}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$PRISM_GITHUB_WEBHOOK_SECRET" | awk '{print $2}')
curl -sS -D- -o /tmp/prism-hook.json \
  -X POST http://127.0.0.1:8741/api/v1/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-GitHub-Delivery: local-1" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  --data "$BODY"
```

For the seeded demo pull request, the stored SHA must match or the outcome is `sha_not_pinned_locally`. A second request with the same delivery id is a duplicate.
