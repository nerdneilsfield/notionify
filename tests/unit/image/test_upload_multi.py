"""Unit tests for upload_multi and async_upload_multi.

All FileAPI calls are mocked with MagicMock / AsyncMock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notionify.image.upload_multi import async_upload_multi, upload_multi


async def _agen(*items):
    for item in items:
        yield item


# ===========================================================================
# Sync: upload_multi
# ===========================================================================

class TestUploadMultiSync:
    def test_single_chunk_with_upload_urls(self):
        """Data fits in one chunk; upload_urls pre-provided."""
        file_api = MagicMock()
        data = b"hello world"
        file_api.create_upload.return_value = {
            "id": "upload-1",
            "upload_urls": [{"upload_url": "https://s3.example.com/part1"}],
        }
        file_api.send_part.return_value = {"etag": "abc123"}
        file_api.complete_upload.return_value = {"id": "upload-1", "status": "complete"}

        result = upload_multi(file_api, "img.png", "image/png", data)

        assert result == "upload-1"
        file_api.create_upload.assert_called_once_with(
            name="img.png", content_type="image/png", mode="multi_part"
        )
        file_api.send_part.assert_called_once_with(
            "https://s3.example.com/part1", data, "image/png"
        )
        file_api.complete_upload.assert_called_once()
        parts = file_api.complete_upload.call_args[0][1]
        assert parts[0]["part_number"] == 1
        assert parts[0]["etag"] == "abc123"

    def test_multiple_chunks(self):
        """15 bytes with chunk_size=5 → 3 chunks uploaded to 3 distinct URLs."""
        file_api = MagicMock()
        data = b"abcdefghijklmno"  # 15 bytes
        file_api.create_upload.return_value = {
            "id": "upload-2",
            "upload_urls": [
                {"upload_url": "https://s3/p1"},
                {"upload_url": "https://s3/p2"},
                {"upload_url": "https://s3/p3"},
            ],
        }
        file_api.send_part.return_value = None  # 204 No Content

        result = upload_multi(file_api, "big.png", "image/png", data, chunk_size=5)

        assert result == "upload-2"
        assert file_api.send_part.call_count == 3

        calls = file_api.send_part.call_args_list
        assert calls[0][0][0] == "https://s3/p1"
        assert calls[0][0][1] == b"abcde"
        assert calls[1][0][0] == "https://s3/p2"
        assert calls[1][0][1] == b"fghij"
        assert calls[2][0][0] == "https://s3/p3"
        assert calls[2][0][1] == b"klmno"

        parts = file_api.complete_upload.call_args[0][1]
        assert len(parts) == 3
        # When result is None, part_info has only part_number
        assert parts[0] == {"part_number": 1}
        assert parts[1] == {"part_number": 2}
        assert parts[2] == {"part_number": 3}

    def test_fallback_upload_url_when_no_upload_urls(self):
        """No upload_urls in response → falls back to upload.get('upload_url', '')."""
        file_api = MagicMock()
        data = b"xy"
        file_api.create_upload.return_value = {
            "id": "upload-3",
            "upload_url": "https://s3/fallback",
            # no "upload_urls" key
        }
        file_api.send_part.return_value = {}
        file_api.complete_upload.return_value = {}

        result = upload_multi(file_api, "f.png", "image/png", data)

        assert result == "upload-3"
        file_api.send_part.assert_called_once_with(
            "https://s3/fallback", data, "image/png"
        )

    def test_fallback_url_missing_entirely(self):
        """Neither upload_urls nor upload_url → fallback URL is empty string."""
        file_api = MagicMock()
        data = b"z"
        file_api.create_upload.return_value = {"id": "upload-4"}
        file_api.send_part.return_value = {}
        file_api.complete_upload.return_value = {}

        result = upload_multi(file_api, "g.png", "image/png", data)

        assert result == "upload-4"
        # send_part called with empty string URL
        file_api.send_part.assert_called_once_with("", data, "image/png")

    def test_etag_merged_into_parts(self):
        """When send_part returns a dict, its fields are merged into part_info."""
        file_api = MagicMock()
        data = b"chunk1chunk2"
        file_api.create_upload.return_value = {
            "id": "upload-5",
            "upload_urls": [
                {"upload_url": "https://s3/q1"},
                {"upload_url": "https://s3/q2"},
            ],
        }
        file_api.send_part.side_effect = [
            {"etag": "etag1"},
            {"etag": "etag2"},
        ]
        file_api.complete_upload.return_value = {}

        upload_multi(file_api, "h.png", "image/png", data, chunk_size=6)

        parts = file_api.complete_upload.call_args[0][1]
        assert parts[0] == {"part_number": 1, "etag": "etag1"}
        assert parts[1] == {"part_number": 2, "etag": "etag2"}

    def test_complete_upload_called_with_upload_id(self):
        """complete_upload receives the correct upload_id."""
        file_api = MagicMock()
        file_api.create_upload.return_value = {
            "id": "my-upload-id",
            "upload_urls": [{"upload_url": "https://s3/u1"}],
        }
        file_api.send_part.return_value = {}
        file_api.complete_upload.return_value = {}

        upload_multi(file_api, "i.png", "image/png", b"data")

        args = file_api.complete_upload.call_args[0]
        assert args[0] == "my-upload-id"

    def test_chunk_size_boundary(self):
        """Exactly chunk_size bytes → 1 chunk; chunk_size+1 bytes → 2 chunks."""
        file_api = MagicMock()
        file_api.send_part.return_value = {}
        file_api.complete_upload.return_value = {}

        # Exactly chunk_size
        file_api.create_upload.return_value = {
            "id": "up-exact",
            "upload_urls": [{"upload_url": "https://s3/e1"}],
        }
        upload_multi(file_api, "exact.png", "image/png", b"A" * 10, chunk_size=10)
        assert file_api.send_part.call_count == 1

        file_api.send_part.reset_mock()
        file_api.create_upload.return_value = {
            "id": "up-over",
            "upload_urls": [
                {"upload_url": "https://s3/o1"},
                {"upload_url": "https://s3/o2"},
            ],
        }
        upload_multi(file_api, "over.png", "image/png", b"A" * 11, chunk_size=10)
        assert file_api.send_part.call_count == 2

    def test_content_type_forwarded_to_send_part(self):
        """Content-type passed to create_upload is forwarded to send_part."""
        file_api = MagicMock()
        file_api.create_upload.return_value = {
            "id": "up-ct",
            "upload_urls": [{"upload_url": "https://s3/ct"}],
        }
        file_api.send_part.return_value = {}
        file_api.complete_upload.return_value = {}

        upload_multi(file_api, "doc.pdf", "application/pdf", b"bytes")

        _, called_ct = file_api.send_part.call_args[0][1], file_api.send_part.call_args[0][2]
        assert called_ct == "application/pdf"


# ===========================================================================
# Async: async_upload_multi
# ===========================================================================

class TestAsyncUploadMulti:
    async def test_single_chunk_async(self):
        """Single chunk with upload_urls; checks result and call count."""
        file_api = MagicMock()
        file_api.create_upload = AsyncMock(return_value={
            "id": "up-async-1",
            "upload_urls": [{"upload_url": "https://s3/ap1"}],
        })
        file_api.send_part = AsyncMock(return_value={"etag": "xyz"})
        file_api.complete_upload = AsyncMock(return_value={"id": "up-async-1"})

        result = await async_upload_multi(file_api, "img.png", "image/png", b"data")

        assert result == "up-async-1"
        file_api.create_upload.assert_called_once_with(
            name="img.png", content_type="image/png", mode="multi_part"
        )
        file_api.send_part.assert_called_once_with("https://s3/ap1", b"data", "image/png")
        file_api.complete_upload.assert_called_once()

        parts = file_api.complete_upload.call_args[0][1]
        assert parts[0]["part_number"] == 1
        assert parts[0]["etag"] == "xyz"

    async def test_multiple_chunks_async(self):
        """10 bytes / chunk_size=5 → 2 async send_part calls."""
        file_api = MagicMock()
        file_api.create_upload = AsyncMock(return_value={
            "id": "up-async-2",
            "upload_urls": [
                {"upload_url": "https://s3/a1"},
                {"upload_url": "https://s3/a2"},
            ],
        })
        file_api.send_part = AsyncMock(return_value=None)
        file_api.complete_upload = AsyncMock(return_value={})

        result = await async_upload_multi(
            file_api, "b.png", "image/png", b"abcdefghij", chunk_size=5
        )

        assert result == "up-async-2"
        assert file_api.send_part.call_count == 2

        calls = file_api.send_part.call_args_list
        assert calls[0][0][1] == b"abcde"
        assert calls[1][0][1] == b"fghij"

        parts = file_api.complete_upload.call_args[0][1]
        assert len(parts) == 2
        assert parts[0] == {"part_number": 1}
        assert parts[1] == {"part_number": 2}

    async def test_fallback_url_async(self):
        """No upload_urls → falls back to upload_url field (async)."""
        file_api = MagicMock()
        file_api.create_upload = AsyncMock(return_value={
            "id": "up-async-3",
            "upload_url": "https://s3/fb",
        })
        file_api.send_part = AsyncMock(return_value={})
        file_api.complete_upload = AsyncMock(return_value={})

        result = await async_upload_multi(file_api, "x.png", "image/png", b"hi")

        assert result == "up-async-3"
        file_api.send_part.assert_called_once_with("https://s3/fb", b"hi", "image/png")

    async def test_fallback_url_missing_entirely_async(self):
        """Neither upload_urls nor upload_url → empty string URL (async)."""
        file_api = MagicMock()
        file_api.create_upload = AsyncMock(return_value={"id": "up-async-4"})
        file_api.send_part = AsyncMock(return_value={})
        file_api.complete_upload = AsyncMock(return_value={})

        result = await async_upload_multi(file_api, "y.png", "image/png", b"z")

        assert result == "up-async-4"
        file_api.send_part.assert_called_once_with("", b"z", "image/png")

    async def test_etag_merged_into_parts_async(self):
        """Etag from send_part response is merged into part descriptor (async)."""
        file_api = MagicMock()
        file_api.create_upload = AsyncMock(return_value={
            "id": "up-async-5",
            "upload_urls": [
                {"upload_url": "https://s3/m1"},
                {"upload_url": "https://s3/m2"},
            ],
        })
        file_api.send_part = AsyncMock(side_effect=[
            {"etag": "e1"},
            {"etag": "e2"},
        ])
        file_api.complete_upload = AsyncMock(return_value={})

        await async_upload_multi(
            file_api, "merged.png", "image/png", b"aabbccdd", chunk_size=4
        )

        parts = file_api.complete_upload.call_args[0][1]
        assert parts[0] == {"part_number": 1, "etag": "e1"}
        assert parts[1] == {"part_number": 2, "etag": "e2"}

    async def test_complete_upload_called_with_correct_id_async(self):
        file_api = MagicMock()
        file_api.create_upload = AsyncMock(return_value={
            "id": "correct-id",
            "upload_urls": [{"upload_url": "https://s3/c1"}],
        })
        file_api.send_part = AsyncMock(return_value={})
        file_api.complete_upload = AsyncMock(return_value={})

        await async_upload_multi(file_api, "c.png", "image/png", b"data")

        args = file_api.complete_upload.call_args[0]
        assert args[0] == "correct-id"

    async def test_three_chunks_async(self):
        """15 bytes / chunk_size=5 → 3 async send_part calls."""
        file_api = MagicMock()
        file_api.create_upload = AsyncMock(return_value={
            "id": "up-async-6",
            "upload_urls": [
                {"upload_url": "https://s3/t1"},
                {"upload_url": "https://s3/t2"},
                {"upload_url": "https://s3/t3"},
            ],
        })
        file_api.send_part = AsyncMock(return_value={"etag": "et"})
        file_api.complete_upload = AsyncMock(return_value={})

        result = await async_upload_multi(
            file_api, "tri.png", "image/png", b"abcdefghijklmno", chunk_size=5
        )

        assert result == "up-async-6"
        assert file_api.send_part.call_count == 3

        parts = file_api.complete_upload.call_args[0][1]
        assert len(parts) == 3
        for i, p in enumerate(parts, start=1):
            assert p["part_number"] == i
            assert p["etag"] == "et"


# ===========================================================================
# Sync: failure paths
# ===========================================================================


class TestUploadMultiSyncFailures:
    """Sync: API exception propagation for upload_multi."""

    def _make_api(self, *, url: str = "https://s3/p1") -> MagicMock:
        api = MagicMock()
        api.create_upload.return_value = {
            "id": "upload-fail",
            "upload_urls": [{"upload_url": url}],
        }
        api.send_part.return_value = {}
        api.complete_upload.return_value = {}
        return api

    def test_create_upload_exception_propagates(self):
        api = MagicMock()
        api.create_upload.side_effect = RuntimeError("Create failed")
        with pytest.raises(RuntimeError, match="Create failed"):
            upload_multi(api, "img.png", "image/png", b"data")

    def test_create_upload_missing_id_raises_key_error(self):
        api = MagicMock()
        api.create_upload.return_value = {
            "upload_urls": [{"upload_url": "https://s3/x"}]
        }
        with pytest.raises(KeyError):
            upload_multi(api, "img.png", "image/png", b"data")

    def test_send_part_exception_propagates_mid_upload(self):
        """Exception during chunk 2 of 3 propagates; complete_upload never called."""
        api = MagicMock()
        api.create_upload.return_value = {
            "id": "upload-mid",
            "upload_urls": [
                {"upload_url": "https://s3/r1"},
                {"upload_url": "https://s3/r2"},
                {"upload_url": "https://s3/r3"},
            ],
        }
        api.send_part.side_effect = [{"etag": "e1"}, OSError("Chunk 2 failed"), {}]

        with pytest.raises(OSError, match="Chunk 2 failed"):
            upload_multi(api, "img.png", "image/png", b"abcdefghijklmno", chunk_size=5)

        # Only 2 parts were sent before failure
        assert api.send_part.call_count == 2
        api.complete_upload.assert_not_called()

    def test_complete_upload_exception_propagates(self):
        """complete_upload raises after all chunks uploaded."""
        api = self._make_api()
        api.complete_upload.side_effect = RuntimeError("Complete failed")

        with pytest.raises(RuntimeError, match="Complete failed"):
            upload_multi(api, "img.png", "image/png", b"data")

        # All parts were sent before failure
        api.send_part.assert_called_once()

    def test_zero_length_data_calls_complete_with_empty_parts(self):
        """Empty bytes: no chunks, complete_upload called with empty parts list."""
        api = self._make_api()
        result = upload_multi(api, "empty.png", "image/png", b"")
        assert result == "upload-fail"
        api.send_part.assert_not_called()
        args = api.complete_upload.call_args[0]
        assert args[0] == "upload-fail"
        assert args[1] == []


# ===========================================================================
# Async: failure paths
# ===========================================================================


class TestUploadMultiAsyncFailures:
    """Async: API exception propagation for async_upload_multi."""

    def _make_api(self, *, url: str = "https://s3/ap1") -> MagicMock:
        api = MagicMock()
        api.create_upload = AsyncMock(return_value={
            "id": "upload-async-fail",
            "upload_urls": [{"upload_url": url}],
        })
        api.send_part = AsyncMock(return_value={})
        api.complete_upload = AsyncMock(return_value={})
        return api

    async def test_create_upload_exception_propagates(self):
        api = MagicMock()
        api.create_upload = AsyncMock(side_effect=RuntimeError("Async create failed"))
        with pytest.raises(RuntimeError, match="Async create failed"):
            await async_upload_multi(api, "img.png", "image/png", b"data")

    async def test_create_upload_missing_id_raises_key_error(self):
        api = MagicMock()
        api.create_upload = AsyncMock(return_value={
            "upload_urls": [{"upload_url": "https://s3/x"}]
        })
        with pytest.raises(KeyError):
            await async_upload_multi(api, "img.png", "image/png", b"data")

    async def test_send_part_exception_propagates_mid_upload(self):
        """Async exception during chunk 2 propagates; complete_upload never called."""
        api = MagicMock()
        api.create_upload = AsyncMock(return_value={
            "id": "upload-async-mid",
            "upload_urls": [
                {"upload_url": "https://s3/ar1"},
                {"upload_url": "https://s3/ar2"},
                {"upload_url": "https://s3/ar3"},
            ],
        })
        api.send_part = AsyncMock(
            side_effect=[{"etag": "e1"}, OSError("Async chunk 2 failed"), {}]
        )
        api.complete_upload = AsyncMock(return_value={})

        with pytest.raises(OSError, match="Async chunk 2 failed"):
            await async_upload_multi(
                api, "img.png", "image/png", b"abcdefghijklmno", chunk_size=5
            )

        assert api.send_part.call_count == 2
        api.complete_upload.assert_not_awaited()

    async def test_complete_upload_exception_propagates(self):
        """Async complete_upload raises after all chunks uploaded."""
        api = self._make_api()
        api.complete_upload.side_effect = RuntimeError("Async complete failed")

        with pytest.raises(RuntimeError, match="Async complete failed"):
            await async_upload_multi(api, "img.png", "image/png", b"data")

        api.send_part.assert_awaited_once()

    async def test_zero_length_data_calls_complete_with_empty_parts(self):
        """Async empty bytes: no chunks, complete_upload called with empty parts."""
        api = self._make_api()
        result = await async_upload_multi(api, "empty.png", "image/png", b"")
        assert result == "upload-async-fail"
        api.send_part.assert_not_awaited()
        args = api.complete_upload.call_args[0]
        assert args[0] == "upload-async-fail"
        assert args[1] == []
