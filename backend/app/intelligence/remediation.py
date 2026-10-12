"""Mechanical patch builders.

These functions return full file text. They do not run shell commands and they do not
merge anything. Unknown patterns are declined instead of guessed.
"""

from __future__ import annotations

import re

from app.intelligence.diffutil import unified_diff

_FSTRING_EXECUTE = re.compile(
    r"(?P<indent>^[ \t]*)(?P<recv>[A-Za-z_][A-Za-z0-9_]*)\.execute\(\s*"
    r"f(?P<q>['\"])(?P<body>.*?)(?P=q)\s*\)",
    re.M | re.S,
)
_SHELL = re.compile(
    r"subprocess\.run\(\s*f([\"'])curl -X POST \{url\}\1\s*,\s*shell=True\s*,\s*check=False\s*\)"
)
_SECRET = re.compile(
    r"""^(?P<name>[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|API_KEY|PASSWORD)[A-Z0-9_]*)\s*=\s*['\"][^'\"]+['\"]\s*$""",
    re.M,
)


def propose(rule_id: str, path: str, source: str) -> dict | None:
    if rule_id == "sql_injection":
        updated = rewrite_fstring_execute(source, paramstyle=_paramstyle(source))
        if updated is None:
            return None
        return _proposal(
            path,
            source,
            updated,
            strategy="parameterize_sql",
            title="Bind webhook status and invoice id as SQL parameters",
        )
    if rule_id == "shell_true":
        updated = rewrite_shell(source)
        if updated is None:
            return None
        return _proposal(
            path,
            source,
            updated,
            strategy="shell_to_argv",
            title="Run curl with an argument list instead of a shell",
        )
    if rule_id == "hardcoded_secret":
        updated = rewrite_secret(source, empty_default=True)
        if updated is None:
            return None
        return _proposal(
            path,
            source,
            updated,
            strategy="secret_from_environment",
            title="Read the webhook secret from the environment",
        )
    return None


def decline_reason(rule_id: str) -> str:
    reasons = {
        "n_plus_one": "Declined to patch. The finding is a hypothesis without a query trace, so a rewrite could change behavior.",
        "missing_auth": "Declined to patch. Adding an auth dependency without knowing the project's auth type would invent an API.",
        "float_currency": "Declined to patch. Replacing float money math changes the function contract and needs a human rounding decision.",
        "test_gap": "Declined to invent a test that would pass on the current bug. A useful regression test has to fail first.",
        "pickle_loads": "Declined to patch. Replacing pickle changes the session contract and needs a human design.",
        "eval_exec": "Declined to patch. Removing eval changes a public behavior that the rule cannot reconstruct.",
    }
    return reasons.get(rule_id, f"No mechanical patch for {rule_id}.")


def rewrite_fstring_execute(source: str, paramstyle: str) -> str | None:
    match = _FSTRING_EXECUTE.search(source)
    if match is None:
        return None
    body = match.group("body")
    exprs = re.findall(r"\{([^{}]+)\}", body)
    if not exprs:
        return None
    placeholder = "%s" if paramstyle == "format" else "?"
    sql = body
    sql = re.sub(r"'\{([^{}]+)\}'", placeholder, sql)
    sql = re.sub(r'"\{([^{}]+)\}"', placeholder, sql)
    sql = re.sub(r"\{([^{}]+)\}", placeholder, sql)
    indent = match.group("indent")
    recv = match.group("recv")
    params = ", ".join(exprs)
    if len(exprs) == 1:
        params += ","
    replacement = (
        f"{indent}{recv}.execute(\n"
        f'{indent}    "{sql}",\n'
        f"{indent}    ({params}),\n"
        f"{indent})"
    )
    return source[: match.start()] + replacement + source[match.end() :]


def rewrite_shell(source: str) -> str | None:
    if not _SHELL.search(source):
        return None
    return _SHELL.sub('subprocess.run(["curl", "-X", "POST", url], check=False)', source)


def rewrite_secret(source: str, empty_default: bool) -> str | None:
    match = _SECRET.search(source)
    if match is None:
        return None
    name = match.group("name")
    if empty_default:
        replacement = f'{name} = os.environ.get("{name}", "")'
    else:
        replacement = f'{name} = os.environ["{name}"]'
    updated = source[: match.start()] + replacement + source[match.end() :]
    if "import os" not in updated:
        updated = "import os\n" + updated
    return updated


def repair_from_failure(source: str, notes: str) -> tuple[str | None, str]:
    """Bounded, explicit repairs. Returns (new_source, strategy) or (None, reason)."""
    if "empty_secret_default" in notes:
        match = re.search(
            r"""os\.environ\.get\(\s*(['\"])(?P<name>[^'\"]+)\1\s*,\s*(['\"])\s*\3\s*\)""",
            source,
        )
        if match:
            name = match.group("name")
            updated = source[: match.start()] + f'os.environ["{name}"]' + source[match.end() :]
            return updated, "require_secret_env"
        return None, "empty default was reported but the assignment was not recognized"
    if "bindings" in notes.lower() and "%s" in source:
        return source.replace("%s", "?"), "sqlite_qmark_paramstyle"
    return None, "no repair rule matched the failure evidence"


def policy_issues(source: str) -> list[str]:
    issues = []
    if re.search(
        r"""(?m)^[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|API_KEY|PASSWORD)[A-Z0-9_]*\s*=\s*os\.environ\.get\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]['\"]\s*\)""",
        source,
    ):
        issues.append("empty_secret_default")
    if "shell=True" in source:
        issues.append("shell_true_remains")
    if re.search(r"""execute\(\s*f['\"]""", source) or re.search(r"""execute\(\s*\n\s*f['\"]""", source):
        issues.append("sql_fstring_remains")
    if re.search(r"\beval\s*\(", source):
        issues.append("introduces_eval")
    return issues


def issues_for_rule(rule_id: str, source: str) -> list[str]:
    """Policy checks that belong to this patch. Unrelated defects in the same file stay separate findings."""
    relevant = {
        "sql_injection": {"sql_fstring_remains", "introduces_eval"},
        "shell_true": {"shell_true_remains", "introduces_eval"},
        "hardcoded_secret": {"empty_secret_default", "introduces_eval"},
    }.get(rule_id, {"introduces_eval"})
    return [issue for issue in policy_issues(source) if issue in relevant]


def _paramstyle(source: str) -> str:
    if "sqlite3" in source:
        return "qmark"
    return "format"


def _proposal(path: str, before: str, after: str, strategy: str, title: str) -> dict:
    return {
        "path": path,
        "strategy": strategy,
        "title": title,
        "content": after,
        "diff": unified_diff(path, before, after),
    }
