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


_OPENAI_DEFAULT_MODEL = "gpt-4.1-mini"
_HOSTED = {
    "openai": ("openai", "PRISM_OPENAI_API_KEY", "openai_api_key", "openai_base_url", _OPENAI_DEFAULT_MODEL),
    "openai-compatible": ("openai", "PRISM_OPENAI_API_KEY", "openai_api_key", "openai_base_url", _OPENAI_DEFAULT_MODEL),
    "groq": ("groq", "PRISM_GROQ_API_KEY", "groq_api_key", "groq_base_url", "llama-3.3-70b-versatile"),
    "gemini": ("gemini", "PRISM_GEMINI_API_KEY", "gemini_api_key", "gemini_base_url", "gemini-2.5-flash"),
}


def llm_view(settings: Settings) -> dict:
    name = (settings.llm_provider or "mock").strip().lower()
    mock = name in {"mock", "deterministic", "deterministic-mock"}
    target = _hosted_target(settings)
    if mock or target is None:
        active = "deterministic-mock" if mock else (name or "unconfigured")
        model = settings.llm_model
    else:
        active = f"{target['vendor']}:{target['model']}"
        model = target["model"]
    return {
        "active_provider": active,
        "configured_provider": name or "mock",
        "model": model,
        "openai_configured": bool(settings.openai_api_key),
        "groq_configured": bool(settings.groq_api_key),
        "gemini_configured": bool(settings.gemini_api_key),
        "timeout_seconds": max(1, min(int(settings.llm_timeout_seconds), 60)),
        "max_calls": max(0, min(int(settings.llm_max_calls), 3)),
        "max_input_chars": max(1_000, min(int(settings.llm_max_input_chars), 80_000)),
        "note": (
            "Demo mode uses the deterministic mock and does not call a model. "
            "Set PRISM_LLM_PROVIDER to gemini or groq and the matching key, PRISM_GEMINI_API_KEY or PRISM_GROQ_API_KEY. "
            "Leave PRISM_LLM_MODEL at its default to use gemini-2.5-flash or llama-3.3-70b-versatile. "
            "Keys stay in the API process. A failed or budget-exhausted model call does not complete the review. "
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
    target = _hosted_target(settings)
    if target is None:
        return UnavailableProvider(name or "empty")
    return OpenAIReviewProvider(
        api_key=target["api_key"],
        base_url=target["base_url"],
        model=target["model"],
        timeout_seconds=settings.llm_timeout_seconds,
        max_calls=settings.llm_max_calls,
        max_input_chars=settings.llm_max_input_chars,
        vendor=target["vendor"],
        key_env=target["key_env"],
    )


def _hosted_target(settings: Settings) -> dict | None:
    name = (settings.llm_provider or "mock").strip().lower()
    profile = _HOSTED.get(name)
    if profile is None:
        return None
    vendor, key_env, key_attr, url_attr, default_model = profile
    return {
        "vendor": vendor,
        "key_env": key_env,
        "api_key": getattr(settings, key_attr) or "",
        "base_url": getattr(settings, url_attr),
        "model": _model_for(settings, vendor, default_model),
    }


def _model_for(settings: Settings, vendor: str, default_model: str) -> str:
    chosen = (settings.llm_model or "").strip()
    if vendor in {"groq", "gemini"} and chosen in {"", _OPENAI_DEFAULT_MODEL}:
        return default_model
    return chosen or default_model
