import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(slots=True)
class _UsageEvent:
    timestamp: float
    tokens: int


@dataclass(slots=True)
class LLMRateLimiter:
    """Process-local rolling-window limiter for sequential LLM requests."""

    tokens_per_minute: int = 0
    requests_per_minute: int = 0
    safety_factor: float = 0.9
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _events: deque[_UsageEvent] = field(default_factory=deque, init=False)
    _blocked_until: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        if self.tokens_per_minute < 0 or self.requests_per_minute < 0:
            raise ValueError("rate limits must be >= 0")
        if not 0 < self.safety_factor <= 1:
            raise ValueError("safety_factor must be in (0, 1]")

    def acquire(self, estimated_tokens: int) -> None:
        estimated_tokens = max(1, estimated_tokens)
        while True:
            with self._lock:
                now = self.clock()
                self._evict(now)
                wait = max(0.0, self._blocked_until - now)
                if wait == 0:
                    wait = self._capacity_wait(now, estimated_tokens)
                if wait == 0:
                    self._events.append(_UsageEvent(now, estimated_tokens))
                    return
            self.sleep(wait)

    def block(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(
                self._blocked_until, self.clock() + max(0.0, seconds)
            )

    def _evict(self, now: float) -> None:
        while self._events and now - self._events[0].timestamp >= 60:
            self._events.popleft()

    def _capacity_wait(self, now: float, estimated_tokens: int) -> float:
        token_budget = int(self.tokens_per_minute * self.safety_factor)
        request_budget = int(self.requests_per_minute * self.safety_factor)
        token_usage = sum(event.tokens for event in self._events)

        over_tokens = token_budget > 0 and token_usage + estimated_tokens > token_budget
        over_requests = request_budget > 0 and len(self._events) + 1 > request_budget
        if not over_tokens and not over_requests:
            return 0.0
        if not self._events:
            return 0.0
        return max(0.001, 60 - (now - self._events[0].timestamp))


def estimate_tokens(prompt: str, max_tokens: int) -> int:
    """Conservative dependency-free estimate for admission control."""
    estimated_input = max(1, (len(prompt) + 3) // 4)
    return estimated_input + max(0, max_tokens)
