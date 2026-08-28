from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RateLimitPolicy:
    requests_per_second: float
    burst: int = 1

    def __post_init__(self) -> None:
        if self.requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if self.burst != 1:
            raise ValueError("Only burst=1 is supported")


@dataclass
class RateLimiter:
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _next_allowed: dict[str, float] = field(default_factory=dict)

    def wait(self, key: str, policy: RateLimitPolicy) -> None:
        now = self.monotonic()
        delay = max(0.0, self._next_allowed.get(key, now) - now)
        if delay:
            self.sleep(delay)
        checked_at = self.monotonic()
        interval = 1.0 / policy.requests_per_second
        self._next_allowed[key] = checked_at + interval
