"""Shared test fixtures for the notionify test suite."""

from __future__ import annotations

import pytest

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer


@pytest.fixture
def config() -> NotionifyConfig:
    """Default test configuration with a dummy token."""
    return NotionifyConfig(token="test_token_1234")


@pytest.fixture
def converter(config: NotionifyConfig) -> MarkdownToNotionConverter:
    """Markdown-to-Notion converter using the default test config."""
    return MarkdownToNotionConverter(config)


@pytest.fixture
def renderer(config: NotionifyConfig) -> NotionToMarkdownRenderer:
    """Notion-to-Markdown renderer using the default test config."""
    return NotionToMarkdownRenderer(config)
