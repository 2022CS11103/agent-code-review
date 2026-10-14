"""Runtime configuration.

Environment variables use the PRISM_ prefix. Defaults are safe for demo mode:
SQLite on disk, no GitHub calls, and the deterministic mock model provider.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRISM_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/prism.db"
    demo_mode: bool = True
    auto_seed: bool = True
    worker_mode: str = "embedded"  # embedded | external | disabled
    stage_delay_ms: int = 450
    github_webhook_secret: str = "dev-only-webhook-secret"
    github_api_base: str = "https://api.github.com"
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_app_private_key: str | None = None
    github_app_private_key_path: str | None = None
    # Public https origin GitHub can call. Empty means Sync, not inbound webhooks.
    public_base_url: str = ""
    llm_provider: str = "mock"
    llm_model: str = "gpt-4.1-mini"
    llm_timeout_seconds: int = 20
    llm_max_calls: int = 2
    llm_max_input_chars: int = 24_000
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    sandbox_driver: str = "subprocess"  # subprocess | docker
    sandbox_image: str = "python:3.12-slim"
    sandbox_timeout_seconds: int = 20
    max_repair_attempts: int = 3
    token_budget: int = 24_000
    allowed_origins: str = "http://127.0.0.1:43123,http://localhost:43123"
    otlp_endpoint: str | None = None
    demo_user_email: str = "demo@prism.local"
    log_level: str = "INFO"
    request_max_bytes: int = 1_000_000
    rate_limit_per_minute: int = 180
    webhook_rate_limit_per_minute: int = 30

    @property
    def resolved_database_url(self) -> str:
        url = self.database_url
        prefix = "sqlite:///./"
        if url.startswith(prefix):
            path = (BACKEND_ROOT / url.removeprefix("sqlite:///")).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            # as_posix keeps Windows paths as C:/... which SQLite URLs accept.
            return "sqlite:///" + path.as_posix()
        return url

    @property
    def origin_allowlist(self) -> set[str]:
        return {item.strip() for item in self.allowed_origins.split(",") if item.strip()}

    @property
    def repair_cap(self) -> int:
        return max(0, min(self.max_repair_attempts, 3))


@lru_cache
def get_settings() -> Settings:
    return Settings()
