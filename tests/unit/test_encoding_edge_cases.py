"""Encoding edge case tests for the conversion pipeline.

Tests BOM handling, CRLF/mixed line endings, NFD/NFC unicode
normalization, and other text encoding boundary conditions.

PRD hardening: Section 20 edge cases, iteration 13.
"""

from __future__ import annotations

import unicodedata

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer


def _converter(**kwargs: object) -> MarkdownToNotionConverter:
    return MarkdownToNotionConverter(NotionifyConfig(token="test-token", **kwargs))


def _get_text(block: dict) -> str:
    """Extract plain text from a block's rich_text."""
    bt = block["type"]
    return "".join(
        seg.get("text", {}).get("content", "")
        if seg.get("type") == "text"
        else seg.get("equation", {}).get("expression", "")
        for seg in block[bt].get("rich_text", [])
    )


# =========================================================================
# BOM (Byte Order Mark) handling
# =========================================================================


class TestBOMHandling:
    """Markdown with a leading UTF-8 BOM (\ufeff) should not break parsing."""

    def test_bom_before_heading(self):
        md = "\ufeff# Hello"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        # The heading should still be recognized
        first = result.blocks[0]
        assert first["type"] in ("heading_1", "paragraph")

    def test_bom_before_paragraph(self):
        md = "\ufeffHello world"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        text = _get_text(result.blocks[0])
        # BOM may be stripped or preserved, but content must be intact
        assert "Hello world" in text

    def test_bom_in_middle_of_text(self):
        md = "Hello\ufeffWorld"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        text = _get_text(result.blocks[0])
        assert "Hello" in text
        assert "World" in text

    def test_bom_only_produces_empty_or_minimal(self):
        md = "\ufeff"
        result = _converter().convert(md)
        # Either no blocks or a single empty paragraph
        assert len(result.blocks) <= 1


# =========================================================================
# CRLF and mixed line endings
# =========================================================================


class TestLineEndings:
    """Markdown with CRLF, LF, and CR line endings should parse correctly."""

    def test_crlf_paragraphs(self):
        md = "Hello\r\n\r\nWorld"
        result = _converter().convert(md)
        assert len(result.blocks) == 2
        assert _get_text(result.blocks[0]) == "Hello"
        assert _get_text(result.blocks[1]) == "World"

    def test_crlf_heading_and_paragraph(self):
        md = "# Title\r\n\r\nBody text"
        result = _converter().convert(md)
        assert len(result.blocks) == 2
        assert result.blocks[0]["type"] == "heading_1"
        assert _get_text(result.blocks[1]) == "Body text"

    def test_crlf_list_items(self):
        md = "- Item 1\r\n- Item 2\r\n- Item 3"
        result = _converter().convert(md)
        assert len(result.blocks) == 3
        for block in result.blocks:
            assert block["type"] == "bulleted_list_item"

    def test_crlf_code_block(self):
        md = "```python\r\ndef hello():\r\n    pass\r\n```"
        result = _converter().convert(md)
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "code"

    def test_bare_cr_line_endings(self):
        # Classic Mac line endings (CR only)
        md = "Hello\r\rWorld"
        result = _converter().convert(md)
        # Should produce at least some content
        assert len(result.blocks) >= 1

    def test_mixed_line_endings(self):
        # Mix of LF, CRLF, and CR
        md = "Line 1\nLine 2\r\nLine 3\rLine 4"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1

    def test_crlf_block_quote(self):
        md = "> Quote line 1\r\n> Quote line 2"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        assert result.blocks[0]["type"] == "quote"

    def test_crlf_horizontal_rule(self):
        md = "Above\r\n\r\n---\r\n\r\nBelow"
        result = _converter().convert(md)
        types = [b["type"] for b in result.blocks]
        assert "divider" in types

    def test_crlf_table(self):
        md = "| A | B |\r\n| - | - |\r\n| 1 | 2 |"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        assert result.blocks[0]["type"] == "table"


# =========================================================================
# Unicode normalization (NFC vs NFD)
# =========================================================================


class TestUnicodeNormalization:
    """Ensure conversion handles composed and decomposed Unicode forms."""

    def test_nfc_text_preserved(self):
        # NFC: e-acute as single code point U+00E9
        text = unicodedata.normalize("NFC", "\u00e9")
        md = f"# {text}"
        result = _converter().convert(md)
        assert len(result.blocks) == 1
        content = _get_text(result.blocks[0])
        assert "\u00e9" in unicodedata.normalize("NFC", content)

    def test_nfd_text_preserved(self):
        # NFD: e + combining acute accent (U+0065 U+0301)
        text = unicodedata.normalize("NFD", "\u00e9")
        assert len(text) == 2  # decomposed: e + combining accent
        md = f"# {text}"
        result = _converter().convert(md)
        assert len(result.blocks) == 1
        content = _get_text(result.blocks[0])
        # Content should be visually equivalent regardless of normalization form
        assert unicodedata.normalize("NFC", content) == "\u00e9"

    def test_mixed_nfc_nfd_in_same_paragraph(self):
        nfc = unicodedata.normalize("NFC", "caf\u00e9")
        nfd = unicodedata.normalize("NFD", "na\u00efve")
        md = f"{nfc} and {nfd}"
        result = _converter().convert(md)
        assert len(result.blocks) == 1
        text = _get_text(result.blocks[0])
        # Both words should be present
        normalized = unicodedata.normalize("NFC", text)
        assert "caf\u00e9" in normalized
        assert "na\u00efve" in normalized

    def test_combining_marks_in_heading(self):
        # Tilde combining mark: n + combining tilde
        text = "a\u0303"  # a with combining tilde -> ã
        md = f"# {text}"
        result = _converter().convert(md)
        content = _get_text(result.blocks[0])
        # Should be visually equivalent to ã
        assert unicodedata.normalize("NFC", content) == "\u00e3"

    def test_hangul_jamo_composition(self):
        # Korean: composed vs decomposed Hangul
        composed = "\uD55C"  # 한 (single code point)
        decomposed = unicodedata.normalize("NFD", composed)
        md_c = f"# {composed}"
        md_d = f"# {decomposed}"
        r1 = _converter().convert(md_c)
        r2 = _converter().convert(md_d)
        t1 = unicodedata.normalize("NFC", _get_text(r1.blocks[0]))
        t2 = unicodedata.normalize("NFC", _get_text(r2.blocks[0]))
        assert t1 == t2


# =========================================================================
# Special Unicode content
# =========================================================================


class TestSpecialUnicode:
    """Test various Unicode edge cases in markdown conversion."""

    def test_zero_width_joiner_in_emoji(self):
        # Family emoji: composed with ZWJ
        md = "# \U0001F468\u200D\U0001F469\u200D\U0001F467"
        result = _converter().convert(md)
        assert len(result.blocks) == 1
        text = _get_text(result.blocks[0])
        assert "\U0001F468" in text

    def test_right_to_left_text(self):
        # Arabic text
        md = "\u0645\u0631\u062D\u0628\u0627"  # مرحبا (marhaba)
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        text = _get_text(result.blocks[0])
        assert "\u0645" in text

    def test_bidi_mixed_text(self):
        # Mixed LTR and RTL
        md = "Hello \u0645\u0631\u062D\u0628\u0627 World"
        result = _converter().convert(md)
        text = _get_text(result.blocks[0])
        assert "Hello" in text
        assert "World" in text

    def test_mathematical_symbols(self):
        md = "\u2200x \u2208 S: \u2203y > x"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1

    def test_null_byte_in_text(self):
        # Null byte should not crash the converter
        md = "Hello\x00World"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1

    def test_surrogate_range_text(self):
        # Astral plane characters (emoji)
        md = "\U0001F600 \U0001F4A9 \U0001F680"  # 😀 💩 🚀
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        text = _get_text(result.blocks[0])
        assert "\U0001F600" in text

    def test_fullwidth_latin_characters(self):
        # Fullwidth A-Z (used in CJK contexts)
        md = "\uFF21\uFF22\uFF23"  # fullwidth ABC (U+FF21..FF23)
        result = _converter().convert(md)
        assert len(result.blocks) >= 1

    def test_variation_selectors(self):
        # Text vs emoji presentation selectors
        md = "\u2764\uFE0F"  # ❤️ (heart with emoji presentation)
        result = _converter().convert(md)
        assert len(result.blocks) >= 1


# =========================================================================
# Round-trip with encoding edge cases
# =========================================================================


class TestEncodingRoundTrip:
    """Test that encoding edge cases survive round-trip (MD -> Notion -> MD)."""

    def test_cjk_round_trip(self):
        md = "# \u4F60\u597D\u4E16\u754C"  # 你好世界
        conv = _converter()
        result = conv.convert(md)
        renderer = NotionToMarkdownRenderer(NotionifyConfig(token="test-token"))
        out = renderer.render_blocks(result.blocks)
        assert "\u4F60\u597D\u4E16\u754C" in out

    def test_emoji_round_trip(self):
        md = "# \U0001F600 Smiling face"
        conv = _converter()
        result = conv.convert(md)
        renderer = NotionToMarkdownRenderer(NotionifyConfig(token="test-token"))
        out = renderer.render_blocks(result.blocks)
        assert "\U0001F600" in out
        assert "Smiling face" in out

    def test_accented_text_round_trip(self):
        md = "Cr\u00e8me br\u00fbl\u00e9e"
        conv = _converter()
        result = conv.convert(md)
        renderer = NotionToMarkdownRenderer(NotionifyConfig(token="test-token"))
        out = renderer.render_blocks(result.blocks)
        normalized = unicodedata.normalize("NFC", out)
        assert "br\u00fbl\u00e9e" in normalized
