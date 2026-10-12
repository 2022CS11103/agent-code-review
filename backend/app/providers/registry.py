"""Select the review provider from process configuration."""

from __future__ import annotations

from app.config import Settings
from app.providers.base import ProviderResult, ReviewContext, ReviewProvider
from app.providers.deterministic import DeterministicReviewProvider
from app.providers.openai_provider import OpenAIReviewProvider


class UnavailableProvider:
    kind = "model"
    label = "unconfigured"

    def __init__(self, name: str):
        self.label = f"unconfigured:{name}"[:64]
        self._name = name

    def review(self, context: ReviewContext) -> ProviderResult:
        del context
        return ProviderResult(
            status="failed",
            error=f"Unknown model provider '{self._name}'. No model was called.",
            usage={
                "token_input": 0,
                "token_output": 0,
                "estimated_cost_usd": 0,
                "billed": False,
                "rate_card": "none",
                "provider": self.label,
                "calls": 0,
                "max_calls": 0,
                "note": "Provider name was not recognized. This review is not a clean result.",
            },
        )


def llm_view(settings: Settings) -> dict:
    name = (settings.llm_provider or "mock").strip().lower()
    mock = name in {"mock", "deterministic", "deterministic-mock"}
    active = "deterministic-mock" if mock else name or "unconfigured"
    if name in {"openai", "openai-compatible"}:
        model = settings.llm_model
        active = f"openai:{model}"
    else:
        model = settings.llm_model
    return {
        "active_provider": active,
        "configured_provider": name or "mock",
        "model": model,
        "openai_configured": bool(settings.openai_api_key),
        "timeout_seconds": max(1, min(int(settings.llm_timeout_seconds), 60)),
        "max_calls": max(0, min(int(settings.llm_max_calls), 3)),
        "max_input_chars": max(1_000, min(int(settings.llm_max_input_chars), 80_000)),
        "note": (
            "Demo mode uses the deterministic mock and does not call a model. "
            "Set PRISM_LLM_PROVIDER=openai with PRISM_OPENAI_API_KEY to request validated findings. "
            "The key stays in the API process. A failed or budget-exhausted model call does not complete the review. "
            "Model findings do not generate patches or execute pull-request code."
        ),
        "workspace_isolation": (
            "GitHub installations attach only to the prism-demo workspace. "
            "Installation-to-repository authorization is not enforced for multiple users."
        ),
    }


def build_provider(settings: Settings) -> ReviewProvider:
    name = (settings.llm_provider or "mock").strip().lower()
    if name in {"mock", "deterministic", "deterministic-mock"}:
        return DeterministicReviewProvider()
    if name in {"openai", "openai-compatible"}:
        return OpenAIReviewProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_calls=settings.llm_max_calls,
            max_input_chars=settings.llm_max_input_chars,
        )
    return UnavailableProvider(name or "empty")
