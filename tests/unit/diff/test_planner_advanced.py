"""Advanced diff planner tests for complex interleaving and upgrade logic.

Covers: multiple unmatched blocks between anchors, blocks before first and
after last anchor, DELETE+INSERT upgrade to UPDATE/REPLACE, full-overwrite
fallback threshold, and async executor mirror operations.
"""
import pytest

from notionify.config import NotionifyConfig
from notionify.diff.planner import DiffPlanner
from notionify.diff.executor import DiffExecutor, AsyncDiffExecutor
from notionify.models import DiffOp, DiffOpType


def _para(text: str, block_id: str | None = None) -> dict:
    block = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
            "color": "default",
        },
    }
    if block_id:
        block["id"] = block_id
    return block


def _heading(text: str, block_id: str | None = None) -> dict:
    block = {
        "type": "heading_1",
        "heading_1": {
            "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
            "color": "default",
            "is_toggleable": False,
        },
    }
    if block_id:
        block["id"] = block_id
    return block


def _code(text: str, block_id: str | None = None) -> dict:
    block = {
        "type": "code",
        "code": {
            "rich_text": [{"type": "text", "text": {"content": text}, "plain_text": text}],
            "language": "python",
        },
    }
    if block_id:
        block["id"] = block_id
    return block


class TestPlannerInterleaving:
    """Test complex interleaving patterns in _build_ops."""

    def _plan(self, existing, new):
        planner = DiffPlanner(NotionifyConfig(token="test"))
        return planner.plan(existing, new)

    def test_multiple_deletes_between_anchors(self):
        """Multiple existing blocks between two matching anchors should all be deleted."""
        existing = [
            _para("A", "e1"),  # anchor
            _para("X", "e2"),  # delete
            _para("Y", "e3"),  # delete
            _para("Z", "e4"),  # delete
            _para("B", "e5"),  # anchor
        ]
        new = [_para("A"), _para("B")]

        ops = self._plan(existing, new)
        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        deletes = [o for o in ops if o.op_type == DiffOpType.DELETE]
        assert len(keeps) == 2  # A and B matched
        assert len(deletes) == 3  # X, Y, Z deleted

    def test_multiple_inserts_between_anchors(self):
        """Multiple new blocks between two matching anchors should all be inserted."""
        existing = [_para("A", "e1"), _para("B", "e2")]
        new = [_para("A"), _para("X"), _para("Y"), _para("Z"), _para("B")]

        ops = self._plan(existing, new)
        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        inserts = [o for o in ops if o.op_type == DiffOpType.INSERT]
        assert len(keeps) == 2
        assert len(inserts) == 3

    def test_blocks_before_first_anchor(self):
        """Unmatched blocks before the first anchor: existing ones deleted, new ones inserted."""
        existing = [
            _para("X", "e0"),  # unmatched existing → delete
            _para("A", "e1"),  # anchor
        ]
        new = [
            _para("Y"),  # unmatched new → insert
            _para("A"),  # anchor
        ]

        ops = self._plan(existing, new)
        op_types = [o.op_type for o in ops]
        # Should have a delete for X, insert for Y (or upgrade to UPDATE/REPLACE), and KEEP for A
        assert DiffOpType.KEEP in op_types
        total_writes = sum(
            1 for o in ops if o.op_type in (DiffOpType.DELETE, DiffOpType.INSERT,
                                             DiffOpType.UPDATE, DiffOpType.REPLACE)
        )
        assert total_writes >= 1

    def test_blocks_after_last_anchor(self):
        """Unmatched blocks after the last anchor."""
        existing = [
            _para("A", "e1"),  # anchor
            _para("X", "e2"),  # unmatched → delete
        ]
        new = [
            _para("A"),  # anchor
            _para("Y"),  # unmatched → insert
        ]

        ops = self._plan(existing, new)
        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        assert len(keeps) == 1  # A
        # X→Y should become UPDATE (same type) or DELETE+INSERT
        non_keeps = [o for o in ops if o.op_type != DiffOpType.KEEP]
        assert len(non_keeps) >= 1

    def test_mixed_before_and_after_anchor(self):
        """With only 1 anchor out of 4 blocks, match ratio 0.25 < 0.3 triggers full overwrite."""
        existing = [
            _para("X1", "e0"),
            _para("X2", "e1"),
            _para("A", "e2"),  # would be sole anchor
            _para("X3", "e3"),
        ]
        new = [
            _para("Y1"),
            _para("A"),  # would match
            _para("Y2"),
            _para("Y3"),
        ]

        ops = self._plan(existing, new)
        # Match ratio 1/4 = 0.25 < 0.3 → full overwrite
        deletes = [o for o in ops if o.op_type == DiffOpType.DELETE]
        inserts = [o for o in ops if o.op_type == DiffOpType.INSERT]
        assert len(deletes) == 4
        assert len(inserts) == 4

    def test_mixed_before_and_after_with_sufficient_anchors(self):
        """With enough anchors to exceed match ratio, diff logic applies."""
        existing = [
            _para("X1", "e0"),      # unmatched
            _para("A", "e1"),       # anchor 1
            _para("B", "e2"),       # anchor 2
            _para("C", "e3"),       # anchor 3
            _para("X2", "e4"),      # unmatched
        ]
        new = [
            _para("Y1"),            # unmatched
            _para("A"),             # anchor 1
            _para("B"),             # anchor 2
            _para("C"),             # anchor 3
            _para("Y2"),            # unmatched
        ]

        ops = self._plan(existing, new)
        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        assert len(keeps) == 3  # A, B, C


class TestUpgradeToUpdates:
    """Test the DELETE+INSERT → UPDATE/REPLACE upgrade logic.

    The upgrade logic in _upgrade_to_updates only runs when the diff path
    is used (match ratio >= 0.3). Tests must include enough matching anchors.
    """

    def _plan(self, existing, new):
        planner = DiffPlanner(NotionifyConfig(token="test"))
        return planner.plan(existing, new)

    def test_same_type_change_becomes_update(self):
        """Changing paragraph text with enough anchors should produce UPDATE."""
        existing = [
            _para("unchanged-1", "e0"),  # anchor
            _para("old text", "e1"),     # changed
            _para("unchanged-2", "e2"),  # anchor
        ]
        new = [
            _para("unchanged-1"),
            _para("new text"),           # changed same type
            _para("unchanged-2"),
        ]

        ops = self._plan(existing, new)
        updates = [o for o in ops if o.op_type == DiffOpType.UPDATE]
        assert len(updates) == 1
        assert updates[0].existing_id == "e1"

    def test_different_type_becomes_replace(self):
        """Changing block type with enough anchors should produce REPLACE."""
        existing = [
            _para("unchanged-1", "e0"),  # anchor
            _para("some text", "e1"),    # will be replaced
            _para("unchanged-2", "e2"),  # anchor
        ]
        new = [
            _para("unchanged-1"),
            _heading("some text"),       # different type
            _para("unchanged-2"),
        ]

        ops = self._plan(existing, new)
        replaces = [o for o in ops if o.op_type == DiffOpType.REPLACE]
        assert len(replaces) == 1
        assert replaces[0].existing_id == "e1"

    def test_multiple_changes_in_gap(self):
        """Multiple unmatched blocks in a gap: DELETEs emitted first, then INSERTs.

        The upgrade logic only pairs the boundary DELETE+INSERT, so with 3
        changed blocks in a gap, we get: 2 DELETEs + 1 UPDATE + 2 INSERTs.
        """
        existing = [
            _para("anchor-1", "e0"),  # anchor
            _para("A", "e1"),         # changed
            _para("B", "e2"),         # changed
            _para("C", "e3"),         # changed
            _para("anchor-2", "e4"),  # anchor
        ]
        new = [
            _para("anchor-1"),
            _para("A-modified"),
            _para("B-modified"),
            _para("C-modified"),
            _para("anchor-2"),
        ]

        ops = self._plan(existing, new)
        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        updates = [o for o in ops if o.op_type == DiffOpType.UPDATE]
        deletes = [o for o in ops if o.op_type == DiffOpType.DELETE]
        inserts = [o for o in ops if o.op_type == DiffOpType.INSERT]
        assert len(keeps) == 2  # anchor-1, anchor-2
        # The boundary DELETE+INSERT pair becomes an UPDATE
        assert len(updates) == 1
        # Remaining are unpaired deletes and inserts
        assert len(deletes) == 2
        assert len(inserts) == 2

    def test_single_change_in_gap_fully_upgraded(self):
        """A single changed block between anchors produces exactly one UPDATE."""
        existing = [
            _para("anchor-1", "e0"),
            _para("old", "e1"),
            _para("anchor-2", "e2"),
        ]
        new = [
            _para("anchor-1"),
            _para("new"),
            _para("anchor-2"),
        ]

        ops = self._plan(existing, new)
        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        updates = [o for o in ops if o.op_type == DiffOpType.UPDATE]
        assert len(keeps) == 2
        assert len(updates) == 1

    def test_low_match_ratio_skips_upgrade(self):
        """When match ratio < 0.3, full overwrite is used (no upgrade)."""
        existing = [_para("old text", "e1")]
        new = [_para("new text")]

        ops = self._plan(existing, new)
        # 0 matches / 1 = 0.0 < 0.3 → full overwrite → DELETE + INSERT
        assert len(ops) == 2
        assert ops[0].op_type == DiffOpType.DELETE
        assert ops[1].op_type == DiffOpType.INSERT


class TestFullOverwriteFallback:
    """Test the match ratio threshold for full overwrite."""

    def test_low_match_ratio_triggers_overwrite(self):
        """When match ratio < 0.3, planner falls back to full overwrite."""
        # 10 completely different existing blocks
        existing = [_para(f"old-{i}", f"e{i}") for i in range(10)]
        # 10 completely different new blocks
        new = [_heading(f"new-{i}") for i in range(10)]

        planner = DiffPlanner(NotionifyConfig(token="test"))
        ops = planner.plan(existing, new)

        # Full overwrite: 10 deletes + 10 inserts
        deletes = [o for o in ops if o.op_type == DiffOpType.DELETE]
        inserts = [o for o in ops if o.op_type == DiffOpType.INSERT]
        assert len(deletes) == 10
        assert len(inserts) == 10

    def test_high_match_ratio_uses_diff(self):
        """When match ratio is high, planner uses diff strategy."""
        # 10 blocks, 8 identical
        existing = [_para(f"text-{i}", f"e{i}") for i in range(10)]
        new = [_para(f"text-{i}") for i in range(10)]
        new[0] = _para("changed-0")
        new[9] = _para("changed-9")

        planner = DiffPlanner(NotionifyConfig(token="test"))
        ops = planner.plan(existing, new)

        keeps = [o for o in ops if o.op_type == DiffOpType.KEEP]
        assert len(keeps) >= 6  # most blocks should be kept


class TestAsyncExecutorMirror:
    """Verify the async executor mirrors sync executor behavior."""

    @pytest.fixture()
    def mock_api(self):
        class AsyncMockBlockAPI:
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

        return AsyncMockBlockAPI()

    @pytest.mark.asyncio
    async def test_async_keep_and_delete(self, mock_api):
        config = NotionifyConfig(token="test")
        executor = AsyncDiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.KEEP, existing_id="blk-1"),
            DiffOp(op_type=DiffOpType.DELETE, existing_id="blk-2"),
        ]
        result = await executor.execute("page-1", ops)
        assert result.blocks_kept == 1
        assert result.blocks_deleted == 1
        assert "blk-2" in mock_api.deletes

    @pytest.mark.asyncio
    async def test_async_update(self, mock_api):
        config = NotionifyConfig(token="test")
        executor = AsyncDiffExecutor(mock_api, config)
        ops = [
            DiffOp(
                op_type=DiffOpType.UPDATE,
                existing_id="blk-1",
                new_block=_para("updated"),
            )
        ]
        result = await executor.execute("page-1", ops)
        assert len(mock_api.updates) == 1
        assert result.blocks_inserted == 1

    @pytest.mark.asyncio
    async def test_async_replace(self, mock_api):
        config = NotionifyConfig(token="test")
        executor = AsyncDiffExecutor(mock_api, config)
        ops = [
            DiffOp(
                op_type=DiffOpType.REPLACE,
                existing_id="blk-old",
                new_block=_para("replacement"),
            )
        ]
        result = await executor.execute("page-1", ops)
        assert "blk-old" in mock_api.deletes
        assert len(mock_api.appends) == 1
        assert result.blocks_replaced == 1

    @pytest.mark.asyncio
    async def test_async_consecutive_inserts(self, mock_api):
        config = NotionifyConfig(token="test")
        executor = AsyncDiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para(f"p{i}"))
            for i in range(5)
        ]
        result = await executor.execute("page-1", ops)
        assert result.blocks_inserted == 5
        assert len(mock_api.appends) == 1


class TestSyncExecutor:
    """Verify the sync DiffExecutor edge cases."""

    @pytest.fixture()
    def mock_api(self):
        class SyncMockBlockAPI:
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

        return SyncMockBlockAPI()

    def test_empty_ops_returns_zero_counts(self, mock_api):
        """Empty operation list produces zero counts."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        result = executor.execute("page-1", [])
        assert result.blocks_kept == 0
        assert result.blocks_inserted == 0
        assert result.blocks_deleted == 0
        assert result.blocks_replaced == 0
        assert result.strategy_used == "diff"

    def test_keep_updates_last_block_id(self, mock_api):
        """KEEP operations advance last_block_id for correct insert positioning."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.KEEP, existing_id="blk-1"),
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para("new")),
        ]
        executor.execute("page-1", ops)
        # INSERT should be positioned after blk-1
        assert mock_api.appends[0][2] == "blk-1"  # after=blk-1

    def test_replace_tracks_new_block_id(self, mock_api):
        """REPLACE updates last_block_id from the newly inserted block."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(
                op_type=DiffOpType.REPLACE,
                existing_id="old-1",
                new_block=_para("replacement"),
            ),
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para("after-replace")),
        ]
        executor.execute("page-1", ops)
        assert "old-1" in mock_api.deletes
        # Second append (the INSERT) should be after the replace's new block
        assert mock_api.appends[1][2] == "new-0"

    def test_update_with_none_existing_id_skips_api_call(self, mock_api):
        """UPDATE with None existing_id skips the API call but still advances."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.UPDATE, existing_id=None, new_block=_para("x")),
        ]
        result = executor.execute("page-1", ops)
        assert len(mock_api.updates) == 0
        assert result.blocks_inserted == 1  # still counted as write

    def test_update_with_none_new_block_skips_api_call(self, mock_api):
        """UPDATE with None new_block skips the API call."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.UPDATE, existing_id="blk-1", new_block=None),
        ]
        result = executor.execute("page-1", ops)
        assert len(mock_api.updates) == 0

    def test_delete_with_none_existing_id(self, mock_api):
        """DELETE with None existing_id skips the API call but counts it."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.DELETE, existing_id=None),
        ]
        result = executor.execute("page-1", ops)
        assert len(mock_api.deletes) == 0
        assert result.blocks_deleted == 1  # still counted

    def test_replace_with_none_new_block(self, mock_api):
        """REPLACE with None new_block still deletes the existing block."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id="old-1", new_block=None),
        ]
        result = executor.execute("page-1", ops)
        assert "old-1" in mock_api.deletes
        assert result.blocks_deleted == 1
        assert result.blocks_replaced == 0  # no insert happened

    def test_insert_with_none_new_block_skipped(self, mock_api):
        """INSERT ops with None new_block are silently filtered out."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.INSERT, new_block=None),
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para("real")),
        ]
        result = executor.execute("page-1", ops)
        assert result.blocks_inserted == 1
        assert len(mock_api.appends) == 1

    def test_append_response_without_results_key(self, mock_api):
        """If append_children returns no 'results', last_block_id stays None."""
        mock_api.append_children = lambda pid, ch, after=None: {}
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para("a")),
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para("b")),
        ]
        result = executor.execute("page-1", ops)
        assert result.blocks_inserted == 2

    def test_mixed_ops_sequence(self, mock_api):
        """Full scenario: KEEP → UPDATE → DELETE → INSERT → REPLACE."""
        config = NotionifyConfig(token="test")
        executor = DiffExecutor(mock_api, config)
        ops = [
            DiffOp(op_type=DiffOpType.KEEP, existing_id="blk-1"),
            DiffOp(op_type=DiffOpType.UPDATE, existing_id="blk-2", new_block=_para("updated")),
            DiffOp(op_type=DiffOpType.DELETE, existing_id="blk-3"),
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para("inserted")),
            DiffOp(op_type=DiffOpType.REPLACE, existing_id="blk-4", new_block=_heading("title")),
        ]
        result = executor.execute("page-1", ops)
        assert result.blocks_kept == 1
        assert result.blocks_inserted == 2  # UPDATE + INSERT both counted
        assert result.blocks_deleted == 2  # DELETE + REPLACE's delete
        assert result.blocks_replaced == 1


class TestPlannerEdgeCases:
    """Test edge cases in planner logic."""

    def _plan(self, existing, new):
        planner = DiffPlanner(NotionifyConfig(token="test"))
        return planner.plan(existing, new)

    def test_existing_blocks_missing_id_field(self):
        """Blocks without 'id' should produce ops with existing_id=None."""
        existing = [
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "A"}], "color": "default"}},
        ]
        new = []
        ops = self._plan(existing, new)
        assert len(ops) == 1
        assert ops[0].op_type == DiffOpType.DELETE
        assert ops[0].existing_id is None

    def test_block_type_by_id_returns_none_for_unknown_id(self):
        """_block_type_by_id with unknown ID returns None."""
        result = DiffPlanner._block_type_by_id(
            [_para("A", "e1")], "nonexistent-id"
        )
        assert result is None

    def test_block_type_by_id_returns_none_for_none(self):
        """_block_type_by_id with None ID returns None."""
        result = DiffPlanner._block_type_by_id([_para("A", "e1")], None)
        assert result is None

    def test_upgrade_delete_insert_with_mismatched_types(self):
        """DELETE paragraph + INSERT heading → REPLACE (type differs)."""
        existing = [
            _para("anchor-1", "e0"),
            _para("text", "e1"),
            _para("anchor-2", "e2"),
        ]
        new = [
            _para("anchor-1"),
            _heading("text"),  # different type
            _para("anchor-2"),
        ]
        ops = self._plan(existing, new)
        replaces = [o for o in ops if o.op_type == DiffOpType.REPLACE]
        assert len(replaces) == 1
        assert replaces[0].existing_id == "e1"
        assert replaces[0].new_block["type"] == "heading_1"

    def test_full_overwrite_preserves_block_order(self):
        """Full overwrite produces DELETEs before INSERTs in order."""
        existing = [_para("A", "e1"), _para("B", "e2")]
        new = [_heading("X"), _heading("Y")]
        ops = self._plan(existing, new)
        deletes = [o for o in ops if o.op_type == DiffOpType.DELETE]
        inserts = [o for o in ops if o.op_type == DiffOpType.INSERT]
        # All deletes come before all inserts
        last_delete_idx = max(i for i, o in enumerate(ops) if o.op_type == DiffOpType.DELETE)
        first_insert_idx = min(i for i, o in enumerate(ops) if o.op_type == DiffOpType.INSERT)
        assert last_delete_idx < first_insert_idx
        assert deletes[0].existing_id == "e1"
        assert deletes[1].existing_id == "e2"
