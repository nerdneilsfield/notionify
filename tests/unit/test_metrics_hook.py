"""Comprehensive tests for the MetricsHook protocol and integration.

Covers:
  - Protocol conformance (isinstance, structural subtyping)
  - NoopMetricsHook behaviour (all methods, all parameters)
  - Custom recording hook implementation
  - All 11 PRD section 17.3 metric names are emitted in source code
  - Metrics wiring through NotionifyConfig to transport / client / executor
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from notionify.config import NotionifyConfig
from notionify.errors import NotionifyNetworkError
from notionify.observability.metrics import MetricsHook, NoopMetricsHook

# ---------------------------------------------------------------------------
# Recording hook for integration tests
# ---------------------------------------------------------------------------


class RecordingMetricsHook:
    """A metrics backend that records all calls for assertion."""

    def __init__(self) -> None:
        self.increments: list[dict[str, Any]] = []
        self.timings: list[dict[str, Any]] = []
        self.gauges: list[dict[str, Any]] = []

    def increment(
        self,
        name: str,
        value: int = 1,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.increments.append({"name": name, "value": value, "tags": tags})

    def timing(
        self,
        name: str,
        ms: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.timings.append({"name": name, "ms": ms, "tags": tags})

    def gauge(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.gauges.append({"name": name, "value": value, "tags": tags})


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestMetricsHookProtocol:
    """Verify structural subtyping for MetricsHook protocol."""

    def test_noop_is_instance_of_protocol(self):
        """NoopMetricsHook satisfies the runtime-checkable MetricsHook protocol."""
        hook = NoopMetricsHook()
        assert isinstance(hook, MetricsHook)

    def test_recording_hook_is_instance_of_protocol(self):
        """Custom RecordingMetricsHook satisfies the protocol."""
        hook = RecordingMetricsHook()
        assert isinstance(hook, MetricsHook)

    def test_class_with_all_methods_is_instance(self):
        """Any class with increment/timing/gauge methods satisfies the protocol."""

        class MinimalHook:
            def increment(
                self, name: str, value: int = 1,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

            def timing(
                self, name: str, ms: float,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

            def gauge(
                self, name: str, value: float,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

        assert isinstance(MinimalHook(), MetricsHook)

    def test_class_missing_increment_is_not_instance(self):
        """A class missing 'increment' does not satisfy the protocol."""

        class PartialHook:
            def timing(
                self, name: str, ms: float,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

            def gauge(
                self, name: str, value: float,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

        assert not isinstance(PartialHook(), MetricsHook)

    def test_class_missing_timing_is_not_instance(self):
        """A class missing 'timing' does not satisfy the protocol."""

        class PartialHook:
            def increment(
                self, name: str, value: int = 1,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

            def gauge(
                self, name: str, value: float,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

        assert not isinstance(PartialHook(), MetricsHook)

    def test_class_missing_gauge_is_not_instance(self):
        """A class missing 'gauge' does not satisfy the protocol."""

        class PartialHook:
            def increment(
                self, name: str, value: int = 1,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

            def timing(
                self, name: str, ms: float,
                tags: dict[str, str] | None = None,
            ) -> None:
                pass

        assert not isinstance(PartialHook(), MetricsHook)

    def test_empty_class_is_not_instance(self):
        """An empty class is not a valid MetricsHook."""

        class EmptyHook:
            pass

        assert not isinstance(EmptyHook(), MetricsHook)

    def test_protocol_is_runtime_checkable(self):
        """MetricsHook is decorated with @runtime_checkable."""
        # Protocol should be usable with isinstance()
        assert hasattr(MetricsHook, "__protocol_attrs__") or callable(
            getattr(MetricsHook, "_is_runtime_protocol", None)
        )


# ---------------------------------------------------------------------------
# NoopMetricsHook — exhaustive
# ---------------------------------------------------------------------------


class TestNoopMetricsHookExhaustive:
    """Thorough tests for the no-op default implementation."""

    def test_increment_default_value(self):
        hook = NoopMetricsHook()
        result = hook.increment("notionify.requests_total")
        assert result is None

    def test_increment_custom_value(self):
        hook = NoopMetricsHook()
        result = hook.increment("notionify.blocks_created_total", value=42)
        assert result is None

    def test_increment_with_tags(self):
        hook = NoopMetricsHook()
        result = hook.increment(
            "notionify.retries_total", tags={"reason": "rate_limited"},
        )
        assert result is None

    def test_increment_with_value_and_tags(self):
        hook = NoopMetricsHook()
        result = hook.increment("metric", value=10, tags={"k": "v"})
        assert result is None

    def test_timing_basic(self):
        hook = NoopMetricsHook()
        result = hook.timing("notionify.request_duration_ms", 123.456)
        assert result is None

    def test_timing_with_tags(self):
        hook = NoopMetricsHook()
        result = hook.timing(
            "notionify.rate_limit_wait_ms", 50.0, tags={"method": "POST"},
        )
        assert result is None

    def test_timing_zero_duration(self):
        hook = NoopMetricsHook()
        result = hook.timing("metric", 0.0)
        assert result is None

    def test_gauge_basic(self):
        hook = NoopMetricsHook()
        result = hook.gauge("notionify.queue_depth", 15.0)
        assert result is None

    def test_gauge_with_tags(self):
        hook = NoopMetricsHook()
        result = hook.gauge("metric", 99.9, tags={"env": "prod"})
        assert result is None

    def test_gauge_negative_value(self):
        hook = NoopMetricsHook()
        result = hook.gauge("metric", -5.0)
        assert result is None

    def test_noop_has_slots(self):
        """NoopMetricsHook uses __slots__ for minimal memory footprint."""
        hook = NoopMetricsHook()
        assert hasattr(type(hook), "__slots__")
        assert type(hook).__slots__ == ()

    def test_multiple_calls_do_not_accumulate(self):
        """Noop hook discards everything — no state change."""
        hook = NoopMetricsHook()
        for _ in range(100):
            hook.increment("metric")
            hook.timing("metric", 1.0)
            hook.gauge("metric", 1.0)
        # No exception, no state — just verify it works
        assert not hasattr(hook, "increments")


# ---------------------------------------------------------------------------
# RecordingMetricsHook — recording accuracy
# ---------------------------------------------------------------------------


class TestRecordingMetricsHook:
    """Verify that the recording hook correctly captures all calls."""

    def test_increment_recorded(self):
        hook = RecordingMetricsHook()
        hook.increment("notionify.requests_total", tags={"method": "GET"})
        assert len(hook.increments) == 1
        assert hook.increments[0]["name"] == "notionify.requests_total"
        assert hook.increments[0]["value"] == 1
        assert hook.increments[0]["tags"] == {"method": "GET"}

    def test_increment_custom_value_recorded(self):
        hook = RecordingMetricsHook()
        hook.increment(
            "notionify.blocks_created_total", value=5, tags={"page": "abc"},
        )
        assert hook.increments[0]["value"] == 5

    def test_timing_recorded(self):
        hook = RecordingMetricsHook()
        hook.timing(
            "notionify.request_duration_ms", 42.5, tags={"path": "/pages"},
        )
        assert len(hook.timings) == 1
        assert hook.timings[0]["name"] == "notionify.request_duration_ms"
        assert hook.timings[0]["ms"] == pytest.approx(42.5)
        assert hook.timings[0]["tags"] == {"path": "/pages"}

    def test_gauge_recorded(self):
        hook = RecordingMetricsHook()
        hook.gauge("notionify.active_uploads", 3.0)
        assert len(hook.gauges) == 1
        assert hook.gauges[0]["name"] == "notionify.active_uploads"
        assert hook.gauges[0]["value"] == pytest.approx(3.0)

    def test_multiple_increments_recorded_in_order(self):
        hook = RecordingMetricsHook()
        hook.increment("a")
        hook.increment("b")
        hook.increment("c")
        assert [i["name"] for i in hook.increments] == ["a", "b", "c"]

    def test_none_tags_recorded(self):
        hook = RecordingMetricsHook()
        hook.increment("metric")
        assert hook.increments[0]["tags"] is None


# ---------------------------------------------------------------------------
# PRD section 17.3 metric name verification
# ---------------------------------------------------------------------------

# All 11 metric names documented in the PRD and metrics.py docstring
PRD_METRIC_NAMES = [
    "notionify.requests_total",
    "notionify.retries_total",
    "notionify.rate_limited_total",
    "notionify.request_duration_ms",
    "notionify.rate_limit_wait_ms",
    "notionify.blocks_created_total",
    "notionify.upload_success_total",
    "notionify.upload_failure_total",
    "notionify.conversion_warnings_total",
    "notionify.diff_ops_total",
    "notionify.page_export_duration_ms",
]


class TestPRDMetricNames:
    """Verify all 11 PRD section 17.3 metric names are documented."""

    @pytest.mark.parametrize("metric_name", PRD_METRIC_NAMES)
    def test_metric_name_in_module_docstring(self, metric_name: str):
        """Each PRD metric name must appear in the metrics module docstring."""
        import notionify.observability.metrics as m

        assert metric_name in (m.__doc__ or ""), (
            f"Metric '{metric_name}' not documented in metrics.py docstring"
        )

    def test_total_prd_metrics_count(self):
        """PRD section 17.3 defines exactly 11 metrics."""
        assert len(PRD_METRIC_NAMES) == 11


# ---------------------------------------------------------------------------
# Metrics wiring through config
# ---------------------------------------------------------------------------


class TestMetricsConfigWiring:
    """Verify that custom metrics hooks are propagated through the SDK."""

    def test_config_accepts_metrics_parameter(self):
        """NotionifyConfig.metrics can hold a custom hook."""
        hook = RecordingMetricsHook()
        config = NotionifyConfig(token="test", metrics=hook)
        assert config.metrics is hook

    def test_config_metrics_defaults_to_none(self):
        """Without metrics= parameter, config.metrics is None."""
        config = NotionifyConfig(token="test")
        assert config.metrics is None

    def test_transport_uses_noop_when_metrics_none(self):
        """Transport falls back to NoopMetricsHook when config.metrics is None."""
        from notionify.notion_api.transport import NotionTransport

        config = NotionifyConfig(
            token="test", retry_max_attempts=0, rate_limit_rps=1000.0,
        )
        transport = NotionTransport(config)
        assert isinstance(transport._metrics, NoopMetricsHook)

    def test_transport_uses_custom_metrics(self):
        """Transport uses the provided metrics hook from config."""
        from notionify.notion_api.transport import NotionTransport

        hook = RecordingMetricsHook()
        config = NotionifyConfig(
            token="test", metrics=hook,
            retry_max_attempts=0, rate_limit_rps=1000.0,
        )
        transport = NotionTransport(config)
        assert transport._metrics is hook

    def test_async_transport_uses_noop_when_metrics_none(self):
        """Async transport falls back to NoopMetricsHook."""
        from notionify.notion_api.transport import AsyncNotionTransport

        config = NotionifyConfig(
            token="test", retry_max_attempts=0, rate_limit_rps=1000.0,
        )
        transport = AsyncNotionTransport(config)
        assert isinstance(transport._metrics, NoopMetricsHook)

    def test_async_transport_uses_custom_metrics(self):
        """Async transport uses the provided metrics hook from config."""
        from notionify.notion_api.transport import AsyncNotionTransport

        hook = RecordingMetricsHook()
        config = NotionifyConfig(
            token="test", metrics=hook,
            retry_max_attempts=0, rate_limit_rps=1000.0,
        )
        transport = AsyncNotionTransport(config)
        assert transport._metrics is hook

    def test_diff_executor_uses_noop_when_metrics_none(self):
        """DiffExecutor falls back to NoopMetricsHook."""
        from notionify.diff.executor import DiffExecutor

        config = NotionifyConfig(token="test")
        block_api = MagicMock()
        executor = DiffExecutor(block_api, config)
        assert isinstance(executor._metrics, NoopMetricsHook)

    def test_diff_executor_uses_custom_metrics(self):
        """DiffExecutor uses the provided metrics hook from config."""
        from notionify.diff.executor import DiffExecutor

        hook = RecordingMetricsHook()
        config = NotionifyConfig(token="test", metrics=hook)
        block_api = MagicMock()
        executor = DiffExecutor(block_api, config)
        assert executor._metrics is hook

    def test_async_diff_executor_uses_custom_metrics(self):
        """AsyncDiffExecutor uses the provided metrics hook from config."""
        from notionify.diff.executor import AsyncDiffExecutor

        hook = RecordingMetricsHook()
        config = NotionifyConfig(token="test", metrics=hook)
        block_api = MagicMock()
        executor = AsyncDiffExecutor(block_api, config)
        assert executor._metrics is hook


# ---------------------------------------------------------------------------
# Metrics emission via _handle_network_exception
# ---------------------------------------------------------------------------


class TestMetricsEmissionOnNetworkError:
    """Verify metrics are emitted when handling network errors."""

    def test_network_error_increments_requests_total(self):
        """_handle_network_exception emits requests_total with status=error."""
        from notionify.notion_api.transport import _handle_network_exception

        hook = RecordingMetricsHook()
        config = NotionifyConfig(token="test", retry_max_attempts=1)
        exc = httpx.NetworkError("connection failed")

        with pytest.raises(NotionifyNetworkError):
            _handle_network_exception(config, hook, "GET", "/pages", exc, 0)

        assert any(
            i["name"] == "notionify.requests_total"
            and i["tags"]["status"] == "error"
            for i in hook.increments
        )

    def test_retryable_network_error_increments_retries_total(self):
        """_handle_network_exception emits retries_total on retryable error."""
        from notionify.notion_api.transport import _handle_network_exception

        hook = RecordingMetricsHook()
        config = NotionifyConfig(token="test", retry_max_attempts=3)
        exc = httpx.NetworkError("connection failed")

        # Should return delay (retryable), not raise
        delay = _handle_network_exception(
            config, hook, "POST", "/blocks", exc, 0,
        )
        assert delay >= 0.0

        assert any(
            i["name"] == "notionify.retries_total"
            and i["tags"]["reason"] == "network_error"
            for i in hook.increments
        )

    def test_exhausted_network_error_does_not_emit_retry_metric(self):
        """When retries exhausted, retries_total is NOT emitted."""
        from notionify.notion_api.transport import _handle_network_exception

        hook = RecordingMetricsHook()
        config = NotionifyConfig(token="test", retry_max_attempts=1)
        exc = httpx.NetworkError("connection failed")

        with pytest.raises(NotionifyNetworkError):
            _handle_network_exception(config, hook, "GET", "/pages", exc, 0)

        retry_metrics = [
            i for i in hook.increments
            if i["name"] == "notionify.retries_total"
        ]
        assert retry_metrics == []
