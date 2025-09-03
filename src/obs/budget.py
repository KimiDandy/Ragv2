from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .costs import estimate_cost_idr


@dataclass
class BudgetConfig:
    token_total: int = 0
    cost_idr_total: float = 0.0
    time_seconds_total: float = 0.0
    stop_when_remaining_ratio: float = 0.10  # stop spawning when <=10% left
    model: str = "gpt-4.1"


class Budget:
    def __init__(self, cfg: BudgetConfig):
        self.cfg = cfg
        self.token_used: int = 0
        self.cost_used_idr: float = 0.0
        self.started_at = time.time() if cfg.time_seconds_total > 0 else 0.0

    # --- Token helpers ---
    def can_afford_tokens(self, token_estimate_in: int = 0, token_estimate_out: int = 0) -> bool:
        if self.cfg.token_total <= 0:
            return True
        return (self.token_used + token_estimate_in + token_estimate_out) <= self.cfg.token_total

    def charge_tokens(self, token_in: int = 0, token_out: int = 0):
        self.token_used += max(0, int(token_in)) + max(0, int(token_out))

    # --- Cost helpers ---
    def can_afford_cost(self, token_estimate_in: int = 0, token_estimate_out: int = 0) -> bool:
        if self.cfg.cost_idr_total <= 0:
            return True
        est = estimate_cost_idr(self.cfg.model, token_estimate_in, token_estimate_out)
        return (self.cost_used_idr + est) <= self.cfg.cost_idr_total

    def charge_cost(self, token_in: int = 0, token_out: int = 0):
        self.cost_used_idr += estimate_cost_idr(self.cfg.model, token_in, token_out)

    # --- Time helpers ---
    def time_remaining(self) -> float:
        if self.cfg.time_seconds_total <= 0 or self.started_at <= 0:
            return float("inf")
        spent = time.time() - self.started_at
        return max(0.0, float(self.cfg.time_seconds_total) - float(spent))

    def time_exhausted(self) -> bool:
        if self.cfg.time_seconds_total <= 0:
            return False
        return self.time_remaining() <= 0.0

    # --- Stop-when-low policy ---
    def should_stop_spawning(self) -> bool:
        r = self.cfg.stop_when_remaining_ratio
        if r <= 0:
            return False
        # token-based decision prioritized
        if self.cfg.token_total > 0:
            return (self.token_used / float(self.cfg.token_total)) >= (1.0 - r)
        # else cost-based
        if self.cfg.cost_idr_total > 0:
            return (self.cost_used_idr / float(self.cfg.cost_idr_total)) >= (1.0 - r)
        # else time-based is too volatile; do not block spawning on time
        return False

    # --- Composite checks ---
    def can_spawn(self, token_estimate_in: int = 0, token_estimate_out: int = 0) -> bool:
        if self.should_stop_spawning():
            return False
        return self.can_afford_tokens(token_estimate_in, token_estimate_out) and self.can_afford_cost(token_estimate_in, token_estimate_out) and (not self.time_exhausted())
