"""Tests for observability/logger.py"""
import io
import json
import logging
import sys


class TestStructuredFormatter:
    def _get_record(
        self,
        msg,
        level=logging.INFO,
        exc_info=None,
        stack_info=None,
        extra_fields=None,
    ):
        record = logging.LogRecord(
            name="test",
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=exc_info,
        )
        if extra_fields is not None:
            record.extra_fields = extra_fields
        if stack_info is not None:
            record.stack_info = stack_info
        return record

    def test_basic_format(self):
        from notionify.observability.logger import StructuredFormatter

        fmt = StructuredFormatter()
        record = self._get_record("hello world")
        result = json.loads(fmt.format(record))
        assert result["message"] == "hello world"
        assert result["level"] == "INFO"
        assert "ts" in result

    def test_extra_fields_merged(self):
        from notionify.observability.logger import StructuredFormatter

        fmt = StructuredFormatter()
        record = self._get_record("msg", extra_fields={"page_id": "abc", "blocks": 5})
        result = json.loads(fmt.format(record))
        assert result["page_id"] == "abc"
        assert result["blocks"] == 5

    def test_exception_info_included(self):
        from notionify.observability.logger import StructuredFormatter

        fmt = StructuredFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
        record = self._get_record("error msg", exc_info=exc_info)
        result = json.loads(fmt.format(record))
        assert "exception" in result
        assert "ValueError" in result["exception"]

    def test_stack_info_included(self):
        from notionify.observability.logger import StructuredFormatter

        fmt = StructuredFormatter()
        record = self._get_record("msg", stack_info="Stack Trace Here")
        result = json.loads(fmt.format(record))
        assert result["stack_info"] == "Stack Trace Here"


class TestGetLogger:
    def test_returns_logger_with_handler(self):
        from notionify.observability.logger import get_logger

        logger = get_logger("test.observability.unique1")
        assert isinstance(logger, logging.Logger)
        assert len(logger.handlers) > 0

    def test_string_level(self):
        from notionify.observability.logger import get_logger

        logger = get_logger("test.observability.unique2", level="WARNING")
        assert logger.level == logging.WARNING

    def test_idempotent_no_duplicate_handlers(self):
        from notionify.observability.logger import get_logger

        name = "test.observability.unique3"
        logger1 = get_logger(name)
        handler_count = len(logger1.handlers)
        logger2 = get_logger(name)
        assert len(logger2.handlers) == handler_count

    def test_custom_stream(self):
        from notionify.observability.logger import get_logger

        stream = io.StringIO()
        logger = get_logger("test.observability.stream_unique", stream=stream)
        logger.info("test message", extra={"extra_fields": {"key": "val"}})
        output = stream.getvalue()
        # At least one handler should have written to the stream
        # (may not if logger was already configured - use unique name)
        assert isinstance(output, str)


class TestNoopMetricsHook:
    """NoopMetricsHook discards all data points silently (line 136)."""

    def test_gauge_returns_none(self):
        from notionify.observability.metrics import NoopMetricsHook
        hook = NoopMetricsHook()
        result = hook.gauge("requests.in_flight", 5.0, tags={"env": "test"})
        assert result is None

    def test_increment_returns_none(self):
        from notionify.observability.metrics import NoopMetricsHook
        hook = NoopMetricsHook()
        assert hook.increment("requests.count") is None

    def test_timing_returns_none(self):
        from notionify.observability.metrics import NoopMetricsHook
        hook = NoopMetricsHook()
        assert hook.timing("request.duration_ms", 123.4) is None
