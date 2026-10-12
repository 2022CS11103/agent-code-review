"""Deterministic provider. It adopts static findings and does not call a model."""

from __future__ import annotations

import time

from app.agents.graph import run_review_graph
from app.providers.base import ProviderResult, ReviewContext
from app.versions import RATE_CARD


class DeterministicReviewProvider:
    kind = "mock"
    label = "deterministic-mock"

    def review(self, context: ReviewContext) -> ProviderResult:
        started = time.perf_counter()
        state = run_review_graph(context.static_findings, context.sources, context.markers)
        traces = state.get("traces") or []
        for trace in traces:
            trace["billed"] = False
        usage = _usage(traces)
        usage["calls"] = 0
        usage["max_calls"] = 0
        usage["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return ProviderResult(
            status="completed",
            findings=list(state.get("deduped") or []),
            proposals=list(state.get("proposals") or []),
            declines=list(state.get("declines") or []),
            traces=traces,
            plan=state.get("plan"),
            usage=usage,
            calls=0,
            rejected=list(state.get("rejected") or []),
            error=None,
        )


def _usage(traces: list[dict]) -> dict:
    token_input = sum(item.get("token_input") or 0 for item in traces)
    token_output = sum(item.get("token_output") or 0 for item in traces)
    cost = sum(item.get("estimated_cost_usd") or 0 for item in traces)
    if not traces:
        token_input = 0
        token_output = 0
        cost = 0
    return {
        "token_input": token_input,
        "token_output": token_output,
        "estimated_cost_usd": round(cost, 8),
        "billed": False,
        "rate_card": RATE_CARD,
        "provider": "deterministic-mock",
        "note": "Estimate only. The mock provider did not call a model API.",
    }
