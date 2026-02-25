"""Deep nesting stress tests for the conversion pipeline.

Tests deeply nested structures (>8 levels) for both MD->Notion and
Notion->MD directions, ensuring graceful handling beyond practical limits.

PRD hardening: Section 20 edge cases, iteration 13.
"""

from __future__ import annotations

import pytest

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer


def _converter(**kwargs: object) -> MarkdownToNotionConverter:
    return MarkdownToNotionConverter(NotionifyConfig(token="test-token", **kwargs))


def _renderer(**kwargs: object) -> NotionToMarkdownRenderer:
    return NotionToMarkdownRenderer(NotionifyConfig(token="test-token", **kwargs))


def _get_text(block: dict) -> str:
    bt = block["type"]
    return "".join(
        seg.get("text", {}).get("content", "")
        for seg in block[bt].get("rich_text", [])
    )


# =========================================================================
# Deeply nested lists (MD -> Notion)
# =========================================================================


class TestDeepNestedLists:
    """Test lists nested beyond the practical ~8 level limit."""

    @pytest.mark.parametrize("depth", [3, 5, 8, 10, 15])
    def test_nested_unordered_list_at_depth(self, depth: int):
        """Nested bullet list at various depths should not crash."""
        lines = []
        for i in range(depth):
            indent = "  " * i
            lines.append(f"{indent}- Level {i + 1}")
        md = "\n".join(lines)
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        # All produced blocks should be valid list items
        for block in result.blocks:
            assert block["type"] in ("bulleted_list_item", "numbered_list_item")

    @pytest.mark.parametrize("depth", [3, 5, 8, 10, 15])
    def test_nested_ordered_list_at_depth(self, depth: int):
        """Nested numbered list at various depths should not crash."""
        lines = []
        for i in range(depth):
            indent = "   " * i
            lines.append(f"{indent}1. Level {i + 1}")
        md = "\n".join(lines)
        result = _converter().convert(md)
        assert len(result.blocks) >= 1

    def test_mixed_list_types_deeply_nested(self):
        """Alternating bullet and numbered lists at depth."""
        md = "\n".join([
            "- Bullet 1",
            "  1. Number 1",
            "    - Bullet 2",
            "      1. Number 2",
            "        - Bullet 3",
            "          1. Number 3",
            "            - Bullet 4",
            "              1. Number 4",
            "                - Bullet 5",
        ])
        result = _converter().convert(md)
        assert len(result.blocks) >= 1


# =========================================================================
# Deeply nested block quotes (MD -> Notion)
# =========================================================================


class TestDeepNestedBlockQuotes:
    """Test block quotes nested beyond typical depths."""

    @pytest.mark.parametrize("depth", [2, 4, 6, 8, 10])
    def test_nested_blockquote_at_depth(self, depth: int):
        """Nested block quotes should not crash at any depth."""
        prefix = "> " * depth
        md = f"{prefix}Deep quote"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1

    def test_blockquote_with_nested_list(self):
        """Block quote containing deeply nested list."""
        md = "\n".join([
            "> - Item 1",
            ">   - Item 2",
            ">     - Item 3",
            ">       - Item 4",
            ">         - Item 5",
        ])
        result = _converter().convert(md)
        assert len(result.blocks) >= 1

    def test_deeply_nested_quote_content_preserved(self):
        """Text content in deeply nested quotes should survive."""
        md = "> > > > Content at depth 4"
        result = _converter().convert(md)
        assert len(result.blocks) >= 1
        # Walk into the nested structure to find the text
        def find_text(blocks):
            for b in blocks:
                bt = b["type"]
                rich_text = b.get(bt, {}).get("rich_text", [])
                for seg in rich_text:
                    content = seg.get("text", {}).get("content", "")
                    if "Content at depth 4" in content:
                        return True
                children = b.get(bt, {}).get("children", [])
                if children and find_text(children):
                    return True
            return False
        assert find_text(result.blocks)


# =========================================================================
# Deep nesting: Notion -> Markdown
# =========================================================================


class TestDeepNestingExport:
    """Test Notion block trees with deep nesting export correctly."""

    def _make_nested_quote(self, depth: int, text: str) -> dict:
        """Create a nested quote block structure at given depth."""
        innermost = {
            "type": "quote",
            "quote": {
                "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
            },
        }
        current = innermost
        for _ in range(depth - 1):
            current = {
                "type": "quote",
                "quote": {
                    "rich_text": [],
                    "children": [current],
                },
            }
        return current

    def _make_nested_list(self, depth: int, text: str) -> dict:
        """Create a nested bulleted list block at given depth."""
        innermost = {
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
            },
        }
        current = innermost
        for _ in range(depth - 1):
            parent_rt = [{"type": "text", "text": {"content": "Parent"}, "plain_text": "Parent"}]
            current = {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": parent_rt,
                    "children": [current],
                },
            }
        return current

    @pytest.mark.parametrize("depth", [2, 4, 6, 8, 10, 12])
    def test_nested_quote_export_at_depth(self, depth: int):
        """Export nested quotes at various depths without crashing."""
        block = self._make_nested_quote(depth, "Deep text")
        r = _renderer()
        result = r.render_blocks([block])
        assert "Deep text" in result

    @pytest.mark.parametrize("depth", [2, 4, 6, 8, 10])
    def test_nested_list_export_at_depth(self, depth: int):
        """Export nested lists at various depths."""
        block = self._make_nested_list(depth, "Leaf item")
        r = _renderer()
        result = r.render_blocks([block])
        assert "Leaf item" in result
        # Should have indentation markers
        assert "-" in result or "*" in result

    def test_quote_nesting_preserves_structure(self):
        """Block quote nesting produces `>` markers."""
        block = self._make_nested_quote(3, "Three deep")
        r = _renderer()
        result = r.render_blocks([block])
        # Count '>' markers on the line with the text
        for line in result.splitlines():
            if "Three deep" in line:
                gt_count = line.count(">")
                assert gt_count >= 2  # At least some nesting visible


# =========================================================================
# Stress tests: large flat and nested content
# =========================================================================


class TestStressContent:
    """Stress tests for large content at various nesting depths."""

    def test_100_sequential_paragraphs(self):
        """100 paragraphs should convert without issues."""
        md = "\n\n".join(f"Paragraph {i}" for i in range(100))
        result = _converter().convert(md)
        assert len(result.blocks) == 100

    def test_100_list_items(self):
        """100 flat list items should convert without issues."""
        md = "\n".join(f"- Item {i}" for i in range(100))
        result = _converter().convert(md)
        assert len(result.blocks) == 100

    def test_50_headings(self):
        """50 headings should convert without issues."""
        md = "\n\n".join(f"# Heading {i}" for i in range(50))
        result = _converter().convert(md)
        assert len(result.blocks) == 50

    def test_nested_list_with_wide_siblings(self):
        """Each level has multiple siblings alongside nesting."""
        lines = [
            f"{'  ' * depth}- D{depth} S{sib}"
            for depth in range(5)
            for sib in range(3)
        ]
        md = "\n".join(lines)
        result = _converter().convert(md)
        assert len(result.blocks) >= 3  # At least root siblings

    def test_mixed_deep_structure(self):
        """Mix of headings, paragraphs, lists, quotes, code blocks."""
        md = "\n\n".join([
            "# Heading",
            "Paragraph 1",
            "> > > Deep quote",
            "- Item 1\n  - Nested\n    - Deeper\n      - Deepest",
            "```python\nprint('hello')\n```",
            "---",
            "1. First\n   1. Second\n      1. Third",
            "## Another heading",
            "Final paragraph",
        ])
        result = _converter().convert(md)
        types = [b["type"] for b in result.blocks]
        assert "heading_1" in types
        assert "paragraph" in types
        assert "code" in types
        assert "divider" in types
