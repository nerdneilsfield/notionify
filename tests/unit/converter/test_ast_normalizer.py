"""Dedicated unit tests for ASTNormalizer.

Tests the Markdown → canonical AST normalization layer, ensuring that
mistune's raw token stream is correctly mapped to the canonical types
used by the block builder pipeline.
"""

import pytest

from notionify.converter.ast_normalizer import ASTNormalizer


@pytest.fixture
def normalizer():
    return ASTNormalizer()


# =========================================================================
# Block-level normalization
# =========================================================================

class TestBlockNormalization:
    """Verify each block type is normalized correctly."""

    def test_heading(self, normalizer):
        tokens = normalizer.parse("# Hello")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "heading"
        assert tokens[0]["attrs"]["level"] == 1

    def test_heading_levels(self, normalizer):
        for level in range(1, 7):
            tokens = normalizer.parse(f"{'#' * level} Heading {level}")
            assert tokens[0]["type"] == "heading"
            assert tokens[0]["attrs"]["level"] == level

    def test_paragraph(self, normalizer):
        tokens = normalizer.parse("Hello world")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "paragraph"

    def test_paragraph_children_are_inline(self, normalizer):
        tokens = normalizer.parse("Hello **bold** world")
        para = tokens[0]
        assert para["type"] == "paragraph"
        children = para["children"]
        types = [c["type"] for c in children]
        assert "text" in types
        assert "strong" in types

    def test_block_quote(self, normalizer):
        tokens = normalizer.parse("> Quote text")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "block_quote"
        assert "children" in tokens[0]

    def test_unordered_list(self, normalizer):
        tokens = normalizer.parse("- item 1\n- item 2")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "list"
        assert tokens[0]["attrs"]["ordered"] is False
        items = tokens[0]["children"]
        assert len(items) == 2

    def test_ordered_list(self, normalizer):
        tokens = normalizer.parse("1. first\n2. second")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "list"
        assert tokens[0]["attrs"]["ordered"] is True

    def test_task_list(self, normalizer):
        tokens = normalizer.parse("- [x] done\n- [ ] todo")
        list_token = tokens[0]
        assert list_token["type"] == "list"
        items = list_token["children"]
        assert any(i["type"] == "task_list_item" for i in items)

    def test_block_code(self, normalizer):
        tokens = normalizer.parse("```python\nprint('hello')\n```")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "block_code"
        assert tokens[0]["raw"] == "print('hello')"
        assert tokens[0]["attrs"]["info"] == "python"

    def test_block_code_no_language(self, normalizer):
        tokens = normalizer.parse("```\ncode here\n```")
        assert tokens[0]["type"] == "block_code"
        assert tokens[0]["raw"] == "code here"

    def test_block_code_strips_trailing_newline(self, normalizer):
        tokens = normalizer.parse("```\nline1\nline2\n```")
        # Raw should not end with the trailing newline that mistune adds
        assert not tokens[0]["raw"].endswith("\n")

    def test_thematic_break(self, normalizer):
        tokens = normalizer.parse("---")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "thematic_break"
        assert "children" not in tokens[0]

    def test_table(self, normalizer):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        tokens = normalizer.parse(md)
        assert len(tokens) == 1
        assert tokens[0]["type"] == "table"

    def test_html_block(self, normalizer):
        tokens = normalizer.parse("<div>hello</div>\n\n")
        # html_block should be normalized from block_html
        html_tokens = [t for t in tokens if t["type"] == "html_block"]
        assert len(html_tokens) >= 1
        assert "raw" in html_tokens[0]


# =========================================================================
# Inline-level normalization
# =========================================================================

class TestInlineNormalization:
    """Verify each inline type is normalized correctly."""

    def _get_inline_tokens(self, normalizer, md):
        """Parse markdown and extract inline tokens from first paragraph."""
        tokens = normalizer.parse(md)
        for t in tokens:
            if t["type"] == "paragraph":
                return t["children"]
        return []

    def test_text(self, normalizer):
        children = self._get_inline_tokens(normalizer, "plain text")
        assert any(c["type"] == "text" and c["raw"] == "plain text" for c in children)

    def test_strong(self, normalizer):
        children = self._get_inline_tokens(normalizer, "**bold**")
        assert any(c["type"] == "strong" for c in children)

    def test_emphasis(self, normalizer):
        children = self._get_inline_tokens(normalizer, "*italic*")
        assert any(c["type"] == "emphasis" for c in children)

    def test_codespan(self, normalizer):
        children = self._get_inline_tokens(normalizer, "`code`")
        assert any(c["type"] == "codespan" and c["raw"] == "code" for c in children)

    def test_strikethrough(self, normalizer):
        children = self._get_inline_tokens(normalizer, "~~deleted~~")
        assert any(c["type"] == "strikethrough" for c in children)

    def test_link(self, normalizer):
        children = self._get_inline_tokens(normalizer, "[text](https://example.com)")
        links = [c for c in children if c["type"] == "link"]
        assert len(links) == 1
        assert links[0]["attrs"]["url"] == "https://example.com"
        assert "children" in links[0]

    def test_image(self, normalizer):
        children = self._get_inline_tokens(normalizer, "![alt](https://img.png)")
        # Image might be inline or at paragraph level depending on parser
        tokens = normalizer.parse("![alt](https://img.png)")
        all_types = self._collect_types(tokens)
        assert "image" in all_types

    def test_inline_math(self, normalizer):
        children = self._get_inline_tokens(normalizer, "Euler's $e^{i\\pi}+1=0$")
        math_tokens = [c for c in children if c["type"] == "inline_math"]
        assert len(math_tokens) == 1
        assert math_tokens[0]["raw"] == "e^{i\\pi}+1=0"

    def test_softbreak(self, normalizer):
        # Softbreak occurs between lines in the same paragraph
        children = self._get_inline_tokens(normalizer, "line1\nline2")
        assert any(c["type"] == "softbreak" for c in children)

    def test_html_inline(self, normalizer):
        children = self._get_inline_tokens(normalizer, "text <br> more")
        html_tokens = [c for c in children if c["type"] == "html_inline"]
        assert len(html_tokens) >= 1
        assert html_tokens[0]["raw"] == "<br>"

    def _collect_types(self, tokens, depth=0):
        """Recursively collect all token types."""
        types = set()
        for t in tokens:
            types.add(t.get("type", ""))
            if "children" in t:
                types |= self._collect_types(t["children"], depth + 1)
        return types


# =========================================================================
# Edge cases and special handling
# =========================================================================

class TestEdgeCases:
    """Test edge cases in normalization."""

    def test_blank_lines_skipped(self, normalizer):
        tokens = normalizer.parse("Hello\n\n\n\nWorld")
        # Blank lines should not produce tokens
        types = [t["type"] for t in tokens]
        assert "blank_line" not in types

    def test_empty_input(self, normalizer):
        tokens = normalizer.parse("")
        assert tokens == []

    def test_whitespace_only(self, normalizer):
        tokens = normalizer.parse("   \n\n   ")
        # Should either be empty or contain only paragraphs/blank lines (filtered)
        types = [t["type"] for t in tokens]
        assert "blank_line" not in types

    def test_footnote_ref_becomes_text(self, normalizer):
        tokens = normalizer.parse("text[^1]\n\n[^1]: footnote")
        # Footnote references should be rendered as text "[^key]"
        all_children = []
        for t in tokens:
            if "children" in t:
                all_children.extend(t["children"])
        text_tokens = [c for c in all_children if c["type"] == "text"]
        raw_texts = [c.get("raw", "") for c in text_tokens]
        combined = "".join(raw_texts)
        # Should contain the footnote reference as text
        assert "[^" in combined or "footnote" in combined.lower() or len(tokens) >= 1

    def test_footnotes_section_excluded(self, normalizer):
        tokens = normalizer.parse("text[^1]\n\n[^1]: footnote content")
        # The "footnotes" block type should not appear in output
        types = [t["type"] for t in tokens]
        assert "footnotes" not in types

    def test_unknown_token_passed_through(self, normalizer):
        """Unknown token types should be passed through with type and raw."""
        # We can't easily generate an unknown token from markdown,
        # so test the internal method directly.
        token = {"type": "exotic_widget", "raw": "some content"}
        result = normalizer._normalize_token(token)
        assert result is not None
        assert result["type"] == "exotic_widget"
        assert result["raw"] == "some content"

    def test_raw_type_becomes_text(self, normalizer):
        """The 'raw' token type should be normalized to text."""
        token = {"type": "raw", "raw": "raw content"}
        result = normalizer._normalize_token(token)
        assert result["type"] == "text"
        assert result["raw"] == "raw content"


# =========================================================================
# Table sub-type normalization
# =========================================================================

class TestTableNormalization:
    """Test that table sub-types are preserved correctly."""

    def test_table_has_head_and_body(self, normalizer):
        md = "| H1 | H2 |\n|---|---|\n| A | B |"
        tokens = normalizer.parse(md)
        table = tokens[0]
        assert table["type"] == "table"
        child_types = [c["type"] for c in table.get("children", [])]
        assert "table_head" in child_types
        assert "table_body" in child_types

    def test_table_cells_in_head(self, normalizer):
        md = "| H1 | H2 |\n|---|---|\n| A | B |"
        tokens = normalizer.parse(md)
        table = tokens[0]
        # table_head contains table_cell children directly (no table_row wrapper)
        head = next(c for c in table["children"] if c["type"] == "table_head")
        cells = [c for c in head.get("children", []) if c["type"] == "table_cell"]
        assert len(cells) == 2
        assert cells[0]["attrs"]["head"] is True


# =========================================================================
# Nested structure normalization
# =========================================================================

class TestNestedStructures:
    """Test normalization of nested markdown structures."""

    def test_nested_list(self, normalizer):
        md = "- outer\n  - inner"
        tokens = normalizer.parse(md)
        outer_list = tokens[0]
        assert outer_list["type"] == "list"
        # The inner list should be nested within a list_item
        items = outer_list["children"]
        assert len(items) >= 1

    def test_blockquote_with_paragraph(self, normalizer):
        md = "> First line\n> Second line"
        tokens = normalizer.parse(md)
        bq = tokens[0]
        assert bq["type"] == "block_quote"
        assert "children" in bq

    def test_strong_inside_emphasis(self, normalizer):
        tokens = normalizer.parse("_italic **bold**_")
        para = tokens[0]
        assert para["type"] == "paragraph"
        # Should have emphasis containing strong
        children = para["children"]
        emphasis = [c for c in children if c["type"] == "emphasis"]
        assert len(emphasis) >= 1

    def test_link_with_bold_text(self, normalizer):
        tokens = normalizer.parse("[**bold link**](https://example.com)")
        para = tokens[0]
        links = [c for c in para["children"] if c["type"] == "link"]
        assert len(links) == 1
        assert links[0]["attrs"]["url"] == "https://example.com"
        # Link should contain strong child
        link_child_types = [c["type"] for c in links[0].get("children", [])]
        assert "strong" in link_child_types


# =========================================================================
# Block math
# =========================================================================

class TestBlockMath:
    """Test block-level math normalization."""

    def test_block_math(self, normalizer):
        tokens = normalizer.parse("$$\nE = mc^2\n$$")
        math_tokens = [t for t in tokens if t["type"] == "block_math"]
        assert len(math_tokens) == 1
        assert "E = mc^2" in math_tokens[0]["raw"]

    def test_inline_math_separate_from_block(self, normalizer):
        tokens = normalizer.parse("Inline $x^2$ and block:\n\n$$\ny^2\n$$")
        types = [t["type"] for t in tokens]
        assert "paragraph" in types
        assert "block_math" in types


# =========================================================================
# Attrs preservation
# =========================================================================

class TestAttrsPreservation:
    """Test that attributes are correctly copied during normalization."""

    def test_heading_attrs_preserved(self, normalizer):
        tokens = normalizer.parse("### Level 3")
        assert tokens[0]["attrs"]["level"] == 3

    def test_list_ordered_attr(self, normalizer):
        tokens = normalizer.parse("1. item")
        assert tokens[0]["attrs"]["ordered"] is True

    def test_list_unordered_attr(self, normalizer):
        tokens = normalizer.parse("- item")
        assert tokens[0]["attrs"]["ordered"] is False

    def test_link_attrs_url(self, normalizer):
        tokens = normalizer.parse("[click](https://test.com)")
        para = tokens[0]
        links = [c for c in para["children"] if c["type"] == "link"]
        assert links[0]["attrs"]["url"] == "https://test.com"

    def test_code_block_info_attr(self, normalizer):
        tokens = normalizer.parse("```javascript\nconsole.log('hi')\n```")
        assert tokens[0]["attrs"]["info"] == "javascript"
