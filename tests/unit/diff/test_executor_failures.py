"""Tests for DiffExecutor and AsyncDiffExecutor error handling.

Tests exception propagation on API failures, conditional skip branches
(missing existing_id / new_block), and last_block_id state tracking.

PRD hardening: diff executor resilience, iteration 16.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notionify.config import NotionifyConfig
from notionify.diff.executor import AsyncDiffExecutor, DiffExecutor
from notionify.models import DiffOp, DiffOpType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _para() -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": []}}


def _config() -> NotionifyConfig:
    return NotionifyConfig(token="test-token")


def _make_sync_api(
    append_return: dict | None = None,
) -> MagicMock:
    api = MagicMock()
    api.update.return_value = {"id": "blk-1"}
    api.delete.return_value = {"id": "blk-1"}
    api.append_children.return_value = append_return or {
        "results": [{"id": "new-1"}]
    }
    return api


def _make_async_api(
    append_return: dict | None = None,
) -> MagicMock:
    api = MagicMock()
    api.update = AsyncMock(return_value={"id": "blk-1"})
    api.delete = AsyncMock(return_value={"id": "blk-1"})
    api.append_children = AsyncMock(
        return_value=append_return or {"results": [{"id": "new-1"}]}
    )
    return api


# =========================================================================
# Sync executor: exception propagation
# =========================================================================


class TestSyncExecutorExceptions:
    """Exceptions from the block API propagate out of execute()."""

    def test_update_api_error_propagates(self):
        api = _make_sync_api()
        api.update.side_effect = RuntimeError("Update failed")
        ops = [DiffOp(op_type=DiffOpType.UPDATE, existing_id="b1", new_block=_para())]
        with pytest.raises(RuntimeError, match="Update failed"):
            DiffExecutor(api, _config()).execute("page-1", ops)

    def test_delete_api_error_propagates(self):
        api = _make_sync_api()
        api.delete.side_effect = RuntimeError("Delete failed")
        ops = [DiffOp(op_type=DiffOpType.DELETE, existing_id="b1")]
        with pytest.raises(RuntimeError, match="Delete failed"):
            DiffExecutor(api, _config()).execute("page-1", ops)

    def test_insert_api_error_propagates(self):
        api = _make_sync_api()
        api.append_children.side_effect = RuntimeError("Append failed")
        ops = [DiffOp(op_type=DiffOpType.INSERT, new_block=_para())]
        with pytest.raises(RuntimeError, match="Append failed"):
            DiffExecutor(api, _config()).execute("page-1", ops)

    def test_replace_delete_error_propagates(self):
        api = _make_sync_api()
        api.delete.side_effect = RuntimeError("Delete in replace failed")
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id="b1", new_block=_para())
        ]
        with pytest.raises(RuntimeError, match="Delete in replace failed"):
            DiffExecutor(api, _config()).execute("page-1", ops)

    def test_replace_insert_error_propagates_after_delete(self):
        """Delete succeeds but append raises - orphaned block is deleted."""
        api = _make_sync_api()
        api.delete.return_value = {"id": "b1"}
        api.append_children.side_effect = RuntimeError("Append in replace failed")
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id="b1", new_block=_para())
        ]
        with pytest.raises(RuntimeError, match="Append in replace failed"):
            DiffExecutor(api, _config()).execute("page-1", ops)
        # Delete was called before the failure
        api.delete.assert_called_once_with("b1")


# =========================================================================
# Sync executor: conditional skip branches
# =========================================================================


class TestSyncExecutorSkipBranches:
    """Verify operations are skipped when required fields are absent."""

    def test_update_missing_existing_id_skips_api_call(self):
        api = _make_sync_api()
        ops = [DiffOp(op_type=DiffOpType.UPDATE, existing_id=None, new_block=_para())]
        result = DiffExecutor(api, _config()).execute("page-1", ops)
        api.update.assert_not_called()
        assert result.blocks_inserted == 1  # still counts as a write

    def test_update_missing_new_block_skips_api_call(self):
        api = _make_sync_api()
        ops = [DiffOp(op_type=DiffOpType.UPDATE, existing_id="b1", new_block=None)]
        result = DiffExecutor(api, _config()).execute("page-1", ops)
        api.update.assert_not_called()
        assert result.blocks_inserted == 1

    def test_delete_missing_existing_id_skips_api_call(self):
        api = _make_sync_api()
        ops = [DiffOp(op_type=DiffOpType.DELETE, existing_id=None)]
        result = DiffExecutor(api, _config()).execute("page-1", ops)
        api.delete.assert_not_called()
        assert result.blocks_deleted == 1  # still counted

    def test_replace_missing_existing_id_skips_delete_only_inserts(self):
        api = _make_sync_api()
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id=None, new_block=_para())
        ]
        result = DiffExecutor(api, _config()).execute("page-1", ops)
        api.delete.assert_not_called()
        api.append_children.assert_called_once()
        assert result.blocks_replaced == 1
        assert result.blocks_deleted == 0

    def test_replace_missing_new_block_skips_insert_only_deletes(self):
        api = _make_sync_api()
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id="b1", new_block=None)
        ]
        result = DiffExecutor(api, _config()).execute("page-1", ops)
        api.append_children.assert_not_called()
        api.delete.assert_called_once_with("b1")
        assert result.blocks_deleted == 1
        assert result.blocks_replaced == 0

    def test_insert_none_new_block_not_batched(self):
        """INSERT ops with None new_block are filtered; no API call made."""
        api = _make_sync_api()
        ops = [DiffOp(op_type=DiffOpType.INSERT, new_block=None)]
        result = DiffExecutor(api, _config()).execute("page-1", ops)
        api.append_children.assert_not_called()
        assert result.blocks_inserted == 0


# =========================================================================
# Sync executor: last_block_id state tracking
# =========================================================================


class TestSyncExecutorStateTracking:
    """last_block_id is correctly maintained across operations."""

    def test_keep_updates_last_block_id(self):
        api = _make_sync_api()
        keep_op = DiffOp(op_type=DiffOpType.KEEP, existing_id="kept-blk")
        insert_op = DiffOp(op_type=DiffOpType.INSERT, new_block=_para())
        DiffExecutor(api, _config()).execute("page-1", [keep_op, insert_op])
        # The INSERT should use "kept-blk" as the `after` keyword parameter
        assert api.append_children.call_args.kwargs["after"] == "kept-blk"

    def test_insert_tracks_new_block_id_for_next_call(self):
        """After a batch insert, last_block_id = last inserted block."""
        api = _make_sync_api()
        api.append_children.return_value = {"results": [{"id": "ins-1"}]}
        ops = [
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para()),
            DiffOp(op_type=DiffOpType.INSERT, new_block=_para()),
        ]
        # Two INSERTs are batched into one append_children call
        result = DiffExecutor(api, _config()).execute("page-1", ops)
        assert result.blocks_inserted == 2
        api.append_children.assert_called_once()

    def test_empty_append_results_keeps_last_block_id_unchanged(self):
        """append_children with no results doesn't update last_block_id."""
        api = _make_sync_api(append_return={"results": []})
        keep_op = DiffOp(op_type=DiffOpType.KEEP, existing_id="anchor")
        insert_op = DiffOp(op_type=DiffOpType.INSERT, new_block=_para())
        DiffExecutor(api, _config()).execute("page-1", [keep_op, insert_op])
        # After empty results, append uses "anchor" as the after param
        assert api.append_children.call_args.kwargs["after"] == "anchor"

    def test_results_missing_id_not_included_in_ids(self):
        """Results without 'id' field are safely ignored."""
        api = _make_sync_api(append_return={"results": [{"type": "paragraph"}]})
        ops = [DiffOp(op_type=DiffOpType.INSERT, new_block=_para())]
        result = DiffExecutor(api, _config()).execute("page-1", ops)
        assert result.blocks_inserted == 1


# =========================================================================
# Async executor: exception propagation
# =========================================================================


class TestAsyncExecutorExceptions:
    """Exceptions from the async block API propagate out of execute()."""

    async def test_update_api_error_propagates(self):
        api = _make_async_api()
        api.update.side_effect = RuntimeError("Async update failed")
        ops = [DiffOp(op_type=DiffOpType.UPDATE, existing_id="b1", new_block=_para())]
        with pytest.raises(RuntimeError, match="Async update failed"):
            await AsyncDiffExecutor(api, _config()).execute("page-1", ops)

    async def test_delete_api_error_propagates(self):
        api = _make_async_api()
        api.delete.side_effect = RuntimeError("Async delete failed")
        ops = [DiffOp(op_type=DiffOpType.DELETE, existing_id="b1")]
        with pytest.raises(RuntimeError, match="Async delete failed"):
            await AsyncDiffExecutor(api, _config()).execute("page-1", ops)

    async def test_insert_api_error_propagates(self):
        api = _make_async_api()
        api.append_children.side_effect = RuntimeError("Async append failed")
        ops = [DiffOp(op_type=DiffOpType.INSERT, new_block=_para())]
        with pytest.raises(RuntimeError, match="Async append failed"):
            await AsyncDiffExecutor(api, _config()).execute("page-1", ops)

    async def test_replace_delete_error_propagates(self):
        api = _make_async_api()
        api.delete.side_effect = RuntimeError("Async delete in replace failed")
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id="b1", new_block=_para())
        ]
        with pytest.raises(RuntimeError, match="Async delete in replace failed"):
            await AsyncDiffExecutor(api, _config()).execute("page-1", ops)

    async def test_replace_insert_error_propagates_after_delete(self):
        """Async: delete succeeds but append raises."""
        api = _make_async_api()
        api.delete.return_value = {"id": "b1"}
        api.append_children.side_effect = RuntimeError("Async append in replace failed")
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id="b1", new_block=_para())
        ]
        with pytest.raises(RuntimeError, match="Async append in replace failed"):
            await AsyncDiffExecutor(api, _config()).execute("page-1", ops)
        api.delete.assert_awaited_once_with("b1")


# =========================================================================
# Async executor: conditional skip branches
# =========================================================================


class TestAsyncExecutorSkipBranches:
    """Async: verify operations are skipped when required fields are absent."""

    async def test_update_missing_existing_id_skips_api_call(self):
        api = _make_async_api()
        ops = [DiffOp(op_type=DiffOpType.UPDATE, existing_id=None, new_block=_para())]
        result = await AsyncDiffExecutor(api, _config()).execute("page-1", ops)
        api.update.assert_not_awaited()
        assert result.blocks_inserted == 1

    async def test_update_missing_new_block_skips_api_call(self):
        api = _make_async_api()
        ops = [DiffOp(op_type=DiffOpType.UPDATE, existing_id="b1", new_block=None)]
        result = await AsyncDiffExecutor(api, _config()).execute("page-1", ops)
        api.update.assert_not_awaited()
        assert result.blocks_inserted == 1

    async def test_delete_missing_existing_id_skips_api_call(self):
        api = _make_async_api()
        ops = [DiffOp(op_type=DiffOpType.DELETE, existing_id=None)]
        result = await AsyncDiffExecutor(api, _config()).execute("page-1", ops)
        api.delete.assert_not_awaited()
        assert result.blocks_deleted == 1

    async def test_replace_missing_existing_id_skips_delete(self):
        api = _make_async_api()
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id=None, new_block=_para())
        ]
        result = await AsyncDiffExecutor(api, _config()).execute("page-1", ops)
        api.delete.assert_not_awaited()
        api.append_children.assert_awaited_once()
        assert result.blocks_replaced == 1

    async def test_replace_missing_new_block_skips_insert(self):
        api = _make_async_api()
        ops = [
            DiffOp(op_type=DiffOpType.REPLACE, existing_id="b1", new_block=None)
        ]
        result = await AsyncDiffExecutor(api, _config()).execute("page-1", ops)
        api.append_children.assert_not_awaited()
        api.delete.assert_awaited_once_with("b1")
        assert result.blocks_deleted == 1

    async def test_insert_none_new_block_not_batched(self):
        api = _make_async_api()
        ops = [DiffOp(op_type=DiffOpType.INSERT, new_block=None)]
        result = await AsyncDiffExecutor(api, _config()).execute("page-1", ops)
        api.append_children.assert_not_awaited()
        assert result.blocks_inserted == 0
