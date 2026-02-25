"""Tests for diff/conflict.py — conflict detection and snapshot building."""

from datetime import datetime, timezone

import pytest

from notionify.diff.conflict import detect_conflict, take_snapshot
from notionify.errors import NotionifyDiffConflictError
from notionify.models import PageSnapshot


class TestTakeSnapshot:
    def test_basic_snapshot(self):
        page = {"last_edited_time": "2024-01-15T10:00:00.000Z"}
        blocks = [
            {"id": "block-1", "last_edited_time": "2024-01-15T09:00:00.000Z"},
            {"id": "block-2", "last_edited_time": "2024-01-15T09:30:00.000Z"},
        ]
        snap = take_snapshot("page-123", page, blocks)
        assert snap.page_id == "page-123"
        assert snap.last_edited == datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        assert snap.block_etags == {
            "block-1": "2024-01-15T09:00:00.000Z",
            "block-2": "2024-01-15T09:30:00.000Z",
        }

    def test_empty_blocks(self):
        page = {"last_edited_time": "2024-01-15T10:00:00.000Z"}
        snap = take_snapshot("page-123", page, [])
        assert snap.block_etags == {}

    def test_missing_last_edited(self):
        page = {}
        snap = take_snapshot("page-123", page, [])
        assert snap.last_edited == datetime.min

    def test_blocks_without_id_skipped(self):
        page = {"last_edited_time": "2024-01-15T10:00:00.000Z"}
        blocks = [{"last_edited_time": "2024-01-15T09:00:00.000Z"}]
        snap = take_snapshot("page-123", page, blocks)
        assert snap.block_etags == {}

    def test_blocks_without_edited_time_skipped(self):
        page = {"last_edited_time": "2024-01-15T10:00:00.000Z"}
        blocks = [{"id": "block-1"}]
        snap = take_snapshot("page-123", page, blocks)
        assert snap.block_etags == {}


class TestDetectConflict:
    def _make_snapshot(
        self,
        last_edited: str = "2024-01-15T10:00:00.000Z",
        etags: dict | None = None,
    ) -> PageSnapshot:
        dt = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
        return PageSnapshot(
            page_id="page-123",
            last_edited=dt,
            block_etags=etags or {},
        )

    def test_identical_no_conflict(self):
        snap = self._make_snapshot(etags={"b1": "t1"})
        current = self._make_snapshot(etags={"b1": "t1"})
        assert detect_conflict(snap, current) is False

    def test_page_edited_time_changed(self):
        snap = self._make_snapshot(last_edited="2024-01-15T10:00:00.000Z")
        current = self._make_snapshot(last_edited="2024-01-15T10:05:00.000Z")
        assert detect_conflict(snap, current) is True

    def test_block_etag_changed(self):
        snap = self._make_snapshot(etags={"b1": "t1", "b2": "t2"})
        current = self._make_snapshot(etags={"b1": "t1", "b2": "t2-changed"})
        assert detect_conflict(snap, current) is True

    def test_block_removed_from_current(self):
        """Block present in snapshot but missing from current → conflict."""
        snap = self._make_snapshot(etags={"b1": "t1", "b2": "t2"})
        current = self._make_snapshot(etags={"b1": "t1"})
        assert detect_conflict(snap, current) is True

    def test_empty_etags_no_conflict(self):
        snap = self._make_snapshot(etags={})
        current = self._make_snapshot(etags={})
        assert detect_conflict(snap, current) is False

    def test_new_block_added_but_no_page_edit(self):
        """New block in current but not in snapshot is NOT a conflict
        (only snapshot block_etags are checked)."""
        snap = self._make_snapshot(etags={"b1": "t1"})
        current = self._make_snapshot(etags={"b1": "t1", "b2": "t2"})
        assert detect_conflict(snap, current) is False


class TestConflictErrorRaising:
    def test_error_has_correct_context(self):
        with pytest.raises(NotionifyDiffConflictError) as exc_info:
            raise NotionifyDiffConflictError(
                message="Page modified",
                context={
                    "page_id": "page-123",
                    "snapshot_time": "2024-01-15T10:00:00Z",
                    "detected_time": "2024-01-15T10:05:00Z",
                },
            )
        assert exc_info.value.context["page_id"] == "page-123"
