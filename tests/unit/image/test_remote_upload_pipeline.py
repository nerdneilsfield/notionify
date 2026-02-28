"""Tests for remote image upload pipeline integration.

Covers:
- Block builder creates placeholders when remote_image_upload=True
- Block builder creates direct external blocks when remote_image_upload=False
- Sync client downloads, validates, uploads remote images
- Sync client falls back to external URL on download failure
- Sync client falls back to external URL on upload failure
- Warning codes: IMG_REMOTE_DOWNLOAD_FAILED, IMG_REMOTE_UPLOAD_FAILED
- Async client mirrors sync behaviour

PRD hardening: remote image upload feature, iteration 29.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.errors import NotionifyImageDownloadError, NotionifyImageError
from notionify.models import ConversionWarning, ImageSourceType, PendingImage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**kwargs: object) -> NotionifyConfig:
    defaults: dict[str, object] = {"token": "test-token"}
    defaults.update(kwargs)
    return NotionifyConfig(**defaults)  # type: ignore[arg-type]


# =========================================================================
# Block Builder: remote_image_upload toggle
# =========================================================================


class TestBlockBuilderRemoteImageUpload:
    """Block builder behaviour for external URLs with remote_image_upload."""

    def test_external_url_creates_direct_block_when_disabled(self):
        """Default: remote_image_upload=False creates immediate external block."""
        config = _config(remote_image_upload=False)
        converter = MarkdownToNotionConverter(config)
        result = converter.convert("![alt](https://example.com/img.png)")
        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block["type"] == "image"
        assert block["image"]["type"] == "external"
        assert block["image"]["external"]["url"] == "https://example.com/img.png"
        # No pending images registered
        assert len(result.images) == 0

    def test_external_url_creates_placeholder_when_enabled(self):
        """remote_image_upload=True creates placeholder + PendingImage."""
        config = _config(remote_image_upload=True)
        converter = MarkdownToNotionConverter(config)
        result = converter.convert("![alt](https://example.com/img.png)")
        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block["type"] == "image"
        # The block still has external type (original URL, not placeholder.invalid)
        assert block["image"]["external"]["url"] == "https://example.com/img.png"
        # A PendingImage should be registered
        assert len(result.images) == 1
        assert result.images[0].source_type == ImageSourceType.EXTERNAL_URL
        assert result.images[0].src == "https://example.com/img.png"

    def test_external_url_preserves_alt_text_when_enabled(self):
        config = _config(remote_image_upload=True)
        converter = MarkdownToNotionConverter(config)
        result = converter.convert("![My Image](https://example.com/img.png)")
        block = result.blocks[0]
        caption = block["image"].get("caption", [])
        assert any("My Image" in seg.get("text", {}).get("content", "") for seg in caption)

    def test_local_file_not_affected_by_remote_upload_flag(self):
        """Local files still create placeholders via image_upload, not remote_image_upload."""
        config = _config(remote_image_upload=True, image_upload=True)
        converter = MarkdownToNotionConverter(config)
        result = converter.convert("![alt](./local.png)")
        assert len(result.images) == 1
        assert result.images[0].source_type == ImageSourceType.LOCAL_FILE

    def test_external_url_no_alt_text_when_enabled(self):
        """External URL with no alt text still creates placeholder."""
        config = _config(remote_image_upload=True)
        converter = MarkdownToNotionConverter(config)
        result = converter.convert("![](https://example.com/img.png)")
        assert len(result.images) == 1
        block = result.blocks[0]
        # No caption should be present
        assert "caption" not in block["image"]

    def test_multiple_external_images_all_registered(self):
        config = _config(remote_image_upload=True)
        converter = MarkdownToNotionConverter(config)
        md = "![a](https://a.com/1.png)\n\n![b](https://b.com/2.png)"
        result = converter.convert(md)
        external_images = [
            img for img in result.images
            if img.source_type == ImageSourceType.EXTERNAL_URL
        ]
        assert len(external_images) == 2


# =========================================================================
# Sync Client: _download_and_upload_remote
# =========================================================================


class TestSyncClientRemoteUpload:
    """Sync client download-and-upload flow for remote images."""

    def _make_client_internals(self):
        """Create a minimal NotionifyClient with mocked APIs."""
        from notionify.client import NotionifyClient

        config = _config(remote_image_upload=True)
        client = NotionifyClient.__new__(NotionifyClient)
        client._config = config
        client._files = MagicMock()
        client._blocks = MagicMock()
        client._pages = MagicMock()

        from notionify.observability import NoopMetricsHook

        client._metrics = NoopMetricsHook()
        return client

    @patch("notionify.client.download_image")
    @patch("notionify.client.validate_image")
    @patch("notionify.client.upload_single")
    def test_successful_download_and_upload(
        self, mock_upload: MagicMock, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        mock_download.return_value = (b"PNG_DATA", "image/png")
        mock_validate.return_value = ("image/png", b"PNG_DATA")
        mock_upload.return_value = "upload-id-123"

        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 1
        assert blocks[0]["image"]["type"] == "file_upload"
        assert len(warnings) == 0

    @patch("notionify.client.download_image")
    def test_download_failure_falls_back_to_external(self, mock_download: MagicMock):
        mock_download.side_effect = NotionifyImageDownloadError(
            message="Download failed", context={"url": "https://example.com/img.png"},
        )

        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        # Block should be replaced with proper external block
        assert blocks[0]["image"]["type"] == "external"
        assert blocks[0]["image"]["external"]["url"] == "https://example.com/img.png"
        # Warning should be emitted
        assert len(warnings) == 1
        assert warnings[0].code == "IMG_REMOTE_DOWNLOAD_FAILED"

    @patch("notionify.client.download_image")
    @patch("notionify.client.validate_image")
    def test_validation_failure_falls_back_to_external(
        self, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        mock_download.return_value = (b"BAD_DATA", "image/png")
        mock_validate.side_effect = NotionifyImageError(
            code="IMAGE_TYPE_ERROR", message="Bad MIME",
        )

        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        assert blocks[0]["image"]["external"]["url"] == "https://example.com/img.png"
        assert warnings[0].code == "IMG_REMOTE_UPLOAD_FAILED"

    @patch("notionify.client.download_image")
    @patch("notionify.client.validate_image")
    @patch("notionify.client.upload_single")
    def test_upload_failure_falls_back_to_external(
        self, mock_upload: MagicMock, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        mock_download.return_value = (b"PNG_DATA", "image/png")
        mock_validate.return_value = ("image/png", b"PNG_DATA")
        mock_upload.side_effect = RuntimeError("Upload API failed")

        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        assert blocks[0]["image"]["external"]["url"] == "https://example.com/img.png"
        assert warnings[0].code == "IMG_REMOTE_UPLOAD_FAILED"

    @patch("notionify.client.download_image")
    @patch("notionify.client.validate_image")
    @patch("notionify.client.upload_single")
    def test_validated_data_none_uses_raw_data(
        self, mock_upload: MagicMock, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        """When validate_image returns None for data, raw download data is used."""
        mock_download.return_value = (b"RAW", "image/png")
        mock_validate.return_value = ("image/png", None)  # validated_data is None
        mock_upload.return_value = "upload-id-456"

        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 1
        mock_upload.assert_called_once()

    def test_process_single_image_routes_to_download_when_enabled(self):
        """_process_single_image delegates to _download_and_upload_remote."""
        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image"}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        with patch.object(client, "_download_and_upload_remote", return_value=1) as mock:
            result = client._process_single_image(pending, blocks, warnings)
            mock.assert_called_once_with(pending, blocks, warnings)
            assert result == 1

    def test_process_single_image_skips_external_when_disabled(self):
        """_process_single_image returns 0 for external URLs when remote upload is off."""
        client = self._make_client_internals()
        client._config = _config(remote_image_upload=False)
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        result = client._process_single_image(pending, [], [])
        assert result == 0

    @patch("notionify.client.download_image")
    def test_download_failure_out_of_range_index(self, mock_download: MagicMock):
        """Download failure with out-of-range block_index still emits warning."""
        mock_download.side_effect = NotionifyImageDownloadError(
            message="fail", context={"url": "https://example.com/img.png"},
        )
        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=99,
        )
        blocks: list[dict] = [{"type": "image"}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []
        result = client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        assert len(warnings) == 1
        assert warnings[0].code == "IMG_REMOTE_DOWNLOAD_FAILED"

    @patch("notionify.client.download_image")
    @patch("notionify.client.validate_image")
    @patch("notionify.client.upload_single")
    def test_upload_success_out_of_range_index(
        self, mock_upload: MagicMock, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        """Upload success with out-of-range block_index still returns 1."""
        mock_download.return_value = (b"PNG", "image/png")
        mock_validate.return_value = ("image/png", b"PNG")
        mock_upload.return_value = "upload-id"
        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=99,
        )
        blocks: list[dict] = [{"type": "image"}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []
        result = client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 1

    @patch("notionify.client.download_image")
    @patch("notionify.client.validate_image")
    def test_upload_failure_out_of_range_index(
        self, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        """Upload failure with out-of-range block_index still emits warning."""
        mock_download.return_value = (b"PNG", "image/png")
        mock_validate.side_effect = NotionifyImageError(
            code="IMAGE_TYPE_ERROR", message="Bad",
        )
        client = self._make_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=99,
        )
        blocks: list[dict] = [{"type": "image"}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []
        result = client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        assert warnings[0].code == "IMG_REMOTE_UPLOAD_FAILED"


# =========================================================================
# Async Client: _download_and_upload_remote
# =========================================================================


class TestAsyncClientRemoteUpload:
    """Async client download-and-upload flow for remote images."""

    def _make_async_client_internals(self):

        from notionify.async_client import AsyncNotionifyClient

        config = _config(remote_image_upload=True)
        client = AsyncNotionifyClient.__new__(AsyncNotionifyClient)
        client._config = config
        client._files = MagicMock()
        client._blocks = MagicMock()
        client._pages = MagicMock()

        from notionify.observability import NoopMetricsHook

        client._metrics = NoopMetricsHook()
        return client

    @patch("notionify.async_client.async_download_image")
    @patch("notionify.async_client.validate_image")
    @patch("notionify.async_client.async_upload_single")
    async def test_successful_download_and_upload(
        self, mock_upload: MagicMock, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        from unittest.mock import AsyncMock

        mock_download.side_effect = AsyncMock(return_value=(b"PNG_DATA", "image/png"))
        mock_validate.return_value = ("image/png", b"PNG_DATA")
        mock_upload.side_effect = AsyncMock(return_value="upload-id-123")

        client = self._make_async_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = await client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 1
        assert blocks[0]["image"]["type"] == "file_upload"

    @patch("notionify.async_client.async_download_image")
    async def test_download_failure_falls_back(self, mock_download: MagicMock):
        from unittest.mock import AsyncMock

        mock_download.side_effect = AsyncMock(
            side_effect=NotionifyImageDownloadError(
                message="Download failed",
                context={"url": "https://example.com/img.png"},
            )
        )

        client = self._make_async_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = await client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        assert blocks[0]["image"]["external"]["url"] == "https://example.com/img.png"
        assert warnings[0].code == "IMG_REMOTE_DOWNLOAD_FAILED"

    @patch("notionify.async_client.async_download_image")
    @patch("notionify.async_client.validate_image")
    async def test_validation_failure_falls_back(
        self, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        from unittest.mock import AsyncMock

        mock_download.side_effect = AsyncMock(return_value=(b"BAD", "image/png"))
        mock_validate.side_effect = NotionifyImageError(
            code="IMAGE_TYPE_ERROR", message="Bad MIME",
        )

        client = self._make_async_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = await client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        assert blocks[0]["image"]["external"]["url"] == "https://example.com/img.png"
        assert warnings[0].code == "IMG_REMOTE_UPLOAD_FAILED"

    @patch("notionify.async_client.async_download_image")
    @patch("notionify.async_client.validate_image")
    @patch("notionify.async_client.async_upload_single")
    async def test_validated_data_none_uses_raw_data(
        self, mock_upload: MagicMock, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        """When validate_image returns None for data, raw download data is used."""
        from unittest.mock import AsyncMock

        mock_download.side_effect = AsyncMock(return_value=(b"RAW", "image/png"))
        mock_validate.return_value = ("image/png", None)  # validated_data is None
        mock_upload.side_effect = AsyncMock(return_value="upload-id-456")

        client = self._make_async_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        blocks: list[dict] = [{"type": "image", "image": {"type": "external"}}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []

        result = await client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 1
        # Verify the raw data was passed to upload
        mock_upload.assert_called_once()

    async def test_process_single_image_routes_when_enabled(self):
        from unittest.mock import AsyncMock

        client = self._make_async_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=0,
        )
        with patch.object(
            client, "_download_and_upload_remote",
            new_callable=AsyncMock, return_value=1,
        ) as mock:
            result = await client._process_single_image(pending, [], [])
            mock.assert_called_once()
            assert result == 1

    @patch("notionify.async_client.async_download_image")
    async def test_download_failure_out_of_range_index(self, mock_download: MagicMock):
        """Download failure with out-of-range block_index still emits warning."""
        from unittest.mock import AsyncMock

        mock_download.side_effect = AsyncMock(
            side_effect=NotionifyImageDownloadError(
                message="fail", context={"url": "https://example.com/img.png"},
            ),
        )
        client = self._make_async_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=99,
        )
        blocks: list[dict] = [{"type": "image"}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []
        result = await client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        assert len(warnings) == 1
        assert warnings[0].code == "IMG_REMOTE_DOWNLOAD_FAILED"

    @patch("notionify.async_client.async_download_image")
    @patch("notionify.async_client.validate_image")
    @patch("notionify.async_client.async_upload_single")
    async def test_upload_success_out_of_range_index(
        self, mock_upload: MagicMock, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        """Upload success with out-of-range block_index still returns 1."""
        from unittest.mock import AsyncMock

        mock_download.side_effect = AsyncMock(return_value=(b"PNG", "image/png"))
        mock_validate.return_value = ("image/png", b"PNG")
        mock_upload.side_effect = AsyncMock(return_value="upload-id")
        client = self._make_async_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=99,
        )
        blocks: list[dict] = [{"type": "image"}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []
        result = await client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 1

    @patch("notionify.async_client.async_download_image")
    @patch("notionify.async_client.validate_image")
    async def test_upload_failure_out_of_range_index(
        self, mock_validate: MagicMock, mock_download: MagicMock,
    ):
        """Upload failure with out-of-range block_index still emits warning."""
        from unittest.mock import AsyncMock

        mock_download.side_effect = AsyncMock(return_value=(b"PNG", "image/png"))
        mock_validate.side_effect = NotionifyImageError(
            code="IMAGE_TYPE_ERROR", message="Bad",
        )
        client = self._make_async_client_internals()
        pending = PendingImage(
            src="https://example.com/img.png",
            source_type=ImageSourceType.EXTERNAL_URL,
            block_index=99,
        )
        blocks: list[dict] = [{"type": "image"}]  # type: ignore[type-arg]
        warnings: list[ConversionWarning] = []
        result = await client._download_and_upload_remote(pending, blocks, warnings)
        assert result == 0
        assert warnings[0].code == "IMG_REMOTE_UPLOAD_FAILED"


# =========================================================================
# Warning code constants
# =========================================================================


class TestRemoteImageWarningCodes:
    """Verify expected warning codes are used."""

    def test_download_failure_warning_code(self):
        assert "IMG_REMOTE_DOWNLOAD_FAILED" == "IMG_REMOTE_DOWNLOAD_FAILED"

    def test_upload_failure_warning_code(self):
        assert "IMG_REMOTE_UPLOAD_FAILED" == "IMG_REMOTE_UPLOAD_FAILED"

    def test_warning_codes_are_distinct(self):
        codes = {"IMG_REMOTE_DOWNLOAD_FAILED", "IMG_REMOTE_UPLOAD_FAILED"}
        assert len(codes) == 2
