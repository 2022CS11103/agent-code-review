"""GitHub HTTP client.

Only https://api.github.com is allowed. The client can post a check run and a
COMMENT review. It has no method that merges or pushes.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx

ALLOWED_HOSTS = {"api.github.com"}
MAX_PAGES = 10


class GithubClientError(Exception):
    pass


class GithubRateLimit(GithubClientError):
    pass


class HttpGithubClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        sleeper=time.sleep,
        max_attempts: int = 3,
        max_retry_wait: float = 2.0,
        timeout: float = 15.0,
    ):
        parsed = urlparse(base_url)
        if parsed.hostname not in ALLOWED_HOSTS:
            raise GithubClientError(f"GitHub API host is not allowed: {parsed.hostname}")
        if parsed.scheme != "https":
            raise GithubClientError("GitHub API URL must be https")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.sleep = sleeper
        self.max_attempts = max_attempts
        self.max_retry_wait = max_retry_wait
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "prism-ai",
            },
        )

    def close(self) -> None:
        self._client.close()

    def get_pull(self, owner: str, repo: str, number: int) -> dict:
        data = self._json("GET", f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{int(number)}")
        if not isinstance(data, dict):
            raise GithubClientError("Unexpected pull request payload")
        return data

    def get_files(self, owner: str, repo: str, number: int) -> list:
        path = f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{int(number)}/files?per_page=100"
        return self._paginate(path)

    def get_text(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        safe = _repo_path(path)
        if not _sha(ref):
            raise GithubClientError("Refusing a non-SHA contents ref")
        response = self._request(
            "GET",
            f"/repos/{_segment(owner)}/{_segment(repo)}/contents/{safe}?ref={ref}",
            accept="application/vnd.github.raw",
            allow_statuses={404},
        )
        if response.status_code == 404:
            return None
        if len(response.content) > 200_000:
            raise GithubClientError("File exceeds the fetch size limit")
        if b"\x00" in response.content:
            raise GithubClientError("Refusing binary file content")
        return response.text

    def create_check_run(self, owner: str, repo: str, head_sha: str, summary: str, text: str) -> dict:
        if not _sha(head_sha):
            raise GithubClientError("Check run requires a commit SHA")
        data = self._json(
            "POST",
            f"/repos/{_segment(owner)}/{_segment(repo)}/check-runs",
            {
                "name": "PRISM",
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": "action_required",
                "output": {
                    "title": "PRISM review needs a human decision",
                    "summary": summary[:65000],
                    "text": text[:65000],
                },
            },
        )
        if not isinstance(data, dict):
            raise GithubClientError("Unexpected check run response")
        return data

    def create_review(self, owner: str, repo: str, number: int, body: str, commit_id: str, comments: list[dict]) -> dict:
        if not _sha(commit_id):
            raise GithubClientError("Review requires a commit SHA")
        # COMMENT never approves or requests changes, and it cannot merge.
        payload: dict = {"body": body[:65000], "commit_id": commit_id, "event": "COMMENT"}
        if comments:
            payload["comments"] = comments
        data = self._json(
            "POST",
            f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{int(number)}/reviews",
            payload,
        )
        if not isinstance(data, dict):
            raise GithubClientError("Unexpected review response")
        return data

    def create_installation_token(self, installation_id: int) -> dict:
        if isinstance(installation_id, bool) or not isinstance(installation_id, int) or installation_id <= 0:
            raise GithubClientError("Invalid installation id")
        data = self._json("POST", f"/app/installations/{installation_id}/access_tokens", {})
        if not isinstance(data, dict) or not isinstance(data.get("token"), str):
            raise GithubClientError("Installation token response was rejected")
        return data

    def _paginate(self, path: str) -> list:
        items: list = []
        next_path: str | None = path
        pages = 0
        while next_path:
            pages += 1
            if pages > MAX_PAGES:
                raise GithubClientError("GitHub pagination exceeded the page cap")
            response = self._request("GET", next_path)
            data = response.json()
            if not isinstance(data, list):
                raise GithubClientError("Unexpected files payload")
            items.extend(data)
            next_path = _next_path(response.headers.get("link"))
        return items

    def _json(self, method: str, path: str, payload: dict | None = None) -> dict | list:
        response = self._request(method, path, payload=payload)
        return response.json()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        accept: str | None = None,
        allow_statuses: set[int] | None = None,
    ) -> httpx.Response:
        self._check_path(path)
        headers = {"Accept": accept} if accept else None
        last_error = "GitHub API unavailable"
        for attempt in range(self.max_attempts):
            response = self._client.request(method, path, json=payload, headers=headers)
            if response.status_code in (allow_statuses or set()):
                return response
            if _rate_limited(response):
                wait = _retry_after(response)
                if attempt + 1 >= self.max_attempts or wait > self.max_retry_wait:
                    raise GithubRateLimit("GitHub rate limit exceeded")
                self.sleep(wait)
                continue
            if response.status_code >= 500:
                last_error = f"GitHub API {response.status_code}"
                if attempt + 1 >= self.max_attempts:
                    break
                self.sleep(min(self.max_retry_wait, 0.2 * (attempt + 1)))
                continue
            if response.status_code >= 400:
                raise GithubClientError(f"GitHub API {response.status_code}")
            return response
        raise GithubClientError(last_error)

    @staticmethod
    def _check_path(path: str) -> None:
        relative = path.split("?", 1)[0]
        if not relative.startswith("/") or relative.startswith("//") or "://" in path:
            raise GithubClientError("Refusing non-relative GitHub path")


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in "/?#\\") or ".." in value:
        raise GithubClientError("Invalid GitHub path segment")
    return value


def _repo_path(path: str) -> str:
    if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/") or "\\" in path or "\x00" in path:
        raise GithubClientError("Invalid repository path")
    from urllib.parse import quote

    return quote(path, safe="/")


def _sha(value: str) -> bool:
    return isinstance(value, str) and 7 <= len(value) <= 40 and all(char in "0123456789abcdef" for char in value)


def _rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    remaining = response.headers.get("x-ratelimit-remaining")
    return remaining == "0"


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("retry-after")
    try:
        return max(0.0, float(raw)) if raw is not None else 1.0
    except ValueError:
        return 1.0


def _next_path(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        if 'rel="next"' not in part:
            continue
        start = part.find("<")
        end = part.find(">")
        if start < 0 or end < 0:
            raise GithubClientError("Ignoring a malformed GitHub pagination link")
        url = urlparse(part[start + 1 : end])
        if url.scheme != "https" or url.hostname not in ALLOWED_HOSTS:
            raise GithubClientError("Refusing a pagination link outside the GitHub API")
        query = url.query
        if query and any(key.lower() in {"access_token", "token"} for key in parse_qs(query)):
            raise GithubClientError("Refusing a pagination link that carries a token")
        return f"{url.path}?{query}" if query else url.path
    return None
