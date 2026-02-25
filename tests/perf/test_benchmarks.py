"""Performance benchmarks for the notionify SDK.

Run with: pytest tests/perf/ -v -s
"""
import subprocess
import sys
import time
import tracemalloc

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
            lines.append(f"> A blockquote in section {i}\n")
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


class TestImportPerformance:
    """Benchmark package import time (NFR-9: < 500ms)."""

    def test_import_time_under_500ms(self):
        """Import 'notionify' in a fresh subprocess and check it takes < 500ms."""
        code = (
            "import time; "
            "t0 = time.perf_counter(); "
            "import notionify; "
            "elapsed = (time.perf_counter() - t0) * 1000; "
            "print(f'{elapsed:.2f}')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Import failed: {result.stderr}"
        elapsed_ms = float(result.stdout.strip())
        print(f"\n  Package import time: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 500, f"Import too slow: {elapsed_ms:.2f}ms (limit: 500ms)"

    def test_version_accessible(self):
        """Verify __version__ is accessible after import."""
        import notionify

        assert hasattr(notionify, "__version__")
        assert notionify.__version__ == "3.0.0"


def _make_1000_paragraph_markdown() -> str:
    """Generate a ~1000-block markdown document."""
    lines = ["# Performance Test Document\n"]
    for i in range(333):
        lines.append(f"## Section {i}\n")
        lines.append(f"Paragraph {i} with **bold** and *italic* and `code` text.\n")
        lines.append(f"Another paragraph with [link](https://example.com/{i}).\n")
    return "\n".join(lines)


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
        print(
            f"\n  Large doc convert (100 sections):"
            f" {elapsed_ms:.2f}ms, {len(result.blocks)} blocks"
        )
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
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": f"Text {i}"},
                            "plain_text": f"Text {i}",
                        }
                    ],
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


class TestPRDBenchmarks:
    """PRD Section 20.9 performance benchmarks."""

    def test_convert_1000_blocks_under_500ms(self):
        """Convert ~1000-block markdown in < 500ms."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        md = _make_1000_paragraph_markdown()

        start = time.perf_counter()
        result = converter.convert(md)
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  Convert ~1000 blocks: {elapsed_ms:.2f}ms, {len(result.blocks)} blocks")
        assert len(result.blocks) >= 500, f"Expected >= 500 blocks, got {len(result.blocks)}"
        assert elapsed_ms < 500, f"1000-block conversion too slow: {elapsed_ms:.2f}ms"

    def test_export_1000_blocks_under_2s(self):
        """Render ~1000 blocks back to Markdown in < 2s."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        renderer = NotionToMarkdownRenderer(config)
        md = _make_1000_paragraph_markdown()

        result = converter.convert(md)
        blocks = _simulate_notion_blocks(result.blocks)

        start = time.perf_counter()
        output = renderer.render_blocks(blocks)
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  Export {len(blocks)} blocks: {elapsed_ms:.2f}ms")
        assert len(output) > 0
        assert elapsed_ms < 2000, f"1000-block export too slow: {elapsed_ms:.2f}ms"

    def test_diff_plan_500_identical_under_200ms(self):
        """Diff plan for 500 identical blocks in < 200ms."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        planner = DiffPlanner(config)

        md = _make_large_markdown(50)
        result = converter.convert(md)
        blocks = _simulate_notion_blocks(result.blocks)

        for i, block in enumerate(blocks):
            block["id"] = f"block-{i:04d}"

        start = time.perf_counter()
        planner.plan(blocks, result.blocks)
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  Diff plan {len(blocks)} identical blocks: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 200, f"500-block diff plan too slow: {elapsed_ms:.2f}ms"


class TestMemoryUsage:
    """Memory profiling benchmarks using tracemalloc.

    Verifies that peak heap growth stays within acceptable bounds for
    typical workloads.  Limits are deliberately generous to avoid
    flakiness across platforms; the intent is to catch regressions
    that cause order-of-magnitude increases.
    """

    def test_small_document_peak_memory_under_5mb(self):
        """Converting a small document should not allocate more than 5 MiB."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        md = "# Hello\n\nThis is a **bold** paragraph.\n\n- a\n- b\n- c\n"

        tracemalloc.start()
        tracemalloc.clear_traces()
        converter.convert(md)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        print(f"\n  Small doc peak memory: {peak_mb:.2f} MiB")
        assert peak_mb < 5, f"Small doc used too much memory: {peak_mb:.2f} MiB"

    def test_large_document_peak_memory_under_50mb(self):
        """Converting a 100-section document should stay under 50 MiB peak."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        md = _make_large_markdown(100)

        tracemalloc.start()
        tracemalloc.clear_traces()
        result = converter.convert(md)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        print(
            f"\n  Large doc ({len(result.blocks)} blocks) peak memory: {peak_mb:.2f} MiB"
        )
        assert peak_mb < 50, f"Large doc used too much memory: {peak_mb:.2f} MiB"

    def test_renderer_peak_memory_under_20mb(self):
        """Rendering 100 blocks back to Markdown should stay under 20 MiB peak."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        renderer = NotionToMarkdownRenderer(config)

        md = _make_large_markdown(20)
        result = converter.convert(md)
        blocks = _simulate_notion_blocks(result.blocks)

        tracemalloc.start()
        tracemalloc.clear_traces()
        output = renderer.render_blocks(blocks)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        print(f"\n  Renderer ({len(blocks)} blocks) peak memory: {peak_mb:.2f} MiB")
        assert len(output) > 0
        assert peak_mb < 20, f"Renderer used too much memory: {peak_mb:.2f} MiB"

    def test_diff_plan_peak_memory_under_10mb(self):
        """Diff planning 200 blocks should use under 10 MiB peak."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        planner = DiffPlanner(config)

        md = _make_large_markdown(20)
        result = converter.convert(md)
        blocks = _simulate_notion_blocks(result.blocks)
        for i, block in enumerate(blocks):
            block["id"] = f"block-{i:04d}"

        tracemalloc.start()
        tracemalloc.clear_traces()
        planner.plan(blocks, result.blocks)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        print(f"\n  Diff plan ({len(blocks)} blocks) peak memory: {peak_mb:.2f} MiB")
        assert peak_mb < 10, f"Diff plan used too much memory: {peak_mb:.2f} MiB"
