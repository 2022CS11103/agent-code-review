"""Structured agent outputs. Malformed payloads are rejected, not coerced into findings."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["security", "correctness", "performance", "tests"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Claim = Literal["pattern", "hypothesis", "reproduced"]
AgentName = Literal["supervisor", "security", "correctness", "performance", "tests", "remediation"]


class AgentFindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_key: str = Field(min_length=8, max_length=64)
    rule_id: str | None = None
    category: Category
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    claim_level: Claim
    title: str = Field(min_length=3, max_length=180)
    explanation: str = Field(min_length=3)
    file_path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    impact: str
    remediation: str
    uncertain_reason: str | None = None
    observed: bool
    agent_note: str = ""

    @model_validator(mode="after")
    def high_severity_needs_a_pattern(self) -> AgentFindingModel:
        if self.severity in {"critical", "high"} and self.claim_level == "hypothesis":
            raise ValueError("High-severity findings cannot be bare hypotheses")
        if self.end_line < self.start_line:
            raise ValueError("end_line precedes start_line")
        return self


class AgentOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: AgentName
    findings: list[AgentFindingModel]
    notes: list[str]
    declined: list[str] = []


def parse_agent_output(payload: dict) -> AgentOutputModel:
    return AgentOutputModel.model_validate(payload)
