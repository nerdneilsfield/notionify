"""Diff planner: compute minimal edit operations between two block lists.

Given the existing blocks on a Notion page and the desired new blocks (from
the Markdown converter), the planner produces a list of :class:`DiffOp`
operations that transform the page to match the desired state with the
fewest API calls possible.
"""

from __future__ import annotations

from notionify.config import NotionifyConfig
from notionify.models import BlockSignature, DiffOp, DiffOpType

from .lcs_matcher import lcs_match
from .signature import compute_signature


class DiffPlanner:
    """Plans diff operations for page updates.

    Parameters
    ----------
    config:
        SDK configuration (used for debug flags and tuning).
    """

    def __init__(self, config: NotionifyConfig) -> None:
        self._config = config
        self._min_match_ratio: float = 0.3

    def plan(self, existing: list[dict], new: list[dict]) -> list[DiffOp]:
        """Compute minimal diff operations to transform *existing* into *new*.

        Operation types:

        - **KEEP**: block is unchanged -- no API call needed.
        - **UPDATE**: same block type, content has changed -- PATCH.
        - **REPLACE**: block type changed -- archive old, insert new.
        - **INSERT**: a new block that does not exist on the page yet.
        - **DELETE**: an existing block that should be archived.

        If the match ratio (LCS length / max(len(existing), len(new))) falls
        below ``_min_match_ratio``, the planner falls back to a full
        overwrite strategy (delete all existing, insert all new).

        Parameters
        ----------
        existing:
            Block dicts currently on the Notion page.  Each must have an
            ``"id"`` key.
        new:
            Desired block dicts from the converter.

        Returns
        -------
        list[DiffOp]
            Ordered list of operations to execute.
        """
        if not existing and not new:
            return []

        # Fast path: nothing existing, just insert everything.
        if not existing:
            return [
                DiffOp(op_type=DiffOpType.INSERT, new_block=block)
                for block in new
            ]

        # Fast path: nothing new, delete everything.
        if not new:
            return [
                DiffOp(
                    op_type=DiffOpType.DELETE,
                    existing_id=block.get("id"),
                )
                for block in existing
            ]

        # Compute signatures.
        existing_sigs = [compute_signature(b) for b in existing]
        new_sigs = [compute_signature(b) for b in new]

        # LCS matching.
        matched_pairs = lcs_match(existing_sigs, new_sigs)

        # Check match ratio -- fall back to full overwrite if too low.
        max_len = max(len(existing), len(new))
        match_ratio = len(matched_pairs) / max_len if max_len > 0 else 0.0

        if match_ratio < self._min_match_ratio:
            return self._full_overwrite(existing, new)

        return self._build_ops(existing, new, existing_sigs, new_sigs, matched_pairs)

    def _full_overwrite(
        self, existing: list[dict], new: list[dict]
    ) -> list[DiffOp]:
        """Delete all existing blocks and insert all new blocks."""
        ops = [
            DiffOp(op_type=DiffOpType.DELETE, existing_id=block.get("id"))
            for block in existing
        ]
        ops.extend(DiffOp(op_type=DiffOpType.INSERT, new_block=block) for block in new)
        return ops

    def _build_ops(
        self,
        existing: list[dict],
        new: list[dict],
        existing_sigs: list[BlockSignature],
        new_sigs: list[BlockSignature],
        matched_pairs: list[tuple[int, int]],
    ) -> list[DiffOp]:
        """Build fine-grained diff ops from LCS match results."""
        ops: list[DiffOp] = []

        matched_existing: set[int] = {pair[0] for pair in matched_pairs}
        matched_new: set[int] = {pair[1] for pair in matched_pairs}

        # Walk through all indices in a merged order.
        # Strategy: iterate through new blocks in order, interleaving deletes
        # for unmatched existing blocks.

        # Track which existing blocks we've processed.
        existing_ptr = 0
        # For each matched pair, we know the existing_idx.  Build a sorted
        # list of match anchors.
        match_anchors = list(matched_pairs)  # already sorted by both indices

        # Build a set of existing indices that are matched.
        # We process existing blocks that fall between anchors as deletes.
        # We process new blocks that fall between anchors as inserts.

        # Approach: walk through new[0..n] and existing[0..m] using the LCS
        # anchors as synchronisation points.

        anchor_idx = 0
        new_ptr = 0
        existing_ptr = 0

        while anchor_idx < len(match_anchors):
            e_anchor, n_anchor = match_anchors[anchor_idx]

            # Delete unmatched existing blocks before this anchor.
            while existing_ptr < e_anchor:
                if existing_ptr not in matched_existing:
                    ops.append(
                        DiffOp(
                            op_type=DiffOpType.DELETE,
                            existing_id=existing[existing_ptr].get("id"),
                        )
                    )
                existing_ptr += 1

            # Insert or update/replace unmatched new blocks before this anchor.
            while new_ptr < n_anchor:
                if new_ptr not in matched_new:
                    ops.append(
                        DiffOp(
                            op_type=DiffOpType.INSERT,
                            new_block=new[new_ptr],
                        )
                    )
                new_ptr += 1

            # Process the anchor: this is a KEEP.
            ops.append(
                DiffOp(
                    op_type=DiffOpType.KEEP,
                    existing_id=existing[e_anchor].get("id"),
                )
            )
            existing_ptr = e_anchor + 1
            new_ptr = n_anchor + 1
            anchor_idx += 1

        # Handle remaining existing blocks after last anchor.
        while existing_ptr < len(existing):
            if existing_ptr not in matched_existing:
                ops.append(
                    DiffOp(
                        op_type=DiffOpType.DELETE,
                        existing_id=existing[existing_ptr].get("id"),
                    )
                )
            existing_ptr += 1

        # Handle remaining new blocks after last anchor.
        while new_ptr < len(new):
            if new_ptr not in matched_new:
                ops.append(
                    DiffOp(
                        op_type=DiffOpType.INSERT,
                        new_block=new[new_ptr],
                    )
                )
            new_ptr += 1

        # Now handle unmatched existing/new blocks that share the same type
        # at the same position -- upgrade INSERTs adjacent to DELETEs to
        # UPDATEs or REPLACEs.
        return self._upgrade_to_updates(ops, existing, new, existing_sigs, new_sigs)

    def _upgrade_to_updates(
        self,
        ops: list[DiffOp],
        existing: list[dict],
        new: list[dict],
        existing_sigs: list[BlockSignature],
        new_sigs: list[BlockSignature],
    ) -> list[DiffOp]:
        """Scan for adjacent DELETE+INSERT pairs and upgrade them.

        If the deleted block and inserted block share the same type, the
        pair becomes a single UPDATE.  If types differ, the pair becomes
        a REPLACE.
        """
        # Build an ID→type mapping once to avoid O(n) linear scans per pair.
        id_to_type: dict[str, str] = {}
        for block in existing:
            bid = block.get("id")
            btype = block.get("type")
            if bid and btype:
                id_to_type[bid] = btype

        result: list[DiffOp] = []
        i = 0
        while i < len(ops):
            if (
                i + 1 < len(ops)
                and ops[i].op_type == DiffOpType.DELETE
                and ops[i + 1].op_type == DiffOpType.INSERT
            ):
                delete_op = ops[i]
                insert_op = ops[i + 1]

                # Find the block type of the deleted block.
                deleted_type = (
                    id_to_type.get(delete_op.existing_id)
                    if delete_op.existing_id
                    else None
                )
                inserted_type = (insert_op.new_block or {}).get("type", "")

                if deleted_type and deleted_type == inserted_type:
                    # Same type -- UPDATE.
                    result.append(
                        DiffOp(
                            op_type=DiffOpType.UPDATE,
                            existing_id=delete_op.existing_id,
                            new_block=insert_op.new_block,
                        )
                    )
                else:
                    # Different type -- REPLACE.
                    result.append(
                        DiffOp(
                            op_type=DiffOpType.REPLACE,
                            existing_id=delete_op.existing_id,
                            new_block=insert_op.new_block,
                        )
                    )
                i += 2
            else:
                result.append(ops[i])
                i += 1

        return result
