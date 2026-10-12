"""Patch validation runners.

The subprocess driver is for first-party demo fixtures only. It is not a security
boundary for untrusted pull-request code. Docker is the stronger local option and
still is not a complete sandbox: a container is not a VM, and the workspace mount
is a limitation. The interface is the extension point for a later isolate.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from app.intelligence.paths import safe_relative
from app.redact import redact_text, scrub_path

SAFE_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "LANG": "C.UTF-8",
}


@dataclass
class CaseResult:
    name: str
    outcome: str
    duration_ms: int | None
    output: str
    phase: str


@dataclass
class SuiteResult:
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    tests: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for case in self.tests if case.outcome == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for case in self.tests if case.outcome in {"failed", "error"})


def sandbox_env(workspace: Path) -> dict[str, str]:
    """Allowlist. Parent secrets are not inherited."""
    path = os.environ.get("PATH", "/usr/bin:/bin")
    return {
        "PATH": path,
        "HOME": str(workspace),
        "PYTHONPATH": "src",
        **SAFE_ENV,
    }


def docker_run_args(image: str, workspace: Path, timeout: int, test_paths: list[str]) -> list[str]:
    if not image or any(token in image for token in (" ", "\n", ";")):
        raise ValueError("Sandbox image is not a single image reference")
    return [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--user",
        "65534:65534",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-v",
        f"{workspace}:/work:rw",
        "-w",
        "/work",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONPATH=src",
        image,
        "timeout",
        str(int(timeout)),
        "python",
        "-m",
        "pytest",
        "-q",
        *test_paths,
    ]


def write_tree(workspace: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        if not safe_relative(path):
            raise ValueError(f"Refusing to write unsafe path {path}")
        dest = (workspace / path).resolve()
        if not str(dest).startswith(str(workspace.resolve())):
            raise ValueError(f"Path escapes the workspace: {path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)


def run_pytest(workspace: Path, test_paths: list[str], timeout: int) -> SuiteResult:
    xml_path = workspace / "junit.xml"
    if xml_path.exists():
        xml_path.unlink()
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=line",
        f"--junitxml={xml_path.name}",
        *test_paths,
    ]
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        cwd=workspace,
        env=sandbox_env(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        preexec_fn=_limits(timeout),
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc.pid)
        stdout, stderr = proc.communicate()
        code = proc.returncode
    duration = int((time.monotonic() - started) * 1000)
    root = str(workspace)
    out = scrub_path(redact_text(stdout.decode(errors="replace")), root)
    err = scrub_path(redact_text(stderr.decode(errors="replace")), root)
    tests = _parse_junit(xml_path, root) if xml_path.exists() else []
    if timed_out and not tests:
        tests.append(
            CaseResult(name="suite", outcome="error", duration_ms=duration, output="Timed out", phase="patched")
        )
    return SuiteResult(
        exit_code=code,
        timed_out=timed_out,
        duration_ms=duration,
        stdout=out,
        stderr=err,
        tests=tests,
    )


def _limits(timeout: int):
    def _apply() -> None:
        import resource

        cpu = max(timeout + 2, 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        # 1 GiB. Pytest itself needs more than a few dozen megabytes.
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))

    return _apply


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _parse_junit(path: Path, workspace: str) -> list[CaseResult]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    cases: list[CaseResult] = []
    for case in root.iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        duration = case.get("time")
        duration_ms = int(float(duration) * 1000) if duration else None
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if failure is not None:
            outcome = "failed"
            output = failure.get("message") or (failure.text or "")
        elif error is not None:
            outcome = "error"
            output = error.get("message") or (error.text or "")
        elif skipped is not None:
            outcome = "skipped"
            output = skipped.get("message") or ""
        else:
            outcome = "passed"
            output = ""
        cases.append(
            CaseResult(
                name=name or case.get("name") or "test",
                outcome=outcome,
                duration_ms=duration_ms,
                output=scrub_path(redact_text(output, limit=4000), workspace),
                phase="patched",
            )
        )
    return cases
