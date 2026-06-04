import asyncio
import time


class AsyncRateLimiter:
    """Enforces a minimum delay between consecutive async calls."""

    def __init__(self, delay_ms: int = 1500) -> None:
        self._delay = delay_ms / 1000.0
        self._last_call: float = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        remaining = self._delay - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_call = time.monotonic()

    async def backoff(self, attempt: int) -> None:
        """Exponential backoff: 2s, 4s, 8s, ..."""
        delay = min(2 ** attempt, 30)
        await asyncio.sleep(delay)
