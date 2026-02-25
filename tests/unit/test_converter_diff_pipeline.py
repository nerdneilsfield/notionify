"""Integration-style tests: converter → signature → planner → executor pipeline.

Verifies that Markdown conversion output feeds correctly through the diff
engine without needing a real Notion API connection.
"""
import pytest

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.diff.executor import AsyncDiffExecutor, DiffExecutor
from notionify.diff.planner import DiffPlanner
from notionify.diff.signature import compute_signature
from notionify.models import DiffOpType


def _config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


def _convert(md: str, **kwargs):
    """Convert markdown to Notion blocks."""
    converter = MarkdownToNotionConverter(_config(**kwargs))
    return converter.convert(md).blocks


def _add_ids(blocks: list[dict], prefix: str = "existing") -> list[dict]:
    """Add fake IDs to blocks so they look like existing Notion blocks."""
    for i, block in enumerate(blocks):
        block["id"] = f"{prefix}-{i}"
    return blocks


class SyncMockBlockAPI:
    """Minimal mock that records API calls."""

    def __init__(self):
        self.updates = []
        self.deletes = []
        self.appends = []
        self._counter = 0

    def update(self, block_id, payload):
        self.updates.append((block_id, payload))
        return {"id": block_id}

    def delete(self, block_id):
        self.deletes.append(block_id)
        return {"id": block_id}

    def append_children(self, parent_id, children, after=None):
        self.appends.append((parent_id, children, after))
        results = []
        for _ in children:
            results.append({"id": f"new-{self._counter}"})
            self._counter += 1
        return {"results": results}


class AsyncMockBlockAPI:
    """Async mock that records API calls."""

    def __init__(self):
        self.updates = []
        self.deletes = []
        self.appends = []
        self._counter = 0

    async def update(self, block_id, payload):
        self.updates.append((block_id, payload))
        return {"id": block_id}

    async def delete(self, block_id):
        self.deletes.append(block_id)
        return {"id": block_id}

    async def append_children(self, parent_id, children, after=None):
        self.appends.append((parent_id, children, after))
        results = []
        for _ in children:
            results.append({"id": f"new-{self._counter}"})
            self._counter += 1
        return {"results": results}


class TestConverterToDiffPipeline:
    """End-to-end: Markdown → blocks → signature → planner → executor."""

    def test_identical_markdown_produces_all_keeps(self):
        """Same markdown twice produces only KEEP operations."""
        md = "# Title\n\nParagraph one.\n\nParagraph two."
        blocks_new = _convert(md)
        blocks_existing = _add_ids(_convert(md))

        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks_new)

        assert all(op.op_type == DiffOpType.KEEP for op in ops)
        assert len(ops) == len(blocks_new)

    def test_appended_paragraph_produces_keep_plus_insert(self):
        """Adding a paragraph at the end produces KEEPs + INSERT."""
        md_old = "# Title\n\nParagraph one."
        md_new = "# Title\n\nParagraph one.\n\nParagraph two."

        blocks_existing = _add_ids(_convert(md_old))
        blocks_new = _convert(md_new)

        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks_new)

        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        inserts = [o for o in ops if o.op_type == DiffOpType.INSERT]
        assert len(keeps) == 2  # title + paragraph one
        assert len(inserts) == 1  # paragraph two

    def test_removed_paragraph_produces_keep_plus_delete(self):
        """Removing a paragraph produces KEEPs + DELETE."""
        md_old = "# Title\n\nParagraph one.\n\nParagraph two."
        md_new = "# Title\n\nParagraph one."

        blocks_existing = _add_ids(_convert(md_old))
        blocks_new = _convert(md_new)

        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks_new)

        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        deletes = [o for o in ops if o.op_type == DiffOpType.DELETE]
        assert len(keeps) == 2
        assert len(deletes) == 1

    def test_changed_paragraph_in_context(self):
        """Changing one paragraph among many produces UPDATE."""
        md_old = "# Title\n\nFirst paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        md_new = "# Title\n\nFirst paragraph.\n\nModified second.\n\nThird paragraph."

        blocks_existing = _add_ids(_convert(md_old))
        blocks_new = _convert(md_new)

        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks_new)

        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        updates = [o for o in ops if o.op_type == DiffOpType.UPDATE]
        # 3 anchors (title, first, third) out of 4 = 0.75 > 0.3 → diff strategy
        assert len(keeps) == 3
        assert len(updates) == 1
        assert updates[0].existing_id == blocks_existing[2]["id"]

    def test_type_change_produces_replace(self):
        """Changing heading to paragraph produces REPLACE."""
        md_old = "# Title\n\nParagraph one.\n\n## Subtitle\n\nParagraph two."
        md_new = "# Title\n\nParagraph one.\n\nJust a paragraph now.\n\nParagraph two."

        blocks_existing = _add_ids(_convert(md_old))
        blocks_new = _convert(md_new)

        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks_new)

        replaces = [o for o in ops if o.op_type == DiffOpType.REPLACE]
        assert len(replaces) == 1
        assert replaces[0].new_block["type"] == "paragraph"

    def test_full_pipeline_with_executor(self):
        """Run through the complete pipeline including executor."""
        md_old = "# Title\n\nOld paragraph.\n\nKeep this."
        md_new = "# Title\n\nNew paragraph.\n\nKeep this."

        blocks_existing = _add_ids(_convert(md_old))
        blocks_new = _convert(md_new)

        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks_new)

        mock_api = SyncMockBlockAPI()
        executor = DiffExecutor(mock_api, _config())
        result = executor.execute("page-123", ops)

        assert result.strategy_used == "diff"
        assert result.blocks_kept == 2  # title + "Keep this."
        assert result.blocks_inserted >= 1  # updated paragraph

    @pytest.mark.asyncio
    async def test_full_pipeline_with_async_executor(self):
        """Async variant of the full pipeline."""
        md_old = "# Title\n\nOld paragraph.\n\nKeep this."
        md_new = "# Title\n\nNew paragraph.\n\nKeep this."

        blocks_existing = _add_ids(_convert(md_old))
        blocks_new = _convert(md_new)

        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks_new)

        mock_api = AsyncMockBlockAPI()
        executor = AsyncDiffExecutor(mock_api, _config())
        result = await executor.execute("page-123", ops)

        assert result.strategy_used == "diff"
        assert result.blocks_kept == 2

    def test_complete_rewrite_on_drastically_different_content(self):
        """Totally different content triggers full overwrite strategy."""
        md_old = "# Old Title\n\nOld content here.\n\nMore old stuff."
        md_new = "## New Section\n\n```python\nprint('hello')\n```\n\n> A quote"

        blocks_existing = _add_ids(_convert(md_old))
        blocks_new = _convert(md_new)

        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks_new)

        # Match ratio should be 0 (completely different) → full overwrite
        deletes = [o for o in ops if o.op_type == DiffOpType.DELETE]
        inserts = [o for o in ops if o.op_type == DiffOpType.INSERT]
        assert len(deletes) == len(blocks_existing)
        assert len(inserts) == len(blocks_new)

    def test_signatures_are_stable_across_conversions(self):
        """Converting the same markdown twice produces identical signatures."""
        md = "# Title\n\n- Item 1\n- Item 2\n\n```python\nx = 1\n```"
        blocks1 = _convert(md)
        blocks2 = _convert(md)

        sigs1 = [compute_signature(b) for b in blocks1]
        sigs2 = [compute_signature(b) for b in blocks2]

        assert sigs1 == sigs2

    def test_complex_document_structure(self):
        """Verify pipeline handles mixed block types correctly."""
        md = """# Main Title

Introductory paragraph.

## Section One

- Bullet one
- Bullet two
- Bullet three

## Section Two

1. Numbered one
2. Numbered two

> A blockquote with **bold** text.

```javascript
console.log("hello");
```

---

Final paragraph.
"""
        blocks = _convert(md)
        # Verify all blocks have a valid type
        for block in blocks:
            assert "type" in block
            assert block["type"] in {
                "heading_1", "heading_2", "heading_3",
                "paragraph", "bulleted_list_item", "numbered_list_item",
                "quote", "code", "divider", "to_do", "toggle",
                "callout", "equation", "table", "image", "bookmark",
                "embed", "column_list",
            }

        # Should produce stable diff against itself
        blocks_existing = _add_ids(_convert(md))
        planner = DiffPlanner(_config())
        ops = planner.plan(blocks_existing, blocks)
        assert all(op.op_type == DiffOpType.KEEP for op in ops)
