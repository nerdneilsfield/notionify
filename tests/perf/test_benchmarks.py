"""Performance benchmarks for the notionify SDK.

Run with: pytest tests/perf/ -v -s
"""
import asyncio
import subprocess
import sys
import time
import tracemalloc
from unittest.mock import AsyncMock, MagicMock

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer
from notionify.diff.executor import DiffExecutor
from notionify.diff.planner import DiffPlanner
from notionify.diff.signature import compute_signature
from notionify.image.upload_single import async_upload_single
from notionify.models import DiffOp, DiffOpType


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
        """Import 'notionify' in a fresh subprocess and check it takes < 500ms.

        Takes the best of 3 runs to reduce flakiness from system load spikes.
        """
        code = (
            "import time; "
            "t0 = time.perf_counter(); "
            "import notionify; "
            "elapsed = (time.perf_counter() - t0) * 1000; "
            "print(f'{elapsed:.2f}')"
        )
        times = []
        for _ in range(3):
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"Import failed: {result.stderr}"
            times.append(float(result.stdout.strip()))
        best_ms = min(times)
        print(f"\n  Package import times: {times} best={best_ms:.2f}ms")
        assert best_ms < 500, f"Import too slow: best {best_ms:.2f}ms of {times} (limit: 500ms)"

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

    def test_diff_execute_500_blocks_10_changes_under_3s(self):
        """Execute 10 UPDATE ops on a 500-block page in < 3s (mocked API)."""
        config = NotionifyConfig(token="test")

        # Build a mock block_api whose methods return instantly.
        mock_block_api = MagicMock()
        mock_block_api.update.return_value = {"id": "updated", "type": "paragraph"}
        mock_block_api.delete.return_value = {"id": "deleted", "archived": True}
        mock_block_api.append_children.return_value = {
            "results": [{"id": "new-block-0001"}],
        }

        executor = DiffExecutor(mock_block_api, config)

        # Build 500 ops: 10 UPDATE ops scattered among 490 KEEP ops.
        ops: list[DiffOp] = []
        update_indices = set(range(0, 500, 50))  # indices 0, 50, 100, ...
        for i in range(500):
            block_id = f"block-{i:04d}"
            if i in update_indices:
                ops.append(DiffOp(
                    op_type=DiffOpType.UPDATE,
                    existing_id=block_id,
                    new_block={
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{
                                "type": "text",
                                "text": {"content": f"Updated text {i}"},
                            }],
                            "color": "default",
                        },
                    },
                ))
            else:
                ops.append(DiffOp(
                    op_type=DiffOpType.KEEP,
                    existing_id=block_id,
                ))

        start = time.perf_counter()
        result = executor.execute("page-id-0000", ops)
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(
            f"\n  Diff execute 500 blocks (10 updates): {elapsed_ms:.2f}ms, "
            f"kept={result.blocks_kept}, inserted={result.blocks_inserted}"
        )
        assert result.blocks_kept == 490
        assert result.blocks_inserted == 10  # UPDATE increments inserted counter
        assert mock_block_api.update.call_count == 10
        assert elapsed_ms < 3000, (
            f"Diff execute 500 blocks too slow: {elapsed_ms:.2f}ms (limit: 3000ms)"
        )

    def test_async_upload_20_images_under_5s(self):
        """Upload 20 images concurrently via mocked async pipeline in < 5s."""

        async def _run_concurrent_uploads() -> list[str]:
            # Build a mock async file_api.
            mock_file_api = AsyncMock()

            # create_upload returns a dict with an id and upload_url.
            call_count = 0

            async def _mock_create_upload(**kwargs):
                nonlocal call_count
                call_count += 1
                return {
                    "id": f"upload-{call_count:04d}",
                    "upload_url": f"https://mock.notion.so/upload/{call_count}",
                }

            mock_file_api.create_upload.side_effect = _mock_create_upload
            # send_part returns None (just uploads bytes).
            mock_file_api.send_part.return_value = None

            # Create 20 fake image payloads (1 KB each).
            fake_data = b"\x89PNG" + b"\x00" * 1020

            tasks = [
                asyncio.create_task(
                    async_upload_single(
                        mock_file_api,
                        name=f"image_{i:02d}.png",
                        content_type="image/png",
                        data=fake_data,
                    )
                )
                for i in range(20)
            ]
            return await asyncio.gather(*tasks)

        start = time.perf_counter()
        upload_ids = asyncio.run(_run_concurrent_uploads())
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n  Async upload 20 images: {elapsed_ms:.2f}ms, ids={len(upload_ids)}")
        assert len(upload_ids) == 20
        # All upload IDs should be unique.
        assert len(set(upload_ids)) == 20
        assert elapsed_ms < 5000, (
            f"Async 20-image upload too slow: {elapsed_ms:.2f}ms (limit: 5000ms)"
        )


class TestNFRRequirements:
    """Verify non-functional requirements from PRD Section 7."""

    def test_nfr3_deterministic_output(self):
        """NFR-3: Same input + config must produce identical output."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        md = (
            "# Title\n\n"
            "Paragraph with **bold**, *italic*, `code`, and [link](https://x.com).\n\n"
            "- item 1\n- item 2\n  - nested\n\n"
            "```python\nprint('hello')\n```\n\n"
            "> blockquote\n\n"
            "---\n"
        )

        results = [converter.convert(md) for _ in range(5)]
        # All runs must produce identical block structures
        baseline = results[0].blocks
        for i, r in enumerate(results[1:], 1):
            assert r.blocks == baseline, f"Run {i} produced different output"

    def test_nfr3_renderer_deterministic(self):
        """NFR-3: Same blocks → same Markdown on every run."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        renderer = NotionToMarkdownRenderer(config)

        md = "# Hello\n\nWorld with **bold** and *italic*.\n\n- a\n- b\n"
        result = converter.convert(md)
        blocks = _simulate_notion_blocks(result.blocks)

        outputs = [renderer.render_blocks(blocks) for _ in range(5)]
        for i, output in enumerate(outputs[1:], 1):
            assert output == outputs[0], f"Render run {i} produced different output"

    def test_nfr8_package_size_under_5mb(self):
        """NFR-8: Installed package size must be < 5 MB."""
        import pathlib

        pkg_dir = pathlib.Path(__file__).resolve().parents[2] / "src" / "notionify"
        total_size = sum(
            f.stat().st_size for f in pkg_dir.rglob("*") if f.is_file()
        )
        total_mb = total_size / (1024 * 1024)
        print(f"\n  Package size: {total_mb:.2f} MiB")
        assert total_mb < 5, f"Package too large: {total_mb:.2f} MiB (limit: 5 MiB)"


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

    def test_diff_plan_500_differing_blocks_peak_memory_under_30mb(self):
        """Diff planning 500 differing blocks should use under 30 MiB peak."""
        config = NotionifyConfig(token="test")
        converter = MarkdownToNotionConverter(config)
        planner = DiffPlanner(config)

        md_old = _make_large_markdown(50)
        md_new = _make_large_markdown(55)
        result_old = converter.convert(md_old)
        result_new = converter.convert(md_new)
        blocks_old = _simulate_notion_blocks(result_old.blocks)
        for i, block in enumerate(blocks_old):
            block["id"] = f"block-{i:04d}"

        tracemalloc.start()
        tracemalloc.clear_traces()
        planner.plan(blocks_old, result_new.blocks)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        print(f"\n  Diff plan 500 differing blocks peak memory: {peak_mb:.2f} MiB")
        assert peak_mb < 30, f"Diff plan used too much memory: {peak_mb:.2f} MiB"
