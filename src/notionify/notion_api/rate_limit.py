"""Token-bucket rate limiters for client-side pacing.

Provides both a thread-safe synchronous :class:`TokenBucket` and an
async-safe :class:`AsyncTokenBucket`.  Each implements a classic token-bucket
algorithm: tokens are replenished at a fixed *rate* (tokens per second) up to
a configurable *burst* ceiling.  When a caller requests more tokens than are
currently available, the bucket computes how long the caller must wait and
either blocks (sync) or awaits (async) for that duration.

PRD reference: section 16.1.
"""

from __future__ import annotations

import asyncio
import threading
import time


class TokenBucket:
    """Thread-safe token bucket for synchronous rate limiting.

    Parameters
    ----------
    rate_rps:
        Sustained token-refill rate in tokens per second.
    burst:
        Maximum number of tokens the bucket can hold (burst ceiling).
    """

    __slots__ = ("_lock", "burst", "last_refill", "rate", "tokens")

    def __init__(self, rate_rps: float, burst: int = 10) -> None:
        if rate_rps <= 0:
            raise ValueError(f"rate_rps must be > 0, got {rate_rps}")
        if burst < 1:
            raise ValueError(f"burst must be >= 1, got {burst}")

        self.rate: float = rate_rps
        self.burst: int = burst
        self.tokens: float = float(burst)
        self.last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> float:
        """Acquire *tokens* from the bucket, blocking if necessary.

        Returns the number of seconds the caller had to wait (``0.0`` if
        tokens were immediately available).
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0

            deficit = tokens - self.tokens
            wait = deficit / self.rate
            self.tokens = 0.0
            self.last_refill = now

        # Sleep outside the lock so other threads can acquire concurrently
        # once their wait elapses.
        time.sleep(wait)
        return wait


class AsyncTokenBucket:
    """Async-safe token bucket for asynchronous rate limiting.

    Mirrors :class:`TokenBucket` but uses :func:`asyncio.sleep` and an
    :class:`asyncio.Lock` instead of OS-level threading primitives.

    Parameters
    ----------
    rate_rps:
        Sustained token-refill rate in tokens per second.
    burst:
        Maximum number of tokens the bucket can hold (burst ceiling).
    """

    __slots__ = ("_lock", "burst", "last_refill", "rate", "tokens")

    def __init__(self, rate_rps: float, burst: int = 10) -> None:
        if rate_rps <= 0:
            raise ValueError(f"rate_rps must be > 0, got {rate_rps}")
        if burst < 1:
            raise ValueError(f"burst must be >= 1, got {burst}")

        self.rate: float = rate_rps
        self.burst: int = burst
        self.tokens: float = float(burst)
        self.last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> float:
        """Acquire *tokens* from the bucket, awaiting if necessary.

        Returns the number of seconds the caller had to wait (``0.0`` if
        tokens were immediately available).
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0

            deficit = tokens - self.tokens
            wait = deficit / self.rate
            self.tokens = 0.0
            self.last_refill = now

        # Sleep outside the lock so other coroutines can proceed.
        await asyncio.sleep(wait)
        return wait
