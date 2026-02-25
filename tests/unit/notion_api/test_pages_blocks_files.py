"""Unit tests for PageAPI, AsyncPageAPI, BlockAPI, AsyncBlockAPI, FileAPI, AsyncFileAPI.

All HTTP calls go through a transport mock (MagicMock / AsyncMock).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from notionify.notion_api.blocks import AsyncBlockAPI, BlockAPI
from notionify.notion_api.files import AsyncFileAPI, FileAPI
from notionify.notion_api.pages import AsyncPageAPI, PageAPI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sync_transport(**kwargs):
    t = MagicMock()
    for attr, val in kwargs.items():
        setattr(t, attr, val)
    return t


def make_async_transport(**kwargs):
    t = MagicMock()
    for attr, val in kwargs.items():
        setattr(t, attr, val)
    return t


async def _agen(*items):
    """Async generator yielding each item."""
    for item in items:
        yield item


# ===========================================================================
# PageAPI --sync
# ===========================================================================

class TestPageAPISync:
    def test_create_without_children(self):
        t = MagicMock()
        t.request.return_value = {"id": "pg-1"}
        api = PageAPI(t)
        result = api.create(
            parent={"page_id": "p1"},
            properties={"title": [{"text": {"content": "T"}}]},
        )
        t.request.assert_called_once_with(
            "POST",
            "/pages",
            json={
                "parent": {"page_id": "p1"},
                "properties": {"title": [{"text": {"content": "T"}}]},
            },
        )
        assert result == {"id": "pg-1"}

    def test_create_with_children(self):
        t = MagicMock()
        t.request.return_value = {"id": "pg-2"}
        api = PageAPI(t)
        children = [{"object": "block", "type": "paragraph"}]
        result = api.create(
            parent={"database_id": "db-1"},
            properties={"Name": {"title": [{"text": {"content": "Row"}}]}},
            children=children,
        )
        t.request.assert_called_once_with(
            "POST",
            "/pages",
            json={
                "parent": {"database_id": "db-1"},
                "properties": {"Name": {"title": [{"text": {"content": "Row"}}]}},
                "children": children,
            },
        )
        assert result == {"id": "pg-2"}

    def test_create_children_none_not_in_body(self):
        """When children=None, the key must NOT appear in the request body."""
        t = MagicMock()
        t.request.return_value = {}
        api = PageAPI(t)
        api.create(parent={"page_id": "p"}, properties={}, children=None)
        _, _, kwargs = t.request.mock_calls[0]
        assert "children" not in kwargs.get("json", {})

    def test_retrieve(self):
        t = MagicMock()
        t.request.return_value = {"id": "pg-3", "object": "page"}
        api = PageAPI(t)
        result = api.retrieve("pg-3")
        t.request.assert_called_once_with("GET", "/pages/pg-3")
        assert result["id"] == "pg-3"

    def test_update_properties_only(self):
        t = MagicMock()
        t.request.return_value = {"id": "pg-4"}
        api = PageAPI(t)
        props = {"title": [{"text": {"content": "New"}}]}
        result = api.update("pg-4", properties=props)
        t.request.assert_called_once_with(
            "PATCH", "/pages/pg-4", json={"properties": props}
        )
        assert result == {"id": "pg-4"}

    def test_update_archived_only(self):
        t = MagicMock()
        t.request.return_value = {"id": "pg-5", "archived": True}
        api = PageAPI(t)
        result = api.update("pg-5", archived=True)
        t.request.assert_called_once_with(
            "PATCH", "/pages/pg-5", json={"archived": True}
        )
        assert result["archived"] is True

    def test_update_both(self):
        t = MagicMock()
        t.request.return_value = {"id": "pg-6"}
        api = PageAPI(t)
        props = {"Name": {"title": []}}
        _result = api.update("pg-6", properties=props, archived=False)
        t.request.assert_called_once_with(
            "PATCH",
            "/pages/pg-6",
            json={"properties": props, "archived": False},
        )

    def test_update_empty(self):
        """update() with no args sends an empty body."""
        t = MagicMock()
        t.request.return_value = {"id": "pg-7"}
        api = PageAPI(t)
        api.update("pg-7")
        t.request.assert_called_once_with("PATCH", "/pages/pg-7", json={})

    def test_update_archived_false(self):
        """archived=False must be included (falsy but not None)."""
        t = MagicMock()
        t.request.return_value = {}
        api = PageAPI(t)
        api.update("pg-8", archived=False)
        _, _, kwargs = t.request.mock_calls[0]
        assert kwargs["json"]["archived"] is False


# ===========================================================================
# AsyncPageAPI
# ===========================================================================

class TestPageAPIAsync:
    async def test_create_without_children(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "pg-a1"})
        api = AsyncPageAPI(t)
        result = await api.create(
            parent={"page_id": "pa"},
            properties={"title": [{"text": {"content": "Async"}}]},
        )
        t.request.assert_called_once_with(
            "POST",
            "/pages",
            json={
                "parent": {"page_id": "pa"},
                "properties": {"title": [{"text": {"content": "Async"}}]},
            },
        )
        assert result == {"id": "pg-a1"}

    async def test_create_with_children(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "pg-a2"})
        api = AsyncPageAPI(t)
        children = [{"type": "paragraph"}]
        result = await api.create(
            parent={"database_id": "db-a"},
            properties={},
            children=children,
        )
        _, _, kwargs = t.request.mock_calls[0]
        assert kwargs["json"]["children"] == children
        assert result == {"id": "pg-a2"}

    async def test_create_children_none_not_in_body(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={})
        api = AsyncPageAPI(t)
        await api.create(parent={"page_id": "p"}, properties={}, children=None)
        _, _, kwargs = t.request.mock_calls[0]
        assert "children" not in kwargs.get("json", {})

    async def test_retrieve(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "pg-a3"})
        api = AsyncPageAPI(t)
        result = await api.retrieve("pg-a3")
        t.request.assert_called_once_with("GET", "/pages/pg-a3")
        assert result["id"] == "pg-a3"

    async def test_update_properties_only(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "pg-a4"})
        api = AsyncPageAPI(t)
        props = {"title": []}
        await api.update("pg-a4", properties=props)
        t.request.assert_called_once_with(
            "PATCH", "/pages/pg-a4", json={"properties": props}
        )

    async def test_update_archived_only(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "pg-a5", "archived": True})
        api = AsyncPageAPI(t)
        result = await api.update("pg-a5", archived=True)
        t.request.assert_called_once_with(
            "PATCH", "/pages/pg-a5", json={"archived": True}
        )
        assert result["archived"] is True

    async def test_update_both(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={})
        api = AsyncPageAPI(t)
        props = {"x": 1}
        await api.update("pg-a6", properties=props, archived=False)
        t.request.assert_called_once_with(
            "PATCH",
            "/pages/pg-a6",
            json={"properties": props, "archived": False},
        )

    async def test_update_empty(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={})
        api = AsyncPageAPI(t)
        await api.update("pg-a7")
        t.request.assert_called_once_with("PATCH", "/pages/pg-a7", json={})


# ===========================================================================
# BlockAPI --sync
# ===========================================================================

class TestBlockAPISync:
    def test_retrieve(self):
        t = MagicMock()
        t.request.return_value = {"id": "blk-1", "type": "paragraph"}
        api = BlockAPI(t)
        result = api.retrieve("blk-1")
        t.request.assert_called_once_with("GET", "/blocks/blk-1")
        assert result["id"] == "blk-1"

    def test_update(self):
        t = MagicMock()
        t.request.return_value = {"id": "blk-2"}
        api = BlockAPI(t)
        payload = {"paragraph": {"rich_text": [{"text": {"content": "hi"}}]}}
        result = api.update("blk-2", payload)
        t.request.assert_called_once_with("PATCH", "/blocks/blk-2", json=payload)
        assert result == {"id": "blk-2"}

    def test_delete(self):
        t = MagicMock()
        t.request.return_value = {"id": "blk-3", "archived": True}
        api = BlockAPI(t)
        result = api.delete("blk-3")
        t.request.assert_called_once_with("DELETE", "/blocks/blk-3")
        assert result["archived"] is True

    def test_get_children_returns_list(self):
        t = MagicMock()
        t.paginate.return_value = iter([{"id": "c1"}, {"id": "c2"}])
        api = BlockAPI(t)
        result = api.get_children("blk-4")
        t.paginate.assert_called_once_with("/blocks/blk-4/children", method="GET")
        assert result == [{"id": "c1"}, {"id": "c2"}]

    def test_get_children_empty(self):
        t = MagicMock()
        t.paginate.return_value = iter([])
        api = BlockAPI(t)
        result = api.get_children("blk-empty")
        assert result == []

    def test_get_children_single(self):
        t = MagicMock()
        t.paginate.return_value = iter([{"id": "only"}])
        api = BlockAPI(t)
        result = api.get_children("blk-5")
        assert len(result) == 1
        assert result[0]["id"] == "only"

    def test_append_children_without_after(self):
        t = MagicMock()
        t.request.return_value = {"results": []}
        api = BlockAPI(t)
        children = [{"type": "paragraph"}]
        result = api.append_children("blk-6", children)
        t.request.assert_called_once_with(
            "PATCH",
            "/blocks/blk-6/children",
            json={"children": children},
        )
        assert result == {"results": []}

    def test_append_children_with_after(self):
        t = MagicMock()
        t.request.return_value = {"results": []}
        api = BlockAPI(t)
        children = [{"type": "heading_1"}]
        api.append_children("blk-7", children, after="blk-x")
        t.request.assert_called_once_with(
            "PATCH",
            "/blocks/blk-7/children",
            json={"children": children, "after": "blk-x"},
        )

    def test_append_children_after_none_not_in_body(self):
        """after=None must NOT put an 'after' key in the request body."""
        t = MagicMock()
        t.request.return_value = {}
        api = BlockAPI(t)
        api.append_children("blk-8", [])
        _, _, kwargs = t.request.mock_calls[0]
        assert "after" not in kwargs.get("json", {})


# ===========================================================================
# AsyncBlockAPI
# ===========================================================================

class TestBlockAPIAsync:
    async def test_retrieve(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "blk-a1"})
        api = AsyncBlockAPI(t)
        result = await api.retrieve("blk-a1")
        t.request.assert_called_once_with("GET", "/blocks/blk-a1")
        assert result["id"] == "blk-a1"

    async def test_update(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "blk-a2"})
        api = AsyncBlockAPI(t)
        payload = {"heading_1": {"rich_text": []}}
        result = await api.update("blk-a2", payload)
        t.request.assert_called_once_with("PATCH", "/blocks/blk-a2", json=payload)
        assert result["id"] == "blk-a2"

    async def test_delete(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "blk-a3", "archived": True})
        api = AsyncBlockAPI(t)
        result = await api.delete("blk-a3")
        t.request.assert_called_once_with("DELETE", "/blocks/blk-a3")
        assert result["archived"] is True

    async def test_get_children_returns_list(self):
        t = MagicMock()
        t.paginate = MagicMock(return_value=_agen({"id": "c1"}, {"id": "c2"}))
        api = AsyncBlockAPI(t)
        result = await api.get_children("blk-a4")
        t.paginate.assert_called_once_with("/blocks/blk-a4/children", method="GET")
        assert result == [{"id": "c1"}, {"id": "c2"}]

    async def test_get_children_empty(self):
        t = MagicMock()
        t.paginate = MagicMock(return_value=_agen())
        api = AsyncBlockAPI(t)
        result = await api.get_children("blk-a-empty")
        assert result == []

    async def test_get_children_single(self):
        t = MagicMock()
        t.paginate = MagicMock(return_value=_agen({"id": "only-a"}))
        api = AsyncBlockAPI(t)
        result = await api.get_children("blk-a5")
        assert len(result) == 1
        assert result[0]["id"] == "only-a"

    async def test_append_children_without_after(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"results": []})
        api = AsyncBlockAPI(t)
        children = [{"type": "bulleted_list_item"}]
        _result = await api.append_children("blk-a6", children)
        t.request.assert_called_once_with(
            "PATCH",
            "/blocks/blk-a6/children",
            json={"children": children},
        )

    async def test_append_children_with_after(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={})
        api = AsyncBlockAPI(t)
        children = [{"type": "divider"}]
        await api.append_children("blk-a7", children, after="blk-y")
        t.request.assert_called_once_with(
            "PATCH",
            "/blocks/blk-a7/children",
            json={"children": children, "after": "blk-y"},
        )

    async def test_append_children_after_none_not_in_body(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={})
        api = AsyncBlockAPI(t)
        await api.append_children("blk-a8", [])
        _, _, kwargs = t.request.mock_calls[0]
        assert "after" not in kwargs.get("json", {})


# ===========================================================================
# FileAPI --sync
# ===========================================================================

class TestFileAPISync:
    def test_create_upload_single_part_default(self):
        t = MagicMock()
        t.request.return_value = {"id": "upl-1", "upload_url": "https://s3/1"}
        api = FileAPI(t)
        result = api.create_upload("photo.png", "image/png")
        t.request.assert_called_once_with(
            "POST",
            "/file-uploads",
            json={"name": "photo.png", "content_type": "image/png", "mode": "single_part"},
        )
        assert result["id"] == "upl-1"

    def test_create_upload_multi_part(self):
        t = MagicMock()
        t.request.return_value = {"id": "upl-2"}
        api = FileAPI(t)
        api.create_upload("big.png", "image/png", mode="multi_part")
        t.request.assert_called_once_with(
            "POST",
            "/file-uploads",
            json={"name": "big.png", "content_type": "image/png", "mode": "multi_part"},
        )

    def test_create_upload_explicit_single_part(self):
        t = MagicMock()
        t.request.return_value = {"id": "upl-3"}
        api = FileAPI(t)
        api.create_upload("f.jpg", "image/jpeg", mode="single_part")
        _, _, kwargs = t.request.mock_calls[0]
        assert kwargs["json"]["mode"] == "single_part"

    def test_send_part(self):
        t = MagicMock()
        t.request.return_value = {"etag": "abc"}
        api = FileAPI(t)
        result = api.send_part("https://s3/part1", b"rawbytes", "image/png")
        t.request.assert_called_once_with(
            "PUT",
            "https://s3/part1",
            content=b"rawbytes",
            headers={"Content-Type": "image/png"},
        )
        assert result == {"etag": "abc"}

    def test_send_part_returns_none(self):
        """204 No Content → transport returns None."""
        t = MagicMock()
        t.request.return_value = None
        api = FileAPI(t)
        result = api.send_part("https://s3/part2", b"chunk", "image/png")
        assert result is None

    def test_complete_upload(self):
        t = MagicMock()
        t.request.return_value = {"id": "upl-4", "status": "complete"}
        api = FileAPI(t)
        parts = [{"part_number": 1, "etag": "e1"}, {"part_number": 2, "etag": "e2"}]
        result = api.complete_upload("upl-4", parts)
        t.request.assert_called_once_with(
            "POST",
            "/file-uploads/upl-4/complete",
            json={"parts": parts},
        )
        assert result["status"] == "complete"

    def test_complete_upload_empty_parts(self):
        t = MagicMock()
        t.request.return_value = {"status": "complete"}
        api = FileAPI(t)
        api.complete_upload("upl-5", [])
        _, _, kwargs = t.request.mock_calls[0]
        assert kwargs["json"]["parts"] == []

    def test_retrieve_upload(self):
        t = MagicMock()
        t.request.return_value = {"id": "upl-6", "status": "pending"}
        api = FileAPI(t)
        result = api.retrieve_upload("upl-6")
        t.request.assert_called_once_with("GET", "/file-uploads/upl-6")
        assert result["status"] == "pending"

    def test_retrieve_upload_complete_status(self):
        t = MagicMock()
        t.request.return_value = {"id": "upl-7", "status": "complete"}
        api = FileAPI(t)
        result = api.retrieve_upload("upl-7")
        assert result["status"] == "complete"


# ===========================================================================
# AsyncFileAPI
# ===========================================================================

class TestFileAPIAsync:
    async def test_create_upload_single_part_default(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "upl-a1"})
        api = AsyncFileAPI(t)
        result = await api.create_upload("photo.png", "image/png")
        t.request.assert_called_once_with(
            "POST",
            "/file-uploads",
            json={"name": "photo.png", "content_type": "image/png", "mode": "single_part"},
        )
        assert result["id"] == "upl-a1"

    async def test_create_upload_multi_part(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "upl-a2"})
        api = AsyncFileAPI(t)
        await api.create_upload("large.png", "image/png", mode="multi_part")
        _, _, kwargs = t.request.mock_calls[0]
        assert kwargs["json"]["mode"] == "multi_part"

    async def test_send_part(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"etag": "xyz"})
        api = AsyncFileAPI(t)
        result = await api.send_part("https://s3/ap1", b"bytes", "image/png")
        t.request.assert_called_once_with(
            "PUT",
            "https://s3/ap1",
            content=b"bytes",
            headers={"Content-Type": "image/png"},
        )
        assert result == {"etag": "xyz"}

    async def test_send_part_returns_none(self):
        t = MagicMock()
        t.request = AsyncMock(return_value=None)
        api = AsyncFileAPI(t)
        result = await api.send_part("https://s3/ap2", b"chunk", "image/png")
        assert result is None

    async def test_complete_upload(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "upl-a3", "status": "complete"})
        api = AsyncFileAPI(t)
        parts = [{"part_number": 1, "etag": "e1"}]
        result = await api.complete_upload("upl-a3", parts)
        t.request.assert_called_once_with(
            "POST",
            "/file-uploads/upl-a3/complete",
            json={"parts": parts},
        )
        assert result["status"] == "complete"

    async def test_complete_upload_empty_parts(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={})
        api = AsyncFileAPI(t)
        await api.complete_upload("upl-a4", [])
        _, _, kwargs = t.request.mock_calls[0]
        assert kwargs["json"]["parts"] == []

    async def test_retrieve_upload(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "upl-a5", "status": "pending"})
        api = AsyncFileAPI(t)
        result = await api.retrieve_upload("upl-a5")
        t.request.assert_called_once_with("GET", "/file-uploads/upl-a5")
        assert result["status"] == "pending"

    async def test_retrieve_upload_complete_status(self):
        t = MagicMock()
        t.request = AsyncMock(return_value={"id": "upl-a6", "status": "complete"})
        api = AsyncFileAPI(t)
        result = await api.retrieve_upload("upl-a6")
        assert result["status"] == "complete"
