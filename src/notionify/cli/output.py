"""CLI output helpers."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from notionify.cli._common import format_error


class Reporter:
    """Verbosity-aware output writer for CLI commands."""

    def __init__(
        self,
        *,
        verbosity: int,
        json_mode: bool,
        out: TextIO | None = None,
        err: TextIO | None = None,
    ) -> None:
        self.verbosity = verbosity
        self.json_mode = json_mode
        self._out = out or sys.stdout
        self._err = err or sys.stderr

    def step(self, message: str) -> None:
        if self.verbosity >= 1:
            print(message, file=self._err)

    def detail(self, payload: Any) -> None:
        if self.verbosity >= 2:
            if isinstance(payload, str):
                text = payload
            else:
                text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            print(text, file=self._err)

    def warn(self, message: str) -> None:
        print(f"warning: {message}", file=self._err)

    def result(self, payload: dict[str, Any]) -> None:
        if self.json_mode:
            text = json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str)
            print(text, file=self._out)
            return

        for key, value in payload.items():
            print(f"{key}: {value}", file=self._out)

    def write_raw(self, text: str) -> None:
        self._out.write(text)
        if not text.endswith("\n"):
            self._out.write("\n")

    def fail(self, err: BaseException, *, exit_code: int = 1) -> int:
        print(format_error(err, json_mode=self.json_mode), file=self._err)
        return exit_code
