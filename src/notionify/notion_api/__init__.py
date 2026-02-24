"""notionify.notion_api -- Notion API transport and endpoint wrappers.

This sub-package provides:

* :mod:`.rate_limit` -- Token bucket rate limiters (sync and async).
* :mod:`.retries` -- Retry decision logic and exponential backoff.
* :mod:`.transport` -- HTTP transport with auth, retries, and rate limiting.
* :mod:`.pages` -- Page API wrappers.
* :mod:`.blocks` -- Block API wrappers.
* :mod:`.files` -- File upload API wrappers.
"""

from __future__ import annotations

from .blocks import AsyncBlockAPI, BlockAPI
from .files import AsyncFileAPI, FileAPI
from .pages import AsyncPageAPI, PageAPI
from .rate_limit import AsyncTokenBucket, TokenBucket
from .retries import compute_backoff, should_retry
from .transport import AsyncNotionTransport, NotionTransport

__all__ = [
    "AsyncBlockAPI",
    "AsyncFileAPI",
    "AsyncNotionTransport",
    "AsyncPageAPI",
    "AsyncTokenBucket",
    "BlockAPI",
    "FileAPI",
    "NotionTransport",
    "PageAPI",
    "TokenBucket",
    "compute_backoff",
    "should_retry",
]
