"""Review providers. The pipeline depends on this package, not on a vendor SDK."""

from app.providers.registry import build_provider

__all__ = ["build_provider"]
