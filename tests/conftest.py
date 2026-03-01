"""Shared test fixtures for the notionify test suite."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, settings

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer

# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------
# "dev" profile: fast feedback loop for local development.
settings.register_profile(
    "dev",
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow],
)
# "ci" profile: thorough checks for CI pipelines.
settings.register_profile(
    "ci",
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)
# Default profile keeps standard settings.
settings.register_profile(
    "default",
    suppress_health_check=[HealthCheck.too_slow],
)
# Load profile from HYPOTHESIS_PROFILE env var (or fall back to "default").
settings.load_profile("default")


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
