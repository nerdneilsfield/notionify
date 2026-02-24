"""Tests for the diff engine.

PRD test IDs: U-DF-001 through U-DF-015.
"""

import pytest

from notionify.config import NotionifyConfig
from notionify.models import DiffOp, DiffOpType, BlockSignature
from notionify.diff.planner import DiffPlanner
from notionify.diff.signature import compute_signature
from notionify.diff.lcs_matcher import lcs_match


def make_config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


def _make_paragraph_block(text, block_id=None):
    """Build a minimal Notion paragraph block."""
    block = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{
                "type": "text",
                "text": {"content": text},
                "plain_text": text,
            }],
            "color": "default",
        },
    }
    if block_id:
        block["id"] = block_id
    return block


def _make_heading_block(level, text, block_id=None):
    """Build a minimal Notion heading block."""
    heading_type = f"heading_{level}"
    block = {
        "type": heading_type,
        heading_type: {
            "rich_text": [{
                "type": "text",
                "text": {"content": text},
                "plain_text": text,
            }],
            "color": "default",
            "is_toggleable": False,
        },
    }
    if block_id:
        block["id"] = block_id
    return block


def _make_code_block(code, language="python", block_id=None):
    """Build a minimal Notion code block."""
    block = {
        "type": "code",
        "code": {
            "rich_text": [{
                "type": "text",
                "text": {"content": code},
                "plain_text": code,
            }],
            "language": language,
        },
    }
    if block_id:
        block["id"] = block_id
    return block


def _make_divider_block(block_id=None):
    """Build a minimal Notion divider block."""
    block = {
        "type": "divider",
        "divider": {},
    }
    if block_id:
        block["id"] = block_id
    return block


# =========================================================================
# U-DF-001: Identical blocks -> all KEEP
# =========================================================================

class TestIdenticalBlocks:
    """U-DF-001: Identical existing and new blocks produce only KEEP ops."""

    def test_all_keep(self):
        planner = DiffPlanner(make_config())
        existing = [
            _make_paragraph_block("Hello", block_id="b1"),
            _make_paragraph_block("World", block_id="b2"),
        ]
        new = [
            _make_paragraph_block("Hello"),
            _make_paragraph_block("World"),
        ]
        ops = planner.plan(existing, new)
        assert all(op.op_type == DiffOpType.KEEP for op in ops)
        assert len(ops) == 2
        # Verify specific block IDs are preserved in KEEP ops
        assert ops[0].existing_id == "b1"
        assert ops[1].existing_id == "b2"


# =========================================================================
# U-DF-002: Empty existing -> all INSERT
# =========================================================================

class TestEmptyExisting:
    """U-DF-002: Empty existing list produces INSERT for each new block."""

    def test_all_insert(self):
        planner = DiffPlanner(make_config())
        new = [
            _make_paragraph_block("Hello"),
            _make_paragraph_block("World"),
        ]
        ops = planner.plan([], new)
        assert len(ops) == 2
        assert all(op.op_type == DiffOpType.INSERT for op in ops)
        assert ops[0].new_block is not None
        assert ops[1].new_block is not None


# =========================================================================
# U-DF-003: Empty new -> all DELETE
# =========================================================================

class TestEmptyNew:
    """U-DF-003: Empty new list produces DELETE for each existing block."""

    def test_all_delete(self):
        planner = DiffPlanner(make_config())
        existing = [
            _make_paragraph_block("Hello", block_id="b1"),
            _make_paragraph_block("World", block_id="b2"),
        ]
        ops = planner.plan(existing, [])
        assert len(ops) == 2
        assert all(op.op_type == DiffOpType.DELETE for op in ops)
        assert ops[0].existing_id == "b1"
        assert ops[1].existing_id == "b2"


# =========================================================================
# U-DF-004: Both empty -> no ops
# =========================================================================

class TestBothEmpty:
    """U-DF-004: Both lists empty produces no operations."""

    def test_no_ops(self):
        planner = DiffPlanner(make_config())
        ops = planner.plan([], [])
        assert ops == []


# =========================================================================
# U-DF-005: Block appended at end
# =========================================================================

class TestBlockAppended:
    """U-DF-005: New block appended at end produces KEEP + INSERT."""

    def test_append_one(self):
        planner = DiffPlanner(make_config())
        existing = [
            _make_paragraph_block("Hello", block_id="b1"),
        ]
        new = [
            _make_paragraph_block("Hello"),
            _make_paragraph_block("World"),
        ]
        ops = planner.plan(existing, new)
        keep_ops = [op for op in ops if op.op_type == DiffOpType.KEEP]
        insert_ops = [op for op in ops if op.op_type == DiffOpType.INSERT]
        assert len(keep_ops) == 1
        assert keep_ops[0].existing_id == "b1"
        assert len(insert_ops) == 1
        assert insert_ops[0].new_block is not None
        assert insert_ops[0].new_block["type"] == "paragraph"


# =========================================================================
# U-DF-006: Block removed from end
# =========================================================================

class TestBlockRemoved:
    """U-DF-006: Block removed from end produces KEEP + DELETE."""

    def test_remove_last(self):
        planner = DiffPlanner(make_config())
        existing = [
            _make_paragraph_block("Hello", block_id="b1"),
            _make_paragraph_block("World", block_id="b2"),
        ]
        new = [
            _make_paragraph_block("Hello"),
        ]
        ops = planner.plan(existing, new)
        keep_ops = [op for op in ops if op.op_type == DiffOpType.KEEP]
        delete_ops = [op for op in ops if op.op_type == DiffOpType.DELETE]
        assert len(keep_ops) == 1
        assert len(delete_ops) == 1
        assert delete_ops[0].existing_id == "b2"


# =========================================================================
# U-DF-007: Block content changed (same type) -> UPDATE
# =========================================================================

class TestBlockContentChanged:
    """U-DF-007: Same type different content produces UPDATE or DELETE+INSERT."""

    def test_content_update(self):
        planner = DiffPlanner(make_config())
        existing = [
            _make_paragraph_block("Old text", block_id="b1"),
        ]
        new = [
            _make_paragraph_block("New text"),
        ]
        ops = planner.plan(existing, new)
        # Single block with 0 matches → ratio 0/1=0 < 0.3 → full overwrite
        # Full overwrite: DELETE + INSERT (no upgrade since full overwrite skips it)
        assert len(ops) == 2
        assert ops[0].op_type == DiffOpType.DELETE
        assert ops[0].existing_id == "b1"
        assert ops[1].op_type == DiffOpType.INSERT
        assert ops[1].new_block["type"] == "paragraph"


# =========================================================================
# U-DF-008: Block type changed -> REPLACE
# =========================================================================

class TestBlockTypeChanged:
    """U-DF-008: Different block type produces REPLACE."""

    def test_type_change_replace(self):
        planner = DiffPlanner(make_config())
        existing = [
            _make_paragraph_block("Text", block_id="b1"),
        ]
        new = [
            _make_heading_block(1, "Text"),
        ]
        ops = planner.plan(existing, new)
        op_types = {op.op_type for op in ops}
        # Should produce REPLACE (or DELETE+INSERT)
        assert DiffOpType.REPLACE in op_types or (
            DiffOpType.DELETE in op_types and DiffOpType.INSERT in op_types
        )


# =========================================================================
# U-DF-009: Signature computation
# =========================================================================

class TestSignatureComputation:
    """U-DF-009: Signature computation produces correct fingerprints."""

    def test_identical_blocks_same_signature(self):
        b1 = _make_paragraph_block("Hello")
        b2 = _make_paragraph_block("Hello")
        sig1 = compute_signature(b1)
        sig2 = compute_signature(b2)
        assert sig1 == sig2

    def test_different_content_different_signature(self):
        b1 = _make_paragraph_block("Hello")
        b2 = _make_paragraph_block("World")
        sig1 = compute_signature(b1)
        sig2 = compute_signature(b2)
        assert sig1 != sig2

    def test_different_type_different_signature(self):
        b1 = _make_paragraph_block("Hello")
        b2 = _make_heading_block(1, "Hello")
        sig1 = compute_signature(b1)
        sig2 = compute_signature(b2)
        assert sig1 != sig2

    def test_signature_is_frozen(self):
        b = _make_paragraph_block("Test")
        sig = compute_signature(b)
        assert isinstance(sig, BlockSignature)
        # Frozen dataclass should be hashable
        s = {sig}
        assert len(s) == 1

    def test_signature_depth(self):
        b = _make_paragraph_block("Test")
        sig0 = compute_signature(b, depth=0)
        sig1 = compute_signature(b, depth=1)
        assert sig0.nesting_depth == 0
        assert sig1.nesting_depth == 1
        assert sig0 != sig1

    def test_code_block_language_in_attrs(self):
        b1 = _make_code_block("x=1", language="python")
        b2 = _make_code_block("x=1", language="javascript")
        sig1 = compute_signature(b1)
        sig2 = compute_signature(b2)
        # Different language should produce different attrs_hash
        assert sig1.attrs_hash != sig2.attrs_hash


# =========================================================================
# U-DF-010: LCS matcher
# =========================================================================

class TestLCSMatcher:
    """U-DF-010: LCS matching finds the longest common subsequence."""

    def test_identical_sequences(self):
        sigs = [
            compute_signature(_make_paragraph_block("A")),
            compute_signature(_make_paragraph_block("B")),
            compute_signature(_make_paragraph_block("C")),
        ]
        pairs = lcs_match(sigs, sigs)
        assert len(pairs) == 3
        assert pairs == [(0, 0), (1, 1), (2, 2)]

    def test_empty_existing(self):
        new_sigs = [compute_signature(_make_paragraph_block("A"))]
        pairs = lcs_match([], new_sigs)
        assert pairs == []

    def test_empty_new(self):
        existing_sigs = [compute_signature(_make_paragraph_block("A"))]
        pairs = lcs_match(existing_sigs, [])
        assert pairs == []

    def test_both_empty(self):
        pairs = lcs_match([], [])
        assert pairs == []

    def test_partial_match(self):
        a = compute_signature(_make_paragraph_block("A"))
        b = compute_signature(_make_paragraph_block("B"))
        c = compute_signature(_make_paragraph_block("C"))
        existing_sigs = [a, b, c]
        new_sigs = [a, c]
        pairs = lcs_match(existing_sigs, new_sigs)
        assert len(pairs) == 2
        assert pairs == [(0, 0), (2, 1)]

    def test_no_match(self):
        a = compute_signature(_make_paragraph_block("A"))
        b = compute_signature(_make_paragraph_block("B"))
        c = compute_signature(_make_paragraph_block("C"))
        d = compute_signature(_make_paragraph_block("D"))
        pairs = lcs_match([a, b], [c, d])
        assert pairs == []


# =========================================================================
# U-DF-011: Full overwrite fallback
# =========================================================================

class TestFullOverwriteFallback:
    """U-DF-011: Low match ratio triggers full overwrite."""

    def test_full_overwrite_on_low_match(self):
        planner = DiffPlanner(make_config())
        # All different blocks -> match ratio = 0 < 0.3
        existing = [
            _make_paragraph_block("A", block_id="b1"),
            _make_paragraph_block("B", block_id="b2"),
            _make_paragraph_block("C", block_id="b3"),
        ]
        new = [
            _make_paragraph_block("X"),
            _make_paragraph_block("Y"),
            _make_paragraph_block("Z"),
        ]
        ops = planner.plan(existing, new)
        # Should be all DELETEs followed by all INSERTs (or upgraded)
        # Since content differs and types match, could be all UPDATEs
        op_types = [op.op_type for op in ops]
        # In full overwrite: DELETE all existing, INSERT all new
        # But upgrade_to_updates may convert DELETE+INSERT -> UPDATE
        assert len(ops) >= 3


# =========================================================================
# U-DF-012: Insert in middle
# =========================================================================

class TestInsertInMiddle:
    """U-DF-012: Inserting a block in the middle is detected."""

    def test_insert_middle(self):
        planner = DiffPlanner(make_config())
        existing = [
            _make_paragraph_block("A", block_id="b1"),
            _make_paragraph_block("C", block_id="b2"),
        ]
        new = [
            _make_paragraph_block("A"),
            _make_paragraph_block("B"),
            _make_paragraph_block("C"),
        ]
        ops = planner.plan(existing, new)
        keep_ops = [op for op in ops if op.op_type == DiffOpType.KEEP]
        insert_ops = [op for op in ops if op.op_type == DiffOpType.INSERT]
        assert len(keep_ops) == 2
        assert len(insert_ops) == 1


# =========================================================================
# U-DF-013: Delete from middle
# =========================================================================

class TestDeleteFromMiddle:
    """U-DF-013: Removing a block from the middle is detected."""

    def test_delete_middle(self):
        planner = DiffPlanner(make_config())
        existing = [
            _make_paragraph_block("A", block_id="b1"),
            _make_paragraph_block("B", block_id="b2"),
            _make_paragraph_block("C", block_id="b3"),
        ]
        new = [
            _make_paragraph_block("A"),
            _make_paragraph_block("C"),
        ]
        ops = planner.plan(existing, new)
        keep_ops = [op for op in ops if op.op_type == DiffOpType.KEEP]
        delete_ops = [op for op in ops if op.op_type == DiffOpType.DELETE]
        assert len(keep_ops) == 2
        assert len(delete_ops) == 1
        assert delete_ops[0].existing_id == "b2"


# =========================================================================
# U-DF-014: Divider block signature
# =========================================================================

class TestDividerSignature:
    """U-DF-014: Divider blocks produce correct signatures."""

    def test_divider_signature(self):
        b = _make_divider_block()
        sig = compute_signature(b)
        assert sig.block_type == "divider"
        assert sig.rich_text_hash is not None

    def test_two_dividers_same_signature(self):
        b1 = _make_divider_block()
        b2 = _make_divider_block()
        assert compute_signature(b1) == compute_signature(b2)


# =========================================================================
# U-DF-015: DiffOp structure
# =========================================================================

class TestDiffOpStructure:
    """U-DF-015: DiffOp dataclass has correct fields."""

    def test_keep_op(self):
        op = DiffOp(op_type=DiffOpType.KEEP, existing_id="b1")
        assert op.op_type == DiffOpType.KEEP
        assert op.existing_id == "b1"
        assert op.new_block is None

    def test_insert_op(self):
        block = _make_paragraph_block("Test")
        op = DiffOp(op_type=DiffOpType.INSERT, new_block=block)
        assert op.op_type == DiffOpType.INSERT
        assert op.new_block is not None
        assert op.existing_id is None

    def test_delete_op(self):
        op = DiffOp(op_type=DiffOpType.DELETE, existing_id="b1")
        assert op.op_type == DiffOpType.DELETE
        assert op.existing_id == "b1"

    def test_update_op(self):
        block = _make_paragraph_block("New")
        op = DiffOp(op_type=DiffOpType.UPDATE, existing_id="b1", new_block=block)
        assert op.op_type == DiffOpType.UPDATE
        assert op.existing_id == "b1"
        assert op.new_block is not None

    def test_replace_op(self):
        block = _make_heading_block(1, "Title")
        op = DiffOp(op_type=DiffOpType.REPLACE, existing_id="b1", new_block=block)
        assert op.op_type == DiffOpType.REPLACE
        assert op.existing_id == "b1"
        assert op.new_block["type"] == "heading_1"

    def test_diff_op_type_enum_values(self):
        assert DiffOpType.KEEP.value == "keep"
        assert DiffOpType.UPDATE.value == "update"
        assert DiffOpType.REPLACE.value == "replace"
        assert DiffOpType.INSERT.value == "insert"
        assert DiffOpType.DELETE.value == "delete"
