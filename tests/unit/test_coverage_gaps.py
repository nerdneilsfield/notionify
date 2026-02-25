"""Targeted tests to cover previously missed lines.

Each test class documents exactly which source file and line(s) it covers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from notionify.async_client import AsyncNotionifyClient
from notionify.client import NotionifyClient
from notionify.config import NotionifyConfig
from notionify.diff.executor import AsyncDiffExecutor, DiffExecutor
from notionify.diff.planner import DiffPlanner
from notionify.errors import (
    NotionifyImageNotFoundError,
    NotionifyImageParseError,
    NotionifyRetryExhaustedError,
)
from notionify.models import (
    ConversionWarning,
    DiffOp,
    DiffOpType,
    ImageSourceType,
    InsertResult,
    PendingImage,
)
from notionify.notion_api.transport import AsyncNotionTransport, NotionTransport
from notionify.observability.metrics import NoopMetricsHook

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    import json as _json
    content = _json.dumps(body).encode() if body is not None else b""
    resp = httpx.Response(status_code, content=content, headers=headers or {})
    resp.request = httpx.Request("GET", "https://api.notion.com/v1/test")
    return resp


def _make_transport_config(**overrides) -> NotionifyConfig:
    defaults = dict(
        token="test-token",
        retry_max_attempts=2,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
        retry_jitter=False,
        rate_limit_rps=10_000.0,
    )
    defaults.update(overrides)
    return NotionifyConfig(**defaults)


class _SyncBucket:
    def __init__(self, wait: float = 0.0):
        self._wait = wait

    def acquire(self, tokens: int = 1) -> float:
        return self._wait


class _AsyncBucket:
    def __init__(self, wait: float = 0.0):
        self._wait = wait

    async def acquire(self, tokens: int = 1) -> float:
        return self._wait


def _para(text: str, block_id: str | None = None) -> dict:
    block = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
            "color": "default",
        },
    }
    if block_id:
        block["id"] = block_id
    return block


# ===========================================================================
# observability/metrics.py  -- line 136: NoopMetricsHook.gauge
# ===========================================================================

class TestNoopMetricsHookGauge:
    """Covers NoopMetricsHook.gauge (line 136 in metrics.py)."""

    def test_gauge_returns_none(self):
        hook = NoopMetricsHook()
        result = hook.gauge("notionify.some_gauge", 42.0)
        assert result is None

    def test_gauge_with_tags_returns_none(self):
        hook = NoopMetricsHook()
        result = hook.gauge("notionify.blocks", 10.0, tags={"op": "test"})
        assert result is None

    def test_gauge_without_tags_returns_none(self):
        hook = NoopMetricsHook()
        result = hook.gauge("notionify.something", 0.0)
        assert result is None


# ===========================================================================
# converter/ast_normalizer.py -- lines 182-183: html_inline token
# ===========================================================================

class TestAstNormalizerHtmlInline:
    """Covers the html_inline branch (lines 182-183 in ast_normalizer.py)."""

    def test_html_inline_in_paragraph(self):
        from notionify.converter.md_to_notion import MarkdownToNotionConverter

        config = NotionifyConfig(token="test-token")
        converter = MarkdownToNotionConverter(config)
        # Inline HTML inside a paragraph triggers the html_inline token type.
        result = converter.convert("Hello <em>world</em> text")
        # The conversion should produce at least one block without crashing.
        assert len(result.blocks) >= 1

    def test_html_inline_normalizer_direct(self):
        """Directly test the _normalize_inline method with an html_inline token."""
        from notionify.converter.ast_normalizer import ASTNormalizer

        normalizer = ASTNormalizer()
        # _normalize_inline is called via _normalize_token -> _normalize_block -> children
        # The html_inline token (inline_html maps to html_inline) is an inline type.
        # We call _normalize_inline directly with the canonical type "html_inline".
        token = {"type": "inline_html", "raw": "<b>bold</b>"}
        result = normalizer._normalize_inline(token, "html_inline")
        assert result["type"] == "html_inline"
        assert result["raw"] == "<b>bold</b>"

    def test_html_inline_via_parse(self):
        """Parse markdown with inline HTML to exercise the html_inline code path."""
        from notionify.converter.ast_normalizer import ASTNormalizer

        normalizer = ASTNormalizer()
        # This markdown has inline HTML which mistune parses as inline_html tokens
        tokens = normalizer.parse("Hello <b>bold</b> world")
        # Should produce at least one paragraph token
        assert len(tokens) >= 1


# ===========================================================================
# diff/planner.py -- lines 269, 273: _block_type_by_id edge cases
# ===========================================================================

class TestPlannerBlockTypeById:
    """Covers DiffPlanner._block_type_by_id (lines 269, 273)."""

    def test_block_id_none_returns_none(self):
        """Line 269: block_id is None -> return None immediately."""
        result = DiffPlanner._block_type_by_id([], None)
        assert result is None

    def test_block_id_not_found_returns_none(self):
        """Line 273: block not found in list -> return None."""
        blocks = [{"id": "blk-1", "type": "paragraph"}]
        result = DiffPlanner._block_type_by_id(blocks, "nonexistent-id")
        assert result is None

    def test_block_id_found_returns_type(self):
        """Happy path: block found -> return type."""
        blocks = [{"id": "blk-1", "type": "heading_1"}]
        result = DiffPlanner._block_type_by_id(blocks, "blk-1")
        assert result == "heading_1"


# ===========================================================================
# diff/executor.py -- line 125: unknown op type in sync DiffExecutor
# ===========================================================================

class TestDiffExecutorUnknownOpType:
    """Covers the else branch (line 125) for unknown op types in DiffExecutor."""

    def test_unknown_op_type_increments_index(self):
        """An op with an unrecognized op_type should be skipped (i += 1)."""
        mock_api = MagicMock()
        mock_api.append_children.return_value = {"results": []}
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)

        # Create an op with a fake/unknown op_type by manipulating the object
        _op = DiffOp(op_type=DiffOpType.KEEP, existing_id="blk-1")
        # We need to monkeypatch the op_type to something not in the executor's branches
        # The executor checks: KEEP, UPDATE, REPLACE, INSERT, DELETE, else.
        # Since DiffOpType is an enum, we'll use patch to make the comparison fail.
        # Easiest: call execute with an op that has an op_type not matching any branch.
        # We can do this by creating an object whose op_type doesn't match any DiffOpType.
        class FakeOp:
            op_type = "UNKNOWN_TYPE_XYZ"
            existing_id = None
            new_block = None

        result = executor.execute("page-1", [FakeOp()])
        # Should complete without error, index moved past the unknown op
        assert result.blocks_kept == 0
        assert result.blocks_inserted == 0
        assert result.blocks_deleted == 0


# ===========================================================================
# diff/executor.py -- line 240: unknown op type in async DiffExecutor
# ===========================================================================

class TestAsyncDiffExecutorUnknownOpType:
    """Covers the else branch (line 240) for unknown op types in AsyncDiffExecutor."""

    @pytest.mark.asyncio
    async def test_unknown_op_type_increments_index(self):
        """An op with an unrecognized op_type is skipped (i += 1)."""
        mock_api = MagicMock()
        mock_api.append_children = AsyncMock(return_value={"results": []})
        config = NotionifyConfig(token="test")
        executor = AsyncDiffExecutor(mock_api, config)

        class FakeOp:
            op_type = "UNKNOWN_TYPE_XYZ"
            existing_id = None
            new_block = None

        result = await executor.execute("page-1", [FakeOp()])
        assert result.blocks_kept == 0
        assert result.blocks_inserted == 0
        assert result.blocks_deleted == 0


# ===========================================================================
# client.py -- line 412: insert_after returns early when markdown is empty
# ===========================================================================

class TestSyncInsertAfterEmptyBlocks:
    """Covers client.py line 412: insert_after returns early with empty blocks."""

    def test_empty_markdown_returns_empty_insert_result(self):
        """When markdown produces no blocks, InsertResult(inserted_block_ids=[]) is returned."""
        client = NotionifyClient(token="test-token")
        # Empty markdown produces no blocks -> early return
        result = client.insert_after(block_id="block-1", markdown_fragment="")
        assert isinstance(result, InsertResult)
        assert result.inserted_block_ids == []
        client.close()

    def test_whitespace_only_markdown_returns_empty(self):
        """Whitespace-only markdown also produces no blocks."""
        client = NotionifyClient(token="test-token")
        result = client.insert_after(block_id="block-1", markdown_fragment="   \n   ")
        assert isinstance(result, InsertResult)
        assert result.inserted_block_ids == []
        client.close()


# ===========================================================================
# async_client.py -- line 586: _process_single_image fallthrough return 0
# async_client.py -- lines 597-600: path traversal in _upload_local_file
# ===========================================================================

class TestAsyncClientImageEdgeCases:
    """Covers async_client.py lines 586 and 597-600."""

    @pytest.mark.asyncio
    async def test_process_single_image_fallthrough_returns_zero(self):
        """Line 586: An image with an unrecognized source_type falls through all branches
        and hits the final 'return 0' at line 586."""
        client = AsyncNotionifyClient(token="test-token")

        class FakePending:
            src = "test.png"
            block_index = 0
            # A fake source type that doesn't match EXTERNAL_URL, UNKNOWN,
            # LOCAL_FILE, or DATA_URI - triggers the fallthrough return 0
            source_type = "FAKE_UNRECOGNIZED_TYPE"

        blocks = [{"type": "image"}]
        warnings: list[ConversionWarning] = []
        result = await client._process_single_image(FakePending(), blocks, warnings)
        assert result == 0
        await client.close()

    @pytest.mark.asyncio
    async def test_process_single_image_unknown_source_type_returns_zero(self):
        """Line 578: An image with UNKNOWN source_type returns 0 at line 578."""
        client = AsyncNotionifyClient(token="test-token")
        pending = PendingImage(src="???", source_type=ImageSourceType.UNKNOWN, block_index=0)
        blocks = [{"type": "image"}]
        warnings: list[ConversionWarning] = []
        result = await client._process_single_image(pending, blocks, warnings)
        assert result == 0
        await client.close()

    @pytest.mark.asyncio
    async def test_path_traversal_raises_error(self, tmp_path):
        """Lines 597-600: Path escaping image_base_dir raises NotionifyImageNotFoundError."""
        client = AsyncNotionifyClient(token="test-token", image_base_dir=str(tmp_path))

        # Use a path that would escape the base directory via ..
        pending = PendingImage(
            src="../../../etc/passwd",
            source_type=ImageSourceType.LOCAL_FILE,
            block_index=0,
        )
        blocks = [{"type": "image"}]
        warnings: list[ConversionWarning] = []

        with pytest.raises(NotionifyImageNotFoundError) as exc_info:
            await client._upload_local_file(pending, blocks, warnings)
        assert "escapes base directory" in exc_info.value.message
        await client.close()


# ===========================================================================
# client.py -- line 580: _process_single_image fallthrough return 0 (sync)
# client.py -- lines 591-594: path traversal in sync _upload_local_file
# ===========================================================================

class TestSyncClientImageEdgeCases:
    """Covers client.py lines 580 and 591-594."""

    def test_process_single_image_fallthrough_returns_zero(self):
        """Line 580: Fallthrough return 0 for unhandled source type."""
        client = NotionifyClient(token="test-token")
        # UNKNOWN source_type returns early at line 572, but we need the
        # final return 0 at line 580. We can hit it via a source type
        # that somehow bypasses LOCAL_FILE and DATA_URI. Since UNKNOWN returns
        # at 572, we can construct a mock PendingImage with a new source type.
        # The fallthrough at line 580 is only reached if source_type is not
        # LOCAL_FILE, DATA_URI, EXTERNAL_URL, or UNKNOWN.
        # We use monkeypatching to simulate this.
        # Patch source_type to something that passes UNKNOWN check but fails LOCAL_FILE and DATA_URI
        class FakePending:
            src = "test.png"
            block_index = 0
            source_type = "FAKE_TYPE_NOT_IN_ENUM"

        blocks: list[dict] = [{"type": "image"}]
        warnings: list[ConversionWarning] = []
        result = client._process_single_image(FakePending(), blocks, warnings)
        assert result == 0
        client.close()

    def test_path_traversal_raises_error(self, tmp_path):
        """Lines 591-594: Path escaping image_base_dir raises NotionifyImageNotFoundError."""
        client = NotionifyClient(token="test-token", image_base_dir=str(tmp_path))

        pending = PendingImage(
            src="../../../etc/passwd",
            source_type=ImageSourceType.LOCAL_FILE,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image"}]
        warnings: list[ConversionWarning] = []

        with pytest.raises(NotionifyImageNotFoundError) as exc_info:
            client._upload_local_file(pending, blocks, warnings)
        assert "escapes base directory" in exc_info.value.message
        client.close()


# ===========================================================================
# client.py -- line 614: validated_data = data when validate_image returns None
# ===========================================================================

class TestSyncUploadLocalFileValidatedDataFallback:
    """Covers client.py line 614: validated_data = data when validate_image returns None."""

    def test_validated_data_fallback_when_none_returned(self, tmp_path):
        """validate_image returning (mime, None) causes validated_data = data (line 614)."""
        img_file = tmp_path / "test.png"
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        img_file.write_bytes(png_bytes)

        client = NotionifyClient(token="test-token")
        client._files.create_upload = MagicMock(return_value={
            "id": "upload-999",
            "upload_url": "https://upload.example.com/999",
        })
        client._files.send_part = MagicMock(return_value=None)

        # Patch validate_image to return (mime_type, None) so line 614 is hit
        with patch("notionify.client.validate_image", return_value=("image/png", None)):
            pending = PendingImage(
                src=str(img_file),
                source_type=ImageSourceType.LOCAL_FILE,
                block_index=0,
            )
            blocks: list[dict] = [{"type": "image", "image": {}}]
            warnings: list[ConversionWarning] = []
            result = client._upload_local_file(pending, blocks, warnings)

        assert result == 1
        client.close()


# ===========================================================================
# client.py -- line 640: return 0 when decoded_data is None in _upload_data_uri
# ===========================================================================

class TestSyncUploadDataUriNoneDecoded:
    """Covers client.py line 640: return 0 when validate_image returns None data."""

    def test_returns_zero_when_decoded_data_is_none(self):
        """Line 640: _upload_data_uri returns 0 if validate_image gives None data."""
        client = NotionifyClient(token="test-token")

        with patch("notionify.client.validate_image", return_value=("image/png", None)):
            pending = PendingImage(
                src="data:image/png;base64,abc",
                source_type=ImageSourceType.DATA_URI,
                block_index=0,
            )
            blocks: list[dict] = [{"type": "image"}]
            warnings: list[ConversionWarning] = []
            result = client._upload_data_uri(pending, blocks, warnings)

        assert result == 0
        client.close()


# ===========================================================================
# client.py -- line 659: upload_multi when data exceeds image_max_size_bytes
# ===========================================================================

class TestSyncDoUploadMultiPart:
    """Covers client.py line 659: upload_multi called when data > image_max_size_bytes."""

    def test_large_data_uses_multipart_upload(self, tmp_path):
        """Line 659: When data size > image_max_size_bytes, upload_multi is used."""
        img_file = tmp_path / "big.png"
        # Write data larger than the small threshold we'll configure
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

        # Set max_size_bytes very small so multipart is triggered
        client = NotionifyClient(token="test-token", image_max_size_bytes=10)
        client._files.create_upload = MagicMock(return_value={
            "id": "upload-multi-1",
            "upload_url": "https://upload.example.com/multi",
        })
        client._files.send_part = MagicMock(return_value=None)

        with patch("notionify.client.upload_multi", return_value="upload-multi-1") as mock_multi, \
             patch("notionify.client.validate_image", return_value=("image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)):
            pending = PendingImage(
                src=str(img_file),
                source_type=ImageSourceType.LOCAL_FILE,
                block_index=0,
            )
            blocks: list[dict] = [{"type": "image", "image": {}}]
            warnings: list[ConversionWarning] = []
            result = client._upload_local_file(pending, blocks, warnings)

        assert result == 1
        mock_multi.assert_called_once()
        client.close()


# ===========================================================================
# client.py -- lines 739, 749: _fetch_blocks_recursive edge cases
# ===========================================================================

class TestFetchBlocksRecursiveEdgeCases:
    """Covers client.py lines 739 and 749 in _fetch_blocks_recursive."""

    def test_block_without_id_is_skipped(self):
        """Line 739: block with has_children=True but no 'id' -> skip get_children."""
        client = NotionifyClient(token="test-token")
        # Block has has_children=True but no 'id' key
        block_without_id = {
            "type": "paragraph",
            "has_children": True,
            "paragraph": {"rich_text": []},
        }
        client._blocks.get_children = MagicMock(return_value=[])

        client._fetch_blocks_recursive([block_without_id], current_depth=0, max_depth=None)
        # get_children should NOT be called since block has no id
        client._blocks.get_children.assert_not_called()
        client.close()

    def test_block_type_not_in_block_uses_children_key(self):
        """Line 749: when block_type is not a key in block, use block['children']."""
        client = NotionifyClient(token="test-token")
        child = {"type": "paragraph", "id": "c1", "has_children": False,
                 "paragraph": {"rich_text": []}}

        # A block where block.get("type") is set but not as a key in the block dict
        block = {
            "id": "b1",
            "has_children": True,
            "type": "some_unknown_type",
            # Note: "some_unknown_type" is not a key in this dict
        }
        client._blocks.get_children = MagicMock(return_value=[child])

        client._fetch_blocks_recursive([block], current_depth=0, max_depth=None)
        # Children should be attached under block["children"] (line 749)
        assert "children" in block
        assert block["children"] == [child]
        client.close()


# ===========================================================================
# image/validate.py -- lines 194-195: URL decode error in data URI parsing
# ===========================================================================

class TestDataUriUrlDecodeError:
    """Covers image/validate.py lines 194-195: exception during URL decoding."""

    def test_url_decode_error_raises_image_parse_error(self):
        """Lines 194-195: When unquote_to_bytes raises, NotionifyImageParseError is raised."""
        from notionify.image.validate import _parse_data_uri

        # A non-base64 data URI that will trigger the URL decode path
        # We patch unquote_to_bytes to raise an exception
        with patch("notionify.image.validate.unquote_to_bytes" if hasattr(
            __import__("notionify.image.validate", fromlist=["unquote_to_bytes"]),
            "unquote_to_bytes",
        ) else "urllib.parse.unquote_to_bytes") as _:
            # Actually, unquote_to_bytes is imported inside the function.
            # We need to patch it where it is used.
            pass

        # Approach: patch urllib.parse.unquote_to_bytes at module level during call
        with patch("urllib.parse.unquote_to_bytes", side_effect=ValueError("decode error")):
            # A data URI without base64 encoding triggers URL decode path
            src = "data:image/svg+xml,%3Csvg%3E"
            with pytest.raises(NotionifyImageParseError) as exc_info:
                _parse_data_uri(src)
        assert "url_decode_error" in exc_info.value.context.get("reason", "")


# ===========================================================================
# notion_api/transport.py -- lines 292-293: response.json() fails, use .text (sync)
# ===========================================================================

class TestSyncTransportDebugDumpNonJsonResponseProper:
    """Covers transport.py lines 292-293 properly."""

    def test_debug_dump_json_failure_falls_back_to_text(self, capsys):
        """Lines 292-293: debug dump uses response.text when response.json() raises."""
        transport = NotionTransport(_make_transport_config(debug_dump_payload=True))
        transport._bucket = _SyncBucket()

        call_count = [0]
        original_json_content = b'{"id": "p1"}'

        resp = httpx.Response(200, content=original_json_content, headers={})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/test")

        # Patch response.json to fail on first call (debug dump) but succeed on second (success path)
        def patched_json():
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("JSON decode error")
            return {"id": "p1"}

        resp.json = patched_json

        with patch.object(transport._client, "request", return_value=resp):
            result = transport.request("GET", "/test")

        # Verify the debug dump was written to stderr despite json() failing
        captured = capsys.readouterr()
        assert captured.err.strip() != ""
        assert result == {"id": "p1"}
        transport.close()


# ===========================================================================
# notion_api/transport.py -- line 365: exhausted with last_exception (sync)
# ===========================================================================

class TestSyncTransportRetryExhaustedWithException:
    """Covers transport.py line 365: RetryExhaustedError raised with last_exception."""

    def test_retry_exhausted_with_network_exception_as_last_state(self):
        """Line 365: all attempts raise network errors and should_retry always returns True
        so the for loop exhausts naturally. last_exception is not None -> line 365 is hit.

        We patch should_retry in the transport module to always return True,
        so both attempts continue (not raise NetworkError). When the for loop
        ends naturally, last_exception is not None and line 365 triggers.
        """
        transport = NotionTransport(_make_transport_config(retry_max_attempts=2))
        transport._bucket = _SyncBucket()

        def always_network_error(*args, **kwargs):
            raise httpx.NetworkError("connection reset by peer")

        # Patch should_retry to always return True so network errors always continue
        # (instead of raising NotionifyNetworkError on the last attempt)
        with patch("notionify.notion_api.transport.should_retry", return_value=True):
            with patch.object(transport._client, "request", side_effect=always_network_error):
                with pytest.raises(NotionifyRetryExhaustedError) as exc_info:
                    transport.request("GET", "/pages")

        assert exc_info.value.context["attempts"] == 2
        # The error message should mention "last error" since last_exception was set
        assert "last error" in exc_info.value.message
        transport.close()


# ===========================================================================
# notion_api/transport.py -- lines 574-575: async debug dump non-JSON (async)
# notion_api/transport.py -- line 646: async retry exhausted with last_exception
# ===========================================================================

class TestAsyncTransportDebugDumpNonJsonResponse:
    """Covers transport.py lines 574-575: async debug dump falls back to response.text."""

    @pytest.mark.asyncio
    async def test_async_debug_dump_json_failure_falls_back_to_text(self, capsys):
        """Lines 574-575: async debug dump uses response.text when response.json() raises."""
        transport = AsyncNotionTransport(_make_transport_config(debug_dump_payload=True))
        transport._bucket = _AsyncBucket()

        call_count = [0]
        resp = httpx.Response(200, content=b'{"id": "p1"}', headers={})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/test")

        def patched_json():
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("JSON decode error")
            return {"id": "p1"}

        resp.json = patched_json

        with patch.object(transport._client, "request", new=AsyncMock(return_value=resp)):
            result = await transport.request("GET", "/test")

        captured = capsys.readouterr()
        assert captured.err.strip() != ""
        assert result == {"id": "p1"}
        await transport.close()


class TestAsyncTransportRetryExhaustedWithException:
    """Covers transport.py line 646: async RetryExhaustedError raised with last_exception."""

    @pytest.mark.asyncio
    async def test_async_retry_exhausted_with_network_exception_as_last_state(self):
        """Line 646: all async attempts raise network errors and should_retry always returns True
        so the for loop exhausts naturally. last_exception is not None -> line 646 is hit.
        """
        transport = AsyncNotionTransport(_make_transport_config(retry_max_attempts=2))
        transport._bucket = _AsyncBucket()

        async def always_network_error(*args, **kwargs):
            raise httpx.NetworkError("connection reset by peer")

        with patch("notionify.notion_api.transport.should_retry", return_value=True):
            with patch.object(transport._client, "request", side_effect=always_network_error):
                with pytest.raises(NotionifyRetryExhaustedError) as exc_info:
                    await transport.request("GET", "/pages")

        assert exc_info.value.context["attempts"] == 2
        assert "last error" in exc_info.value.message
        await transport.close()
