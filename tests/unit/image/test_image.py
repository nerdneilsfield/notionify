"""Tests for the image pipeline.

PRD test IDs: U-IM-001 through U-IM-017.
"""

import base64

import pytest

from notionify.config import NotionifyConfig
from notionify.errors import (
    NotionifyImageParseError,
    NotionifyImageSizeError,
    NotionifyImageTypeError,
    NotionifyUploadExpiredError,
)
from notionify.image.attach import build_image_block_external, build_image_block_uploaded
from notionify.image.detect import detect_image_source
from notionify.image.state import UploadStateMachine
from notionify.image.validate import validate_image
from notionify.models import ImageSourceType, UploadState


def make_config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


# =========================================================================
# U-IM-001: Detect external URL
# =========================================================================

class TestDetectExternalURL:
    """U-IM-001: HTTP/HTTPS URLs are classified as EXTERNAL_URL."""

    def test_https_url(self):
        assert detect_image_source("https://example.com/img.png") == ImageSourceType.EXTERNAL_URL

    def test_http_url(self):
        assert detect_image_source("http://example.com/img.jpg") == ImageSourceType.EXTERNAL_URL

    def test_url_with_query(self):
        url = "https://cdn.example.com/img.png?w=300"
        assert detect_image_source(url) == ImageSourceType.EXTERNAL_URL


# =========================================================================
# U-IM-002: Detect local file
# =========================================================================

class TestDetectLocalFile:
    """U-IM-002: Local file paths are classified as LOCAL_FILE."""

    def test_relative_path(self):
        assert detect_image_source("./images/photo.png") == ImageSourceType.LOCAL_FILE

    def test_parent_relative_path(self):
        assert detect_image_source("../images/photo.jpg") == ImageSourceType.LOCAL_FILE

    def test_absolute_path(self):
        assert detect_image_source("/home/user/photo.png") == ImageSourceType.LOCAL_FILE

    def test_image_extension_heuristic(self):
        assert detect_image_source("photo.png") == ImageSourceType.LOCAL_FILE

    def test_tilde_path(self):
        assert detect_image_source("~/images/photo.png") == ImageSourceType.LOCAL_FILE


# =========================================================================
# U-IM-003: Detect data URI
# =========================================================================

class TestDetectDataURI:
    """U-IM-003: Data URIs are classified as DATA_URI."""

    def test_data_uri_base64(self):
        src = "data:image/png;base64,iVBORw0KGgo="
        assert detect_image_source(src) == ImageSourceType.DATA_URI

    def test_data_uri_no_base64(self):
        src = "data:image/svg+xml,%3Csvg%3E%3C/svg%3E"
        assert detect_image_source(src) == ImageSourceType.DATA_URI

    def test_data_uri_case_insensitive(self):
        src = "DATA:image/png;base64,iVBOR="
        assert detect_image_source(src) == ImageSourceType.DATA_URI


# =========================================================================
# U-IM-004: Detect unknown source
# =========================================================================

class TestDetectUnknown:
    """U-IM-004: Unrecognizable sources are classified as UNKNOWN."""

    def test_empty_string(self):
        assert detect_image_source("") == ImageSourceType.UNKNOWN

    def test_whitespace(self):
        assert detect_image_source("   ") == ImageSourceType.UNKNOWN

    def test_random_string(self):
        assert detect_image_source("ftp://server/file") == ImageSourceType.UNKNOWN

    def test_malformed_ipv6_url_is_unknown(self):
        """Malformed IPv6 URL triggers ValueError in urlparse -> UNKNOWN (line 50-52)."""
        assert detect_image_source("http://[") == ImageSourceType.UNKNOWN


# =========================================================================
# U-IM-005: Validate MIME type - allowed
# =========================================================================

class TestValidateMimeAllowed:
    """U-IM-005: Allowed MIME types pass validation."""

    def test_png_allowed(self):
        # PNG magic bytes
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mime, data = validate_image(
            "photo.png", ImageSourceType.LOCAL_FILE, png_header, make_config()
        )
        assert mime == "image/png"
        assert data == png_header

    def test_jpeg_allowed(self):
        jpeg_header = b"\xff\xd8\xff" + b"\x00" * 100
        mime, data = validate_image(
            "photo.jpg", ImageSourceType.LOCAL_FILE, jpeg_header, make_config()
        )
        assert mime == "image/jpeg"

    def test_external_url_mime_from_extension(self):
        mime, data = validate_image(
            "https://example.com/image.png",
            ImageSourceType.EXTERNAL_URL,
            None,
            make_config(),
        )
        assert mime == "image/png"
        assert data is None


# =========================================================================
# U-IM-006: Validate MIME type - disallowed
# =========================================================================

class TestValidateMimeDisallowed:
    """U-IM-006: Disallowed MIME types raise NotionifyImageTypeError."""

    def test_disallowed_mime(self):
        config = make_config(image_allowed_mimes_upload=["image/png"])
        jpeg_header = b"\xff\xd8\xff" + b"\x00" * 100
        with pytest.raises(NotionifyImageTypeError) as exc_info:
            validate_image("photo.jpg", ImageSourceType.LOCAL_FILE, jpeg_header, config)
        assert exc_info.value.context["detected_mime"] == "image/jpeg"


# =========================================================================
# U-IM-007: Validate file size - within limit
# =========================================================================

class TestValidateSizeWithinLimit:
    """U-IM-007: Files within size limit pass validation."""

    def test_within_limit(self):
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        config = make_config(image_max_size_bytes=1024)
        mime, data = validate_image("photo.png", ImageSourceType.LOCAL_FILE, png_header, config)
        assert mime == "image/png"


# =========================================================================
# U-IM-008: Validate file size - exceeds limit
# =========================================================================

class TestValidateSizeExceedsLimit:
    """U-IM-008: Oversized files raise NotionifyImageSizeError."""

    def test_exceeds_limit(self):
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000
        config = make_config(image_max_size_bytes=100)
        with pytest.raises(NotionifyImageSizeError) as exc_info:
            validate_image("photo.png", ImageSourceType.LOCAL_FILE, png_header, config)
        assert exc_info.value.context["size_bytes"] > 100
        assert exc_info.value.context["max_bytes"] == 100


# =========================================================================
# U-IM-009: Data URI parsing - valid
# =========================================================================

class TestDataURIParsing:
    """U-IM-009: Valid data URIs are decoded correctly."""

    def test_valid_base64_data_uri(self):
        # Create a valid PNG-like data URI
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        b64_data = base64.b64encode(fake_png).decode("ascii")
        src = f"data:image/png;base64,{b64_data}"
        mime, data = validate_image(src, ImageSourceType.DATA_URI, None, make_config())
        assert mime == "image/png"
        assert data == fake_png


# =========================================================================
# U-IM-010: Data URI parsing - invalid
# =========================================================================

class TestDataURIParsingInvalid:
    """U-IM-010: Invalid data URIs raise NotionifyImageParseError."""

    def test_invalid_base64(self):
        src = "data:image/png;base64,!!not-valid-base64!!"
        with pytest.raises(NotionifyImageParseError):
            validate_image(src, ImageSourceType.DATA_URI, None, make_config())

    def test_malformed_data_uri(self):
        src = "data:"
        # This might parse but with application/octet-stream MIME type
        # and could fail on mime validation
        with pytest.raises((NotionifyImageParseError, NotionifyImageTypeError)):
            validate_image(src, ImageSourceType.DATA_URI, None, make_config())


# =========================================================================
# U-IM-011: State machine - valid transitions
# =========================================================================

class TestStateMachineValidTransitions:
    """U-IM-011: Upload state machine allows valid transitions."""

    def test_pending_to_uploading(self):
        sm = UploadStateMachine("upload-1")
        assert sm.state == UploadState.PENDING
        sm.transition(UploadState.UPLOADING)
        assert sm.state == UploadState.UPLOADING

    def test_uploading_to_uploaded(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        assert sm.state == UploadState.UPLOADED

    def test_uploading_to_failed(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.FAILED)
        assert sm.state == UploadState.FAILED

    def test_uploaded_to_attached(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.ATTACHED)
        assert sm.state == UploadState.ATTACHED

    def test_uploaded_to_expired(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.EXPIRED)
        assert sm.state == UploadState.EXPIRED

    def test_expired_to_uploading_retry(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.EXPIRED)
        sm.transition(UploadState.UPLOADING)
        assert sm.state == UploadState.UPLOADING


# =========================================================================
# U-IM-012: State machine - invalid transitions
# =========================================================================

class TestStateMachineInvalidTransitions:
    """U-IM-012: Invalid state transitions raise ValueError."""

    def test_pending_to_uploaded(self):
        sm = UploadStateMachine("upload-1")
        with pytest.raises(ValueError, match="Invalid state transition"):
            sm.transition(UploadState.UPLOADED)

    def test_attached_to_anything(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.ATTACHED)
        with pytest.raises(ValueError, match="Invalid state transition"):
            sm.transition(UploadState.UPLOADING)

    def test_failed_to_anything(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.FAILED)
        with pytest.raises(ValueError, match="Invalid state transition"):
            sm.transition(UploadState.UPLOADING)

    def test_expired_to_attached_raises_expired_error(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.EXPIRED)
        with pytest.raises(NotionifyUploadExpiredError):
            sm.transition(UploadState.ATTACHED)


# =========================================================================
# U-IM-013: assert_can_attach
# =========================================================================

class TestAssertCanAttach:
    """U-IM-013: assert_can_attach validates upload state."""

    def test_can_attach_in_uploaded_state(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.assert_can_attach()  # Should not raise

    def test_cannot_attach_in_pending_state(self):
        sm = UploadStateMachine("upload-1")
        with pytest.raises(ValueError, match="cannot be attached"):
            sm.assert_can_attach()

    def test_cannot_attach_in_expired_state(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.EXPIRED)
        with pytest.raises(NotionifyUploadExpiredError):
            sm.assert_can_attach()

    def test_cannot_attach_in_uploading_state(self):
        sm = UploadStateMachine("upload-1")
        sm.transition(UploadState.UPLOADING)
        with pytest.raises(ValueError, match="cannot be attached"):
            sm.assert_can_attach()


# =========================================================================
# U-IM-014: build_image_block_external
# =========================================================================

class TestBuildImageBlockExternal:
    """U-IM-014: Build external image block dict."""

    def test_external_block_structure(self):
        block = build_image_block_external("https://example.com/img.png")
        assert block["type"] == "image"
        assert block["image"]["type"] == "external"
        assert block["image"]["external"]["url"] == "https://example.com/img.png"


# =========================================================================
# U-IM-015: build_image_block_uploaded
# =========================================================================

class TestBuildImageBlockUploaded:
    """U-IM-015: Build uploaded image block dict."""

    def test_uploaded_block_structure(self):
        block = build_image_block_uploaded("upload-uuid-123")
        assert block["type"] == "image"
        assert block["image"]["type"] == "file_upload"
        assert block["image"]["file_upload"]["id"] == "upload-uuid-123"


# =========================================================================
# U-IM-016: UploadState enum values
# =========================================================================

class TestUploadStateEnum:
    """U-IM-016: UploadState enum has correct values."""

    def test_enum_values(self):
        assert UploadState.PENDING.value == "pending"
        assert UploadState.UPLOADING.value == "uploading"
        assert UploadState.UPLOADED.value == "uploaded"
        assert UploadState.ATTACHED.value == "attached"
        assert UploadState.FAILED.value == "failed"
        assert UploadState.EXPIRED.value == "expired"


# =========================================================================
# U-IM-017: ImageSourceType enum values
# =========================================================================

class TestImageSourceTypeEnum:
    """U-IM-017: ImageSourceType enum has correct values."""

    def test_enum_values(self):
        assert ImageSourceType.EXTERNAL_URL.value == "external_url"
        assert ImageSourceType.LOCAL_FILE.value == "local_file"
        assert ImageSourceType.DATA_URI.value == "data_uri"
        assert ImageSourceType.UNKNOWN.value == "unknown"

    def test_enum_is_str(self):
        """ImageSourceType values are also strings."""
        assert isinstance(ImageSourceType.EXTERNAL_URL, str)
        assert ImageSourceType.EXTERNAL_URL == "external_url"
