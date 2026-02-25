"""Edge-case tests for previously uncovered code paths.

Covers: config validation, notion_to_md renderers (table, callout, bookmark,
file, embed, link_preview, media, child_page/child_database), diff executor
operations, image validation (URL-encoded data URIs, unknown source types),
and the unsupported block policy.
"""
import pytest

from notionify.config import NotionifyConfig
from notionify.converter.notion_to_md import NotionToMarkdownRenderer
from notionify.diff.executor import DiffExecutor
from notionify.errors import (
    NotionifyImageParseError,
    NotionifyImageTypeError,
)
from notionify.image.validate import validate_image
from notionify.models import DiffOp, DiffOpType, ImageSourceType
from notionify.notion_api.blocks import extract_block_ids

# ── Config: base_url validation ───────────────────────────────────────


class TestBaseUrlValidation:
    def test_https_url_accepted(self):
        config = NotionifyConfig(token="test", base_url="https://api.notion.com/v1")
        assert config.base_url == "https://api.notion.com/v1"

    def test_http_localhost_accepted(self):
        config = NotionifyConfig(token="test", base_url="http://localhost:8080/v1")
        assert config.base_url == "http://localhost:8080/v1"

    def test_http_127_0_0_1_accepted(self):
        config = NotionifyConfig(token="test", base_url="http://127.0.0.1:3000")
        assert config.base_url == "http://127.0.0.1:3000"

    def test_http_remote_rejected(self):
        with pytest.raises(ValueError, match="insecure HTTP"):
            NotionifyConfig(token="test", base_url="http://evil.example.com/v1")

    def test_http_ipv6_localhost_accepted(self):
        config = NotionifyConfig(token="test", base_url="http://[::1]:8080/v1")
        assert "::1" in config.base_url

    def test_default_url_accepted(self):
        config = NotionifyConfig(token="test")
        assert config.base_url.startswith("https://")


# ── Config: numeric parameter validation ─────────────────────────────


class TestNumericConfigValidation:
    def test_negative_retry_max_attempts_rejected(self):
        with pytest.raises(ValueError, match="retry_max_attempts"):
            NotionifyConfig(token="test", retry_max_attempts=-1)

    def test_zero_retry_max_attempts_accepted(self):
        config = NotionifyConfig(token="test", retry_max_attempts=0)
        assert config.retry_max_attempts == 0

    def test_negative_retry_base_delay_rejected(self):
        with pytest.raises(ValueError, match="retry_base_delay"):
            NotionifyConfig(token="test", retry_base_delay=-0.5)

    def test_negative_retry_max_delay_rejected(self):
        with pytest.raises(ValueError, match="retry_max_delay"):
            NotionifyConfig(token="test", retry_max_delay=-1.0)

    def test_zero_rate_limit_rps_rejected(self):
        with pytest.raises(ValueError, match="rate_limit_rps"):
            NotionifyConfig(token="test", rate_limit_rps=0)

    def test_negative_rate_limit_rps_rejected(self):
        with pytest.raises(ValueError, match="rate_limit_rps"):
            NotionifyConfig(token="test", rate_limit_rps=-1.0)

    def test_zero_timeout_seconds_rejected(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            NotionifyConfig(token="test", timeout_seconds=0)

    def test_zero_image_max_size_bytes_rejected(self):
        with pytest.raises(ValueError, match="image_max_size_bytes"):
            NotionifyConfig(token="test", image_max_size_bytes=0)

    def test_zero_image_max_concurrent_rejected(self):
        with pytest.raises(ValueError, match="image_max_concurrent"):
            NotionifyConfig(token="test", image_max_concurrent=0)

    def test_retry_base_delay_exceeds_max_delay_rejected(self):
        with pytest.raises(ValueError, match="retry_base_delay"):
            NotionifyConfig(
                token="test",
                retry_base_delay=10.0,
                retry_max_delay=5.0,
            )

    def test_retry_delays_equal_accepted(self):
        config = NotionifyConfig(
            token="test",
            retry_base_delay=5.0,
            retry_max_delay=5.0,
        )
        assert config.retry_base_delay == config.retry_max_delay

    def test_valid_numeric_config_accepted(self):
        config = NotionifyConfig(
            token="test",
            retry_max_attempts=3,
            retry_base_delay=0.5,
            retry_max_delay=30.0,
            rate_limit_rps=5.0,
            timeout_seconds=10.0,
            image_max_size_bytes=1024,
            image_max_concurrent=2,
        )
        assert config.retry_max_attempts == 3
        assert config.rate_limit_rps == 5.0

    def test_empty_upload_mimes_rejected(self):
        with pytest.raises(ValueError, match="image_allowed_mimes_upload must not be empty"):
            NotionifyConfig(token="test", image_allowed_mimes_upload=[])

    def test_empty_external_mimes_rejected(self):
        with pytest.raises(ValueError, match="image_allowed_mimes_external must not be empty"):
            NotionifyConfig(token="test", image_allowed_mimes_external=[])

    def test_invalid_mime_format_upload_rejected(self):
        with pytest.raises(ValueError, match=r"Invalid MIME type.*image_allowed_mimes_upload"):
            NotionifyConfig(token="test", image_allowed_mimes_upload=["png"])

    def test_invalid_mime_format_external_rejected(self):
        with pytest.raises(ValueError, match=r"Invalid MIME type.*image_allowed_mimes_external"):
            NotionifyConfig(token="test", image_allowed_mimes_external=["jpeg"])

    def test_custom_valid_mimes_accepted(self):
        config = NotionifyConfig(
            token="test",
            image_allowed_mimes_upload=["image/png"],
            image_allowed_mimes_external=["image/jpeg"],
        )
        assert config.image_allowed_mimes_upload == ["image/png"]
        assert config.image_allowed_mimes_external == ["image/jpeg"]


# ── Notion→MD: table rendering edge cases ─────────────────────────────


def _make_renderer() -> NotionToMarkdownRenderer:
    config = NotionifyConfig(token="test")
    return NotionToMarkdownRenderer(config)


def _table_row(cells: list[str]) -> dict:
    """Build a Notion table_row block with plain text cells."""
    return {
        "type": "table_row",
        "table_row": {
            "cells": [
                [{"type": "text", "text": {"content": c}, "plain_text": c}]
                for c in cells
            ]
        },
    }


class TestTableRendering:
    def test_empty_table_returns_empty(self):
        r = _make_renderer()
        block = {"type": "table", "table": {"table_width": 3, "children": []}}
        assert r._render_table(block, 0) == ""

    def test_no_table_row_children_returns_empty(self):
        r = _make_renderer()
        block = {
            "type": "table",
            "table": {
                "table_width": 2,
                "children": [{"type": "paragraph", "paragraph": {}}],
            },
        }
        assert r._render_table(block, 0) == ""

    def test_header_only_table(self):
        r = _make_renderer()
        block = {
            "type": "table",
            "table": {
                "table_width": 2,
                "has_column_header": True,
                "children": [_table_row(["A", "B"])],
            },
        }
        result = r._render_table(block, 0)
        assert "| A | B |" in result
        assert "|---|---|" in result

    def test_row_padding_to_table_width(self):
        r = _make_renderer()
        block = {
            "type": "table",
            "table": {
                "table_width": 3,
                "children": [
                    _table_row(["A", "B", "C"]),
                    _table_row(["X"]),  # short row
                ],
            },
        }
        result = r._render_table(block, 0)
        lines = result.strip().split("\n")
        # Header row
        assert lines[0].count("|") == 4  # 3 cells + borders
        # Data row should be padded
        assert lines[2].count("|") == 4

    def test_children_from_block_level(self):
        """Table block with children at block level, not inside table data."""
        r = _make_renderer()
        block = {
            "type": "table",
            "table": {"table_width": 2},
            "children": [_table_row(["X", "Y"])],
        }
        result = r._render_table(block, 0)
        assert "| X | Y |" in result


# ── Notion→MD: callout rendering ──────────────────────────────────────


class TestCalloutRendering:
    def test_callout_with_emoji_icon(self):
        r = _make_renderer()
        block = {
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "plain_text": "Important note"}],
                "icon": {"type": "emoji", "emoji": "\U0001f4a1"},
            },
        }
        result = r._render_callout(block, 0)
        assert result.startswith(">")
        assert "\U0001f4a1" in result
        assert "Important note" in result

    def test_callout_with_external_icon(self):
        r = _make_renderer()
        block = {
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "plain_text": "Note"}],
                "icon": {
                    "type": "external",
                    "external": {"url": "https://example.com/icon.png"},
                },
            },
        }
        result = r._render_callout(block, 0)
        assert "https://example.com/icon.png" in result

    def test_callout_without_icon(self):
        r = _make_renderer()
        block = {
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "plain_text": "Plain callout"}],
            },
        }
        result = r._render_callout(block, 0)
        assert "> Plain callout" in result

    def test_callout_with_children(self):
        r = _make_renderer()
        block = {
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "plain_text": "Parent"}],
                "icon": {"type": "emoji", "emoji": "\u2139\ufe0f"},
                "children": [
                    {
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"type": "text", "plain_text": "Child paragraph"}
                            ],
                            "color": "default",
                        },
                    }
                ],
            },
        }
        result = r._render_callout(block, 0)
        assert "Parent" in result
        assert "Child paragraph" in result
        # All lines should be blockquoted
        for line in result.strip().split("\n"):
            assert line.startswith(">")

    def test_callout_empty_text(self):
        r = _make_renderer()
        block = {
            "type": "callout",
            "callout": {"rich_text": [], "icon": {"type": "emoji", "emoji": "\u26a0\ufe0f"}},
        }
        result = r._render_callout(block, 0)
        assert "> " in result


# ── Notion→MD: bookmark, embed, link_preview, file, media ─────────────


class TestBookmarkRendering:
    def test_bookmark_with_caption(self):
        r = _make_renderer()
        block = {
            "type": "bookmark",
            "bookmark": {
                "url": "https://example.com",
                "caption": [{"type": "text", "plain_text": "Example site"}],
            },
        }
        result = r._render_bookmark(block, 0)
        assert "[https://example.com]" in result
        assert "> Example site" in result

    def test_bookmark_without_caption(self):
        r = _make_renderer()
        block = {
            "type": "bookmark",
            "bookmark": {"url": "https://example.com", "caption": []},
        }
        result = r._render_bookmark(block, 0)
        assert "[https://example.com]" in result
        assert "> " not in result


class TestEmbedRendering:
    def test_embed_url(self):
        r = _make_renderer()
        block = {"type": "embed", "embed": {"url": "https://youtube.com/watch?v=abc"}}
        result = r._render_embed(block, 0)
        assert "[Embed]" in result
        assert "youtube.com" in result


class TestLinkPreviewRendering:
    def test_link_preview(self):
        r = _make_renderer()
        block = {
            "type": "link_preview",
            "link_preview": {"url": "https://github.com/org/repo"},
        }
        result = r._render_link_preview(block, 0)
        assert "github.com" in result


class TestFileRendering:
    def test_external_file_with_name(self):
        r = _make_renderer()
        block = {
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": "https://cdn.example.com/report.pdf"},
                "name": "Quarterly Report",
            },
        }
        result = r._render_file(block, 0)
        assert "Quarterly Report" in result
        assert "report.pdf" in result

    def test_file_name_from_url(self):
        r = _make_renderer()
        block = {
            "type": "file",
            "file": {
                "type": "file",
                "file": {"url": "https://s3.amazonaws.com/doc.xlsx?token=abc"},
            },
        }
        result = r._render_file(block, 0)
        assert "doc.xlsx" in result

    def test_file_url_trailing_slash_defaults_to_file(self):
        """URL with trailing slash should default to 'File' label."""
        r = _make_renderer()
        block = {
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": "https://cdn.example.com/"},
            },
        }
        result = r._render_file(block, 0)
        assert "[File]" in result
        assert "cdn.example.com" in result

    def test_file_with_caption(self):
        r = _make_renderer()
        block = {
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": "https://cdn.example.com/file.zip"},
                "caption": [{"type": "text", "plain_text": "Download here"}],
            },
        }
        result = r._render_file(block, 0)
        assert "Download here" in result

    def test_file_no_url(self):
        r = _make_renderer()
        block = {"type": "file", "file": {"type": "external"}}
        result = r._render_file(block, 0)
        assert "File" in result


class TestChildPageAndDatabase:
    def test_child_page(self):
        r = _make_renderer()
        block = {
            "type": "child_page",
            "child_page": {"title": "Subpage Title"},
            "id": "abc123",
        }
        result = r._render_child_page(block, 0)
        assert "Subpage Title" in result
        assert "[Page:" in result

    def test_child_database(self):
        r = _make_renderer()
        block = {
            "type": "child_database",
            "child_database": {"title": "My DB"},
            "id": "def456",
        }
        result = r._render_child_database(block, 0)
        assert "My DB" in result
        assert "[Database:" in result


class TestUnsupportedBlockPolicy:
    def test_unsupported_block_comment(self):
        config = NotionifyConfig(token="test", unsupported_block_policy="comment")
        r = NotionToMarkdownRenderer(config)
        # Use a truly unknown block type (not in _OMITTED_TYPES, _PASSTHROUGH, _MEDIA, or renderers)
        blocks = [{"type": "ai_block", "ai_block": {}, "id": "x"}]
        result = r.render_blocks(blocks)
        assert "<!-- notion:" in result
        assert "ai_block" in result

    def test_unsupported_block_skip(self):
        config = NotionifyConfig(token="test", unsupported_block_policy="skip")
        r = NotionToMarkdownRenderer(config)
        blocks = [{"type": "ai_block", "ai_block": {}, "id": "x"}]
        result = r.render_blocks(blocks)
        assert result.strip() == ""

    def test_omitted_types_always_skipped(self):
        """breadcrumb and table_of_contents are silently omitted regardless of policy."""
        config = NotionifyConfig(token="test", unsupported_block_policy="comment")
        r = NotionToMarkdownRenderer(config)
        blocks = [{"type": "breadcrumb", "breadcrumb": {}, "id": "x"}]
        result = r.render_blocks(blocks)
        assert result.strip() == ""

    def test_unsupported_block_comment_sanitized(self):
        """Block types containing '--' are escaped in HTML comments."""
        config = NotionifyConfig(token="test", unsupported_block_policy="comment")
        r = NotionToMarkdownRenderer(config)
        blocks = [{"type": "evil-->hack", "evil-->hack": {}, "id": "x"}]
        result = r.render_blocks(blocks)
        # The inner content between <!-- and --> must not contain raw "--"
        inner = result.split("<!--")[1].split("-->")[0]
        assert "--" not in inner
        assert "&#45;&#45;" in inner


# ── Diff executor edge cases ──────────────────────────────────────────


class MockBlockAPI:
    """Simple mock for the BlockAPI used by DiffExecutor."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []
        self.deletes: list[str] = []
        self.appends: list[tuple[str, list, str | None]] = []
        self._next_ids = iter(range(1000))

    def update(self, block_id: str, payload: dict) -> dict:
        self.updates.append((block_id, payload))
        return {"id": block_id}

    def delete(self, block_id: str) -> dict:
        self.deletes.append(block_id)
        return {"id": block_id}

    def append_children(
        self, parent_id: str, children: list[dict], after: str | None = None
    ) -> dict:
        self.appends.append((parent_id, children, after))
        results = [{"id": f"new-{next(self._next_ids)}"} for _ in children]
        return {"results": results}


class TestDiffExecutorOperations:
    def _para_block(self, text: str) -> dict:
        return {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text}}],
                "color": "default",
            },
        }

    def test_keep_tracks_last_block_id(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [DiffOp(op_type=DiffOpType.KEEP, existing_id="blk-1")]
        result = executor.execute("page-1", ops)
        assert result.blocks_kept == 1
        assert result.blocks_inserted == 0

    def test_update_calls_api(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [
            DiffOp(
                op_type=DiffOpType.UPDATE,
                existing_id="blk-1",
                new_block=self._para_block("updated"),
            )
        ]
        result = executor.execute("page-1", ops)
        assert len(api.updates) == 1
        assert api.updates[0][0] == "blk-1"
        assert result.blocks_inserted == 1  # UPDATE counts as a write

    def test_update_with_missing_block_skips_api(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [DiffOp(op_type=DiffOpType.UPDATE, existing_id="blk-1", new_block=None)]
        result = executor.execute("page-1", ops)
        assert len(api.updates) == 0
        assert result.blocks_inserted == 1  # still counted

    def test_replace_deletes_and_inserts(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [
            DiffOp(
                op_type=DiffOpType.REPLACE,
                existing_id="blk-old",
                new_block=self._para_block("replacement"),
            )
        ]
        result = executor.execute("page-1", ops)
        assert "blk-old" in api.deletes
        assert len(api.appends) == 1
        assert result.blocks_deleted == 1
        assert result.blocks_replaced == 1

    def test_replace_without_existing_id_no_delete(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [
            DiffOp(
                op_type=DiffOpType.REPLACE,
                existing_id=None,
                new_block=self._para_block("new"),
            )
        ]
        result = executor.execute("page-1", ops)
        assert len(api.deletes) == 0
        assert result.blocks_deleted == 0
        assert result.blocks_replaced == 1

    def test_consecutive_inserts_batched(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [
            DiffOp(op_type=DiffOpType.INSERT, new_block=self._para_block(f"p{i}"))
            for i in range(5)
        ]
        result = executor.execute("page-1", ops)
        # All 5 should be in a single append call (< 100 blocks)
        assert len(api.appends) == 1
        assert len(api.appends[0][1]) == 5
        assert result.blocks_inserted == 5

    def test_insert_with_none_block_skipped(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [
            DiffOp(op_type=DiffOpType.INSERT, new_block=self._para_block("real")),
            DiffOp(op_type=DiffOpType.INSERT, new_block=None),
            DiffOp(op_type=DiffOpType.INSERT, new_block=self._para_block("also real")),
        ]
        result = executor.execute("page-1", ops)
        assert len(api.appends) == 1
        assert len(api.appends[0][1]) == 2  # None skipped
        assert result.blocks_inserted == 2

    def test_delete_calls_api(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [DiffOp(op_type=DiffOpType.DELETE, existing_id="blk-del")]
        result = executor.execute("page-1", ops)
        assert "blk-del" in api.deletes
        assert result.blocks_deleted == 1

    def test_mixed_operations(self):
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [
            DiffOp(op_type=DiffOpType.KEEP, existing_id="blk-1"),
            DiffOp(op_type=DiffOpType.DELETE, existing_id="blk-2"),
            DiffOp(op_type=DiffOpType.INSERT, new_block=self._para_block("new-1")),
            DiffOp(op_type=DiffOpType.INSERT, new_block=self._para_block("new-2")),
            DiffOp(op_type=DiffOpType.KEEP, existing_id="blk-3"),
        ]
        result = executor.execute("page-1", ops)
        assert result.blocks_kept == 2
        assert result.blocks_deleted == 1
        assert result.blocks_inserted == 2

    def test_extract_block_ids_empty(self):
        assert extract_block_ids({}) == []
        assert extract_block_ids({"results": []}) == []

    def test_extract_block_ids_skips_missing(self):
        assert extract_block_ids({"results": [{"type": "block"}, {"id": "a"}]}) == ["a"]


# ── Image validation edge cases ───────────────────────────────────────


class TestImageValidationEdgeCases:
    def test_url_encoded_data_uri(self):
        """Non-base64 data URI using URL encoding (like SVGs)."""
        svg = "<svg><circle r='10'/></svg>"
        data_uri = f"data:image/svg+xml,{svg}"
        config = NotionifyConfig(token="test")
        mime, data = validate_image(data_uri, ImageSourceType.DATA_URI, None, config)
        assert mime == "image/svg+xml"
        assert data is not None
        assert b"<svg>" in data

    def test_external_url_no_extension(self):
        """External URL with no file extension defaults to application/octet-stream."""
        config = NotionifyConfig(token="test")
        with pytest.raises(NotionifyImageTypeError, match="not allowed"):
            validate_image(
                "https://cdn.example.com/img",
                ImageSourceType.EXTERNAL_URL,
                None,
                config,
            )

    def test_unknown_source_type_with_known_extension(self):
        config = NotionifyConfig(token="test")
        mime, data = validate_image(
            "photo.png",
            getattr(ImageSourceType, "UNKNOWN", ImageSourceType.LOCAL_FILE),
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            config,
        )
        assert mime == "image/png"

    def test_data_uri_malformed(self):
        config = NotionifyConfig(token="test")
        with pytest.raises(NotionifyImageParseError, match="Invalid data URI"):
            validate_image(
                "not-a-data-uri",
                ImageSourceType.DATA_URI,
                None,
                config,
            )

    def test_local_file_sniff_overrides_extension(self):
        """MIME sniffing from bytes takes precedence over file extension."""
        config = NotionifyConfig(token="test")
        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        mime, _ = validate_image(
            "image.png",  # extension says PNG
            ImageSourceType.LOCAL_FILE,
            jpeg_bytes,  # bytes say JPEG
            config,
        )
        assert mime == "image/jpeg"

    def test_external_url_with_query_params(self):
        """Extension extraction works through query parameters."""
        config = NotionifyConfig(token="test")
        mime, _ = validate_image(
            "https://cdn.example.com/img.png?token=abc&w=200",
            ImageSourceType.EXTERNAL_URL,
            None,
            config,
        )
        assert mime == "image/png"

    def test_webp_magic_check(self):
        """RIFF header must also have WEBP marker at offset 8."""
        config = NotionifyConfig(token="test")
        # RIFF without WEBP → falls back to extension → audio/x-wav → rejected
        fake_riff = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 100
        with pytest.raises(NotionifyImageTypeError, match="not allowed"):
            validate_image("audio.wav", ImageSourceType.LOCAL_FILE, fake_riff, config)

    def test_webp_magic_with_marker_accepted(self):
        """RIFF with WEBP marker at offset 8 is correctly identified as image/webp."""
        config = NotionifyConfig(token="test")
        webp_data = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100
        mime, _ = validate_image("file.bin", ImageSourceType.LOCAL_FILE, webp_data, config)
        assert mime == "image/webp"


# ── Branch coverage: OR/AND condition gaps ───────────────────────────


class TestDiffExecutorBranchCoverage:
    """Cover conditional branches in diff/executor.py."""

    def _para_block(self, text: str) -> dict:
        return {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text}}],
                "color": "default",
            },
        }

    def test_replace_without_new_block_deletes_only(self):
        """REPLACE with existing_id but no new_block: delete only, no insert."""
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [
            DiffOp(
                op_type=DiffOpType.REPLACE,
                existing_id="blk-old",
                new_block=None,
            )
        ]
        result = executor.execute("page-1", ops)
        assert "blk-old" in api.deletes
        assert len(api.appends) == 0
        assert result.blocks_deleted == 1

    def test_update_without_existing_id_skips_api_call(self):
        """UPDATE with no existing_id: no API update call."""
        api = MockBlockAPI()
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(api, config)
        ops = [
            DiffOp(
                op_type=DiffOpType.UPDATE,
                existing_id=None,
                new_block=self._para_block("new"),
            )
        ]
        result = executor.execute("page-1", ops)
        assert len(api.updates) == 0
        assert result.blocks_inserted == 1  # still counted


class TestInsertAfterBranchCoverage:
    """Cover the block_id parent fallback in client.insert_after."""

    def test_insert_after_with_block_parent(self):
        """When block parent has block_id instead of page_id."""
        from unittest.mock import MagicMock

        from notionify.client import NotionifyClient
        from notionify.models import InsertResult

        client = NotionifyClient(token="test-token")
        client._blocks.retrieve = MagicMock(return_value={
            "id": "child-block",
            "parent": {"block_id": "parent-block"},
        })
        client._blocks.append_children = MagicMock(
            return_value={"results": [{"id": "new-1"}]},
        )
        result = client.insert_after(
            block_id="child-block",
            markdown_fragment="Hello",
        )
        assert isinstance(result, InsertResult)
        # Verify the parent_id used was the block_id, not page_id
        call_args = client._blocks.append_children.call_args
        assert call_args[0][0] == "parent-block"
        client.close()


class TestLocalFileSniffFallsBackToExtension:
    """Cover the branch where MIME sniffing returns None for local files."""

    def test_local_file_no_magic_bytes_uses_extension(self):
        """When file bytes don't match any magic signature, fall back to ext."""
        config = NotionifyConfig(token="test")
        # Random bytes that don't match any magic signature
        random_data = b"\x00\x01\x02\x03" * 25
        mime, _ = validate_image(
            "photo.jpeg",
            ImageSourceType.LOCAL_FILE,
            random_data,
            config,
        )
        assert mime == "image/jpeg"
