"""Tests for NotionifyClient (sync) and AsyncNotionifyClient (async).

All Notion API calls are mocked so that these tests run entirely offline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notionify.client import NotionifyClient
from notionify.async_client import AsyncNotionifyClient
from notionify.models import (
    AppendResult,
    BlockUpdateResult,
    ConversionResult,
    ConversionWarning,
    ImageSourceType,
    InsertResult,
    PageCreateResult,
    PendingImage,
    UpdateResult,
)
from notionify.errors import NotionifyImageNotFoundError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sync_client() -> NotionifyClient:
    """Create a NotionifyClient with a mocked transport."""
    client = NotionifyClient(token="test-token")
    return client


def _page_create_response(page_id: str = "page-1", url: str = "https://notion.so/page-1") -> dict:
    return {"id": page_id, "url": url}


def _append_response(*block_ids: str) -> dict:
    return {"results": [{"id": bid} for bid in block_ids]}


def _block_dict(block_type: str = "paragraph", block_id: str = "blk-1", text: str = "hello") -> dict:
    return {
        "id": block_id,
        "type": block_type,
        "has_children": False,
        block_type: {
            "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
        },
    }


# ===========================================================================
# Sync client tests
# ===========================================================================


class TestNotionifyClientInit:
    def test_creates_all_components(self):
        client = NotionifyClient(token="test-token")
        assert client._config.token == "test-token"
        assert client._transport is not None
        assert client._pages is not None
        assert client._blocks is not None
        assert client._files is not None
        assert client._converter is not None
        assert client._renderer is not None
        assert client._diff_planner is not None
        assert client._diff_executor is not None
        client.close()

    def test_forwards_kwargs_to_config(self):
        client = NotionifyClient(
            token="test-token",
            base_url="https://custom.api/v1",
            retry_max_attempts=10,
        )
        assert client._config.base_url == "https://custom.api/v1"
        assert client._config.retry_max_attempts == 10
        client.close()

    def test_context_manager(self):
        with NotionifyClient(token="test-token") as client:
            assert client._config.token == "test-token"


class TestCreatePageWithMarkdown:
    def test_basic_page_creation(self):
        client = _make_sync_client()
        client._pages.create = MagicMock(return_value=_page_create_response())
        client._blocks.append_children = MagicMock(return_value=_append_response())

        result = client.create_page_with_markdown(
            parent_id="parent-1",
            title="Test Page",
            markdown="Hello world",
        )

        assert isinstance(result, PageCreateResult)
        assert result.page_id == "page-1"
        assert result.url == "https://notion.so/page-1"
        assert result.blocks_created >= 1
        assert result.images_uploaded == 0

        # Verify page was created with correct parent
        call_kwargs = client._pages.create.call_args
        assert call_kwargs[1]["parent"] == {"page_id": "parent-1"} or \
               call_kwargs.kwargs.get("parent") == {"page_id": "parent-1"} or \
               (call_kwargs[0][0] if call_kwargs[0] else None) == {"page_id": "parent-1"}

        client.close()

    def test_database_parent_type(self):
        client = _make_sync_client()
        client._pages.create = MagicMock(return_value=_page_create_response())

        client.create_page_with_markdown(
            parent_id="db-1",
            title="DB Page",
            markdown="Content",
            parent_type="database",
        )

        call_args = client._pages.create.call_args
        parent = call_args.kwargs.get("parent") or call_args[0][0]
        assert parent == {"database_id": "db-1"}
        client.close()

    def test_title_from_h1(self):
        client = _make_sync_client()
        client._pages.create = MagicMock(return_value=_page_create_response())
        client._blocks.append_children = MagicMock(return_value=_append_response())

        result = client.create_page_with_markdown(
            parent_id="parent-1",
            title="Fallback Title",
            markdown="# My Real Title\n\nSome content.",
            title_from_h1=True,
        )

        call_args = client._pages.create.call_args
        properties = call_args.kwargs.get("properties") or call_args[0][1]
        title_content = properties["title"][0]["text"]["content"]
        assert title_content == "My Real Title"
        client.close()

    def test_large_content_chunked(self):
        """Verify that >100 blocks are chunked into multiple API calls."""
        client = _make_sync_client()

        # Generate markdown that produces >100 blocks
        lines = [f"Paragraph {i}" for i in range(120)]
        markdown = "\n\n".join(lines)

        client._pages.create = MagicMock(return_value=_page_create_response())
        client._blocks.append_children = MagicMock(return_value=_append_response())

        result = client.create_page_with_markdown(
            parent_id="parent-1",
            title="Big Page",
            markdown=markdown,
        )

        assert result.blocks_created == 120
        # First batch goes via create, remaining via append_children
        assert client._blocks.append_children.call_count >= 1
        client.close()

    def test_custom_properties_merged(self):
        client = _make_sync_client()
        client._pages.create = MagicMock(return_value=_page_create_response())

        client.create_page_with_markdown(
            parent_id="p-1",
            title="Title",
            markdown="text",
            properties={"custom_prop": "value"},
        )

        call_args = client._pages.create.call_args
        properties = call_args.kwargs.get("properties") or call_args[0][1]
        assert "title" in properties
        assert properties["custom_prop"] == "value"
        client.close()


class TestAppendMarkdown:
    def test_basic_append(self):
        client = _make_sync_client()
        client._blocks.append_children = MagicMock(return_value=_append_response("b1"))

        result = client.append_markdown(
            target_id="page-1",
            markdown="New paragraph",
        )

        assert isinstance(result, AppendResult)
        assert result.blocks_appended >= 1
        assert result.images_uploaded == 0
        client.close()


class TestOverwritePageContent:
    def test_overwrite_archives_existing(self):
        client = _make_sync_client()
        existing_blocks = [
            _block_dict(block_id="old-1"),
            _block_dict(block_id="old-2"),
        ]
        client._blocks.get_children = MagicMock(return_value=existing_blocks)
        client._blocks.delete = MagicMock(return_value={})
        client._blocks.append_children = MagicMock(return_value=_append_response())

        result = client.overwrite_page_content(
            page_id="page-1",
            markdown="Replacement content",
        )

        assert isinstance(result, UpdateResult)
        assert result.strategy_used == "overwrite"
        assert result.blocks_deleted == 2
        assert result.blocks_inserted >= 1
        assert result.blocks_kept == 0
        assert client._blocks.delete.call_count == 2
        client.close()


class TestUpdatePageFromMarkdown:
    def test_overwrite_strategy_delegates(self):
        client = _make_sync_client()
        client._blocks.get_children = MagicMock(return_value=[])
        client._blocks.delete = MagicMock(return_value={})
        client._blocks.append_children = MagicMock(return_value=_append_response())

        result = client.update_page_from_markdown(
            page_id="page-1",
            markdown="Content",
            strategy="overwrite",
        )

        assert result.strategy_used == "overwrite"
        client.close()

    def test_diff_strategy(self):
        client = _make_sync_client()
        existing = [_block_dict(block_id="b1", text="hello")]
        client._blocks.get_children = MagicMock(return_value=existing)
        client._blocks.delete = MagicMock(return_value={})
        client._blocks.append_children = MagicMock(
            return_value=_append_response("new-1")
        )
        client._blocks.update = MagicMock(return_value={})

        result = client.update_page_from_markdown(
            page_id="page-1",
            markdown="New content entirely different",
            strategy="diff",
        )

        assert isinstance(result, UpdateResult)
        assert result.strategy_used == "diff"
        client.close()


class TestUpdateBlock:
    def test_basic_update(self):
        client = _make_sync_client()
        client._blocks.update = MagicMock(return_value={})

        result = client.update_block(
            block_id="block-1",
            markdown_fragment="Updated text",
        )

        assert isinstance(result, BlockUpdateResult)
        assert result.block_id == "block-1"
        assert client._blocks.update.called
        client.close()

    def test_empty_markdown(self):
        client = _make_sync_client()
        client._blocks.update = MagicMock(return_value={})

        result = client.update_block(block_id="b-1", markdown_fragment="")
        assert result.block_id == "b-1"
        client.close()


class TestDeleteBlock:
    def test_delete_calls_api(self):
        client = _make_sync_client()
        client._blocks.delete = MagicMock(return_value={})

        client.delete_block(block_id="block-1")
        client._blocks.delete.assert_called_once_with("block-1")
        client.close()


class TestInsertAfter:
    def test_basic_insert(self):
        client = _make_sync_client()
        client._blocks.retrieve = MagicMock(return_value={
            "id": "block-1",
            "parent": {"page_id": "page-1"},
        })
        client._blocks.append_children = MagicMock(
            return_value=_append_response("new-1", "new-2")
        )

        result = client.insert_after(
            block_id="block-1",
            markdown_fragment="First\n\nSecond",
        )

        assert isinstance(result, InsertResult)
        assert len(result.inserted_block_ids) >= 1
        client.close()


class TestPageToMarkdown:
    def test_basic_export(self):
        client = _make_sync_client()
        client._blocks.get_children = MagicMock(return_value=[
            _block_dict(block_type="paragraph", block_id="p1", text="Hello world"),
        ])

        md = client.page_to_markdown(page_id="page-1")
        assert "Hello world" in md
        client.close()

    def test_recursive_export(self):
        parent_block = _block_dict(block_type="paragraph", block_id="p1", text="Parent")
        parent_block["has_children"] = True

        child_block = _block_dict(block_type="paragraph", block_id="c1", text="Child")

        client = _make_sync_client()
        call_count = [0]
        def mock_get_children(block_id):
            call_count[0] += 1
            if block_id == "page-1":
                return [parent_block]
            elif block_id == "p1":
                return [child_block]
            return []

        client._blocks.get_children = MagicMock(side_effect=mock_get_children)

        md = client.page_to_markdown(page_id="page-1", recursive=True)
        assert "Parent" in md
        assert "Child" in md
        client.close()

    def test_recursive_max_depth(self):
        """Ensure recursion stops at max_depth."""
        parent = _block_dict(block_type="paragraph", block_id="p1", text="Level 0")
        parent["has_children"] = True

        child = _block_dict(block_type="paragraph", block_id="c1", text="Level 1")
        child["has_children"] = True

        grandchild = _block_dict(block_type="paragraph", block_id="g1", text="Level 2")

        client = _make_sync_client()
        def mock_get_children(block_id):
            if block_id == "page-1":
                return [parent]
            elif block_id == "p1":
                return [child]
            elif block_id == "c1":
                return [grandchild]
            return []

        client._blocks.get_children = MagicMock(side_effect=mock_get_children)

        # max_depth=1 means only fetch children of the top-level blocks
        md = client.page_to_markdown(page_id="page-1", recursive=True, max_depth=1)
        assert "Level 0" in md
        assert "Level 1" in md
        # Level 2 should NOT be fetched because depth=1 stops recursion
        # at depth >= max_depth
        assert "Level 2" not in md
        client.close()


class TestBlockToMarkdown:
    def test_basic_block_export(self):
        client = _make_sync_client()
        client._blocks.get_children = MagicMock(return_value=[
            _block_dict(block_type="heading_1", block_id="h1", text="Title"),
            _block_dict(block_type="paragraph", block_id="p1", text="Body"),
        ])

        md = client.block_to_markdown(block_id="some-block")
        assert "Title" in md
        assert "Body" in md
        client.close()


class TestProcessImages:
    def test_no_images_returns_zero(self):
        client = _make_sync_client()
        conversion = ConversionResult(blocks=[{"type": "paragraph"}], images=[], warnings=[])
        count = client._process_images(conversion)
        assert count == 0
        client.close()

    def test_external_url_skipped(self):
        client = _make_sync_client()
        conversion = ConversionResult(
            blocks=[{"type": "image"}],
            images=[PendingImage(src="https://example.com/img.png", source_type=ImageSourceType.EXTERNAL_URL, block_index=0)],
            warnings=[],
        )
        count = client._process_images(conversion)
        assert count == 0
        client.close()

    def test_unknown_source_skipped(self):
        client = _make_sync_client()
        conversion = ConversionResult(
            blocks=[{"type": "paragraph"}],
            images=[PendingImage(src="???", source_type=ImageSourceType.UNKNOWN, block_index=0)],
            warnings=[],
        )
        count = client._process_images(conversion)
        assert count == 0
        client.close()

    def test_local_file_not_found_skip_policy(self):
        client = NotionifyClient(token="test-token", image_fallback="skip")
        conversion = ConversionResult(
            blocks=[{"type": "image"}],
            images=[PendingImage(
                src="/nonexistent/image.png",
                source_type=ImageSourceType.LOCAL_FILE,
                block_index=0,
            )],
            warnings=[],
        )
        count = client._process_images(conversion)
        assert count == 0
        # Block should have been removed (None cleaned up)
        assert len(conversion.blocks) == 0
        assert any(w.code == "IMAGE_SKIPPED" for w in conversion.warnings)
        client.close()

    def test_local_file_not_found_placeholder_policy(self):
        client = NotionifyClient(token="test-token", image_fallback="placeholder")
        conversion = ConversionResult(
            blocks=[{"type": "image"}],
            images=[PendingImage(
                src="/nonexistent/image.png",
                source_type=ImageSourceType.LOCAL_FILE,
                block_index=0,
            )],
            warnings=[],
        )
        count = client._process_images(conversion)
        assert count == 0
        assert len(conversion.blocks) == 1
        assert "[image:" in conversion.blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]
        client.close()

    def test_local_file_not_found_raise_policy(self):
        client = NotionifyClient(token="test-token", image_fallback="raise")
        conversion = ConversionResult(
            blocks=[{"type": "image"}],
            images=[PendingImage(
                src="/nonexistent/image.png",
                source_type=ImageSourceType.LOCAL_FILE,
                block_index=0,
            )],
            warnings=[],
        )
        with pytest.raises(NotionifyImageNotFoundError):
            client._process_images(conversion)
        client.close()

    def test_upload_disabled(self):
        client = NotionifyClient(token="test-token", image_upload=False)
        conversion = ConversionResult(
            blocks=[{"type": "image"}],
            images=[PendingImage(
                src="/some/image.png",
                source_type=ImageSourceType.LOCAL_FILE,
                block_index=0,
            )],
            warnings=[],
        )
        count = client._process_images(conversion)
        assert count == 0
        client.close()

    def test_local_file_upload_success(self, tmp_path):
        """Test successful upload of a local file."""
        # Create a temporary PNG-like file
        img_file = tmp_path / "test.png"
        # Minimal PNG header
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        img_file.write_bytes(png_header)

        client = _make_sync_client()
        # Mock the file upload
        client._files.create_upload = MagicMock(return_value={
            "id": "upload-123",
            "upload_url": "https://upload.example.com/123",
        })
        client._files.send_part = MagicMock(return_value=None)

        conversion = ConversionResult(
            blocks=[{"type": "image", "image": {}}],
            images=[PendingImage(
                src=str(img_file),
                source_type=ImageSourceType.LOCAL_FILE,
                block_index=0,
            )],
            warnings=[],
        )

        count = client._process_images(conversion)
        assert count == 1
        # The block should have been replaced with an uploaded image block
        assert conversion.blocks[0]["image"]["type"] == "file_upload"
        assert conversion.blocks[0]["image"]["file_upload"]["id"] == "upload-123"
        client.close()

    def test_data_uri_upload_success(self):
        """Test successful upload of a data URI image."""
        import base64
        # Create a small PNG data URI
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        encoded = base64.b64encode(png_bytes).decode()
        data_uri = f"data:image/png;base64,{encoded}"

        client = _make_sync_client()
        client._files.create_upload = MagicMock(return_value={
            "id": "upload-456",
            "upload_url": "https://upload.example.com/456",
        })
        client._files.send_part = MagicMock(return_value=None)

        conversion = ConversionResult(
            blocks=[{"type": "image", "image": {}}],
            images=[PendingImage(
                src=data_uri,
                source_type=ImageSourceType.DATA_URI,
                block_index=0,
            )],
            warnings=[],
        )

        count = client._process_images(conversion)
        assert count == 1
        assert conversion.blocks[0]["image"]["type"] == "file_upload"
        assert conversion.blocks[0]["image"]["file_upload"]["id"] == "upload-456"
        client.close()


class TestFetchBlocksRecursive:
    def test_no_children(self):
        client = _make_sync_client()
        blocks = [_block_dict(block_id="b1")]
        client._blocks.get_children = MagicMock(return_value=[])

        client._fetch_blocks_recursive(blocks, current_depth=0, max_depth=None)
        # get_children should NOT be called since has_children is False
        client._blocks.get_children.assert_not_called()
        client.close()

    def test_with_children(self):
        client = _make_sync_client()
        parent = _block_dict(block_id="p1", text="Parent")
        parent["has_children"] = True

        child = _block_dict(block_id="c1", text="Child")

        client._blocks.get_children = MagicMock(return_value=[child])

        blocks = [parent]
        client._fetch_blocks_recursive(blocks, current_depth=0, max_depth=None)

        assert "children" in blocks[0].get("paragraph", {}) or "children" in blocks[0]
        client.close()

    def test_respects_max_depth(self):
        client = _make_sync_client()
        blocks = [_block_dict(block_id="b1")]
        blocks[0]["has_children"] = True

        client._blocks.get_children = MagicMock(return_value=[])

        client._fetch_blocks_recursive(blocks, current_depth=5, max_depth=5)
        # Should not fetch because current_depth >= max_depth
        client._blocks.get_children.assert_not_called()
        client.close()


# ===========================================================================
# Async client tests
# ===========================================================================


class TestAsyncNotionifyClientInit:
    def test_creates_all_components(self):
        client = AsyncNotionifyClient(token="test-token")
        assert client._config.token == "test-token"
        assert client._transport is not None
        assert client._pages is not None
        assert client._blocks is not None
        assert client._files is not None

    def test_forwards_kwargs_to_config(self):
        client = AsyncNotionifyClient(
            token="test-token",
            base_url="https://custom.api/v1",
        )
        assert client._config.base_url == "https://custom.api/v1"


class TestAsyncCreatePageWithMarkdown:
    @pytest.mark.asyncio
    async def test_basic_page_creation(self):
        client = AsyncNotionifyClient(token="test-token")
        client._pages.create = AsyncMock(return_value=_page_create_response())
        client._blocks.append_children = AsyncMock(return_value=_append_response())

        result = await client.create_page_with_markdown(
            parent_id="parent-1",
            title="Test Page",
            markdown="Hello world",
        )

        assert isinstance(result, PageCreateResult)
        assert result.page_id == "page-1"
        assert result.blocks_created >= 1
        await client.close()

    @pytest.mark.asyncio
    async def test_title_from_h1(self):
        client = AsyncNotionifyClient(token="test-token")
        client._pages.create = AsyncMock(return_value=_page_create_response())
        client._blocks.append_children = AsyncMock(return_value=_append_response())

        await client.create_page_with_markdown(
            parent_id="parent-1",
            title="Fallback",
            markdown="# Extracted Title\n\nContent",
            title_from_h1=True,
        )

        call_args = client._pages.create.call_args
        properties = call_args.kwargs.get("properties") or call_args[0][1]
        assert properties["title"][0]["text"]["content"] == "Extracted Title"
        await client.close()


class TestAsyncAppendMarkdown:
    @pytest.mark.asyncio
    async def test_basic_append(self):
        client = AsyncNotionifyClient(token="test-token")
        client._blocks.append_children = AsyncMock(return_value=_append_response("b1"))

        result = await client.append_markdown(
            target_id="page-1",
            markdown="New content",
        )

        assert isinstance(result, AppendResult)
        assert result.blocks_appended >= 1
        await client.close()


class TestAsyncOverwritePageContent:
    @pytest.mark.asyncio
    async def test_overwrite(self):
        client = AsyncNotionifyClient(token="test-token")
        client._blocks.get_children = AsyncMock(return_value=[
            _block_dict(block_id="old-1"),
        ])
        client._blocks.delete = AsyncMock(return_value={})
        client._blocks.append_children = AsyncMock(return_value=_append_response())

        result = await client.overwrite_page_content(
            page_id="page-1",
            markdown="New content",
        )

        assert result.strategy_used == "overwrite"
        assert result.blocks_deleted == 1
        await client.close()


class TestAsyncUpdatePageFromMarkdown:
    @pytest.mark.asyncio
    async def test_diff_strategy(self):
        client = AsyncNotionifyClient(token="test-token")
        client._blocks.get_children = AsyncMock(return_value=[
            _block_dict(block_id="b1", text="old"),
        ])
        client._blocks.delete = AsyncMock(return_value={})
        client._blocks.append_children = AsyncMock(return_value=_append_response("new-1"))
        client._blocks.update = AsyncMock(return_value={})

        result = await client.update_page_from_markdown(
            page_id="page-1",
            markdown="New text",
            strategy="diff",
        )

        assert result.strategy_used == "diff"
        await client.close()


class TestAsyncUpdateBlock:
    @pytest.mark.asyncio
    async def test_basic_update(self):
        client = AsyncNotionifyClient(token="test-token")
        client._blocks.update = AsyncMock(return_value={})

        result = await client.update_block(
            block_id="block-1",
            markdown_fragment="Updated",
        )

        assert isinstance(result, BlockUpdateResult)
        assert result.block_id == "block-1"
        await client.close()


class TestAsyncDeleteBlock:
    @pytest.mark.asyncio
    async def test_delete(self):
        client = AsyncNotionifyClient(token="test-token")
        client._blocks.delete = AsyncMock(return_value={})

        await client.delete_block(block_id="block-1")
        client._blocks.delete.assert_awaited_once_with("block-1")
        await client.close()


class TestAsyncInsertAfter:
    @pytest.mark.asyncio
    async def test_basic_insert(self):
        client = AsyncNotionifyClient(token="test-token")
        client._blocks.retrieve = AsyncMock(return_value={
            "id": "block-1",
            "parent": {"page_id": "page-1"},
        })
        client._blocks.append_children = AsyncMock(
            return_value=_append_response("new-1")
        )

        result = await client.insert_after(
            block_id="block-1",
            markdown_fragment="Inserted text",
        )

        assert isinstance(result, InsertResult)
        assert len(result.inserted_block_ids) >= 1
        await client.close()


class TestAsyncPageToMarkdown:
    @pytest.mark.asyncio
    async def test_basic_export(self):
        client = AsyncNotionifyClient(token="test-token")
        client._blocks.get_children = AsyncMock(return_value=[
            _block_dict(block_type="paragraph", block_id="p1", text="Exported text"),
        ])

        md = await client.page_to_markdown(page_id="page-1")
        assert "Exported text" in md
        await client.close()

    @pytest.mark.asyncio
    async def test_recursive_export(self):
        parent_block = _block_dict(block_type="paragraph", block_id="p1", text="Parent")
        parent_block["has_children"] = True

        child_block = _block_dict(block_type="paragraph", block_id="c1", text="Child")

        client = AsyncNotionifyClient(token="test-token")

        async def mock_get_children(block_id):
            if block_id == "page-1":
                return [parent_block]
            elif block_id == "p1":
                return [child_block]
            return []

        client._blocks.get_children = AsyncMock(side_effect=mock_get_children)

        md = await client.page_to_markdown(page_id="page-1", recursive=True)
        assert "Parent" in md
        assert "Child" in md
        await client.close()


class TestAsyncBlockToMarkdown:
    @pytest.mark.asyncio
    async def test_block_export(self):
        client = AsyncNotionifyClient(token="test-token")
        client._blocks.get_children = AsyncMock(return_value=[
            _block_dict(block_type="heading_1", block_id="h1", text="Heading"),
        ])

        md = await client.block_to_markdown(block_id="blk-root")
        assert "Heading" in md
        await client.close()


class TestAsyncProcessImages:
    @pytest.mark.asyncio
    async def test_no_images(self):
        client = AsyncNotionifyClient(token="test-token")
        conversion = ConversionResult(blocks=[], images=[], warnings=[])
        count = await client._process_images(conversion)
        assert count == 0
        await client.close()

    @pytest.mark.asyncio
    async def test_concurrent_uploads(self, tmp_path):
        """Test that multiple images are uploaded with concurrency control."""
        # Create temp files
        img_files = []
        for i in range(3):
            img_file = tmp_path / f"test{i}.png"
            img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            img_files.append(img_file)

        client = AsyncNotionifyClient(token="test-token", image_max_concurrent=2)
        client._files.create_upload = AsyncMock(side_effect=[
            {"id": f"upload-{i}", "upload_url": f"https://upload.example.com/{i}"}
            for i in range(3)
        ])
        client._files.send_part = AsyncMock(return_value=None)

        conversion = ConversionResult(
            blocks=[{"type": "image"} for _ in range(3)],
            images=[
                PendingImage(src=str(f), source_type=ImageSourceType.LOCAL_FILE, block_index=i)
                for i, f in enumerate(img_files)
            ],
            warnings=[],
        )

        count = await client._process_images(conversion)
        assert count == 3
        await client.close()


class TestAsyncContextManager:
    @pytest.mark.asyncio
    async def test_aenter_aexit(self):
        async with AsyncNotionifyClient(token="test-token") as client:
            assert client._config.token == "test-token"


class TestAsyncFetchBlocksRecursive:
    @pytest.mark.asyncio
    async def test_respects_max_depth(self):
        client = AsyncNotionifyClient(token="test-token")
        blocks = [_block_dict(block_id="b1")]
        blocks[0]["has_children"] = True

        client._blocks.get_children = AsyncMock(return_value=[])

        await client._fetch_blocks_recursive(blocks, current_depth=3, max_depth=3)
        client._blocks.get_children.assert_not_called()
        await client.close()

    @pytest.mark.asyncio
    async def test_unlimited_depth(self):
        client = AsyncNotionifyClient(token="test-token")

        parent = _block_dict(block_id="p1", text="Parent")
        parent["has_children"] = True

        child = _block_dict(block_id="c1", text="Child")

        async def mock_get_children(block_id):
            if block_id == "p1":
                return [child]
            return []

        client._blocks.get_children = AsyncMock(side_effect=mock_get_children)

        blocks = [parent]
        await client._fetch_blocks_recursive(blocks, current_depth=0, max_depth=None)

        # Children should be attached
        block_type = parent["type"]
        assert "children" in parent.get(block_type, {}) or "children" in parent
        await client.close()
