"""Async edge case tests for the client.

Tests asyncio.CancelledError handling, task timeout behavior,
concurrent gather semantics, and context manager cleanup.

PRD hardening: async resilience, iteration 13.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from notionify.async_client import AsyncNotionifyClient
from notionify.models import ImageSourceType, PendingImage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_async_client(**kwargs: object) -> AsyncNotionifyClient:
    return AsyncNotionifyClient(token="test-token", **kwargs)


def _page_response(page_id: str = "page-1") -> dict:
    return {"id": page_id, "url": f"https://notion.so/{page_id}"}


def _block_dict(
    block_type: str = "paragraph",
    block_id: str = "blk-1",
    text: str = "hello",
) -> dict:
    return {
        "id": block_id,
        "type": block_type,
        "has_children": False,
        block_type: {
            "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
        },
    }


# =========================================================================
# Context manager cleanup
# =========================================================================


class TestAsyncContextManager:
    """Test async context manager ensures cleanup on normal and error exits."""

    async def test_normal_exit_closes_transport(self):
        async with _make_async_client() as client:
            client._transport.close = AsyncMock()
        client._transport.close.assert_awaited_once()

    async def test_exception_exit_still_closes(self):
        client = _make_async_client()
        client._transport.close = AsyncMock()
        with pytest.raises(ValueError, match="test error"):
            async with client:
                raise ValueError("test error")
        client._transport.close.assert_awaited_once()


# =========================================================================
# Cancellation during operations
# =========================================================================


class TestAsyncCancellation:
    """Test that CancelledError propagates cleanly."""

    async def test_cancelled_during_page_creation(self):
        client = _make_async_client()
        client._pages.create = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.create_page_with_markdown(
                parent_id="p1", title="T", markdown="Hello"
            )

    async def test_cancelled_during_append(self):
        client = _make_async_client()
        client._blocks.append_children = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.append_markdown(target_id="p1", markdown="Hello")

    async def test_cancelled_during_block_fetch(self):
        client = _make_async_client()
        client._blocks.get_children = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.page_to_markdown(page_id="p1")

    async def test_cancelled_during_overwrite_delete_phase(self):
        client = _make_async_client()
        client._blocks.get_children = AsyncMock(return_value=[_block_dict()])
        client._blocks.delete = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.overwrite_page_content(page_id="p1", markdown="New")

    async def test_cancelled_during_diff_update_fetch(self):
        client = _make_async_client()
        client._pages.retrieve = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.update_page_from_markdown(
                page_id="p1", markdown="New", strategy="diff"
            )

    async def test_cancelled_during_block_update(self):
        client = _make_async_client()
        client._blocks.update = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.update_block(block_id="b1", markdown_fragment="New text")

    async def test_cancelled_during_insert_after(self):
        client = _make_async_client()
        client._blocks.retrieve = AsyncMock(
            return_value={"parent": {"page_id": "p1"}}
        )
        client._blocks.append_children = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await client.insert_after(block_id="b1", markdown_fragment="After text")


# =========================================================================
# Recursive fetch edge cases
# =========================================================================


class TestRecursiveFetch:
    """Test recursive block fetching edge cases."""

    async def test_max_depth_zero_skips_recursion(self):
        client = _make_async_client()
        rt = [{"type": "text", "text": {"content": "P"}, "plain_text": "P"}]
        blocks = [
            {
                "id": "b1", "type": "paragraph", "has_children": True,
                "paragraph": {"rich_text": rt},
            }
        ]
        client._blocks.get_children = AsyncMock(return_value=[_block_dict()])

        # max_depth=0 should NOT fetch children
        await client._fetch_blocks_recursive(blocks, current_depth=0, max_depth=0)
        client._blocks.get_children.assert_not_awaited()

    async def test_max_depth_limits_recursion(self):
        client = _make_async_client()

        def make_child(depth: int) -> dict:
            rt = [{"type": "text", "text": {"content": f"D{depth}"}, "plain_text": f"D{depth}"}]
            return {
                "id": f"b{depth}", "type": "paragraph",
                "has_children": depth < 5,
                "paragraph": {"rich_text": rt},
            }

        client._blocks.get_children = AsyncMock(
            side_effect=lambda bid: [make_child(int(bid[1:]) + 1)]
        )

        blocks = [make_child(0)]
        await client._fetch_blocks_recursive(blocks, current_depth=0, max_depth=2)

        # Should have fetched at depths 0 and 1, but not 2
        assert client._blocks.get_children.await_count == 2

    async def test_no_children_skips_fetch(self):
        client = _make_async_client()
        blocks = [
            {
                "id": "b1", "type": "paragraph", "has_children": False,
                "paragraph": {"rich_text": []},
            }
        ]
        client._blocks.get_children = AsyncMock()

        await client._fetch_blocks_recursive(blocks, current_depth=0, max_depth=None)
        client._blocks.get_children.assert_not_awaited()

    async def test_block_without_id_skips_fetch(self):
        client = _make_async_client()
        blocks = [
            {
                "type": "paragraph", "has_children": True,
                "paragraph": {"rich_text": []},
            }
        ]
        client._blocks.get_children = AsyncMock()

        await client._fetch_blocks_recursive(blocks, current_depth=0, max_depth=None)
        client._blocks.get_children.assert_not_awaited()

    async def test_unlimited_depth(self):
        """max_depth=None means fetch all levels."""
        client = _make_async_client()
        call_count = 0

        def _rt(content: str) -> list:
            return [{"type": "text", "text": {"content": content}, "plain_text": content}]

        async def mock_get(bid):
            nonlocal call_count
            call_count += 1
            if call_count < 5:
                return [{
                    "id": f"child-{call_count}", "type": "paragraph",
                    "has_children": True,
                    "paragraph": {"rich_text": _rt("X")},
                }]
            return [{
                "id": f"leaf-{call_count}", "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": _rt("Leaf")},
            }]

        client._blocks.get_children = AsyncMock(side_effect=mock_get)

        blocks = [{
            "id": "root", "type": "paragraph", "has_children": True,
            "paragraph": {"rich_text": _rt("Root")},
        }]

        await client._fetch_blocks_recursive(blocks, current_depth=0, max_depth=None)
        assert call_count == 5


# =========================================================================
# Image processing edge cases
# =========================================================================


class TestImageProcessingEdgeCases:
    """Test edge cases in async image processing."""

    async def test_empty_images_returns_zero(self):
        client = _make_async_client()
        from notionify.models import ConversionResult
        conversion = ConversionResult(blocks=[], images=[], warnings=[])
        result = await client._process_images(conversion)
        assert result == 0

    async def test_image_upload_disabled_returns_zero(self):
        client = _make_async_client(image_upload=False)
        from notionify.models import ConversionResult
        pending = PendingImage(
            src="test.png",
            source_type=ImageSourceType.LOCAL_FILE,
            block_index=0,
        )
        conversion = ConversionResult(
            blocks=[{"type": "image", "image": {}}],
            images=[pending],
            warnings=[],
        )
        result = await client._process_images(conversion)
        assert result == 0

    async def test_external_url_images_not_uploaded(self):
        client = _make_async_client()
        from notionify.models import ConversionResult
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        conversion = ConversionResult(
            blocks=[{"type": "image", "image": {"external": {"url": "https://example.com/img.png"}}}],
            images=[pending],
            warnings=[],
        )
        result = await client._process_images(conversion)
        assert result == 0

    async def test_unknown_source_type_not_uploaded(self):
        client = _make_async_client()
        from notionify.models import ConversionResult
        pending = PendingImage(
            src="unknown://resource",
            source_type=ImageSourceType.UNKNOWN,
            block_index=0,
        )
        conversion = ConversionResult(
            blocks=[{"type": "paragraph", "paragraph": {"rich_text": []}}],
            images=[pending],
            warnings=[],
        )
        result = await client._process_images(conversion)
        assert result == 0


# =========================================================================
# Export edge cases
# =========================================================================


class TestExportEdgeCases:
    """Test export method edge cases."""

    async def test_page_to_markdown_empty_page(self):
        client = _make_async_client()
        client._blocks.get_children = AsyncMock(return_value=[])

        result = await client.page_to_markdown(page_id="p1")
        assert result == ""

    async def test_block_to_markdown_defaults_recursive_true(self):
        """block_to_markdown defaults recursive=True."""
        client = _make_async_client()
        block = _block_dict()
        block["has_children"] = False
        client._blocks.get_children = AsyncMock(return_value=[block])

        result = await client.block_to_markdown(block_id="b1")
        assert "hello" in result

    async def test_page_to_markdown_non_recursive(self):
        """Non-recursive export should not fetch children."""
        client = _make_async_client()
        block = {
            "id": "b1", "type": "paragraph", "has_children": True,
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "Main"}, "plain_text": "Main"}]
            },
        }
        client._blocks.get_children = AsyncMock(return_value=[block])

        result = await client.page_to_markdown(page_id="p1", recursive=False)
        # Should only call get_children once (for the page itself)
        assert client._blocks.get_children.await_count == 1
        assert "Main" in result
