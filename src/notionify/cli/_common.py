"""Shared CLI helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class InvalidIdError(ValueError):
    """Raised when a value cannot be parsed as a Notion UUID."""


_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
_UUID_HYPHENATED = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_INLINE_IMAGE = re.compile(r"!\[[^\]]*]\([^)]*\)")


def parse_id(raw: str) -> str:
    """Parse a Notion ID from a UUID, raw hex string, or Notion URL."""
    if not raw:
        raise InvalidIdError("empty id")

    hyphenated = _UUID_HYPHENATED.search(raw)
    if hyphenated:
        return hyphenated.group(0).lower()

    hex32 = _HEX32.search(raw)
    if hex32:
        value = hex32.group(0).lower()
        return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}"

    raise InvalidIdError(f"could not extract a Notion id from {raw!r}")


def read_markdown(path: str | Path) -> str:
    """Read a Markdown file as UTF-8 text."""
    return Path(path).read_text(encoding="utf-8")


def strip_images(markdown: str) -> str:
    """Remove Markdown inline image syntax from text."""
    cleaned = _INLINE_IMAGE.sub("", markdown)
    lines = cleaned.splitlines()
    return "\n".join(
        line
        for index, line in enumerate(lines)
        if line.strip() or _has_content_around(lines, index)
    )


def _has_content_around(lines: list[str], index: int) -> bool:
    return any(line.strip() for line in lines[:index]) and any(
        line.strip() for line in lines[index + 1 :]
    )


def format_error(err: BaseException, *, json_mode: bool) -> str:
    """Format an exception for human or JSON CLI output."""
    error_type = type(err).__name__
    message = str(err) or error_type
    code = getattr(err, "code", None)

    if json_mode:
        payload: dict[str, Any] = {
            "ok": False,
            "error_type": error_type,
            "message": message,
        }
        if code is not None:
            payload["code"] = str(code)
        return json.dumps(payload, ensure_ascii=False)

    suffix = f" (code={code})" if code is not None else ""
    return f"error: {error_type}: {message}{suffix}"
