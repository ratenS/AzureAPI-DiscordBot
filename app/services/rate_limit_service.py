from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict


class RateLimitExceeded(Exception):
    """Raised when a caller exceeds the configured request budget."""


class RateLimitService:
    def __init__(self, max_requests_per_minute: int) -> None:
        self.max_requests_per_minute = max_requests_per_minute
        self._requests: Dict[str, Deque[datetime]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=1)
        queue = self._requests[key]

        while queue and queue[0] < window_start:
            queue.popleft()

        if len(queue) >= self.max_requests_per_minute:
            raise RateLimitExceeded(f"Rate limit exceeded for {key}")

        queue.append(now)
