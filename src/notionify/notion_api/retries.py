"""Retry decision logic and exponential backoff computation.

This module provides two pure functions used by the transport layer:

* :func:`should_retry` -- decide whether a failed request is retryable.
* :func:`compute_backoff` -- compute the delay before the next retry attempt.

PRD reference: sections 16.2 and 16.3.
"""

from __future__ import annotations

import random

import httpx

# HTTP status codes that are safe to retry.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Network-level exceptions that warrant a retry.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
)


def should_retry(
    status_code: int | None,
    exception: Exception | None,
    attempt: int,
    max_attempts: int,
) -> bool:
    """Decide whether a request should be retried.

    Parameters
    ----------
    status_code:
        HTTP status code from the response, or ``None`` if the request never
        received a response (e.g. network timeout).
    exception:
        The exception that was raised, or ``None`` if a response was received.
    attempt:
        The current attempt number (0-indexed).
    max_attempts:
        Maximum total attempts allowed (including the initial request).

    Returns
    -------
    bool
        ``True`` if the request should be retried; ``False`` otherwise.
    """
    # Already exhausted all attempts.
    if attempt + 1 >= max_attempts:
        return False

    # A network-level exception occurred before we got a response.
    if exception is not None:
        return isinstance(exception, _RETRYABLE_EXCEPTIONS)

    # We have a status code -- check if it is retryable.
    if status_code is not None:
        return status_code in _RETRYABLE_STATUSES

    # No status code and no exception -- should not happen, but do not retry.
    return False


def compute_backoff(
    attempt: int,
    base: float = 1.0,
    maximum: float = 60.0,
    jitter: bool = True,
    retry_after: float | None = None,
) -> float:
    """Compute the delay before the next retry attempt.

    For ``429`` responses the server-provided ``Retry-After`` value is used
    directly.  For ``5xx`` errors the delay follows exponential backoff
    (``base * 2^attempt``) capped at *maximum*.

    When *jitter* is enabled the computed delay is randomly scaled to between
    50 % and 100 % of its value, reducing thundering-herd effects.

    Parameters
    ----------
    attempt:
        The current attempt number (0-indexed).
    base:
        Base delay in seconds for exponential backoff.
    maximum:
        Maximum delay cap in seconds.
    jitter:
        Whether to apply random jitter.
    retry_after:
        Value of the ``Retry-After`` header (in seconds), if present.

    Returns
    -------
    float
        Delay in seconds before the next retry should be issued.
    """
    if retry_after is not None:
        delay = retry_after
    else:
        delay = min(base * (2 ** attempt), maximum)

    if jitter:
        delay *= 0.5 + random.random() * 0.5

    return delay
