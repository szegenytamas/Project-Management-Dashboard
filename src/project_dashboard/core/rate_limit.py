"""A small in-memory rate limiter (sliding window).

Used for `POST /login` and `POST /auth`: without a limit, anyone can try
passwords as fast as the server answers. argon2 makes each guess slow, but it
also makes each guess cost us CPU - so unlimited guessing is both a brute-force
and a denial-of-service risk.

How it works: for every key (the client's IP address) we keep the times of its
recent requests. A request is allowed if fewer than `limit` requests happened
in the last `window_seconds`; otherwise the caller learns how long to wait.
Rejected requests are not recorded, so a client that waits is let in again.

Limits of this design (documented in docs/security.md):
- The counters live in the memory of ONE process. That fits our deployment
  (one container on one server). Several instances would each count on their
  own; a shared store such as Redis would then be needed.
- Clients behind one NAT share an IP address and therefore share the limit.
- Memory is bounded: at most `max_keys` addresses are tracked; when full, keys
  without recent requests are dropped first, then the oldest ones.
"""

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,  # monotonic: immune to clock changes
    ) -> None:
        if limit < 1 or window_seconds <= 0 or max_keys < 1:
            raise ValueError("limit, window_seconds and max_keys must be positive")
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """Record a request for `key`.

        Returns None if it is allowed, otherwise the number of seconds until the
        next request would be allowed. No `await` inside: on the event loop the
        whole check-and-record runs without interruption, so no lock is needed.
        """
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            self._make_room(now)
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return hits[0] + self.window - now
        hits.append(now)
        return None

    def _make_room(self, now: float) -> None:
        if len(self._hits) < self.max_keys:
            return
        # First drop every key whose requests are all outside the window ...
        for key in [
            k for k, hits in self._hits.items() if not hits or hits[-1] <= now - self.window
        ]:
            del self._hits[key]
        # ... then, if still full, the oldest keys (dicts keep insertion order).
        while len(self._hits) >= self.max_keys:
            del self._hits[next(iter(self._hits))]
