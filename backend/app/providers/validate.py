"""Validate model findings against the schema and the files that were actually sent."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agents.schemas import Category, Claim, Severity

_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_FINDING_CAP = 30


class ModelFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    claim_level: Claim
    title: str = Field(min_length=3, max_length=180)
    explanation: str = Field(min_length=3, max_length=2_000)
    file_path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    impact: str = Field(min_length=3, max_length=1_000)
    remediation: str = Field(min_length=3, max_length=1_000)
    evidence: str = Field(min_length=3, max_length=500)
    uncertain_reason: str | None = None

    @model_validator(mode="after")
    def line_order(self) -> ModelFinding:
        if self.end_line < self.start_line:
            raise ValueError("end_line precedes start_line")
        return self


class ModelEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Any]
    notes: list[str] = Field(default_factory=list, max_length=8)


def validate_model_findings(payload: dict, files: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Return accepted findings and per-finding rejections.

    A schema failure of one item does not drop its siblings. The caller treats
    a payload that is not this envelope as a malformed response.
    """
    envelope = ModelEnvelope.model_validate(payload)
    accepted: list[dict] = []
    rejected: list[dict] = []
    for index, item in enumerate(envelope.findings):
        if index >= _FINDING_CAP:
            rejected.append({"index": index, "reason": "Finding cap exceeded."})
            continue
        if not isinstance(item, dict):
            rejected.append({"index": index, "reason": "Finding was not an object."})
            continue
        try:
            parsed = ModelFinding.model_validate(item)
        except ValidationError as exc:
            rejected.append({"index": index, "reason": _error_message(exc)})
            continue
        reason = _reject_reason(parsed, files)
        if reason:
            rejected.append(
                {
                    "index": index,
                    "reason": reason,
                    "file_path": parsed.file_path,
                    "start_line": parsed.start_line,
                    "category": parsed.category,
                }
            )
            continue
        accepted.append(_public(parsed, files))
    return _dedupe(accepted, rejected)


def _reject_reason(parsed: ModelFinding, files: dict[str, str]) -> str | None:
    if parsed.claim_level == "reproduced":
        return "claim_level reproduced is rejected because the model did not execute the code."
    if parsed.severity in {"critical", "high"} and parsed.claim_level == "hypothesis":
        return "High-severity findings cannot be bare hypotheses."
    content = files.get(parsed.file_path)
    if content is None:
        return "file_path is not one of the files in this review."
    lines = content.splitlines()
    if parsed.end_line > len(lines):
        return "Line number is outside the file."
    cited = "\n".join(lines[parsed.start_line - 1 : parsed.end_line])
    if parsed.evidence not in cited:
        return "evidence is not an exact substring of the cited lines."
    return None


def _public(parsed: ModelFinding, files: dict[str, str]) -> dict:
    lines = files[parsed.file_path].splitlines()
    snippet = "\n".join(lines[parsed.start_line - 1 : parsed.end_line])
    claim = parsed.claim_level
    return {
        "stable_key": _stable_key(parsed),
        "rule_id": None,
        "category": parsed.category,
        "severity": parsed.severity,
        "confidence": parsed.confidence,
        "claim_level": claim,
        "title": parsed.title,
        "explanation": parsed.explanation,
        "file_path": parsed.file_path,
        "start_line": parsed.start_line,
        "end_line": parsed.end_line,
        "impact": parsed.impact,
        "remediation": parsed.remediation,
        "uncertain_reason": parsed.uncertain_reason,
        "observed": True,
        "agent_note": (
            "Validated model finding. The cited lines match the fetched file. "
            "This is not an execution result."
        ),
        "snippet": snippet,
        "source": "model",
        "agent": parsed.category,
    }


def _dedupe(accepted: list[dict], rejected: list[dict]) -> tuple[list[dict], list[dict]]:
    best: dict[tuple[str, int, str], dict] = {}
    order: list[tuple[str, int, str]] = []
    for item in accepted:
        key = (item["file_path"], item["start_line"], item["category"])
        current = best.get(key)
        if current is None:
            best[key] = item
            order.append(key)
            continue
        if _SEVERITY_RANK[item["severity"]] > _SEVERITY_RANK[current["severity"]]:
            discarded = current
            best[key] = item
        else:
            discarded = item
        rejected.append(
            {
                "reason": "duplicate finding",
                "file_path": discarded["file_path"],
                "start_line": discarded["start_line"],
                "category": discarded["category"],
            }
        )
    return [best[key] for key in order], rejected


def _stable_key(parsed: ModelFinding) -> str:
    raw = f"model|{parsed.category}|{parsed.file_path}|{parsed.start_line}|{parsed.title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _error_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Finding did not match the schema."
    message = str(errors[0].get("msg") or "Finding did not match the schema.")
    return message[:240]
