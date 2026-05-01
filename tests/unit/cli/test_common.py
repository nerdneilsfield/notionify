from __future__ import annotations

from pathlib import Path

import pytest

from notionify.cli._common import InvalidIdError, parse_id, read_markdown, strip_images


def test_parse_plain_uuid() -> None:
    assert parse_id("12345678-1234-1234-1234-123456789abc") == (
        "12345678-1234-1234-1234-123456789abc"
    )


def test_parse_notion_url_uuid_without_dashes() -> None:
    assert parse_id("https://www.notion.so/Test-12345678123412341234123456789abc") == (
        "12345678-1234-1234-1234-123456789abc"
    )


def test_parse_invalid_id() -> None:
    with pytest.raises(InvalidIdError):
        parse_id("not-an-id")


def test_read_markdown(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("hello", encoding="utf-8")
    assert read_markdown(path) == "hello"


def test_strip_images() -> None:
    assert strip_images("before ![alt text](image.png) after") == "before  after"
