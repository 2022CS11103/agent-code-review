"""Path policy. Repository-controlled globs cannot escape this matcher."""

from __future__ import annotations

import re

UNTRUSTED_CONFIG_NAMES = {".prism.yml", ".prism.yaml", ".prism.json"}


def safe_relative(path: str) -> bool:
    if not path or path.startswith("/") or path.startswith("\\"):
        return False
    parts = path.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return True


def match_glob(path: str, pattern: str) -> bool:
    """Match a POSIX path. A leading '**/' is optional, so '**/vendor/**' matches 'vendor/a.py'."""
    if pattern.startswith("**/"):
        if _match(path, pattern):
            return True
        return _match(path, pattern[3:])
    return _match(path, pattern)


def _match(path: str, pattern: str) -> bool:
    escaped = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(escaped, path) is not None


def is_excluded(path: str, globs: list[str]) -> bool:
    return any(match_glob(path, pattern) for pattern in globs)
