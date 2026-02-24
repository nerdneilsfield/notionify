"""Integration tests for the Notion API.

These tests require a real Notion API token and a test page.
Set NOTION_TOKEN and NOTION_TEST_PAGE_ID environment variables to run them.

Usage:
    NOTION_TOKEN=ntn_xxx NOTION_TEST_PAGE_ID=xxx pytest tests/integration/ -v
"""
import os
import pytest

# Skip entire module if no token is configured
pytestmark = pytest.mark.skipif(
    not os.environ.get("NOTION_TOKEN"),
    reason="NOTION_TOKEN not set; skipping integration tests",
)

@pytest.fixture
def token():
    return os.environ["NOTION_TOKEN"]

@pytest.fixture
def page_id():
    pid = os.environ.get("NOTION_TEST_PAGE_ID")
    if not pid:
        pytest.skip("NOTION_TEST_PAGE_ID not set")
    return pid

@pytest.fixture
def client(token):
    from notionify import NotionifyClient
    with NotionifyClient(token=token) as c:
        yield c

@pytest.fixture
async def async_client(token):
    from notionify import AsyncNotionifyClient
    async with AsyncNotionifyClient(token=token) as c:
        yield c


class TestCreatePage:
    """Integration tests for page creation."""

    def test_create_page_basic(self, client, page_id):
        result = client.create_page_with_markdown(
            parent_id=page_id,
            title="Integration Test Page",
            markdown="# Test\n\nHello from notionify integration tests.",
        )
        assert result.page_id
        assert result.url
        assert result.blocks_created > 0

    def test_create_page_with_code(self, client, page_id):
        md = "# Code Test\n\n```python\nprint('hello')\n```"
        result = client.create_page_with_markdown(
            parent_id=page_id,
            title="Code Test Page",
            markdown=md,
        )
        assert result.blocks_created >= 2


class TestAppendMarkdown:
    """Integration tests for appending content."""

    def test_append_paragraph(self, client, page_id):
        result = client.append_markdown(
            page_id=page_id,
            markdown="Appended paragraph from integration test.",
        )
        assert result.blocks_appended > 0


class TestExportPage:
    """Integration tests for page export."""

    def test_export_to_markdown(self, client, page_id):
        md = client.page_to_markdown(page_id=page_id)
        assert isinstance(md, str)
        assert len(md) > 0


class TestAsyncOperations:
    """Async integration tests."""

    async def test_async_create_page(self, async_client, page_id):
        result = await async_client.create_page_with_markdown(
            parent_id=page_id,
            title="Async Integration Test",
            markdown="# Async\n\nHello from async client.",
        )
        assert result.page_id

    async def test_async_export(self, async_client, page_id):
        md = await async_client.page_to_markdown(page_id=page_id)
        assert isinstance(md, str)


class TestErrorHandling:
    """Integration tests for error handling."""

    def test_invalid_page_id_raises(self, client):
        from notionify import NotionifyNotFoundError
        with pytest.raises(NotionifyNotFoundError):
            client.page_to_markdown(page_id="00000000-0000-0000-0000-000000000000")

    def test_invalid_token_raises(self):
        from notionify import NotionifyClient, NotionifyAuthError
        with NotionifyClient(token="ntn_invalid_token") as c:
            with pytest.raises((NotionifyAuthError, Exception)):
                c.page_to_markdown(page_id="00000000-0000-0000-0000-000000000000")
