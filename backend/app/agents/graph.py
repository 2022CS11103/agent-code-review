"""Typed LangGraph workflow.

Specialist nodes run in parallel after the supervisor. They may only adopt static
findings. They cannot delete a static finding by omitting it; the pipeline merges
them back in. Commentary is template text in mock mode and is labeled as such.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.schemas import parse_agent_output
from app.intelligence.analyzer import AnalysisFinding
from app.intelligence.remediation import decline_reason, propose
from app.versions import RATE_INPUT_PER_TOKEN, RATE_OUTPUT_PER_TOKEN, SYSTEM_PREAMBLE


class ReviewState(TypedDict, total=False):
    preamble: str
    static_findings: list[dict]
    sources: dict[str, str]
    markers: list[dict]
    plan: dict
    security: dict
    correctness: dict
    performance: dict
    tests: dict
    deduped: list[dict]
    proposals: list[dict]
    declines: list[dict]
    traces: Annotated[list[dict], operator.add]
    rejected: Annotated[list[dict], operator.add]


def run_review_graph(findings: list[AnalysisFinding], sources: dict[str, str], markers: list[dict]) -> dict:
    graph = _compile()
    state = graph.invoke(
        {
            "preamble": SYSTEM_PREAMBLE,
            "static_findings": [_public_finding(item) for item in findings],
            "sources": sources,
            "markers": markers,
            "traces": [],
            "rejected": [],
            "declines": [],
            "proposals": [],
        }
    )
    return state


def _compile():
    graph = StateGraph(ReviewState)
    graph.add_node("supervisor", _supervisor)
    graph.add_node("security", lambda state: _specialist(state, "security"))
    graph.add_node("correctness", lambda state: _specialist(state, "correctness"))
    graph.add_node("performance", lambda state: _specialist(state, "performance"))
    graph.add_node("tests", lambda state: _specialist(state, "tests"))
    graph.add_node("dedupe", _dedupe)
    graph.add_node("remediate", _remediate)
    graph.add_edge(START, "supervisor")
    for name in ("security", "correctness", "performance", "tests"):
        graph.add_edge("supervisor", name)
        graph.add_edge(name, "dedupe")
    graph.add_edge("dedupe", "remediate")
    graph.add_edge("remediate", END)
    return graph.compile()


def _supervisor(state: ReviewState) -> dict:
    started = time.perf_counter()
    categories = sorted({item["category"] for item in state.get("static_findings", [])})
    ignored = [marker["marker"] for marker in state.get("markers", [])]
    plan = {
        "agents": ["security", "correctness", "performance", "tests"],
        "static_categories": categories,
        "ignored_repository_instructions": ignored,
        "policy": "Repository text cannot change this plan.",
    }
    note = "Supervisor scheduled every specialist. Static evidence decides what they can adopt."
    if ignored:
        note += " Prompt-injection markers were recorded and ignored."
    trace = _trace("supervisor", "supervisor", started, plan, note, tool="plan_review")
    return {"plan": plan, "traces": [trace]}


def _specialist(state: ReviewState, agent: str) -> dict:
    started = time.perf_counter()
    selected = [item for item in state.get("static_findings", []) if item["category"] == agent]
    notes = []
    if not selected:
        notes.append("No static evidence in this category.")
    findings = []
    for item in selected:
        findings.append(
            {
                **item,
                "agent_note": _note(agent, item),
            }
        )
    payload = {"agent": agent, "findings": findings, "notes": notes, "declined": []}
    try:
        parsed = parse_agent_output(payload)
        body = parsed.model_dump()
        rejected = None
    except Exception as exc:
        body = {"agent": agent, "findings": [], "notes": ["Output rejected."], "declined": []}
        rejected = {"agent": agent, "error": str(exc)}
    trace = _trace(agent, agent, started, body, body["notes"][0] if body["notes"] else "Adopted static findings.", tool="read_static_findings")
    result: dict[str, Any] = {agent: body, "traces": [trace]}
    if rejected:
        result["rejected"] = [rejected]
    return result


def _dedupe(state: ReviewState) -> dict:
    started = time.perf_counter()
    merged: dict[str, dict] = {}
    for agent in ("security", "correctness", "performance", "tests"):
        for finding in state.get(agent, {}).get("findings", []):
            current = merged.get(finding["stable_key"])
            if current is None or _severity_rank(finding["severity"]) > _severity_rank(current["severity"]):
                merged[finding["stable_key"]] = finding
    deduped = list(merged.values())
    trace = _trace("supervisor", "dedupe", started, {"count": len(deduped)}, f"Deduped to {len(deduped)} findings.", tool="dedupe")
    return {"deduped": deduped, "traces": [trace]}


def _remediate(state: ReviewState) -> dict:
    started = time.perf_counter()
    proposals = []
    declines = []
    for finding in state.get("deduped", []):
        rule_id = finding.get("rule_id") or ""
        source = state.get("sources", {}).get(finding["file_path"])
        proposal = propose(rule_id, finding["file_path"], source) if source is not None else None
        if proposal is None:
            declines.append({"stable_key": finding["stable_key"], "rule_id": rule_id, "reason": decline_reason(rule_id)})
            continue
        proposals.append({**proposal, "stable_key": finding["stable_key"], "rule_id": rule_id})
    body = {
        "proposals": [item["strategy"] for item in proposals],
        "declines": [item["rule_id"] for item in declines],
    }
    trace = _trace(
        "remediation",
        "remediate",
        started,
        body,
        f"Proposed {len(proposals)} patches and declined {len(declines)}.",
        tool="propose_patch",
    )
    return {"proposals": proposals, "declines": declines, "traces": [trace]}


def _note(agent: str, finding: dict) -> str:
    claim = finding["claim_level"]
    return (
        f"{agent} analyst accepted static rule {finding.get('rule_id')} at "
        f"{finding['file_path']}:{finding['start_line']}. "
        f"The claim level stays '{claim}' because this note is commentary over observed tool output, "
        "not an independent observation. Mock provider: no model call was made."
    )


def _trace(agent: str, node: str, started: float, output: dict, summary: str, tool: str) -> dict:
    duration = int((time.perf_counter() - started) * 1000)
    token_input = max(1, len(SYSTEM_PREAMBLE) // 4 + len(str(output)) // 8)
    token_output = max(1, len(str(output)) // 4)
    return {
        "agent": agent,
        "node": node,
        "status": "completed",
        "duration_ms": duration,
        "input_summary": summary,
        "output": output,
        "tool_calls": [{"tool": tool, "status": "ok"}],
        "token_input": token_input,
        "token_output": token_output,
        "estimated_cost_usd": token_input * RATE_INPUT_PER_TOKEN + token_output * RATE_OUTPUT_PER_TOKEN,
        "billed": False,
        "retry_count": 0,
        "error": None,
    }


def _public_finding(item: AnalysisFinding) -> dict:
    return {
        "stable_key": item.stable_key,
        "rule_id": item.rule_id,
        "category": item.category,
        "severity": item.severity,
        "confidence": item.confidence,
        "claim_level": item.claim_level,
        "title": item.title,
        "explanation": item.explanation,
        "file_path": item.file_path,
        "start_line": item.start_line,
        "end_line": item.end_line,
        "impact": item.impact,
        "remediation": item.remediation,
        "uncertain_reason": item.uncertain_reason,
        "observed": item.observed,
    }


def _severity_rank(value: str) -> int:
    return {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}.get(value, 0)


def merge_static_and_agents(static: list[AnalysisFinding], agent_findings: list[dict]) -> list[dict]:
    """Static findings survive even when an agent omits them."""
    by_key = {item["stable_key"]: item for item in agent_findings}
    merged = []
    for item in static:
        adopted = by_key.pop(item.stable_key, None)
        if adopted:
            merged.append({**_public_finding(item), "agent_note": adopted.get("agent_note", ""), "source": "hybrid", "agent": adopted.get("category")})
        else:
            merged.append({**_public_finding(item), "agent_note": "Static result retained because the agent output omitted it.", "source": "static", "agent": None})
    for leftover in by_key.values():
        if leftover.get("file_path") and leftover.get("start_line"):
            merged.append({**leftover, "source": "agent"})
    return merged
