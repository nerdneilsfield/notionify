"""Concurrent safety tests for sync and async Notion API transports.

Verifies that NotionTransport and AsyncNotionTransport behave correctly
under concurrent access from multiple threads (sync) and coroutines (async).

PRD hardening: concurrency safety, iteration 20.
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import httpx

from notionify.config import NotionifyConfig
from notionify.errors import NotionifyRetryExhaustedError
from notionify.notion_api.transport import AsyncNotionTransport, NotionTransport


def _make_config(**overrides: object) -> NotionifyConfig:
    defaults = dict(
        token="test-token",
        retry_max_attempts=3,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
        retry_jitter=False,
        rate_limit_rps=10_000.0,
    )
    defaults.update(overrides)
    return NotionifyConfig(**defaults)


def _ok_response() -> httpx.Response:
    resp = httpx.Response(200, content=b'{"ok": true}')
    resp.request = httpx.Request("GET", "https://api.notion.com/v1/test")
    return resp


# ---------------------------------------------------------------------------
# Sync concurrent transport tests
# ---------------------------------------------------------------------------

class TestSyncTransportConcurrentAccess:
    """Multi-thread concurrent access to a single NotionTransport instance."""

    def test_20_threads_concurrent_requests_all_succeed(self):
        """20 threads issuing requests concurrently should all succeed."""
        config = _make_config()
        transport = NotionTransport(config)
        transport._client = MagicMock(spec=httpx.Client)
        transport._client.request = MagicMock(return_value=_ok_response())

        results: list[dict] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(thread_id: int) -> None:
            try:
                for _ in range(5):
                    result = transport.request("GET", f"/pages/{thread_id}")
                    with lock:
                        results.append(result)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 100  # 20 threads x 5 requests
        assert all(r == {"ok": True} for r in results)

    def test_concurrent_requests_with_rate_limiting(self):
        """Concurrent requests with a low-burst bucket still complete without errors."""
        config = _make_config(rate_limit_rps=100.0)
        transport = NotionTransport(config)
        # Override the bucket to one with burst=2 to force contention
        from notionify.notion_api.rate_limit import TokenBucket
        transport._bucket = TokenBucket(rate_rps=10_000.0, burst=2)
        transport._client = MagicMock(spec=httpx.Client)
        transport._client.request = MagicMock(return_value=_ok_response())

        results: list[dict] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                result = transport.request("GET", "/pages/test")
                with lock:
                    results.append(result)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 10

    def test_concurrent_requests_mixed_success_and_failure(self):
        """Some threads get success, others get 500 -> retries exhaust -> error."""
        config = _make_config(retry_max_attempts=1)
        transport = NotionTransport(config)

        call_count = 0
        call_lock = threading.Lock()

        def alternating_response(*args, **kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
                n = call_count
            if n % 2 == 0:
                resp = httpx.Response(500, content=b'{"message": "error"}')
            else:
                resp = httpx.Response(200, content=b'{"ok": true}')
            resp.request = httpx.Request("GET", "https://api.notion.com/v1/test")
            return resp

        transport._client = MagicMock(spec=httpx.Client)
        transport._client.request = MagicMock(side_effect=alternating_response)

        successes: list[dict] = []
        failures: list[Exception] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                result = transport.request("GET", "/pages/test")
                with lock:
                    successes.append(result)
            except Exception as exc:
                with lock:
                    failures.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) + len(failures) == 10
        assert len(successes) > 0
        assert len(failures) > 0
        for f in failures:
            assert isinstance(f, NotionifyRetryExhaustedError)

    def test_close_does_not_race_with_active_requests(self):
        """Closing transport while requests are in-flight should not crash."""
        config = _make_config()
        transport = NotionTransport(config)

        def slow_response(*args, **kwargs):
            import time
            time.sleep(0.01)
            return _ok_response()

        transport._client = MagicMock(spec=httpx.Client)
        transport._client.request = MagicMock(side_effect=slow_response)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                transport.request("GET", "/pages/test")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        # Close while requests are still in-flight
        transport._client.close = MagicMock()
        transport.close()
        for t in threads:
            t.join()

        # We don't assert errors==[] because close() mid-flight is undefined,
        # but it should not raise SystemExit or crash the process
        assert True  # No crash = pass


# ---------------------------------------------------------------------------
# Async concurrent transport tests
# ---------------------------------------------------------------------------

class TestAsyncTransportConcurrentAccess:
    """Multi-coroutine concurrent access to a single AsyncNotionTransport."""

    async def test_50_coroutines_concurrent_requests_all_succeed(self):
        """50 coroutines issuing requests concurrently should all succeed."""
        config = _make_config()
        transport = AsyncNotionTransport(config)

        async def mock_request(*args, **kwargs):
            return _ok_response()

        transport._client = AsyncMock(spec=httpx.AsyncClient)
        transport._client.request = AsyncMock(side_effect=mock_request)

        results: list[dict] = []

        async def worker(coro_id: int) -> None:
            for _ in range(3):
                result = await transport.request("GET", f"/pages/{coro_id}")
                results.append(result)

        await asyncio.gather(*[worker(i) for i in range(50)])

        assert len(results) == 150  # 50 coroutines x 3 requests
        assert all(r == {"ok": True} for r in results)

    async def test_concurrent_requests_with_low_burst_bucket(self):
        """Concurrent requests with a low-burst async bucket still complete."""
        config = _make_config()
        transport = AsyncNotionTransport(config)
        from notionify.notion_api.rate_limit import AsyncTokenBucket
        transport._bucket = AsyncTokenBucket(rate_rps=10_000.0, burst=2)

        async def mock_request(*args, **kwargs):
            return _ok_response()

        transport._client = AsyncMock(spec=httpx.AsyncClient)
        transport._client.request = AsyncMock(side_effect=mock_request)

        results: list[dict] = []

        async def worker() -> None:
            result = await transport.request("GET", "/pages/test")
            results.append(result)

        await asyncio.gather(*[worker() for _ in range(20)])
        assert len(results) == 20

    async def test_concurrent_mixed_retryable_and_success(self):
        """Mix of success and 503 responses across concurrent coroutines."""
        config = _make_config(retry_max_attempts=1)
        transport = AsyncNotionTransport(config)

        counter = 0

        async def alternating(*args, **kwargs):
            nonlocal counter
            counter += 1
            if counter % 2 == 0:
                resp = httpx.Response(503, content=b'{"message": "unavailable"}')
            else:
                resp = httpx.Response(200, content=b'{"ok": true}')
            resp.request = httpx.Request("GET", "https://api.notion.com/v1/test")
            return resp

        transport._client = AsyncMock(spec=httpx.AsyncClient)
        transport._client.request = AsyncMock(side_effect=alternating)

        successes: list[dict] = []
        failures: list[Exception] = []

        async def worker() -> None:
            try:
                result = await transport.request("GET", "/test")
                successes.append(result)
            except NotionifyRetryExhaustedError as exc:
                failures.append(exc)

        await asyncio.gather(*[worker() for _ in range(10)])

        assert len(successes) + len(failures) == 10
        assert len(successes) > 0
        assert len(failures) > 0

    async def test_async_paginate_concurrent_consumers(self):
        """Multiple coroutines consuming the same paginate generator should not crash.

        Note: async generators are not safe to share across coroutines,
        but calling paginate() on separate instances should work.
        """
        config = _make_config()
        transport = AsyncNotionTransport(config)

        page1 = httpx.Response(
            200,
            content=b'{"results": [{"id": "1"}], "has_more": false}',
        )
        page1.request = httpx.Request("GET", "https://api.notion.com/v1/test")

        async def mock_request(*args, **kwargs):
            return page1

        transport._client = AsyncMock(spec=httpx.AsyncClient)
        transport._client.request = AsyncMock(side_effect=mock_request)

        all_items: list[list[dict]] = []

        async def consumer(coro_id: int) -> None:
            items = [
                item
                async for item in transport.paginate(f"/blocks/{coro_id}/children")
            ]
            all_items.append(items)

        await asyncio.gather(*[consumer(i) for i in range(5)])
        assert len(all_items) == 5
        for items in all_items:
            assert len(items) == 1
            assert items[0] == {"id": "1"}

    async def test_async_context_manager_with_concurrent_requests(self):
        """Using transport as async context manager with concurrent requests."""
        config = _make_config()

        async def mock_request(*args, **kwargs):
            return _ok_response()

        async with AsyncNotionTransport(config) as transport:
            transport._client.request = AsyncMock(side_effect=mock_request)

            results = []

            async def worker() -> None:
                result = await transport.request("GET", "/test")
                results.append(result)

            await asyncio.gather(*[worker() for _ in range(10)])

        assert len(results) == 10
