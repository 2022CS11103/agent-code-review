"""Small Python-aware scanners. Not a substitute for a parser, and not used for non-Python files."""

from __future__ import annotations

import re


def mask_strings(line: str) -> str:
    """Replace string literals so searches do not match examples inside quotes.

    f-strings keep an f"" marker. The prefix is only treated as an f-string when
    it is not part of a longer identifier.
    """
    chars: list[str] = []
    i = 0
    while i < len(line):
        if line[i] in "\"'":
            quote = line[i]
            is_f = False
            if chars and chars[-1] == "f":
                prev = chars[-2] if len(chars) > 1 else ""
                if not (prev.isalnum() or prev == "_"):
                    is_f = True
                    chars.pop()
            j = i + 1
            while j < len(line):
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == quote:
                    j += 1
                    break
                j += 1
            chars.append('f""' if is_f else '""')
            i = j
            continue
        chars.append(line[i])
        i += 1
    return "".join(chars)


def strip_comment(masked_line: str) -> str:
    if "#" not in masked_line:
        return masked_line
    return masked_line.split("#", 1)[0]


def code_only(line: str) -> str:
    if line.lstrip().startswith("#"):
        return ""
    return strip_comment(mask_strings(line))


def mask_block(text: str) -> str:
    return "\n".join(code_only(line) for line in text.splitlines())


EXECUTE_CALL = re.compile(r"\b(execute|executemany)\s*\(", re.M)
SQL_KEYWORD = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.I)


def extract_call(lines: list[str], start: int) -> tuple[str, int]:
    """Return the call text starting at `start` and the exclusive end line index."""
    depth = 0
    buf: list[str] = []
    end = start
    for index in range(start, min(len(lines), start + 16)):
        buf.append(lines[index])
        depth += lines[index].count("(") - lines[index].count(")")
        end = index + 1
        if index > start and depth <= 0:
            break
    return "\n".join(buf), end


def iter_execute_calls(source: str) -> list[tuple[int, str]]:
    lines = source.splitlines()
    calls: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        if EXECUTE_CALL.search(code_only(lines[index])):
            text, end = extract_call(lines, index)
            calls.append((index + 1, text))
            index = max(end, index + 1)
        else:
            index += 1
    return calls
