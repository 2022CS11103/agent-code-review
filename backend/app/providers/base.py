"""Provider contract shared by the deterministic mock and hosted models.

A review either returns validated findings or an explicit unsuccessful status.
It never turns a provider failure into an empty successful review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.intelligence.analyzer import AnalysisFinding

ProviderStatus = Literal["completed", "budget_exhausted", "failed"]


class ProviderError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class BudgetExhausted(ProviderError):
    pass


class ProviderCallError(ProviderError):
    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class FileSlice:
    path: str
    content: str
    language: str
    changed_lines: set[int]


@dataclass
class ReviewContext:
    title: str
    body: str
    files: list[FileSlice]
    sources: dict[str, str]
    markers: list[dict]
    static_findings: list[AnalysisFinding]


@dataclass
class ProviderResult:
    status: ProviderStatus
    findings: list[dict] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    declines: list[dict] = field(default_factory=list)
    traces: list[dict] = field(default_factory=list)
    plan: dict | None = None
    usage: dict = field(default_factory=dict)
    calls: int = 0
    rejected: list[dict] = field(default_factory=list)
    error: str | None = None


class ReviewProvider(Protocol):
    kind: str
    label: str

    def review(self, context: ReviewContext) -> ProviderResult: ...
