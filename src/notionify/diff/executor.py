"""Diff executor: apply diff operations to a Notion page via the API.

Takes the operation plan produced by :class:`DiffPlanner` and executes
each operation against the Notion API through :class:`BlockAPI` (sync)
or :class:`AsyncBlockAPI` (async).
"""

from __future__ import annotations

from typing import Any

from notionify.config import NotionifyConfig
from notionify.models import ConversionWarning, DiffOp, DiffOpType, UpdateResult
from notionify.utils.chunk import chunk_children


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
        kept = 0
        inserted = 0
        deleted = 0
        replaced = 0
        warnings: list[ConversionWarning] = []

        # Track the last known block ID for positioning inserts.
        last_block_id: str | None = None

        i = 0
        while i < len(ops):
            op = ops[i]

            if op.op_type == DiffOpType.KEEP:
                kept += 1
                last_block_id = op.existing_id
                i += 1

            elif op.op_type == DiffOpType.UPDATE:
                if op.existing_id and op.new_block:
                    block_type = op.new_block.get("type", "")
                    type_data = op.new_block.get(block_type, {})
                    payload = {block_type: type_data}
                    self._api.update(op.existing_id, payload)
                last_block_id = op.existing_id
                inserted += 1  # counts as a write operation
                i += 1

            elif op.op_type == DiffOpType.REPLACE:
                # Delete old block.
                if op.existing_id:
                    self._api.delete(op.existing_id)
                    deleted += 1
                # Insert new block after the last known position.
                if op.new_block:
                    response = self._api.append_children(
                        page_id,
                        [op.new_block],
                        after=last_block_id,
                    )
                    new_ids = _extract_block_ids(response)
                    if new_ids:
                        last_block_id = new_ids[-1]
                    replaced += 1
                i += 1

            elif op.op_type == DiffOpType.INSERT:
                # Batch consecutive inserts.
                insert_blocks: list[dict] = []
                while i < len(ops) and ops[i].op_type == DiffOpType.INSERT:
                    block = ops[i].new_block
                    if block is not None:
                        insert_blocks.append(block)
                    i += 1

                if insert_blocks:
                    for batch in chunk_children(insert_blocks):
                        response = self._api.append_children(
                            page_id,
                            batch,
                            after=last_block_id,
                        )
                        new_ids = _extract_block_ids(response)
                        if new_ids:
                            last_block_id = new_ids[-1]
                    inserted += len(insert_blocks)

            elif op.op_type == DiffOpType.DELETE:
                if op.existing_id:
                    self._api.delete(op.existing_id)
                deleted += 1
                i += 1

            else:
                i += 1

        return UpdateResult(
            strategy_used="diff",
            blocks_kept=kept,
            blocks_inserted=inserted,
            blocks_deleted=deleted,
            blocks_replaced=replaced,
            images_uploaded=0,
            warnings=warnings,
        )


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
        kept = 0
        inserted = 0
        deleted = 0
        replaced = 0
        warnings: list[ConversionWarning] = []

        last_block_id: str | None = None

        i = 0
        while i < len(ops):
            op = ops[i]

            if op.op_type == DiffOpType.KEEP:
                kept += 1
                last_block_id = op.existing_id
                i += 1

            elif op.op_type == DiffOpType.UPDATE:
                if op.existing_id and op.new_block:
                    block_type = op.new_block.get("type", "")
                    type_data = op.new_block.get(block_type, {})
                    payload = {block_type: type_data}
                    await self._api.update(op.existing_id, payload)
                last_block_id = op.existing_id
                inserted += 1
                i += 1

            elif op.op_type == DiffOpType.REPLACE:
                if op.existing_id:
                    await self._api.delete(op.existing_id)
                    deleted += 1
                if op.new_block:
                    response = await self._api.append_children(
                        page_id,
                        [op.new_block],
                        after=last_block_id,
                    )
                    new_ids = _extract_block_ids(response)
                    if new_ids:
                        last_block_id = new_ids[-1]
                    replaced += 1
                i += 1

            elif op.op_type == DiffOpType.INSERT:
                insert_blocks: list[dict] = []
                while i < len(ops) and ops[i].op_type == DiffOpType.INSERT:
                    block = ops[i].new_block
                    if block is not None:
                        insert_blocks.append(block)
                    i += 1

                if insert_blocks:
                    for batch in chunk_children(insert_blocks):
                        response = await self._api.append_children(
                            page_id,
                            batch,
                            after=last_block_id,
                        )
                        new_ids = _extract_block_ids(response)
                        if new_ids:
                            last_block_id = new_ids[-1]
                    inserted += len(insert_blocks)

            elif op.op_type == DiffOpType.DELETE:
                if op.existing_id:
                    await self._api.delete(op.existing_id)
                deleted += 1
                i += 1

            else:
                i += 1

        return UpdateResult(
            strategy_used="diff",
            blocks_kept=kept,
            blocks_inserted=inserted,
            blocks_deleted=deleted,
            blocks_replaced=replaced,
            images_uploaded=0,
            warnings=warnings,
        )


def _extract_block_ids(response: dict) -> list[str]:
    """Extract block IDs from an append_children API response."""
    results = response.get("results", [])
    return [r["id"] for r in results if "id" in r]
