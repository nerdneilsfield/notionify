"""Iteration 23: Metrics emission, conflict detection, and logger edge case tests.

Covers:
- Client._emit_conversion_metrics calls MetricsHook correctly
- DiffExecutor._emit_diff_metrics calls MetricsHook with correct op counts
- Conflict detection datetime edge cases (malformed ISO, timezone, empty)
- Logger edge cases (non-serializable objects, missing extra_fields attr)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from typing import Any
from unittest.mock import MagicMock

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.diff.conflict import detect_conflict, take_snapshot
from notionify.diff.executor import DiffExecutor, _emit_diff_metrics
from notionify.models import (
    ConversionResult,
    ConversionWarning,
    DiffOp,
    DiffOpType,
    PageSnapshot,
)
from notionify.observability.logger import get_logger

# ---------------------------------------------------------------------------
# Recording metrics hook (same as in test_metrics_hook.py)
# ---------------------------------------------------------------------------

class _RecordingHook:
    """A metrics backend that records all calls for assertion."""

    def __init__(self) -> None:
        self.increments: list[dict[str, Any]] = []
        self.timings: list[dict[str, Any]] = []
        self.gauges: list[dict[str, Any]] = []

    def increment(
        self,
        name: str,
        value: int = 1,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.increments.append({"name": name, "value": value, "tags": tags})

    def timing(
        self,
        name: str,
        ms: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.timings.append({"name": name, "ms": ms, "tags": tags})

    def gauge(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.gauges.append({"name": name, "value": value, "tags": tags})


# =========================================================================
# 1. TestEmitConversionMetrics
# =========================================================================


class TestEmitConversionMetrics:
    """Test that client._emit_conversion_metrics sends correct metric calls."""

    def _make_client_with_recorder(self):
        """Create a NotionifyClient with a recording metrics hook."""
        from notionify.client import NotionifyClient

        hook = _RecordingHook()
        client = NotionifyClient(token="test", metrics=hook)
        return client, hook

    def test_blocks_created_total_by_type(self):
        """Blocks are counted by type and emitted as blocks_created_total."""
        client, hook = self._make_client_with_recorder()
        conversion = ConversionResult(
            blocks=[
                {"type": "paragraph", "paragraph": {}},
                {"type": "paragraph", "paragraph": {}},
                {"type": "heading_1", "heading_1": {}},
                {"type": "code", "code": {}},
            ],
            warnings=[],
            images=[],
        )
        client._emit_conversion_metrics(conversion)

        block_metrics = [
            m for m in hook.increments
            if m["name"] == "notionify.blocks_created_total"
        ]
        # Should have 3 entries: paragraph(2), heading_1(1), code(1)
        assert len(block_metrics) == 3
        counts = {m["tags"]["block_type"]: m["value"] for m in block_metrics}
        assert counts["paragraph"] == 2
        assert counts["heading_1"] == 1
        assert counts["code"] == 1

    def test_conversion_warnings_total_by_code(self):
        """Warnings are counted by code and emitted as conversion_warnings_total."""
        client, hook = self._make_client_with_recorder()
        conversion = ConversionResult(
            blocks=[],
            warnings=[
                ConversionWarning(code="IMAGE_FALLBACK", message="skip"),
                ConversionWarning(code="IMAGE_FALLBACK", message="skip2"),
                ConversionWarning(code="UNSUPPORTED_BLOCK", message="skip3"),
            ],
            images=[],
        )
        client._emit_conversion_metrics(conversion)

        warn_metrics = [
            m for m in hook.increments
            if m["name"] == "notionify.conversion_warnings_total"
        ]
        assert len(warn_metrics) == 2
        counts = {m["tags"]["code"]: m["value"] for m in warn_metrics}
        assert counts["IMAGE_FALLBACK"] == 2
        assert counts["UNSUPPORTED_BLOCK"] == 1

    def test_empty_conversion_emits_no_metrics(self):
        """An empty conversion should emit no metrics."""
        client, hook = self._make_client_with_recorder()
        conversion = ConversionResult(blocks=[], warnings=[], images=[])
        client._emit_conversion_metrics(conversion)
        assert len(hook.increments) == 0

    def test_unknown_block_type_counted(self):
        """Blocks without a type key fall back to 'unknown'."""
        client, hook = self._make_client_with_recorder()
        conversion = ConversionResult(
            blocks=[{}, {"type": "paragraph", "paragraph": {}}],
            warnings=[],
            images=[],
        )
        client._emit_conversion_metrics(conversion)
        counts = {
            m["tags"]["block_type"]: m["value"]
            for m in hook.increments
            if m["name"] == "notionify.blocks_created_total"
        }
        assert counts["unknown"] == 1
        assert counts["paragraph"] == 1

    def test_real_conversion_emits_metrics(self):
        """Convert actual markdown and verify metrics are emitted."""
        hook = _RecordingHook()
        from notionify.client import NotionifyClient

        client = NotionifyClient(token="test", metrics=hook)
        converter = MarkdownToNotionConverter(client._config)
        md = "# Hello\n\nWorld with **bold**.\n\n- item 1\n- item 2\n"
        conversion = converter.convert(md)
        client._emit_conversion_metrics(conversion)

        block_metrics = [
            m for m in hook.increments
            if m["name"] == "notionify.blocks_created_total"
        ]
        assert len(block_metrics) > 0
        total_blocks = sum(m["value"] for m in block_metrics)
        assert total_blocks == len(conversion.blocks)


# =========================================================================
# 2. TestEmitDiffMetrics
# =========================================================================


class TestEmitDiffMetrics:
    """Test that _emit_diff_metrics sends correct metric calls."""

    def test_diff_ops_total_by_type(self):
        """Diff ops are counted by type and emitted as diff_ops_total."""
        hook = _RecordingHook()
        ops = [
            DiffOp(op_type=DiffOpType.KEEP, existing_id="b1"),
            DiffOp(op_type=DiffOpType.KEEP, existing_id="b2"),
            DiffOp(op_type=DiffOpType.UPDATE, existing_id="b3", new_block={}),
            DiffOp(op_type=DiffOpType.INSERT, new_block={}),
            DiffOp(op_type=DiffOpType.DELETE, existing_id="b4"),
            DiffOp(op_type=DiffOpType.DELETE, existing_id="b5"),
        ]
        _emit_diff_metrics(hook, ops)

        diff_metrics = [
            m for m in hook.increments
            if m["name"] == "notionify.diff_ops_total"
        ]
        counts = {m["tags"]["op_type"]: m["value"] for m in diff_metrics}
        assert counts["keep"] == 2
        assert counts["update"] == 1
        assert counts["insert"] == 1
        assert counts["delete"] == 2

    def test_empty_ops_emits_no_metrics(self):
        """No ops should produce no metric calls."""
        hook = _RecordingHook()
        _emit_diff_metrics(hook, [])
        assert len(hook.increments) == 0

    def test_all_keep_ops(self):
        """All KEEP ops produce a single metric entry."""
        hook = _RecordingHook()
        ops = [
            DiffOp(op_type=DiffOpType.KEEP, existing_id=f"b{i}")
            for i in range(10)
        ]
        _emit_diff_metrics(hook, ops)
        assert len(hook.increments) == 1
        assert hook.increments[0]["tags"]["op_type"] == "keep"
        assert hook.increments[0]["value"] == 10

    def test_replace_ops_counted(self):
        """REPLACE ops are counted correctly."""
        hook = _RecordingHook()
        ops = [
            DiffOp(
                op_type=DiffOpType.REPLACE,
                existing_id="b1",
                new_block={},
            ),
            DiffOp(
                op_type=DiffOpType.REPLACE,
                existing_id="b2",
                new_block={},
            ),
        ]
        _emit_diff_metrics(hook, ops)
        counts = {m["tags"]["op_type"]: m["value"] for m in hook.increments}
        assert counts["replace"] == 2

    def test_executor_calls_emit_diff_metrics(self):
        """DiffExecutor.execute() calls _emit_diff_metrics at the end."""
        hook = _RecordingHook()
        config = NotionifyConfig(token="test", metrics=hook)
        mock_api = MagicMock()
        mock_api.update.return_value = {"id": "u", "type": "paragraph"}
        executor = DiffExecutor(mock_api, config)

        ops = [
            DiffOp(op_type=DiffOpType.KEEP, existing_id="b1"),
            DiffOp(
                op_type=DiffOpType.UPDATE,
                existing_id="b2",
                new_block={
                    "type": "paragraph",
                    "paragraph": {"rich_text": [], "color": "default"},
                },
            ),
        ]
        result = executor.execute("page-1", ops)
        assert result.blocks_kept == 1

        diff_metrics = [
            m for m in hook.increments
            if m["name"] == "notionify.diff_ops_total"
        ]
        counts = {m["tags"]["op_type"]: m["value"] for m in diff_metrics}
        assert counts["keep"] == 1
        assert counts["update"] == 1


# =========================================================================
# 3. TestConflictDetectionEdgeCases
# =========================================================================


class TestConflictDetectionEdgeCases:
    """Edge cases for conflict detection and snapshot building."""

    def test_malformed_iso_timestamp_falls_back_to_min(self):
        """A malformed ISO-8601 timestamp should fall back to datetime.min."""
        page = {"last_edited_time": "not-a-date"}
        snap = take_snapshot("p1", page, [])
        assert snap.last_edited == datetime.min

    def test_empty_string_last_edited(self):
        """An empty string for last_edited_time → datetime.min."""
        page = {"last_edited_time": ""}
        snap = take_snapshot("p1", page, [])
        assert snap.last_edited == datetime.min

    def test_timezone_z_suffix_normalized(self):
        """ISO timestamps with 'Z' suffix are parsed correctly."""
        page = {"last_edited_time": "2025-06-15T12:00:00.000Z"}
        snap = take_snapshot("p1", page, [])
        assert snap.last_edited.tzinfo is not None
        assert snap.last_edited.year == 2025
        assert snap.last_edited.month == 6

    def test_timezone_offset_parsed(self):
        """ISO timestamps with explicit offset are parsed correctly."""
        page = {"last_edited_time": "2025-06-15T12:00:00+05:30"}
        snap = take_snapshot("p1", page, [])
        assert snap.last_edited.utcoffset() is not None

    def test_microsecond_precision(self):
        """Timestamps with microsecond precision are preserved."""
        page = {"last_edited_time": "2025-06-15T12:00:00.123456Z"}
        snap = take_snapshot("p1", page, [])
        assert snap.last_edited.microsecond == 123456

    def test_block_with_empty_id_skipped(self):
        """Blocks with id='' are skipped."""
        blocks = [{"id": "", "last_edited_time": "2025-01-01T00:00:00Z"}]
        snap = take_snapshot("p1", {"last_edited_time": "2025-01-01T00:00:00Z"}, blocks)
        assert snap.block_etags == {}

    def test_many_blocks_snapshot(self):
        """Snapshot building handles 1000 blocks without issue."""
        blocks = [
            {
                "id": f"block-{i:04d}",
                "last_edited_time": "2025-01-01T00:00:00Z",
            }
            for i in range(1000)
        ]
        snap = take_snapshot(
            "p1",
            {"last_edited_time": "2025-01-01T00:00:00Z"},
            blocks,
        )
        assert len(snap.block_etags) == 1000

    def test_detect_conflict_same_microsecond(self):
        """Snapshots at the exact same microsecond should NOT conflict."""
        ts = datetime(2025, 6, 15, 12, 0, 0, 123456, tzinfo=timezone.utc)
        s1 = PageSnapshot(page_id="p1", last_edited=ts, block_etags={})
        s2 = PageSnapshot(page_id="p1", last_edited=ts, block_etags={})
        assert detect_conflict(s1, s2) is False

    def test_detect_conflict_one_microsecond_diff(self):
        """A 1-microsecond difference should be detected as a conflict."""
        ts1 = datetime(2025, 6, 15, 12, 0, 0, 123456, tzinfo=timezone.utc)
        ts2 = datetime(2025, 6, 15, 12, 0, 0, 123457, tzinfo=timezone.utc)
        s1 = PageSnapshot(page_id="p1", last_edited=ts1, block_etags={})
        s2 = PageSnapshot(page_id="p1", last_edited=ts2, block_etags={})
        assert detect_conflict(s1, s2) is True

    def test_detect_conflict_block_etag_case_sensitive(self):
        """Block etag comparison is case-sensitive."""
        s1 = PageSnapshot(
            page_id="p1",
            last_edited=datetime.min,
            block_etags={"b1": "ABC"},
        )
        s2 = PageSnapshot(
            page_id="p1",
            last_edited=datetime.min,
            block_etags={"b1": "abc"},
        )
        assert detect_conflict(s1, s2) is True

    def test_detect_conflict_multiple_blocks_one_changed(self):
        """Only one block changed out of many should still detect conflict."""
        etags = {f"b{i}": "v1" for i in range(50)}
        s1 = PageSnapshot(
            page_id="p1", last_edited=datetime.min, block_etags=etags,
        )
        etags_modified = dict(etags)
        etags_modified["b25"] = "v2"
        s2 = PageSnapshot(
            page_id="p1", last_edited=datetime.min, block_etags=etags_modified,
        )
        assert detect_conflict(s1, s2) is True


# =========================================================================
# 4. TestLoggerEdgeCases
# =========================================================================


class TestLoggerEdgeCases:
    """Edge cases for the structured JSON logger."""

    def test_non_serializable_object_in_extra_fields(self):
        """Non-serializable objects in extra_fields should be str()-ified via default=str."""
        stream = StringIO()
        logger = get_logger(
            "notionify.test_nonserializable",
            stream=stream,
        )
        logger.info(
            "test",
            extra={"extra_fields": {"obj": object(), "set": {1, 2, 3}}},
        )
        output = stream.getvalue()
        data = json.loads(output)
        assert "obj" in data
        assert "set" in data
        # object() repr starts with '<'
        assert data["obj"].startswith("<")

    def test_empty_extra_fields(self):
        """An empty extra_fields dict adds no extra keys."""
        stream = StringIO()
        logger = get_logger("notionify.test_empty_extra", stream=stream)
        logger.info("test", extra={"extra_fields": {}})
        output = stream.getvalue()
        data = json.loads(output)
        assert data["message"] == "test"
        # Only standard keys
        assert set(data.keys()) == {"ts", "level", "logger", "message"}

    def test_no_extra_fields_attribute(self):
        """When extra_fields is not set, only standard keys appear."""
        stream = StringIO()
        logger = get_logger("notionify.test_no_extra", stream=stream)
        logger.info("plain message")
        output = stream.getvalue()
        data = json.loads(output)
        assert data["message"] == "plain message"
        assert "extra_fields" not in data

    def test_formatter_with_exception(self):
        """Exception info is serialised into the 'exception' key."""
        stream = StringIO()
        logger = get_logger("notionify.test_exc", stream=stream)
        try:
            raise ValueError("test error")
        except ValueError:
            logger.exception("caught error")
        output = stream.getvalue()
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]
        assert "test error" in data["exception"]

    def test_formatter_output_is_single_line(self):
        """Even with newlines in the message, output is a single JSON line."""
        stream = StringIO()
        logger = get_logger("notionify.test_singleline", stream=stream)
        logger.info("line1\nline2\nline3")
        output = stream.getvalue().strip()
        # Should parse as valid JSON
        data = json.loads(output)
        assert "line1\nline2\nline3" in data["message"]

    def test_large_extra_fields_payload(self):
        """A large extra_fields payload should be serialised without error."""
        stream = StringIO()
        logger = get_logger("notionify.test_large", stream=stream)
        large_data = {f"key_{i}": f"value_{i}" for i in range(1000)}
        logger.info("large", extra={"extra_fields": large_data})
        output = stream.getvalue()
        data = json.loads(output)
        assert data["key_0"] == "value_0"
        assert data["key_999"] == "value_999"

    def test_get_logger_idempotent(self):
        """Calling get_logger twice with the same name returns the same logger."""
        l1 = get_logger("notionify.test_idempotent")
        l2 = get_logger("notionify.test_idempotent")
        assert l1 is l2
        # Should not add duplicate handlers
        handler_count = len(l1.handlers)
        assert handler_count == 1

    def test_formatter_with_level_string(self):
        """Logger created with string level works correctly."""
        stream = StringIO()
        logger = get_logger(
            "notionify.test_strlevel",
            level="WARNING",
            stream=stream,
        )
        logger.info("should not appear")
        logger.warning("should appear")
        output = stream.getvalue()
        assert "should appear" in output
        # INFO should be filtered out
        lines = [line for line in output.strip().split("\n") if line]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["level"] == "WARNING"

    def test_formatter_timestamp_is_utc(self):
        """The 'ts' field should be a UTC ISO-8601 timestamp."""
        stream = StringIO()
        logger = get_logger("notionify.test_utc", stream=stream)
        logger.info("ts check")
        output = stream.getvalue()
        data = json.loads(output)
        ts = data["ts"]
        assert "+00:00" in ts or ts.endswith("Z")
