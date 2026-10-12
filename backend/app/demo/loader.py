"""Load the checked-in demo repositories. Paths are confined to the catalog directory."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.intelligence.paths import safe_relative

CATALOG = Path(__file__).resolve().parent / "catalog"


def load_side(repo_key: str, side: str) -> dict[str, str]:
    root = (CATALOG / repo_key / side).resolve()
    if not str(root).startswith(str(CATALOG.resolve())):
        raise ValueError("Catalog path escaped its root")
    if not root.is_dir():
        raise FileNotFoundError(root)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if not safe_relative(relative):
            raise ValueError(f"Unsafe catalog path {relative}")
        files[relative] = path.read_text()
    return files


def tree_sha(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(files[path].encode())
    return digest.hexdigest()[:40]


def language_for(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith((".yml", ".yaml")):
        return "yaml"
    return "text"
