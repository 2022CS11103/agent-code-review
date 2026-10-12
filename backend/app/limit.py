"""Single-process rate limit. It does not coordinate across multiple API replicas."""

import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        recent = [stamp for stamp in self.hits[key] if now - stamp < self.window]
        if len(recent) >= self.limit:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        return True
