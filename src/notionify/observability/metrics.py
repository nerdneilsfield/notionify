"""Metrics hook protocol and no-op default implementation.

notionify emits counters, timings, and gauges at key points (API requests,
retries, block creation, uploads, diff operations, etc.).  By default a
:class:`NoopMetricsHook` is used so there is zero overhead.  Users can supply
their own implementation that satisfies the :class:`MetricsHook` protocol to
route metrics to Datadog, Prometheus, StatsD, or any other backend.

Usage::

    from notionify.observability.metrics import MetricsHook, NoopMetricsHook

    # Verify that a custom class satisfies the protocol at runtime:
    assert isinstance(my_backend, MetricsHook)

Emitted metric names (see PRD section 17.3 for the full table):

* ``notionify.requests_total``           -- counter
* ``notionify.retries_total``            -- counter
* ``notionify.rate_limited_total``       -- counter
* ``notionify.request_duration_ms``      -- timing
* ``notionify.rate_limit_wait_ms``      -- timing
* ``notionify.blocks_created_total``     -- counter
* ``notionify.upload_success_total``     -- counter
* ``notionify.upload_failure_total``     -- counter
* ``notionify.conversion_warnings_total``-- counter
* ``notionify.diff_ops_total``           -- counter
* ``notionify.page_export_duration_ms``  -- timing
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsHook(Protocol):
    """Protocol that any metrics backend must satisfy.

    All methods accept an optional *tags* dict whose keys and values are
    strings.  Implementations are free to translate these tags into whatever
    tagging mechanism their backend supports (e.g. Datadog tags, Prometheus
    labels, StatsD suffixes).
    """

    def increment(
        self,
        name: str,
        value: int = 1,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Increment a counter metric.

        Parameters
        ----------
        name:
            Dot-delimited metric name, e.g. ``"notionify.requests_total"``.
        value:
            Amount to increment by.  Defaults to ``1``.
        tags:
            Optional key-value tags for the data point.
        """
        ...

    def timing(
        self,
        name: str,
        ms: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a timing / duration metric.

        Parameters
        ----------
        name:
            Dot-delimited metric name, e.g.
            ``"notionify.request_duration_ms"``.
        ms:
            Duration in milliseconds.
        tags:
            Optional key-value tags for the data point.
        """
        ...

    def gauge(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Set a gauge metric to an absolute value.

        Parameters
        ----------
        name:
            Dot-delimited metric name.
        value:
            Current gauge value.
        tags:
            Optional key-value tags for the data point.
        """
        ...


class NoopMetricsHook:
    """Default metrics implementation that silently discards all data points.

    This is used when the caller does not supply a custom
    :class:`MetricsHook` backend, ensuring that metrics call-sites in the
    SDK never need ``if self._metrics is not None`` guards.
    """

    __slots__ = ()

    def increment(
        self,
        name: str,
        value: int = 1,
        tags: dict[str, str] | None = None,
    ) -> None:
        pass

    def timing(
        self,
        name: str,
        ms: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        pass

    def gauge(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        pass
