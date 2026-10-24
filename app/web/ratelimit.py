"""In-memory per-IP token bucket with bounded memory.

Correct here because there is exactly one process. A restart sheds the
state, which is also what a flood deserves.
"""

from __future__ import annotations

import time
from collections import OrderedDict

MAX_BUCKETS = 4096


class TokenBucket:
    def __init__(self, per_minute: int = 60, burst: int = 20) -> None:
        self.rate = max(1, per_minute) / 60.0
        self.capacity = max(1, burst)
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def allow(self, identity: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        tokens, last = self._buckets.get(identity, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.rate)
        if tokens < 1.0:
            wait = (1.0 - tokens) / self.rate
            self._buckets[identity] = (tokens, now)
            self._buckets.move_to_end(identity)
            return False, max(0.0, wait)
        tokens -= 1.0
        self._buckets[identity] = (tokens, now)
        self._buckets.move_to_end(identity)
        if len(self._buckets) > MAX_BUCKETS:
            self._buckets.popitem(last=False)
        return True, 0.0

    def size(self) -> int:
        return len(self._buckets)
