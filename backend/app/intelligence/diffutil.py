"""Line diffs and unified patches. Pure functions, covered by unit tests."""

from __future__ import annotations

import difflib


def unified_diff(path: str, old: str, new: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    text = "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return text


def changed_new_lines(old: str, new: str) -> set[int]:
    """1-based line numbers in `new` that were inserted or replaced."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    changed: set[int] = set()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "insert"}:
            changed.update(range(j1 + 1, j2 + 1))
    return changed


def line_stats(old: str, new: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines())
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            deletions += i2 - i1
            additions += j2 - j1
        elif tag == "delete":
            deletions += i2 - i1
        elif tag == "insert":
            additions += j2 - j1
    return additions, deletions


def split_rows(old: str, new: str, limit: int = 2500) -> tuple[list[dict], str | None]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    if len(old_lines) + len(new_lines) > limit:
        return [], f"Diff view truncated: {len(old_lines) + len(new_lines)} lines exceeds {limit}."
    rows: list[dict] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                rows.append(
                    _pair("ctx", i1 + offset + 1, old_lines[i1 + offset], "ctx", j1 + offset + 1, new_lines[j1 + offset])
                )
        elif tag == "replace":
            width = max(i2 - i1, j2 - j1)
            for offset in range(width):
                left = _side("del", i1 + offset + 1, old_lines[i1 + offset]) if offset < i2 - i1 else _blank()
                right = _side("add", j1 + offset + 1, new_lines[j1 + offset]) if offset < j2 - j1 else _blank()
                rows.append({**left_prefix(left), **right_prefix(right)})
        elif tag == "delete":
            for offset in range(i2 - i1):
                rows.append(_pair("del", i1 + offset + 1, old_lines[i1 + offset], "empty", None, ""))
        elif tag == "insert":
            for offset in range(j2 - j1):
                rows.append(_pair("empty", None, "", "add", j1 + offset + 1, new_lines[j1 + offset]))
    return rows, None


def _side(kind: str, number: int | None, text: str) -> dict:
    return {"kind": kind, "no": number, "text": text}


def _blank() -> dict:
    return {"kind": "empty", "no": None, "text": ""}


def left_prefix(side: dict) -> dict:
    return {"left_kind": side["kind"], "left_no": side["no"], "left_text": side["text"]}


def right_prefix(side: dict) -> dict:
    return {"right_kind": side["kind"], "right_no": side["no"], "right_text": side["text"]}


def _pair(lk: str, ln: int | None, lt: str, rk: str, rn: int | None, rt: str) -> dict:
    return {
        "left_kind": lk,
        "left_no": ln,
        "left_text": lt,
        "right_kind": rk,
        "right_no": rn,
        "right_text": rt,
    }
