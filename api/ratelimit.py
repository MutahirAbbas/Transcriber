from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Token bucket per key (client IP): `rate` requests/second, bursts up to `burst`."""

    MAX_KEYS = 10_000

    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate, self.burst, self.clock = rate, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            if len(self._buckets) > self.MAX_KEYS:
                self._buckets.clear()
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            return True
