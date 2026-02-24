"""Comprehensive unit tests for retries.py and rate_limit.py.

Targets:
  - retries.py:   should_retry, compute_backoff
  - rate_limit.py: TokenBucket, AsyncTokenBucket
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from notionify.notion_api.retries import compute_backoff, should_retry
from notionify.notion_api.rate_limit import AsyncTokenBucket, TokenBucket


# ---------------------------------------------------------------------------
# should_retry
# ---------------------------------------------------------------------------


class TestShouldRetry:
    """Tests for should_retry()."""

    # -- attempt exhaustion ---------------------------------------------------

    def test_returns_false_when_attempt_plus_one_equals_max(self):
        # attempt=2, max_attempts=3 → attempt+1 == max_attempts → False
        assert should_retry(None, None, attempt=2, max_attempts=3) is False

    def test_returns_false_when_attempt_exceeds_max(self):
        # attempt=5, max_attempts=3 → attempt+1 > max_attempts → False
        assert should_retry(500, None, attempt=5, max_attempts=3) is False

    def test_returns_false_on_last_attempt_with_retryable_status(self):
        assert should_retry(429, None, attempt=4, max_attempts=5) is False

    # -- exception-based decisions --------------------------------------------

    def test_timeout_exception_is_retryable(self):
        exc = httpx.ReadTimeout("timed out", request=MagicMock())
        assert should_retry(None, exc, attempt=0, max_attempts=3) is True

    def test_connect_timeout_subclass_is_retryable(self):
        # httpx.ConnectTimeout is a subclass of httpx.TimeoutException
        exc = httpx.ConnectTimeout("connect timed out", request=MagicMock())
        assert should_retry(None, exc, attempt=0, max_attempts=3) is True

    def test_network_error_is_retryable(self):
        exc = httpx.NetworkError("network error")
        assert should_retry(None, exc, attempt=0, max_attempts=3) is True

    def test_connect_error_subclass_of_network_error_is_retryable(self):
        # httpx.ConnectError is a subclass of httpx.NetworkError
        exc = httpx.ConnectError("connection refused")
        assert should_retry(None, exc, attempt=0, max_attempts=3) is True

    def test_runtime_error_is_not_retryable(self):
        exc = RuntimeError("something went wrong")
        assert should_retry(None, exc, attempt=0, max_attempts=3) is False

    def test_value_error_is_not_retryable(self):
        exc = ValueError("bad value")
        assert should_retry(None, exc, attempt=0, max_attempts=3) is False

    def test_generic_exception_is_not_retryable(self):
        exc = Exception("generic")
        assert should_retry(None, exc, attempt=0, max_attempts=3) is False

    # -- status-code-based decisions ------------------------------------------

    def test_status_429_is_retryable(self):
        assert should_retry(429, None, attempt=0, max_attempts=3) is True

    def test_status_500_is_retryable(self):
        assert should_retry(500, None, attempt=0, max_attempts=3) is True

    def test_status_502_is_retryable(self):
        assert should_retry(502, None, attempt=0, max_attempts=3) is True

    def test_status_503_is_retryable(self):
        assert should_retry(503, None, attempt=0, max_attempts=3) is True

    def test_status_504_is_retryable(self):
        assert should_retry(504, None, attempt=0, max_attempts=3) is True

    def test_status_400_is_not_retryable(self):
        assert should_retry(400, None, attempt=0, max_attempts=3) is False

    def test_status_200_is_not_retryable(self):
        assert should_retry(200, None, attempt=0, max_attempts=3) is False

    def test_status_404_is_not_retryable(self):
        assert should_retry(404, None, attempt=0, max_attempts=3) is False

    def test_status_401_is_not_retryable(self):
        assert should_retry(401, None, attempt=0, max_attempts=3) is False

    # -- no status, no exception ----------------------------------------------

    def test_no_status_no_exception_returns_false(self):
        assert should_retry(None, None, attempt=0, max_attempts=3) is False

    # -- exception takes priority over status code ----------------------------

    def test_exception_checked_before_status_code(self):
        # Both provided: exception path should be taken
        exc = RuntimeError("oops")
        # RuntimeError is not retryable, so even if status 500 would be,
        # we expect False because exception is evaluated first.
        assert should_retry(500, exc, attempt=0, max_attempts=3) is False

    def test_retryable_exception_with_status_code_returns_true(self):
        exc = httpx.TimeoutException("timeout")
        # httpx.TimeoutException has no required args; pass request=None
        assert should_retry(200, exc, attempt=0, max_attempts=3) is True

    # -- boundary: max_attempts=1 means no retries ever ----------------------

    def test_max_attempts_one_never_retries(self):
        assert should_retry(500, None, attempt=0, max_attempts=1) is False

    def test_max_attempts_two_first_attempt_may_retry(self):
        assert should_retry(500, None, attempt=0, max_attempts=2) is True

    def test_max_attempts_two_second_attempt_no_retry(self):
        assert should_retry(500, None, attempt=1, max_attempts=2) is False


# ---------------------------------------------------------------------------
# compute_backoff
# ---------------------------------------------------------------------------


class TestComputeBackoff:
    """Tests for compute_backoff()."""

    # -- retry_after path -----------------------------------------------------

    def test_retry_after_no_jitter_returns_exact_value(self):
        result = compute_backoff(attempt=0, retry_after=5.0, jitter=False)
        assert result == pytest.approx(5.0)

    def test_retry_after_overrides_exponential_backoff(self):
        # Even at attempt=10, retry_after wins.
        result = compute_backoff(attempt=10, base=1.0, maximum=60.0, retry_after=7.5, jitter=False)
        assert result == pytest.approx(7.5)

    def test_retry_after_with_jitter_scaled_between_half_and_full(self):
        retry_after = 8.0
        results = [compute_backoff(attempt=0, retry_after=retry_after, jitter=True) for _ in range(50)]
        for r in results:
            assert retry_after * 0.5 <= r <= retry_after * 1.0 + 1e-9

    def test_retry_after_zero_no_jitter(self):
        result = compute_backoff(attempt=0, retry_after=0.0, jitter=False)
        assert result == pytest.approx(0.0)

    # -- exponential backoff path ---------------------------------------------

    def test_attempt_0_base_1_no_jitter(self):
        # base * 2^0 = 1.0
        result = compute_backoff(attempt=0, base=1.0, maximum=60.0, jitter=False)
        assert result == pytest.approx(1.0)

    def test_attempt_1_base_1_no_jitter(self):
        # base * 2^1 = 2.0
        result = compute_backoff(attempt=1, base=1.0, maximum=60.0, jitter=False)
        assert result == pytest.approx(2.0)

    def test_attempt_2_base_1_no_jitter(self):
        # base * 2^2 = 4.0
        result = compute_backoff(attempt=2, base=1.0, maximum=60.0, jitter=False)
        assert result == pytest.approx(4.0)

    def test_attempt_3_base_2_no_jitter(self):
        # 2.0 * 2^3 = 16.0
        result = compute_backoff(attempt=3, base=2.0, maximum=60.0, jitter=False)
        assert result == pytest.approx(16.0)

    def test_capped_at_maximum(self):
        # base * 2^10 = 1024 >> maximum=10
        result = compute_backoff(attempt=10, base=1.0, maximum=10.0, jitter=False)
        assert result == pytest.approx(10.0)

    def test_capped_at_default_maximum_60(self):
        # base * 2^10 = 1024 >> 60
        result = compute_backoff(attempt=10, base=1.0, maximum=60.0, jitter=False)
        assert result == pytest.approx(60.0)

    def test_exactly_at_maximum_not_exceeded(self):
        # base=4, attempt=1 → 4*2=8; maximum=8 → should be 8
        result = compute_backoff(attempt=1, base=4.0, maximum=8.0, jitter=False)
        assert result == pytest.approx(8.0)

    def test_below_maximum_not_capped(self):
        # base=1, attempt=2 → 4.0; maximum=60 → not capped
        result = compute_backoff(attempt=2, base=1.0, maximum=60.0, jitter=False)
        assert result == pytest.approx(4.0)

    # -- jitter ---------------------------------------------------------------

    def test_jitter_true_result_in_range(self):
        # base * 2^1 = 2.0; jitter scales to [1.0, 2.0]
        delay = 2.0
        results = [compute_backoff(attempt=1, base=1.0, maximum=60.0, jitter=True) for _ in range(100)]
        for r in results:
            assert delay * 0.5 <= r <= delay * 1.0 + 1e-9

    def test_jitter_false_deterministic(self):
        r1 = compute_backoff(attempt=2, base=1.0, maximum=60.0, jitter=False)
        r2 = compute_backoff(attempt=2, base=1.0, maximum=60.0, jitter=False)
        assert r1 == r2 == pytest.approx(4.0)

    def test_jitter_randomises_output(self):
        # With jitter=True, not all results should be identical (probabilistic).
        results = {compute_backoff(attempt=2, base=1.0, maximum=60.0, jitter=True) for _ in range(20)}
        # With 20 samples it's astronomically unlikely all are identical.
        assert len(results) > 1

    def test_jitter_uses_random_from_module(self):
        """Verify jitter path is exercised by mocking random.random."""
        with patch("notionify.notion_api.retries.random.random", return_value=1.0):
            # 0.5 + 1.0*0.5 = 1.0 multiplier → delay stays at 4.0
            result = compute_backoff(attempt=2, base=1.0, maximum=60.0, jitter=True)
        assert result == pytest.approx(4.0)

    def test_jitter_low_random_value(self):
        with patch("notionify.notion_api.retries.random.random", return_value=0.0):
            # 0.5 + 0.0*0.5 = 0.5 multiplier
            result = compute_backoff(attempt=2, base=1.0, maximum=60.0, jitter=True)
        assert result == pytest.approx(2.0)

    # -- return type ----------------------------------------------------------

    def test_returns_float(self):
        result = compute_backoff(attempt=0, jitter=False)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucket:
    """Tests for the synchronous TokenBucket."""

    # -- constructor validation -----------------------------------------------

    def test_zero_rate_raises_value_error(self):
        with pytest.raises(ValueError, match="rate_rps"):
            TokenBucket(rate_rps=0)

    def test_negative_rate_raises_value_error(self):
        with pytest.raises(ValueError, match="rate_rps"):
            TokenBucket(rate_rps=-1.0)

    def test_very_small_negative_rate_raises_value_error(self):
        with pytest.raises(ValueError):
            TokenBucket(rate_rps=-0.001)

    def test_zero_burst_raises_value_error(self):
        with pytest.raises(ValueError, match="burst"):
            TokenBucket(rate_rps=5.0, burst=0)

    def test_negative_burst_raises_value_error(self):
        with pytest.raises(ValueError, match="burst"):
            TokenBucket(rate_rps=5.0, burst=-1)

    # -- initial state --------------------------------------------------------

    def test_initial_tokens_equal_burst(self):
        bucket = TokenBucket(rate_rps=10.0, burst=5)
        assert bucket.tokens == pytest.approx(5.0)

    def test_initial_tokens_equal_burst_default(self):
        bucket = TokenBucket(rate_rps=10.0)
        assert bucket.tokens == pytest.approx(10.0)

    def test_rate_stored_correctly(self):
        bucket = TokenBucket(rate_rps=7.5, burst=3)
        assert bucket.rate == pytest.approx(7.5)

    def test_burst_stored_correctly(self):
        bucket = TokenBucket(rate_rps=7.5, burst=3)
        assert bucket.burst == 3

    # -- immediate acquire (tokens available) ---------------------------------

    def test_acquire_returns_zero_when_tokens_available(self):
        bucket = TokenBucket(rate_rps=10.0, burst=10)
        result = bucket.acquire(tokens=1)
        assert result == pytest.approx(0.0)

    def test_acquire_deducts_tokens(self):
        bucket = TokenBucket(rate_rps=10.0, burst=10)
        bucket.acquire(tokens=3)
        # tokens = 10 - 3 = 7; refill is negligible over microseconds
        assert bucket.tokens <= 7.1

    def test_acquire_full_burst_returns_zero(self):
        bucket = TokenBucket(rate_rps=10.0, burst=5)
        result = bucket.acquire(tokens=5)
        assert result == pytest.approx(0.0)

    # -- deficit acquire (must wait) ------------------------------------------

    def test_acquire_waits_when_tokens_insufficient(self):
        bucket = TokenBucket(rate_rps=10.0, burst=5)
        # Drain the bucket completely first
        bucket.acquire(tokens=5)
        # Now tokens ~ 0; requesting 1 more should trigger a sleep
        with patch("notionify.notion_api.rate_limit.time.sleep") as mock_sleep:
            result = bucket.acquire(tokens=1)
        mock_sleep.assert_called_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert sleep_duration > 0.0
        assert result == pytest.approx(sleep_duration, abs=1e-6)

    def test_acquire_sleep_duration_proportional_to_deficit(self):
        # rate=1 rps, burst=1; drain fully, then request 2 → need 2s
        bucket = TokenBucket(rate_rps=1.0, burst=1)
        bucket.acquire(tokens=1)  # drain
        with patch("notionify.notion_api.rate_limit.time.sleep") as mock_sleep:
            result = bucket.acquire(tokens=2)
        sleep_duration = mock_sleep.call_args[0][0]
        # deficit = 2 tokens / 1 rps = 2.0 s (approximately, tiny refill may occur)
        assert sleep_duration == pytest.approx(2.0, abs=0.05)
        assert result == pytest.approx(sleep_duration, abs=1e-6)

    def test_tokens_set_to_zero_after_deficit_acquire(self):
        bucket = TokenBucket(rate_rps=10.0, burst=5)
        bucket.acquire(tokens=5)  # drain
        with patch("notionify.notion_api.rate_limit.time.sleep"):
            bucket.acquire(tokens=3)
        # After deficit acquire, tokens should be reset to 0
        assert bucket.tokens == pytest.approx(0.0, abs=0.1)

    # -- burst ceiling not exceeded on refill ---------------------------------

    def test_refill_does_not_exceed_burst(self):
        bucket = TokenBucket(rate_rps=100.0, burst=5)
        # Manually set last_refill far in the past so a huge refill would occur.
        with bucket._lock:
            bucket.tokens = 0.0
            bucket.last_refill = time.monotonic() - 1000.0  # 1000s ago → 100000 tokens
        # acquire triggers refill; tokens should be capped at burst
        result = bucket.acquire(tokens=1)
        assert result == pytest.approx(0.0)
        assert bucket.tokens <= bucket.burst

    def test_refill_caps_at_burst_ceiling(self):
        bucket = TokenBucket(rate_rps=10.0, burst=3)
        with bucket._lock:
            bucket.tokens = 0.0
            bucket.last_refill = time.monotonic() - 100.0
        bucket.acquire(tokens=1)
        # After acquire(1), tokens should be burst - 1 = 2
        assert bucket.tokens == pytest.approx(2.0, abs=0.1)

    # -- thread safety smoke test ---------------------------------------------

    def test_concurrent_acquires_do_not_raise(self):
        bucket = TokenBucket(rate_rps=1000.0, burst=200)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(10):
                    bucket.acquire(tokens=1)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    # -- return type ----------------------------------------------------------

    def test_acquire_returns_float(self):
        bucket = TokenBucket(rate_rps=10.0, burst=10)
        result = bucket.acquire(tokens=1)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# AsyncTokenBucket
# ---------------------------------------------------------------------------


class TestAsyncTokenBucket:
    """Tests for the async AsyncTokenBucket."""

    # -- constructor validation -----------------------------------------------

    def test_zero_rate_raises_value_error(self):
        with pytest.raises(ValueError, match="rate_rps"):
            AsyncTokenBucket(rate_rps=0)

    def test_negative_rate_raises_value_error(self):
        with pytest.raises(ValueError, match="rate_rps"):
            AsyncTokenBucket(rate_rps=-5.0)

    def test_zero_burst_raises_value_error(self):
        with pytest.raises(ValueError, match="burst"):
            AsyncTokenBucket(rate_rps=5.0, burst=0)

    def test_negative_burst_raises_value_error(self):
        with pytest.raises(ValueError, match="burst"):
            AsyncTokenBucket(rate_rps=5.0, burst=-1)

    # -- initial state --------------------------------------------------------

    def test_initial_tokens_equal_burst(self):
        bucket = AsyncTokenBucket(rate_rps=10.0, burst=7)
        assert bucket.tokens == pytest.approx(7.0)

    def test_initial_tokens_equal_burst_default(self):
        bucket = AsyncTokenBucket(rate_rps=10.0)
        assert bucket.tokens == pytest.approx(10.0)

    # -- immediate acquire ----------------------------------------------------

    async def test_acquire_returns_zero_when_tokens_available(self):
        bucket = AsyncTokenBucket(rate_rps=10.0, burst=10)
        result = await bucket.acquire(tokens=1)
        assert result == pytest.approx(0.0)

    async def test_acquire_full_burst_returns_zero(self):
        bucket = AsyncTokenBucket(rate_rps=10.0, burst=5)
        result = await bucket.acquire(tokens=5)
        assert result == pytest.approx(0.0)

    async def test_acquire_deducts_tokens(self):
        bucket = AsyncTokenBucket(rate_rps=10.0, burst=10)
        await bucket.acquire(tokens=4)
        assert bucket.tokens <= 6.1

    # -- deficit acquire (must await) -----------------------------------------

    async def test_acquire_awaits_when_tokens_insufficient(self):
        bucket = AsyncTokenBucket(rate_rps=10.0, burst=5)
        await bucket.acquire(tokens=5)  # drain

        sleep_durations: list[float] = []

        async def fake_sleep(duration: float) -> None:
            sleep_durations.append(duration)

        with patch("notionify.notion_api.rate_limit.asyncio.sleep", fake_sleep):
            result = await bucket.acquire(tokens=1)

        assert len(sleep_durations) == 1
        assert sleep_durations[0] > 0.0
        assert result == pytest.approx(sleep_durations[0], abs=1e-6)

    async def test_acquire_sleep_duration_proportional_to_deficit(self):
        bucket = AsyncTokenBucket(rate_rps=1.0, burst=1)
        await bucket.acquire(tokens=1)  # drain

        sleep_calls: list[float] = []

        async def fake_sleep(duration: float) -> None:
            sleep_calls.append(duration)

        with patch("notionify.notion_api.rate_limit.asyncio.sleep", fake_sleep):
            result = await bucket.acquire(tokens=2)

        assert len(sleep_calls) == 1
        assert sleep_calls[0] == pytest.approx(2.0, abs=0.05)
        assert result == pytest.approx(sleep_calls[0], abs=1e-6)

    async def test_tokens_set_to_zero_after_deficit_acquire(self):
        bucket = AsyncTokenBucket(rate_rps=10.0, burst=5)
        await bucket.acquire(tokens=5)  # drain

        async def fake_sleep(_: float) -> None:
            pass

        with patch("notionify.notion_api.rate_limit.asyncio.sleep", fake_sleep):
            await bucket.acquire(tokens=3)

        assert bucket.tokens == pytest.approx(0.0, abs=0.1)

    # -- burst ceiling not exceeded on refill ---------------------------------

    async def test_refill_does_not_exceed_burst(self):
        bucket = AsyncTokenBucket(rate_rps=100.0, burst=5)
        # Set tokens to 0 and last_refill far in the past.
        bucket.tokens = 0.0
        bucket.last_refill = time.monotonic() - 1000.0
        result = await bucket.acquire(tokens=1)
        assert result == pytest.approx(0.0)
        assert bucket.tokens <= bucket.burst

    async def test_refill_caps_at_burst_ceiling(self):
        bucket = AsyncTokenBucket(rate_rps=10.0, burst=3)
        bucket.tokens = 0.0
        bucket.last_refill = time.monotonic() - 100.0
        await bucket.acquire(tokens=1)
        assert bucket.tokens == pytest.approx(2.0, abs=0.1)

    # -- return type ----------------------------------------------------------

    async def test_acquire_returns_float(self):
        bucket = AsyncTokenBucket(rate_rps=10.0, burst=10)
        result = await bucket.acquire(tokens=1)
        assert isinstance(result, float)

    # -- concurrent coroutines smoke test -------------------------------------

    async def test_concurrent_acquires_do_not_raise(self):
        bucket = AsyncTokenBucket(rate_rps=10000.0, burst=500)
        results: list[float] = []

        async def worker() -> None:
            for _ in range(5):
                r = await bucket.acquire(tokens=1)
                results.append(r)

        await asyncio.gather(*[worker() for _ in range(10)])
        assert len(results) == 50
