"""Image pipeline for detecting, validating, uploading, and attaching images.

Exports
-------
detect_image_source
    Classify an image ``src`` as URL, local file, data URI, or unknown.
validate_image
    Validate MIME type and file size.
upload_single / async_upload_single
    Single-part upload flow.
upload_multi / async_upload_multi
    Multi-part upload flow.
build_image_block_external
    Build a Notion image block for an external URL.
build_image_block_uploaded
    Build a Notion image block for an uploaded file.
UploadStateMachine
    Track upload lifecycle state and enforce valid transitions.
"""

from .attach import build_image_block_external, build_image_block_uploaded
from .detect import detect_image_source, mime_to_extension
from .state import UploadStateMachine
from .upload_multi import async_upload_multi, upload_multi
from .upload_single import async_upload_single, upload_single
from .validate import validate_image

__all__ = [
    "UploadStateMachine",
    "async_upload_multi",
    "async_upload_single",
    "build_image_block_external",
    "build_image_block_uploaded",
    "detect_image_source",
    "mime_to_extension",
    "upload_multi",
    "upload_single",
    "validate_image",
]
