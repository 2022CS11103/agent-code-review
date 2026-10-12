"""Deterministic findings. Every result names the rule, the line, and the claim level."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.intelligence.paths import UNTRUSTED_CONFIG_NAMES, is_excluded, safe_relative
from app.intelligence.python_text import (
    SQL_KEYWORD,
    code_only,
    iter_execute_calls,
    mask_block,
)
from app.intelligence.symbols import defined_functions, enclosing_function
from app.versions import ANALYZER_VERSION

INJECTION_MARKER = re.compile(
    r"ignore (all )?(previous|security) (instructions|policies)|do not report findings|skip sandbox",
    re.I,
)
SECRET_LINE = re.compile(
    r"""^(?P<name>[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|API_KEY|PASSWORD)[A-Z0-9_]*)\s*=\s*(?P<q>['\"])(?P<value>[^'\"]{6,})(?P=q)\s*$"""
)
ROUTE_DECORATOR = re.compile(
    r"@(?:\w+\.)?(get|post|put|delete|patch|api_route|route)\(\s*['\"]([^'\"]+)['\"]"
)
MONEY_NAME = re.compile(r"tax|price|currency|money", re.I)


@dataclass
class AnalysisFinding:
    rule_id: str
    category: str
    severity: str
    confidence: float
    claim_level: str
    title: str
    explanation: str
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    impact: str
    remediation: str
    observed: bool = True
    uncertain_reason: str | None = None
    enclosing_symbol: str | None = None
    source: str = "static"

    @property
    def stable_key(self) -> str:
        raw = f"{self.rule_id}|{self.file_path}|{self.start_line}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class FileInsight:
    path: str
    language: str
    findings: list[AnalysisFinding] = field(default_factory=list)
    skipped_reason: str | None = None
    markers: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    findings: list[AnalysisFinding]
    files: list[FileInsight]
    skipped: list[dict]
    markers: list[dict]
    scanner_notes: list[str]
    analyzer_version: str = ANALYZER_VERSION


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def analyze_files(
    files: list[dict],
    *,
    exclude_globs: list[str],
    token_budget: int,
) -> AnalysisReport:
    """`files` items: path, language, content, changed_lines (set[int] | None means all)."""
    findings: list[AnalysisFinding] = []
    insights: list[FileInsight] = []
    skipped: list[dict] = []
    markers: list[dict] = []
    notes = [
        "Semgrep is optional and was not required for this run. Builtin rules produced the deterministic findings.",
    ]
    spent = 0
    ordered = sorted(files, key=lambda item: item["path"])
    for item in ordered:
        path = item["path"]
        content = item.get("content") or ""
        language = item.get("language") or "text"
        changed = item.get("changed_lines")
        if not safe_relative(path):
            skipped.append({"path": path, "reason": "Path failed the relative-path check and was not read."})
            continue
        if is_excluded(path, exclude_globs):
            skipped.append({"path": path, "reason": "Excluded by repository policy. The file was not silently dropped."})
            insights.append(FileInsight(path=path, language=language, skipped_reason="excluded by policy"))
            continue
        if path.split("/")[-1] in UNTRUSTED_CONFIG_NAMES:
            skipped.append(
                {
                    "path": path,
                    "reason": "Repository config cannot override PRISM security controls and was not applied.",
                }
            )
            markers.append({"path": path, "marker": "untrusted-repository-config"})
            insights.append(FileInsight(path=path, language=language, skipped_reason="untrusted repository config"))
            continue
        cost = estimate_tokens(content)
        if spent + cost > token_budget:
            skipped.append(
                {
                    "path": path,
                    "reason": f"Token budget {token_budget} would be exceeded ({spent + cost}). File was not analyzed.",
                }
            )
            insights.append(FileInsight(path=path, language=language, skipped_reason="token budget"))
            continue
        spent += cost
        if language != "python":
            skipped.append({"path": path, "reason": f"No deterministic rules for language '{language}'."})
            insights.append(FileInsight(path=path, language=language, skipped_reason="unsupported language"))
            continue
        if len(content) > 200_000:
            skipped.append({"path": path, "reason": "File exceeds the 200k character analysis limit."})
            insights.append(FileInsight(path=path, language=language, skipped_reason="size limit"))
            continue
        insight = _analyze_python(path, content, changed)
        insights.append(insight)
        findings.extend(insight.findings)
        for marker in insight.markers:
            markers.append({"path": path, "marker": marker})
    return AnalysisReport(
        findings=findings,
        files=insights,
        skipped=skipped,
        markers=markers,
        scanner_notes=notes,
    )


def analyze_source(path: str, source: str, changed_lines: set[int] | None = None) -> list[AnalysisFinding]:
    report = analyze_files(
        [
            {
                "path": path,
                "language": "python",
                "content": source,
                "changed_lines": changed_lines,
            }
        ],
        exclude_globs=[],
        token_budget=100_000,
    )
    return report.findings


def _analyze_python(path: str, source: str, changed: set[int] | None) -> FileInsight:
    lines = source.splitlines()
    if changed is None:
        changed = set(range(1, len(lines) + 1))
    insight = FileInsight(path=path, language="python", symbols=defined_functions(source))
    for match in INJECTION_MARKER.finditer(source):
        insight.markers.append(match.group(0)[:80])

    for start, call in iter_execute_calls(source):
        if _is_sql_injection(call):
            line_no = _anchor_line(call, start, changed, prefer=r"f['\"]|execute")
            if line_no not in changed:
                continue
            insight.findings.append(
                _finding(
                    source,
                    path,
                    line_no,
                    rule_id="sql_injection",
                    category="security",
                    severity="critical",
                    confidence=0.86,
                    claim_level="pattern",
                    title="SQL statement is built with string interpolation",
                    explanation=(
                        "A SQL statement that includes an f-string or concatenated value is passed to execute. "
                        "This is pattern evidence of injection, not a confirmed exploit against production data."
                    ),
                    impact="An attacker who controls an interpolated value may change the statement's meaning.",
                    remediation="Pass values as bound parameters. Do not interpolate them into the SQL text.",
                )
            )
        elif _in_loop(lines, start) and (
            start in changed
            or any(number in changed for number in range(start, start + call.count("\n") + 1))
        ):
            line_no = start if start in changed else next(
                number for number in range(start, start + 12) if number in changed
            )
            insight.findings.append(
                _finding(
                    source,
                    path,
                    line_no,
                    rule_id="n_plus_one",
                    category="performance",
                    severity="medium",
                    confidence=0.56,
                    claim_level="hypothesis",
                    title="Query executed inside a loop",
                    explanation=(
                        "A database call sits in a loop body. This is a hypothesis about repeated queries, "
                        "not a measured N+1 trace."
                    ),
                    impact="Latency can grow with the number of iterations if each call reaches the database.",
                    remediation="Load the set in one query, or measure the query count before rewriting.",
                    observed=False,
                    uncertain_reason="No query log or execution plan was collected.",
                )
            )

    for number, line in enumerate(lines, start=1):
        if number not in changed:
            continue
        code = code_only(line)
        if not code.strip():
            continue
        if "subprocess" in code and "shell=True" in code:
            insight.findings.append(
                _finding(
                    source,
                    path,
                    number,
                    rule_id="shell_true",
                    category="security",
                    severity="high",
                    confidence=0.9,
                    claim_level="pattern",
                    title="Subprocess runs a shell with an interpolated command",
                    explanation="shell=True was found on a changed line. The command string is code, not data.",
                    impact="Metacharacters in the interpolated value can execute additional shell commands.",
                    remediation="Pass an argument list and keep shell=False.",
                )
            )
        if re.search(r"\beval\s*\(", code) or re.search(r"\bexec\s*\(", code):
            insight.findings.append(
                _finding(
                    source,
                    path,
                    number,
                    rule_id="eval_exec",
                    category="security",
                    severity="critical",
                    confidence=0.93,
                    claim_level="pattern",
                    title="eval or exec executes dynamic code",
                    explanation="A changed line calls eval or exec. The rule does not know whether the input is trusted.",
                    impact="Untrusted input becomes executable code in this process.",
                    remediation="Remove eval/exec or replace it with an explicit parser for the expected shape.",
                )
            )
        if "pickle.loads" in code:
            insight.findings.append(
                _finding(
                    source,
                    path,
                    number,
                    rule_id="pickle_loads",
                    category="security",
                    severity="critical",
                    confidence=0.9,
                    claim_level="pattern",
                    title="pickle.loads deserializes untrusted bytes",
                    explanation=(
                        "pickle.loads can execute code while reconstructing an object. "
                        "A comment that calls the producer trusted is not evidence."
                    ),
                    impact="A crafted payload can run code when the bytes are loaded.",
                    remediation="Reject pickled payloads. Use an explicit schema, or keep session state on the server.",
                )
            )
        secret = SECRET_LINE.match(line.strip())
        if secret and "environ" not in line:
            insight.findings.append(
                _finding(
                    source,
                    path,
                    number,
                    rule_id="hardcoded_secret",
                    category="security",
                    severity="high",
                    confidence=0.88,
                    claim_level="pattern",
                    title="Secret material is assigned from a string literal",
                    explanation=(
                        f"{secret.group('name')} is a string literal in source. "
                        "The stored evidence snippet is redacted."
                    ),
                    impact="Anyone who can read the repository can reuse the credential.",
                    remediation="Read the value from the environment or a secret manager. Do not default it to empty in source.",
                    snippet=f"{secret.group('name')} = \"{secret.group('value')[:4]}…[redacted]\"",
                )
            )

    _float_currency(insight, source, lines, changed)
    _missing_auth(insight, source, lines, changed)
    return insight


def _is_sql_injection(call: str) -> bool:
    if not SQL_KEYWORD.search(call):
        return False
    masked = mask_block(call)
    if re.search(r"\b(execute|executemany)\s*\(\s*f\"\"", masked, re.S):
        return True
    if re.search(r"\+\s*[A-Za-z_]", masked):
        return True
    return False


def _in_loop(lines: list[str], execute_line: int) -> bool:
    """True when a previous unclosed `for` has a smaller indent than the execute line."""
    execute_index = execute_line - 1
    if execute_index >= len(lines):
        return False
    execute_indent = len(lines[execute_index]) - len(lines[execute_index].lstrip())
    for index in range(execute_index - 1, max(-1, execute_index - 15), -1):
        text = lines[index]
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        indent = len(text) - len(text.lstrip())
        if re.match(r"for\s+.+\bin\b", text.strip()) and indent < execute_indent:
            return True
        if indent < execute_indent and text.strip().startswith(("def ", "class ")):
            return False
    return False


def _anchor_line(call: str, start: int, changed: set[int], prefer: str) -> int:
    for offset, line in enumerate(call.splitlines()):
        number = start + offset
        if number in changed and re.search(prefer, line):
            return number
    for offset, _line in enumerate(call.splitlines()):
        if start + offset in changed:
            return start + offset
    return start


def _float_currency(insight: FileInsight, source: str, lines: list[str], changed: set[int]) -> None:
    current = ""
    flagged: set[str] = set()
    for number, line in enumerate(lines, start=1):
        match = re.match(r"\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
        if match:
            current = match.group(1)
        if number not in changed or not current or current in flagged or not MONEY_NAME.search(current):
            continue
        if re.search(r"\bfloat\s*\(", code_only(line)):
            flagged.add(current)
            insight.findings.append(
                _finding(
                    source,
                    insight.path,
                    number,
                    rule_id="float_currency",
                    category="correctness",
                    severity="high",
                    confidence=0.8,
                    claim_level="pattern",
                    title="Money value is converted through float",
                    explanation=(
                        f"{current} converts an integer amount with float. "
                        "Binary floating point does not represent decimal cents exactly."
                    ),
                    impact="Tax or totals can round to a cent the business rule did not intend.",
                    remediation="Keep the calculation in integer minor units, or use a decimal type with an explicit rounding mode.",
                )
            )


def _missing_auth(insight: FileInsight, source: str, lines: list[str], changed: set[int]) -> None:
    for number, line in enumerate(lines, start=1):
        match = ROUTE_DECORATOR.search(line)
        if not match or number not in changed:
            continue
        path = match.group(2)
        if "/internal" not in path and "/admin" not in path:
            continue
        window = "\n".join(lines[number - 1 : number + 24])
        if re.search(r"current_user|require_auth|Depends\s*\(|authorize\(|HTTPBearer", window):
            continue
        insight.findings.append(
            _finding(
                source,
                insight.path,
                number,
                rule_id="missing_auth",
                category="security",
                severity="medium",
                confidence=0.58,
                claim_level="hypothesis",
                title="Internal route has no local authorization check",
                explanation=(
                    f"The new route {path} does not reference an auth dependency in the handler. "
                    "A router-level middleware, if any, was not analyzed."
                ),
                impact="If no other layer authenticates the caller, the route is reachable without a session.",
                remediation="Require an explicit auth dependency on the handler, or show the middleware that covers this router.",
                observed=False,
                uncertain_reason="No global middleware analysis was performed.",
            )
        )


def _finding(source: str, path: str, line_no: int, *, snippet: str | None = None, **kwargs) -> AnalysisFinding:
    lines = source.splitlines()
    text = snippet if snippet is not None else (lines[line_no - 1].strip() if 0 < line_no <= len(lines) else "")
    symbol = enclosing_function(source, line_no)
    return AnalysisFinding(
        file_path=path,
        start_line=line_no,
        end_line=line_no,
        snippet=text[:500],
        enclosing_symbol=symbol,
        **kwargs,
    )


def coverage_gap(files: list[dict], findings: list[AnalysisFinding]) -> AnalysisFinding | None:
    symbols = []
    for finding in findings:
        if finding.enclosing_symbol and finding.enclosing_symbol not in symbols:
            symbols.append(finding.enclosing_symbol)
    if not symbols:
        return None
    test_blobs = []
    anchor_path = None
    anchor_line = 1
    anchor_snippet = ""
    for item in files:
        path = item["path"]
        if "/tests/" in f"/{path}" or path.startswith("tests/"):
            test_blobs.append(item.get("content") or "")
            if anchor_path is None:
                anchor_path = path
            for number, line in enumerate((item.get("content") or "").splitlines(), start=1):
                if "assert True" in line or line.strip().startswith("def test_"):
                    anchor_path = path
                    anchor_line = number
                    anchor_snippet = line.strip()
    blob = "\n".join(test_blobs)
    missing = [name for name in symbols if name not in blob]
    if not missing:
        return None
    if anchor_path is None:
        anchor_path = findings[0].file_path
        anchor_line = findings[0].start_line
        anchor_snippet = findings[0].snippet
    listed = ", ".join(missing)
    return AnalysisFinding(
        rule_id="test_gap",
        category="tests",
        severity="medium",
        confidence=0.7,
        claim_level="pattern",
        title="Changed functions are not named by the tests in this pull request",
        explanation=(
            "The test files in the diff do not mention these changed functions: "
            f"{listed}. Absence from a name search is not proof that behavior is untested elsewhere."
        ),
        file_path=anchor_path,
        start_line=anchor_line,
        end_line=anchor_line,
        snippet=anchor_snippet or "tests do not reference the changed functions",
        impact="A regression in the listed functions can merge without a failing test in this pull request.",
        remediation="Add a regression test that fails on the current code and passes after the fix.",
        observed=True,
        uncertain_reason="Only test files included in the pull request were searched.",
        enclosing_symbol=None,
    )
