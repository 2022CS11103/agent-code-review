"""Optional Ruff invocation. Missing output is recorded; it is not treated as a pass."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from app.intelligence.analyzer import AnalysisFinding
from app.intelligence.paths import safe_relative
from app.intelligence.symbols import enclosing_function


def run_ruff(files: dict[str, str]) -> tuple[list[AnalysisFinding], str]:
    binary = Path(sys.executable).parent / "ruff"
    if not binary.exists():
        return [], "ruff binary not found next to the interpreter; S-rules were not run."
    with tempfile.TemporaryDirectory(prefix="prism-ruff-") as tmp:
        root = Path(tmp)
        for path, content in files.items():
            if not path.endswith(".py") or not safe_relative(path):
                continue
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        try:
            proc = subprocess.run(
                [str(binary), "check", "--select", "S", "--output-format", "json", str(root)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=root,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return [], f"ruff failed to run: {exc}"
        note = f"ruff S-rules exited {proc.returncode}."
        if not proc.stdout.strip():
            return [], note + " No diagnostics."
        try:
            diagnostics = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return [], note + " Output was not JSON."
    findings: list[AnalysisFinding] = []
    for item in diagnostics:
        filename = item.get("filename") or ""
        try:
            rel = str(Path(filename).resolve().relative_to(root))
        except Exception:
            rel = filename
        row = int((item.get("location") or {}).get("row") or 1)
        code = (item.get("code") or "S000").strip()
        if code == "S101":
            continue
        message = item.get("message") or code
        source = files.get(rel, "")
        findings.append(
            AnalysisFinding(
                rule_id=f"ruff_{code.lower()}",
                category="security",
                severity="medium",
                confidence=0.6,
                claim_level="pattern",
                title=f"Ruff {code}: {message[:120]}",
                explanation=f"Ruff rule {code} reported: {message}. This is a scanner result, separate from agent commentary.",
                file_path=rel,
                start_line=row,
                end_line=row,
                snippet=(source.splitlines()[row - 1].strip() if 0 < row <= len(source.splitlines()) else code),
                impact="Depends on the Ruff rule. Treat it as a lead, not as a confirmed exploit.",
                remediation="Inspect the rule and keep or dismiss it with a recorded decision.",
                observed=True,
                enclosing_symbol=enclosing_function(source, row) if source else None,
                source="ruff",
            )
        )
    return findings, note
