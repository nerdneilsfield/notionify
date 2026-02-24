"""Performance benchmarks for the notionify SDK.

Run with: pytest tests/perf/ -v -s
"""
import time
from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer
from notionify.diff.planner import DiffPlanner
from notionify.diff.signature import compute_signature


def _make_large_markdown(n_paragraphs: int = 100) -> str:
    """Generate a large markdown document."""
    lines = ["# Large Document\n"]
    for i in range(n_paragraphs):
        lines.append(f"## Section {i}\n")
        lines.append(f"This is paragraph {i} with **bold** and *italic* text.\n")
        lines.append(f"- Item {i}a\n- Item {i}b\n- Item {i}c\n")
        if i % 5 == 0:
            lines.append(f"```python\ndef func_{i}():\n    return {i}\n```\n")
        if i % 10 == 0:
            lines.append("> A blockquote in section {}\n".format(i))
    return "\n".join(lines)


def _simulate_notion_blocks(blocks: list[dict]) -> list[dict]:
    """Add plain_text fields to blocks to simulate API response format."""
    import copy
    result = copy.deepcopy(blocks)
    for block in result:
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})
        if isinstance(block_data, dict):
            for seg in block_data.get("rich_text", []):
                if "text" in seg and "plain_text" not in seg:
                    seg["plain_text"] = seg["text"].get("content", "")
            # Add fake block id for signatures
            block["id"] = f"block-{id(block)}"
    return result


class TestConverterPerformance:
    """Benchmark the Markdown -> Notion converter."""

    def test_small_document_under_50ms(self):
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        md = "# Hello\n\nWorld with **bold** text.\n\n- a\n- b\n"

        start = time.perf_counter()
        for _ in range(100):
            converter.convert(md)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 100) * 1000
        print(f"\n  Small doc convert: {avg_ms:.2f}ms avg")
        assert avg_ms < 50, f"Small document conversion too slow: {avg_ms:.2f}ms"

    def test_medium_document_under_200ms(self):
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        md = _make_large_markdown(20)

        start = time.perf_counter()
        for _ in range(10):
            converter.convert(md)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 10) * 1000
        print(f"\n  Medium doc convert (20 sections): {avg_ms:.2f}ms avg")
        assert avg_ms < 200, f"Medium document conversion too slow: {avg_ms:.2f}ms"

    def test_large_document_under_1s(self):
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        md = _make_large_markdown(100)

        start = time.perf_counter()
        result = converter.convert(md)
        elapsed = time.perf_counter() - start

        elapsed_ms = elapsed * 1000
        print(f"\n  Large doc convert (100 sections): {elapsed_ms:.2f}ms, {len(result.blocks)} blocks")
        assert elapsed_ms < 1000, f"Large document conversion too slow: {elapsed_ms:.2f}ms"


class TestRendererPerformance:
    """Benchmark the Notion -> Markdown renderer."""

    def test_render_100_blocks_under_100ms(self):
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        renderer = NotionToMarkdownRenderer(config)

        md = _make_large_markdown(20)
        result = converter.convert(md)
        blocks = _simulate_notion_blocks(result.blocks)

        start = time.perf_counter()
        for _ in range(10):
            renderer.render_blocks(blocks)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 10) * 1000
        print(f"\n  Render {len(blocks)} blocks: {avg_ms:.2f}ms avg")
        assert avg_ms < 100, f"Rendering too slow: {avg_ms:.2f}ms"


class TestDiffPerformance:
    """Benchmark the diff planner."""

    def test_identical_blocks_diff_under_50ms(self):
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        planner = DiffPlanner(config)

        md = _make_large_markdown(20)
        result = converter.convert(md)
        blocks = _simulate_notion_blocks(result.blocks)

        # Add IDs to simulate existing blocks
        for i, block in enumerate(blocks):
            block["id"] = f"block-{i:04d}"

        start = time.perf_counter()
        for _ in range(10):
            planner.plan(blocks, result.blocks)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 10) * 1000
        print(f"\n  Diff identical {len(blocks)} blocks: {avg_ms:.2f}ms avg")
        assert avg_ms < 50, f"Diff of identical blocks too slow: {avg_ms:.2f}ms"

    def test_signature_computation_under_10ms(self):
        blocks = [
            {
                "id": f"block-{i}",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"Text {i}"}, "plain_text": f"Text {i}"}],
                    "color": "default",
                },
            }
            for i in range(100)
        ]

        start = time.perf_counter()
        for _ in range(10):
            for block in blocks:
                compute_signature(block)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 10) * 1000
        print(f"\n  Compute 100 signatures: {avg_ms:.2f}ms avg")
        assert avg_ms < 10, f"Signature computation too slow: {avg_ms:.2f}ms"
