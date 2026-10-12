"""GitHub HTTP client. Hosts other than the configured API host are rejected."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

ALLOWED_HOSTS = {"api.github.com"}


class GithubClientError(Exception):
    pass


class HttpGithubClient:
    def __init__(self, token: str, base_url: str = "https://api.github.com", transport: httpx.BaseTransport | None = None):
        host = urlparse(base_url).hostname
        if host not in ALLOWED_HOSTS:
            raise GithubClientError(f"GitHub API host is not allowed: {host}")
        if urlparse(base_url).scheme != "https":
            raise GithubClientError("GitHub API URL must be https")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=10,
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
        return self._get(f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{int(number)}")

    def get_files(self, owner: str, repo: str, number: int) -> list:
        data = self._get(f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{int(number)}/files")
        if not isinstance(data, list):
            raise GithubClientError("Unexpected files payload")
        return data

    def create_review(self, owner: str, repo: str, number: int, body: str, commit_id: str, comments: list[dict]) -> dict:
        return self._post(
            f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{int(number)}/reviews",
            {"body": body, "commit_id": commit_id, "event": "COMMENT", "comments": comments},
        )

    def _get(self, path: str) -> dict | list:
        self._check_path(path)
        response = self._client.get(path)
        if response.status_code >= 400:
            raise GithubClientError(f"GitHub API {response.status_code}")
        return response.json()

    def _post(self, path: str, payload: dict) -> dict:
        self._check_path(path)
        response = self._client.post(path, json=payload)
        if response.status_code >= 400:
            raise GithubClientError(f"GitHub API {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise GithubClientError("Unexpected review response")
        return data

    @staticmethod
    def _check_path(path: str) -> None:
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise GithubClientError("Refusing non-relative GitHub path")


def _segment(value: str) -> str:
    if not value or any(char in value for char in "/?#\\") or ".." in value:
        raise GithubClientError("Invalid GitHub path segment")
    return value
