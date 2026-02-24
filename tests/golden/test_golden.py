"""Golden fixture round-trip tests.

These tests verify that Markdown -> Notion blocks -> Markdown conversion
produces semantically consistent output. The round-trip is NOT expected
to be byte-identical (block ordering, whitespace, heading levels may
differ), but key content must survive.
"""
from __future__ import annotations

import copy
from pathlib import Path
import pytest
from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _simulate_api_response(blocks: list[dict]) -> list[dict]:
    """Normalize converter output to look like a Notion API response.

    The converter produces blocks destined for the Notion *write* API, so
    rich_text segments contain ``{"type": "text", "text": {"content": ...}}``
    but lack the ``plain_text`` field that the Notion API adds when it echoes
    blocks back.  The renderer (notion_to_md) relies on ``plain_text``.

    This helper deep-copies the blocks and populates ``plain_text`` from
    ``text.content`` on every rich_text segment, mimicking API behaviour so
    that the round-trip test exercises the real renderer logic.
    """
    blocks = copy.deepcopy(blocks)
    for block in blocks:
        _patch_block(block)
    return blocks


def _patch_block(block: dict) -> None:
    """Recursively add ``plain_text`` to every rich_text segment in *block*."""
    block_type = block.get("type", "")
    block_data = block.get(block_type, {})
    if not isinstance(block_data, dict):
        return

    # Patch rich_text segments
    for seg in block_data.get("rich_text", []):
        if "plain_text" not in seg:
            if seg.get("type") == "text":
                seg["plain_text"] = seg.get("text", {}).get("content", "")
            elif seg.get("type") == "equation":
                seg["plain_text"] = seg.get("equation", {}).get("expression", "")

    # Patch table row cells (list of rich_text arrays)
    if block_type == "table":
        for child in block_data.get("children", []):
            _patch_block(child)
        # Also patch cells in table_row children
        for child in block.get("children", []):
            _patch_block(child)
    elif block_type == "table_row":
        for cell in block_data.get("cells", []):
            for seg in cell:
                if "plain_text" not in seg:
                    if seg.get("type") == "text":
                        seg["plain_text"] = seg.get("text", {}).get("content", "")

    # Recurse into children
    children = block_data.get("children") or block.get("children")
    if children:
        for child in children:
            _patch_block(child)


@pytest.fixture
def config():
    return NotionifyConfig(token="test_token_1234")

@pytest.fixture
def converter(config):
    return MarkdownToNotionConverter(config)

@pytest.fixture
def renderer(config):
    return NotionToMarkdownRenderer(config)


class TestBasicRoundTrip:
    """Verify basic.md round-trips with semantic fidelity."""

    def test_basic_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_basic_round_trip_preserves_headings(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "# Hello World" in round_tripped
        assert "## Code Example" in round_tripped

    def test_basic_round_trip_preserves_bold_italic(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "**bold**" in round_tripped
        # The renderer uses underscores for italic (_text_) which is
        # semantically equivalent to asterisks (*text*) in Markdown.
        assert "_italic_" in round_tripped or "*italic*" in round_tripped

    def test_basic_round_trip_preserves_code_block(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "```python" in round_tripped
        assert "Hello, World!" in round_tripped

    def test_basic_round_trip_preserves_bullet_list(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Item one" in round_tripped
        assert "Item two" in round_tripped

    def test_basic_round_trip_preserves_blockquote(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "A wise blockquote" in round_tripped

    def test_basic_round_trip_preserves_ordered_list(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "First" in round_tripped
        assert "Second" in round_tripped

    def test_basic_round_trip_preserves_divider(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "---" in round_tripped


class TestComplexRoundTrip:
    """Verify complex.md round-trips with semantic fidelity."""

    def test_complex_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0

    def test_complex_preserves_inline_code(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "`inline code`" in round_tripped

    def test_complex_preserves_strikethrough(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "~~strikethrough~~" in round_tripped

    def test_complex_preserves_task_list(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Done task" in round_tripped
        assert "Todo task" in round_tripped

    def test_complex_preserves_table_content(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Name" in round_tripped
        assert "foo" in round_tripped
        assert "bar" in round_tripped

    def test_complex_preserves_nested_list_content(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Level 1" in round_tripped


class TestBlockCounts:
    """Verify block counts match expectations."""

    def test_basic_block_count(self, converter):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        # At minimum: heading, paragraph, heading, code, 3 bullets, quote, divider, 3 numbers
        assert len(result.blocks) >= 10

    def test_complex_block_count(self, converter):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 8
