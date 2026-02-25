"""Tests for rich text splitting and building.

PRD test IDs: U-RT-001 through U-RT-008.
"""

import pytest

from notionify.config import NotionifyConfig
from notionify.converter.rich_text import build_rich_text, split_rich_text


def make_config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


def _make_text_segment(content, annotations=None, href=None):
    """Build a Notion rich_text text segment."""
    seg = {
        "type": "text",
        "text": {"content": content},
    }
    if annotations:
        seg["annotations"] = annotations
    if href:
        seg["href"] = href
    return seg


def _make_equation_segment(expression):
    """Build a Notion rich_text equation segment."""
    return {
        "type": "equation",
        "equation": {"expression": expression},
    }


# =========================================================================
# U-RT-001: Short text not split
# =========================================================================

class TestShortTextNoSplit:
    """U-RT-001: Text under the limit is not split."""

    def test_short_text_passes_through(self):
        seg = _make_text_segment("hello world")
        result = split_rich_text([seg])
        assert len(result) == 1
        assert result[0]["text"]["content"] == "hello world"

    def test_exactly_at_limit(self):
        text = "x" * 2000
        seg = _make_text_segment(text)
        result = split_rich_text([seg])
        assert len(result) == 1
        assert result[0]["text"]["content"] == text


# =========================================================================
# U-RT-002: Long text split at boundary
# =========================================================================

class TestLongTextSplit:
    """U-RT-002: Text over the limit is split into segments."""

    def test_split_at_2000(self):
        text = "a" * 3000
        seg = _make_text_segment(text)
        result = split_rich_text([seg])
        assert len(result) == 2
        assert len(result[0]["text"]["content"]) == 2000
        assert len(result[1]["text"]["content"]) == 1000
        total = result[0]["text"]["content"] + result[1]["text"]["content"]
        assert total == text

    def test_split_large_text(self):
        text = "b" * 6500
        seg = _make_text_segment(text)
        result = split_rich_text([seg])
        # 6500 / 2000 = 3.25 -> 4 segments
        assert len(result) == 4
        total = "".join(r["text"]["content"] for r in result)
        assert total == text
        for r in result:
            assert len(r["text"]["content"]) <= 2000


# =========================================================================
# U-RT-003: Annotations preserved on split
# =========================================================================

class TestAnnotationsPreservedOnSplit:
    """U-RT-003: Annotations are preserved on each split segment."""

    def test_bold_preserved(self):
        text = "c" * 3000
        annotations = {"bold": True, "italic": False, "strikethrough": False,
                       "underline": False, "code": False, "color": "default"}
        seg = _make_text_segment(text, annotations=annotations)
        result = split_rich_text([seg])
        assert len(result) == 2
        for r in result:
            assert r["annotations"]["bold"] is True

    def test_href_preserved(self):
        text = "d" * 3000
        seg = _make_text_segment(text, href="https://example.com")
        result = split_rich_text([seg])
        assert len(result) == 2
        for r in result:
            assert r["href"] == "https://example.com"


# =========================================================================
# U-RT-004: Equation segments pass through unchanged
# =========================================================================

class TestEquationPassThrough:
    """U-RT-004: Equation segments are not split by split_rich_text."""

    def test_equation_not_split(self):
        seg = _make_equation_segment("E=mc^2")
        result = split_rich_text([seg])
        assert len(result) == 1
        assert result[0]["type"] == "equation"
        assert result[0]["equation"]["expression"] == "E=mc^2"

    def test_long_equation_not_split(self):
        """Equations are passed through even if over 2000 chars."""
        long_expr = "x" * 3000
        seg = _make_equation_segment(long_expr)
        result = split_rich_text([seg])
        assert len(result) == 1
        assert result[0]["equation"]["expression"] == long_expr


# =========================================================================
# U-RT-005: Multiple segments mixed
# =========================================================================

class TestMultipleSegments:
    """U-RT-005: Multiple segments are handled independently."""

    def test_mixed_segments(self):
        short_seg = _make_text_segment("short")
        long_seg = _make_text_segment("x" * 3000)
        eq_seg = _make_equation_segment("y=f(x)")
        result = split_rich_text([short_seg, long_seg, eq_seg])
        # short: 1, long: 2, eq: 1 = 4 total
        assert len(result) == 4
        assert result[0]["text"]["content"] == "short"
        assert result[1]["text"]["content"] == "x" * 2000
        assert result[2]["text"]["content"] == "x" * 1000
        assert result[3]["type"] == "equation"


# =========================================================================
# U-RT-006: Empty input
# =========================================================================

class TestEmptyInput:
    """U-RT-006: Empty segment list returns empty list."""

    def test_empty_list(self):
        result = split_rich_text([])
        assert result == []


# =========================================================================
# U-RT-007: Custom limit
# =========================================================================

class TestCustomLimit:
    """U-RT-007: Custom limit parameter works correctly."""

    def test_custom_limit(self):
        text = "hello world"
        seg = _make_text_segment(text)
        result = split_rich_text([seg], limit=5)
        assert len(result) == 3
        assert result[0]["text"]["content"] == "hello"
        assert result[1]["text"]["content"] == " worl"
        assert result[2]["text"]["content"] == "d"

    def test_limit_1(self):
        seg = _make_text_segment("abc")
        result = split_rich_text([seg], limit=1)
        assert len(result) == 3
        assert [r["text"]["content"] for r in result] == ["a", "b", "c"]


# =========================================================================
# U-RT-008: Unicode safety
# =========================================================================

class TestUnicodeSafety:
    """U-RT-008: Multi-byte characters are never split mid-character."""

    def test_emoji_not_split(self):
        # Each emoji is 1 Python code point
        text = "\U0001f600" * 5  # 5 smiley emojis
        seg = _make_text_segment(text)
        result = split_rich_text([seg], limit=3)
        assert len(result) == 2
        total = "".join(r["text"]["content"] for r in result)
        assert total == text

    def test_cjk_characters(self):
        text = "\u4f60\u597d\u4e16\u754c"  # Chinese characters
        seg = _make_text_segment(text)
        result = split_rich_text([seg], limit=2)
        assert len(result) == 2
        total = "".join(r["text"]["content"] for r in result)
        assert total == text


# =========================================================================
# build_rich_text tests
# =========================================================================

class TestBuildRichText:
    """Test build_rich_text from inline AST tokens."""

    def test_plain_text(self):
        tokens = [{"type": "text", "raw": "hello"}]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["text"]["content"] == "hello"

    def test_strong(self):
        tokens = [
            {"type": "strong", "children": [{"type": "text", "raw": "bold"}]}
        ]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["annotations"]["bold"] is True
        assert result[0]["text"]["content"] == "bold"

    def test_emphasis(self):
        tokens = [
            {"type": "emphasis", "children": [{"type": "text", "raw": "italic"}]}
        ]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["annotations"]["italic"] is True

    def test_codespan(self):
        tokens = [{"type": "codespan", "raw": "code"}]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["annotations"]["code"] is True
        assert result[0]["text"]["content"] == "code"

    def test_strikethrough_token(self):
        tokens = [
            {"type": "strikethrough", "children": [{"type": "text", "raw": "del"}]}
        ]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["annotations"]["strikethrough"] is True

    def test_link_token(self):
        tokens = [{
            "type": "link",
            "attrs": {"url": "https://example.com"},
            "children": [{"type": "text", "raw": "click"}],
        }]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["href"] == "https://example.com"
        assert result[0]["text"]["content"] == "click"

    def test_softbreak(self):
        tokens = [
            {"type": "text", "raw": "line1"},
            {"type": "softbreak"},
            {"type": "text", "raw": "line2"},
        ]
        result = build_rich_text(tokens, make_config())
        texts = [seg["text"]["content"] for seg in result]
        assert texts == ["line1", " ", "line2"]

    def test_linebreak(self):
        tokens = [
            {"type": "text", "raw": "line1"},
            {"type": "linebreak"},
            {"type": "text", "raw": "line2"},
        ]
        result = build_rich_text(tokens, make_config())
        texts = [seg["text"]["content"] for seg in result]
        assert texts == ["line1", "\n", "line2"]

    def test_empty_text_skipped(self):
        tokens = [{"type": "text", "raw": ""}]
        result = build_rich_text(tokens, make_config())
        assert result == []

    def test_nested_bold_italic(self):
        tokens = [{
            "type": "strong",
            "children": [{
                "type": "emphasis",
                "children": [{"type": "text", "raw": "both"}],
            }],
        }]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["annotations"]["bold"] is True
        assert result[0]["annotations"]["italic"] is True


class TestBuildRichTextImageToken:
    """Tests for image token inline rendering (lines 149-160)."""

    def test_image_with_alt_and_url(self):
        tokens = [{"type": "image", "attrs": {"url": "https://example.com/img.png"},
                   "children": [{"type": "text", "raw": "alt text"}]}]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["text"]["content"] == "[alt text](https://example.com/img.png)"

    def test_image_with_url_only(self):
        tokens = [{"type": "image", "attrs": {"url": "https://example.com/img.png"},
                   "children": []}]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["text"]["content"] == "https://example.com/img.png"

    def test_image_with_alt_only(self):
        tokens = [{"type": "image", "attrs": {},
                   "children": [{"type": "text", "raw": "my alt"}]}]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["text"]["content"] == "my alt"

    def test_image_with_neither_alt_nor_url(self):
        tokens = [{"type": "image", "attrs": {}, "children": []}]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["text"]["content"] == "[image]"


class TestBuildRichTextHtmlInline:
    """Tests for html_inline token handling (lines 184-189)."""

    def test_html_inline_rendered_as_text(self):
        tokens = [{"type": "html_inline", "raw": "<br/>"}]
        result = build_rich_text(tokens, make_config())
        assert len(result) == 1
        assert result[0]["text"]["content"] == "<br/>"

    def test_empty_html_inline_skipped(self):
        tokens = [{"type": "html_inline", "raw": ""}]
        result = build_rich_text(tokens, make_config())
        assert result == []


class TestBuildRichTextInlineMathList:
    """Tests for inline_math where build_inline_math returns a list (line 170)."""

    def test_inline_math_list_result_extended(self):
        """When build_inline_math returns a list, all segments are extended."""
        from unittest.mock import patch
        mock_segs = [
            {"type": "text", "text": {"content": "E"}},
            {"type": "text", "text": {"content": "=mc^2"}},
        ]
        with patch("notionify.converter.math.build_inline_math",
                   return_value=(mock_segs, [])):
            tokens = [{"type": "inline_math", "raw": "E=mc^2"}]
            result = build_rich_text(tokens, make_config())
        assert len(result) == 2
        assert result[0]["text"]["content"] == "E"
        assert result[1]["text"]["content"] == "=mc^2"


class TestExtractText:
    """Tests for _extract_text recursive helper (lines 289-300)."""

    def test_extract_nested_children(self):
        from notionify.converter.rich_text import _extract_text
        # token with children (no "raw") → recurse
        tokens = [{"type": "strong", "children": [{"type": "text", "raw": "bold"}]}]
        assert _extract_text(tokens) == "bold"

    def test_extract_raw_fallback(self):
        from notionify.converter.rich_text import _extract_text
        # token with "raw" but no "children" and not "text" type
        tokens = [{"type": "codespan", "raw": "code"}]
        assert _extract_text(tokens) == "code"

    def test_extract_mixed(self):
        from notionify.converter.rich_text import _extract_text
        tokens = [
            {"type": "text", "raw": "hello "},
            {"type": "strong", "children": [{"type": "text", "raw": "world"}]},
        ]
        assert _extract_text(tokens) == "hello world"
