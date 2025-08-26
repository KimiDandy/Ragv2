import asyncio
import time

class AsyncLeakyBucket:
    def __init__(self, rps: float, capacity: int = 10):
        self.rps = float(max(rps or 0.1, 0.001))
        self.capacity = int(max(capacity or 1, 1))
        self.tokens = float(self.capacity)
        self.last = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rps)
            self.last = now
            if self.tokens < 1.0:
                wait_s = (1.0 - self.tokens) / self.rps
                await asyncio.sleep(max(wait_s, 0))
                self.tokens = 0.0
            else:
                self.tokens -= 1.0
