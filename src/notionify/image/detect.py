"""Image source detection.

Classifies a raw image ``src`` string (from ``![alt](src)``) into one of
the :class:`ImageSourceType` variants so the pipeline knows how to handle it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from notionify.models import ImageSourceType

# Regex for data URIs: data:[<mediatype>][;base64],<data>
_DATA_URI_RE = re.compile(r"^data:", re.IGNORECASE)

# Common image file extensions for local-file heuristics.
_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".bmp", ".tiff", ".tif", ".ico", ".avif",
})


def detect_image_source(src: str) -> ImageSourceType:
    """Detect whether an image source is a URL, local file, data URI, or unknown.

    Parameters
    ----------
    src:
        The raw source string from a Markdown image token.

    Returns
    -------
    ImageSourceType
        The classification of the source.
    """
    if not src or not src.strip():
        return ImageSourceType.UNKNOWN

    src = src.strip()

    # Data URI check first -- most specific.
    if _DATA_URI_RE.match(src):
        return ImageSourceType.DATA_URI

    # URL check -- http:// or https://.
    parsed = urlparse(src)
    if parsed.scheme in ("http", "https"):
        return ImageSourceType.EXTERNAL_URL

    # Local file check -- either absolute path, relative path with known
    # extension, or starts with ./ or ../.
    # Check for absolute path.
    if Path(src).is_absolute():
        return ImageSourceType.LOCAL_FILE

    # Check for relative path patterns.
    if src.startswith("./") or src.startswith("../") or src.startswith("~"):
        return ImageSourceType.LOCAL_FILE

    # Check if the string looks like a file path with an image extension.
    suffix = Path(src).suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return ImageSourceType.LOCAL_FILE

    return ImageSourceType.UNKNOWN
