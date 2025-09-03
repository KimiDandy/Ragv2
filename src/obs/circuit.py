from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class CircuitConfig:
    failure_threshold: int = 5
    cooldown_seconds: float = 15.0
    half_open_trials: int = 2


class CircuitBreaker:
    """Simple circuit breaker with open/half-open/closed states.

    Usage:
      cb = CircuitBreaker(CircuitConfig(...))
      if not cb.allow():
          # degrade or skip
      try:
          ... call ...
          cb.success()
      except Exception:
          cb.failure()
    """

    def __init__(self, cfg: CircuitConfig | None = None):
        self.cfg = cfg or CircuitConfig()
        self.failures = 0
        self.state = "closed"  # closed|open|half-open
        self.opened_at = 0.0
        self.trials = 0

    def allow(self) -> bool:
        now = time.time()
        if self.state == "open":
            if (now - self.opened_at) >= self.cfg.cooldown_seconds:
                self.state = "half-open"
                self.trials = 0
                return True
            return False
        if self.state == "half-open":
            return self.trials < self.cfg.half_open_trials
        return True

    def success(self):
        if self.state in ("open", "half-open"):
            self.state = "closed"
            self.failures = 0
            self.trials = 0
            self.opened_at = 0.0
        else:
            # remain closed
            self.failures = 0

    def failure(self):
        if self.state == "half-open":
            self.trials += 1
            self.state = "open"
            self.opened_at = time.time()
            return
        self.failures += 1
        if self.failures >= self.cfg.failure_threshold:
            self.state = "open"
            self.opened_at = time.time()
