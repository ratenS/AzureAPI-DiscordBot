from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict


class RateLimitExceeded(Exception):
    """Raised when a caller exceeds the configured request budget."""


class RateLimitService:
    def __init__(self, max_requests_per_minute: int) -> None:
        self.max_requests_per_minute = max_requests_per_minute
        self._requests: Dict[str, Deque[datetime]] = defaultdict(deque)
        self._checks_since_sweep = 0

    def check(self, key: str) -> None:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=1)
        self._checks_since_sweep += 1
        if self._checks_since_sweep >= 100:
            self._sweep_expired(window_start)
            self._checks_since_sweep = 0

        queue = self._requests[key]

        while queue and queue[0] < window_start:
            queue.popleft()

        if len(queue) >= self.max_requests_per_minute:
            raise RateLimitExceeded(f"Rate limit exceeded for {key}")

        queue.append(now)

    def _sweep_expired(self, window_start: datetime) -> None:
        stale_keys: list[str] = []
        for bucket_key, queue in self._requests.items():
            while queue and queue[0] < window_start:
                queue.popleft()
            if not queue:
                stale_keys.append(bucket_key)

        for stale_key in stale_keys:
            self._requests.pop(stale_key, None)
