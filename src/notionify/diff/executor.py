"""Diff executor: apply diff operations to a Notion page via the API.

Takes the operation plan produced by :class:`DiffPlanner` and executes
each operation against the Notion API through :class:`BlockAPI` (sync)
or :class:`AsyncBlockAPI` (async).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from notionify.config import NotionifyConfig
from notionify.models import ConversionWarning, DiffOp, DiffOpType, UpdateResult
from notionify.notion_api.blocks import extract_block_ids
from notionify.observability import NoopMetricsHook
from notionify.utils.chunk import chunk_children


class _ExecState:
    """Mutable state shared across diff execution handlers."""

    __slots__ = ("deleted", "inserted", "kept", "last_block_id", "replaced")

    def __init__(self) -> None:
        self.kept = 0
        self.inserted = 0
        self.deleted = 0
        self.replaced = 0
        self.last_block_id: str | None = None


class DiffExecutor:
    """Synchronous diff executor.

    Parameters
    ----------
    block_api:
        A :class:`BlockAPI` instance for making Notion API calls.
    config:
        SDK configuration.
    """

    def __init__(self, block_api: Any, config: NotionifyConfig) -> None:
        self._api = block_api
        self._config = config
        self._metrics = config.metrics if config.metrics is not None else NoopMetricsHook()

    def execute(self, page_id: str, ops: list[DiffOp]) -> UpdateResult:
        """Execute diff operations via the Notion API.

        Consecutive INSERT operations are batched into a single
        ``append_children`` call (up to 100 blocks per request) for
        efficiency.

        Parameters
        ----------
        page_id:
            The Notion page ID to operate on.
        ops:
            Ordered list of diff operations from the planner.

        Returns
        -------
        UpdateResult
            Summary of what was done.
        """
        state = _ExecState()
        warnings: list[ConversionWarning] = []

        i = 0
        while i < len(ops):
            op = ops[i]

            if op.op_type == DiffOpType.KEEP:
                state.kept += 1
                state.last_block_id = op.existing_id
                i += 1

            elif op.op_type == DiffOpType.UPDATE:
                if op.existing_id and op.new_block:
                    block_type = op.new_block.get("type", "")
                    type_data = op.new_block.get(block_type, {})
                    payload = {block_type: type_data}
                    self._api.update(op.existing_id, payload)
                state.last_block_id = op.existing_id
                state.inserted += 1  # counts as a write operation
                i += 1

            elif op.op_type == DiffOpType.REPLACE:
                self._exec_replace(page_id, op, state)
                i += 1

            elif op.op_type == DiffOpType.INSERT:
                i = self._exec_insert_batch(page_id, ops, i, state)

            elif op.op_type == DiffOpType.DELETE:
                if op.existing_id:
                    self._api.delete(op.existing_id)
                state.deleted += 1
                i += 1

            else:
                i += 1

        _emit_diff_metrics(self._metrics, ops)

        return UpdateResult(
            strategy_used="diff",
            blocks_kept=state.kept,
            blocks_inserted=state.inserted,
            blocks_deleted=state.deleted,
            blocks_replaced=state.replaced,
            images_uploaded=0,
            warnings=warnings,
        )

    def _exec_replace(
        self, page_id: str, op: DiffOp, state: _ExecState,
    ) -> None:
        """Execute a REPLACE op: delete old block and insert new one."""
        if op.existing_id:
            self._api.delete(op.existing_id)
            state.deleted += 1
        if op.new_block:
            response = self._api.append_children(
                page_id, [op.new_block], after=state.last_block_id,
            )
            new_ids = extract_block_ids(response)
            if new_ids:
                state.last_block_id = new_ids[-1]
            state.replaced += 1

    def _exec_insert_batch(
        self, page_id: str, ops: list[DiffOp], start: int, state: _ExecState,
    ) -> int:
        """Batch consecutive INSERT ops into append_children calls. Returns new index."""
        insert_blocks: list[dict] = []
        i = start
        while i < len(ops) and ops[i].op_type == DiffOpType.INSERT:
            block = ops[i].new_block
            if block is not None:
                insert_blocks.append(block)
            i += 1

        if insert_blocks:
            for batch in chunk_children(insert_blocks):
                response = self._api.append_children(
                    page_id, batch, after=state.last_block_id,
                )
                new_ids = extract_block_ids(response)
                if new_ids:
                    state.last_block_id = new_ids[-1]
            state.inserted += len(insert_blocks)
        return i


class AsyncDiffExecutor:
    """Asynchronous diff executor.

    Mirrors :class:`DiffExecutor` but all methods are coroutines.

    Parameters
    ----------
    block_api:
        An :class:`AsyncBlockAPI` instance.
    config:
        SDK configuration.
    """

    def __init__(self, block_api: Any, config: NotionifyConfig) -> None:
        self._api = block_api
        self._config = config
        self._metrics = config.metrics if config.metrics is not None else NoopMetricsHook()

    async def execute(self, page_id: str, ops: list[DiffOp]) -> UpdateResult:
        """Execute diff operations via the Notion API (async).

        Parameters
        ----------
        page_id:
            The Notion page ID to operate on.
        ops:
            Ordered list of diff operations from the planner.

        Returns
        -------
        UpdateResult
            Summary of what was done.
        """
        state = _ExecState()
        warnings: list[ConversionWarning] = []

        i = 0
        while i < len(ops):
            op = ops[i]

            if op.op_type == DiffOpType.KEEP:
                state.kept += 1
                state.last_block_id = op.existing_id
                i += 1

            elif op.op_type == DiffOpType.UPDATE:
                if op.existing_id and op.new_block:
                    block_type = op.new_block.get("type", "")
                    type_data = op.new_block.get(block_type, {})
                    payload = {block_type: type_data}
                    await self._api.update(op.existing_id, payload)
                state.last_block_id = op.existing_id
                state.inserted += 1
                i += 1

            elif op.op_type == DiffOpType.REPLACE:
                await self._exec_replace(page_id, op, state)
                i += 1

            elif op.op_type == DiffOpType.INSERT:
                i = await self._exec_insert_batch(page_id, ops, i, state)

            elif op.op_type == DiffOpType.DELETE:
                if op.existing_id:
                    await self._api.delete(op.existing_id)
                state.deleted += 1
                i += 1

            else:
                i += 1

        _emit_diff_metrics(self._metrics, ops)

        return UpdateResult(
            strategy_used="diff",
            blocks_kept=state.kept,
            blocks_inserted=state.inserted,
            blocks_deleted=state.deleted,
            blocks_replaced=state.replaced,
            images_uploaded=0,
            warnings=warnings,
        )

    async def _exec_replace(
        self, page_id: str, op: DiffOp, state: _ExecState,
    ) -> None:
        """Execute a REPLACE op: delete old block and insert new one."""
        if op.existing_id:
            await self._api.delete(op.existing_id)
            state.deleted += 1
        if op.new_block:
            response = await self._api.append_children(
                page_id, [op.new_block], after=state.last_block_id,
            )
            new_ids = extract_block_ids(response)
            if new_ids:
                state.last_block_id = new_ids[-1]
            state.replaced += 1

    async def _exec_insert_batch(
        self, page_id: str, ops: list[DiffOp], start: int, state: _ExecState,
    ) -> int:
        """Batch consecutive INSERT ops into append_children calls. Returns new index."""
        insert_blocks: list[dict] = []
        i = start
        while i < len(ops) and ops[i].op_type == DiffOpType.INSERT:
            block = ops[i].new_block
            if block is not None:
                insert_blocks.append(block)
            i += 1

        if insert_blocks:
            for batch in chunk_children(insert_blocks):
                response = await self._api.append_children(
                    page_id, batch, after=state.last_block_id,
                )
                new_ids = extract_block_ids(response)
                if new_ids:
                    state.last_block_id = new_ids[-1]
            state.inserted += len(insert_blocks)
        return i


def _emit_diff_metrics(metrics: Any, ops: list[DiffOp]) -> None:
    """Emit ``diff_ops_total`` counters grouped by operation type."""
    op_counts: Counter[str] = Counter()
    for op in ops:
        op_type = op.op_type
        op_counts[getattr(op_type, "value", str(op_type))] += 1
    for op_type_val, count in op_counts.items():
        metrics.increment(
            "notionify.diff_ops_total", count, tags={"op_type": op_type_val},
        )
