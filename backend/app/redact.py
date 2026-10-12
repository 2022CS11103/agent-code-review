"""Redact secrets and host paths before they reach logs, reports, or model prompts."""

import re

_PATTERNS = [
    re.compile(r"whsec_[A-Za-z0-9_\-]{4,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"gho_[A-Za-z0-9]{8,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-_\.=]{8,}"),
]


def redact_text(text: str | None, limit: int = 20_000) -> str:
    if not text:
        return ""
    cleaned = text
    for pattern in _PATTERNS:
        if pattern.pattern.startswith("(?i)(bearer"):
            cleaned = pattern.sub(r"\1[redacted]", cleaned)
        else:
            cleaned = pattern.sub(lambda match: match.group(0).split("_", 1)[0] + "_[redacted]", cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "\n…[truncated]"
    return cleaned


def scrub_path(text: str, workspace: str) -> str:
    if workspace and text:
        return text.replace(workspace, "/sandbox")
    return text
