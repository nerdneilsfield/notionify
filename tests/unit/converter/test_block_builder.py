"""Dedicated unit tests for block_builder.py.

Tests the block builder dispatch table and individual block type handlers
directly, rather than through the full MarkdownToNotionConverter pipeline.
"""

import pytest

from notionify.config import NotionifyConfig
from notionify.converter.block_builder import build_blocks


def _config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


def _ast_heading(level, text):
    return {
        "type": "heading",
        "attrs": {"level": level},
        "children": [{"type": "text", "raw": text}],
    }


def _ast_paragraph(text):
    return {
        "type": "paragraph",
        "children": [{"type": "text", "raw": text}],
    }


# =========================================================================
# Heading blocks
# =========================================================================

class TestBuildHeading:
    """Test heading block building."""

    @pytest.mark.parametrize("level", [1, 2, 3])
    def test_heading_levels_1_to_3(self, level):
        blocks, images, warnings = build_blocks([_ast_heading(level, "Title")], _config())
        assert len(blocks) == 1
        assert blocks[0]["type"] == f"heading_{level}"
        rt = blocks[0][f"heading_{level}"]["rich_text"]
        assert rt[0]["text"]["content"] == "Title"

    def test_heading_4_downgrade_mode(self):
        blocks, _, _ = build_blocks(
            [_ast_heading(4, "H4")], _config(heading_overflow="downgrade"),
        )
        assert blocks[0]["type"] == "heading_3"

    def test_heading_4_paragraph_mode(self):
        blocks, _, _ = build_blocks(
            [_ast_heading(4, "H4")], _config(heading_overflow="paragraph"),
        )
        assert blocks[0]["type"] == "paragraph"
        # Should have bold annotation
        rt = blocks[0]["paragraph"]["rich_text"]
        assert rt[0].get("annotations", {}).get("bold") is True


# =========================================================================
# Paragraph blocks
# =========================================================================

class TestBuildParagraph:
    """Test paragraph block building."""

    def test_simple_paragraph(self):
        blocks, _, _ = build_blocks([_ast_paragraph("Hello")], _config())
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"
        assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "Hello"

    def test_empty_paragraph_skipped(self):
        token = {"type": "paragraph", "children": []}
        blocks, _, _ = build_blocks([token], _config())
        assert len(blocks) == 0

    def test_paragraph_with_single_image_becomes_image_block(self):
        token = {
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "https://example.com/img.png"},
                "children": [{"type": "text", "raw": "alt text"}],
            }],
        }
        blocks, _, _ = build_blocks([token], _config())
        assert len(blocks) == 1
        assert blocks[0]["type"] == "image"


# =========================================================================
# Block quote blocks
# =========================================================================

class TestBuildBlockQuote:
    """Test block quote building."""

    def test_simple_blockquote(self):
        token = {
            "type": "block_quote",
            "children": [_ast_paragraph("Quoted text")],
        }
        blocks, _, _ = build_blocks([token], _config())
        assert len(blocks) == 1
        assert blocks[0]["type"] == "quote"
        rt = blocks[0]["quote"]["rich_text"]
        assert any(s["text"]["content"] == "Quoted text" for s in rt)

    def test_blockquote_with_nested_blocks(self):
        token = {
            "type": "block_quote",
            "children": [
                _ast_paragraph("Quote text"),
                {"type": "block_code", "raw": "code", "attrs": {"info": "python"}},
            ],
        }
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["type"] == "quote"
        assert "children" in blocks[0]["quote"]


# =========================================================================
# List blocks
# =========================================================================

class TestBuildList:
    """Test list block building."""

    def test_unordered_list(self):
        token = {
            "type": "list",
            "attrs": {"ordered": False},
            "children": [
                {
                    "type": "list_item",
                    "children": [
                        {"type": "paragraph", "children": [{"type": "text", "raw": "Item 1"}]},
                    ],
                },
                {
                    "type": "list_item",
                    "children": [
                        {"type": "paragraph", "children": [{"type": "text", "raw": "Item 2"}]},
                    ],
                },
            ],
        }
        blocks, _, _ = build_blocks([token], _config())
        assert len(blocks) == 2
        assert all(b["type"] == "bulleted_list_item" for b in blocks)

    def test_ordered_list(self):
        token = {
            "type": "list",
            "attrs": {"ordered": True},
            "children": [{
                "type": "list_item",
                "children": [{"type": "paragraph", "children": [{"type": "text", "raw": "First"}]}],
            }],
        }
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["type"] == "numbered_list_item"

    def test_task_list(self):
        token = {
            "type": "list",
            "attrs": {"ordered": False},
            "children": [{
                "type": "task_list_item",
                "attrs": {"checked": True},
                "children": [{"type": "paragraph", "children": [{"type": "text", "raw": "Done"}]}],
            }],
        }
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["type"] == "to_do"
        assert blocks[0]["to_do"]["checked"] is True


# =========================================================================
# Code blocks
# =========================================================================

class TestBuildCodeBlock:
    """Test code block building."""

    def test_code_block_with_language(self):
        token = {"type": "block_code", "raw": "print('hi')", "attrs": {"info": "python"}}
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["type"] == "code"
        assert blocks[0]["code"]["language"] == "python"
        assert blocks[0]["code"]["rich_text"][0]["text"]["content"] == "print('hi')"

    def test_code_block_language_alias(self):
        token = {"type": "block_code", "raw": "x", "attrs": {"info": "js"}}
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["code"]["language"] == "javascript"

    def test_code_block_no_language(self):
        token = {"type": "block_code", "raw": "code", "attrs": {}}
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["code"]["language"] == "plain text"

    def test_code_block_language_with_trailing_digits(self):
        """python3 should normalize to python."""
        token = {"type": "block_code", "raw": "x", "attrs": {"info": "python3"}}
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["code"]["language"] == "python"

    def test_code_block_language_case_insensitive(self):
        """Language matching should be case-insensitive."""
        token = {"type": "block_code", "raw": "x", "attrs": {"info": "Python"}}
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["code"]["language"] == "python"

    def test_code_block_language_with_extra_words(self):
        """Extra words after language name should be stripped."""
        token = {"type": "block_code", "raw": "x", "attrs": {"info": "python main.py"}}
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["code"]["language"] == "python"

    def test_code_block_unknown_language(self):
        """Unknown language should fall back to plain text."""
        token = {"type": "block_code", "raw": "x", "attrs": {"info": "brainfuck"}}
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["code"]["language"] == "plain text"

    def test_code_block_none_info(self):
        """info=None should produce plain text language."""
        token = {"type": "block_code", "raw": "x", "attrs": {"info": None}}
        blocks, _, _ = build_blocks([token], _config())
        assert blocks[0]["code"]["language"] == "plain text"


# =========================================================================
# Divider blocks
# =========================================================================

class TestBuildDivider:
    """Test thematic break → divider building."""

    def test_divider(self):
        token = {"type": "thematic_break"}
        blocks, _, _ = build_blocks([token], _config())
        assert len(blocks) == 1
        assert blocks[0]["type"] == "divider"


# =========================================================================
# HTML blocks (skipped with warning)
# =========================================================================

class TestBuildHtmlBlock:
    """Test HTML block handling."""

    def test_html_block_skipped_with_warning(self):
        token = {"type": "html_block", "raw": "<div>hello</div>"}
        blocks, _, warnings = build_blocks([token], _config())
        assert len(blocks) == 0
        assert len(warnings) == 1
        assert warnings[0].code == "HTML_BLOCK_SKIPPED"


# =========================================================================
# Unknown blocks
# =========================================================================

class TestBuildUnknownBlock:
    """Test unknown block type handling."""

    def test_unknown_block_produces_warning(self):
        token = {"type": "custom_widget", "raw": "data"}
        blocks, _, warnings = build_blocks([token], _config())
        assert len(blocks) == 0
        assert len(warnings) == 1
        assert warnings[0].code == "UNKNOWN_TOKEN"

    def test_empty_type_no_warning(self):
        token = {"type": ""}
        blocks, _, warnings = build_blocks([token], _config())
        assert len(blocks) == 0
        assert len(warnings) == 0


# =========================================================================
# Image blocks
# =========================================================================

class TestBuildImageBlock:
    """Test image block building."""

    def test_external_image(self):
        token = {
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "https://example.com/photo.jpg"},
                "children": [{"type": "text", "raw": "A photo"}],
            }],
        }
        blocks, images, _ = build_blocks([token], _config())
        assert blocks[0]["type"] == "image"
        assert blocks[0]["image"]["external"]["url"] == "https://example.com/photo.jpg"

    def test_local_image_with_upload_enabled(self):
        token = {
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "./photo.jpg"},
                "children": [],
            }],
        }
        blocks, images, _ = build_blocks([token], _config(image_upload=True))
        assert len(images) == 1
        assert images[0].src == "./photo.jpg"

    def test_local_image_skip_fallback(self):
        token = {
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "./photo.jpg"},
                "children": [],
            }],
        }
        blocks, _, warnings = build_blocks(
            [token], _config(image_upload=False, image_fallback="skip"),
        )
        assert len(blocks) == 0
        assert any(w.code == "IMAGE_SKIPPED" for w in warnings)

    def test_local_image_placeholder_fallback(self):
        token = {
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "./photo.jpg"},
                "children": [{"type": "text", "raw": "my photo"}],
            }],
        }
        blocks, _, warnings = build_blocks(
            [token], _config(image_upload=False, image_fallback="placeholder"),
        )
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"
        assert any(w.code == "IMAGE_PLACEHOLDER" for w in warnings)

    def test_data_uri_image_with_upload_enabled(self):
        token = {
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "data:image/png;base64,iVBOR..."},
                "children": [{"type": "text", "raw": "data img"}],
            }],
        }
        blocks, images, _ = build_blocks([token], _config(image_upload=True))
        assert len(images) == 1
        assert images[0].src.startswith("data:")

    def test_data_uri_image_without_upload_skip(self):
        token = {
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "data:image/png;base64,iVBOR..."},
                "children": [],
            }],
        }
        blocks, _, warnings = build_blocks(
            [token], _config(image_upload=False, image_fallback="skip"),
        )
        assert len(blocks) == 0
        assert any(w.code == "IMAGE_SKIPPED" for w in warnings)

    def test_image_raise_fallback(self):
        token = {
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "./photo.jpg"},
                "children": [{"type": "text", "raw": "my photo"}],
            }],
        }
        blocks, _, warnings = build_blocks(
            [token], _config(image_upload=False, image_fallback="raise"),
        )
        assert any(w.code == "IMAGE_ERROR" for w in warnings)


# =========================================================================
# Multiple blocks
# =========================================================================

class TestMultipleBlocks:
    """Test building multiple blocks in sequence."""

    def test_heading_then_paragraph(self):
        tokens = [_ast_heading(1, "Title"), _ast_paragraph("Body text")]
        blocks, _, _ = build_blocks(tokens, _config())
        assert len(blocks) == 2
        assert blocks[0]["type"] == "heading_1"
        assert blocks[1]["type"] == "paragraph"

    def test_all_block_types_together(self):
        tokens = [
            _ast_heading(2, "Section"),
            _ast_paragraph("Text"),
            {"type": "block_code", "raw": "code", "attrs": {"info": "python"}},
            {"type": "thematic_break"},
        ]
        blocks, _, _ = build_blocks(tokens, _config())
        assert len(blocks) == 4
        types = [b["type"] for b in blocks]
        assert types == ["heading_2", "paragraph", "code", "divider"]


class TestNestingDepthGuard:
    """Tests for the nesting depth guard (PRD 5.1, ~8 levels)."""

    def _make_nested_list(self, depth: int) -> dict:
        """Build a list AST nested to *depth* levels."""
        inner = {
            "type": "list_item",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "raw": f"level-{depth}"}],
                }
            ],
        }
        for i in range(depth - 1, 0, -1):
            inner = {
                "type": "list_item",
                "children": [
                    {
                        "type": "paragraph",
                        "children": [{"type": "text", "raw": f"level-{i}"}],
                    },
                    {
                        "type": "list",
                        "attrs": {"ordered": False},
                        "children": [inner],
                    },
                ],
            }
        return {
            "type": "list",
            "attrs": {"ordered": False},
            "children": [inner],
        }

    def test_nesting_within_limit_no_warning(self):
        token = self._make_nested_list(4)
        blocks, _, warnings = build_blocks([token], _config())
        assert len(blocks) >= 1
        depth_warnings = [w for w in warnings if w.code == "NESTING_DEPTH_EXCEEDED"]
        assert depth_warnings == []

    def test_nesting_exactly_at_limit_no_warning(self):
        """8 levels is the limit; nesting at exactly 8 should NOT warn."""
        token = self._make_nested_list(8)
        blocks, _, warnings = build_blocks([token], _config())
        assert len(blocks) >= 1
        depth_warnings = [w for w in warnings if w.code == "NESTING_DEPTH_EXCEEDED"]
        assert depth_warnings == []

    def test_nesting_at_limit_emits_warning(self):
        token = self._make_nested_list(9)
        blocks, _, warnings = build_blocks([token], _config())
        assert len(blocks) >= 1
        depth_warnings = [w for w in warnings if w.code == "NESTING_DEPTH_EXCEEDED"]
        assert len(depth_warnings) >= 1
        assert "8" in depth_warnings[0].message

    def test_nesting_at_limit_flattens(self):
        """Items beyond the depth limit should NOT appear as nested children."""
        token = self._make_nested_list(9)
        blocks, _, _ = build_blocks([token], _config())

        # Walk block tree to find maximum nesting depth
        def _max_depth(block: dict, depth: int = 0) -> int:
            bt = block.get("type", "")
            children = block.get(bt, {}).get("children", [])
            if not children:
                return depth
            return max(_max_depth(c, depth + 1) for c in children)

        max_d = _max_depth(blocks[0])
        assert max_d < 9

    def test_task_list_nesting_at_limit_emits_warning(self):
        """Task list items also respect the depth guard."""
        # Build a simple task list 9 levels deep
        inner: dict = {
            "type": "task_list_item",
            "attrs": {"checked": False},
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "raw": "deep task"}],
                }
            ],
        }
        for _ in range(8):
            inner = {
                "type": "task_list_item",
                "attrs": {"checked": False},
                "children": [
                    {
                        "type": "paragraph",
                        "children": [{"type": "text", "raw": "task"}],
                    },
                    {
                        "type": "list",
                        "attrs": {"ordered": False},
                        "children": [inner],
                    },
                ],
            }
        token = {
            "type": "list",
            "attrs": {"ordered": False},
            "children": [inner],
        }
        blocks, _, warnings = build_blocks([token], _config())
        depth_warnings = [w for w in warnings if w.code == "NESTING_DEPTH_EXCEEDED"]
        assert len(depth_warnings) >= 1
