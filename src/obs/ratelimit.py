from __future__ import annotations

# Thin wrappers or re-exports for rate limiting under the Sprint-6 obs namespace
# We keep using the proven implementation in core.rate_limiter to avoid duplication.

try:
    from ..core.rate_limiter import AsyncLeakyBucket  # re-export
except Exception:  # pragma: no cover
    class AsyncLeakyBucket:  # type: ignore
        def __init__(self, rps: float = 1.0, capacity: int = 10):
            self.rps = float(rps)
            self.capacity = int(capacity)
        async def acquire(self):
            return None
