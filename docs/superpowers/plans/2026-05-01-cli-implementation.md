# notionify CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a debug CLI (`notionify-cli` / `python -m notionify.cli`) covering all SDK capabilities: push, sync, pull, convert, inspect, diff.

**Architecture:** A new `src/notionify/cli/` subpackage. argparse + tomllib (stdlib only, no new deps). Each subcommand in its own file with `add_parser` / `run` interface. Shared `Reporter` for `-v`/`-vv`/`--json` output, shared `CLIConfig` loader with documented precedence. Calls into existing public client methods; adds one small SDK helper (`plan_page_update`) so the `diff` / `sync --dry-run` commands can render a diff plan without executing.

**Tech Stack:** Python 3.10+, argparse, tomllib (Py 3.11+), existing notionify SDK (`NotionifyClient`, `NotionifyConfig`, `MarkdownToNotionConverter`).

**Spec:** [`docs/superpowers/specs/2026-05-01-cli-design.md`](../specs/2026-05-01-cli-design.md)

---

## File Structure

**Created:**

- `src/notionify/cli/__init__.py` — exports `main`
- `src/notionify/cli/__main__.py` — `python -m notionify.cli` entry
- `src/notionify/cli/main.py` — top-level argparse + dispatch
- `src/notionify/cli/config.py` — `CLIConfig` + `load_config()`
- `src/notionify/cli/output.py` — `Reporter`
- `src/notionify/cli/_common.py` — `parse_id`, `read_markdown`, `format_error`, `strip_images`
- `src/notionify/cli/commands/__init__.py`
- `src/notionify/cli/commands/push.py`
- `src/notionify/cli/commands/sync.py`
- `src/notionify/cli/commands/pull.py`
- `src/notionify/cli/commands/convert.py`
- `src/notionify/cli/commands/inspect.py`
- `src/notionify/cli/commands/diff.py`
- `tests/unit/cli/__init__.py`
- `tests/unit/cli/conftest.py` — shared fixtures (mock client, tmp markdown)
- `tests/unit/cli/test_common.py`
- `tests/unit/cli/test_config.py`
- `tests/unit/cli/test_output.py`
- `tests/unit/cli/test_convert.py`
- `tests/unit/cli/test_inspect.py`
- `tests/unit/cli/test_pull.py`
- `tests/unit/cli/test_push.py`
- `tests/unit/cli/test_sync.py`
- `tests/unit/cli/test_diff.py`
- `tests/unit/cli/test_main.py` — argparse wiring + `python -m` invocation

**Modified:**

- `pyproject.toml` — add `[project.scripts] notionify-cli = "notionify.cli:main"`
- `src/notionify/client.py` — add `plan_page_update()` public method (returns ops + warnings without executing)

---

## Task 1: Module skeleton + entry point

**Files:**
- Create: `src/notionify/cli/__init__.py`
- Create: `src/notionify/cli/__main__.py`
- Create: `src/notionify/cli/main.py`
- Create: `src/notionify/cli/commands/__init__.py`
- Modify: `pyproject.toml` (add script entry + `tomli; python_version < "3.11"` dep)
- Test: `tests/unit/cli/test_main.py`

- [ ] **Step 1: Write failing test for empty CLI invocation**

`tests/unit/cli/__init__.py` (empty).
`tests/unit/cli/test_main.py`:

```python
from __future__ import annotations

import subprocess
import sys

import pytest

from notionify.cli import main


def test_no_args_prints_help_and_exits_nonzero(capsys):
    rc = main([])
    captured = capsys.readouterr()
    assert rc != 0
    assert "usage:" in captured.out.lower() or "usage:" in captured.err.lower()


def test_help_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_python_m_invocation_runs():
    result = subprocess.run(
        [sys.executable, "-m", "notionify.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "notionify-cli" in result.stdout or "usage" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'notionify.cli'`.

- [ ] **Step 3: Create the package skeleton**

`src/notionify/cli/__init__.py`:

```python
"""notionify-cli — debug command line for notionify SDK."""
from __future__ import annotations

from notionify.cli.main import main

__all__ = ["main"]
```

`src/notionify/cli/__main__.py`:

```python
from __future__ import annotations

import sys

from notionify.cli.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

`src/notionify/cli/commands/__init__.py` (empty).

`src/notionify/cli/main.py`:

```python
"""Top-level argparse parser and dispatch."""
from __future__ import annotations

import argparse
from typing import Sequence


def build_global_parser() -> argparse.ArgumentParser:
    """Parent parser holding global flags. Used as ``parents=[...]`` on every
    subcommand so flags work both before and after the subcommand name."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--token", help="Notion API token (overrides env / config).")
    p.add_argument("-c", "--config", dest="config_path",
                   help="Path to a notionify TOML config file.")
    p.add_argument("--profile", default="default",
                   help="Profile name in the config file.")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="Increase verbosity (-v, -vv).")
    p.add_argument("--json", dest="json_mode", action="store_true",
                   help="Emit machine-readable JSON output.")
    return p


def build_parser() -> argparse.ArgumentParser:
    global_parser = build_global_parser()
    parser = argparse.ArgumentParser(
        prog="notionify-cli",
        description="Debug CLI for the notionify SDK.",
        parents=[global_parser],
    )
    parser.set_defaults(command=None, _command=None)
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    # subcommands registered in later tasks. Each task will pass
    # parents=[global_parser] when calling subparsers.add_parser(...).
    parser._global_parser = global_parser  # type: ignore[attr-defined]
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    # dispatch wired in Task 7+
    return 1
```

**Key idea:** `add_parser` calls in later tasks must do
`subparsers.add_parser("name", parents=[parser._global_parser], help="...")`
so the subcommand also accepts `--token`, `--json`, etc.

- [ ] **Step 4: Update pyproject.toml**

Modify `pyproject.toml` two places:

1. In `[project] dependencies = [...]` add a conditional dep so 3.10 has TOML reading:

```toml
dependencies = [
    "mistune>=3.0,<4.0",
    "httpx>=0.27,<1.0",
    'tomli>=2.0; python_version < "3.11"',
]
```

2. After `[project.urls]` block, add:

```toml
[project.scripts]
notionify-cli = "notionify.cli:main"
```

**TOML scoping reminder (from project memory):** keep `dependencies` BEFORE `[project.urls]`.

- [ ] **Step 5: Reinstall package so the script is registered**

Run: `uv sync`
Expected: succeeds, `.venv/bin/notionify-cli` exists.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_main.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add src/notionify/cli/__init__.py src/notionify/cli/__main__.py \
        src/notionify/cli/main.py src/notionify/cli/commands/__init__.py \
        tests/unit/cli/__init__.py tests/unit/cli/test_main.py \
        pyproject.toml
git commit -m "feat(cli): scaffold notionify-cli entry point and argparse shell"
```

---

## Task 2: ID parser, markdown loader, error formatter (`_common.py`)

**Files:**
- Create: `src/notionify/cli/_common.py`
- Test: `tests/unit/cli/test_common.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/cli/test_common.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from notionify.cli._common import (
    InvalidIdError,
    format_error,
    parse_id,
    read_markdown,
    strip_images,
)
from notionify.errors import NotionifyConversionError


class TestParseId:
    def test_bare_uuid_with_hyphens(self):
        uid = "12345678-1234-1234-1234-123456789abc"
        assert parse_id(uid) == uid

    def test_bare_uuid_without_hyphens(self):
        raw = "123456781234123412341234567890ab"
        # Should normalise to hyphenated form
        assert parse_id(raw) == "12345678-1234-1234-1234-1234567890ab"

    def test_notion_url_with_title(self):
        url = "https://www.notion.so/My-Page-123456781234123412341234567890ab"
        assert parse_id(url) == "12345678-1234-1234-1234-1234567890ab"

    def test_notion_url_without_title(self):
        url = "https://notion.so/123456781234123412341234567890ab"
        assert parse_id(url) == "12345678-1234-1234-1234-1234567890ab"

    def test_invalid_string_raises(self):
        with pytest.raises(InvalidIdError):
            parse_id("not-an-id")

    def test_empty_string_raises(self):
        with pytest.raises(InvalidIdError):
            parse_id("")


class TestReadMarkdown:
    def test_reads_utf8_file(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text("# Hello\n\n世界", encoding="utf-8")
        assert read_markdown(f) == "# Hello\n\n世界"

    def test_missing_file_raises_filenotfound(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            read_markdown(tmp_path / "missing.md")


class TestStripImages:
    def test_removes_inline_image(self):
        md = "Hello ![alt](http://x/y.png) world"
        assert strip_images(md) == "Hello  world"

    def test_removes_image_only_line(self):
        md = "para\n\n![alt](pic.png)\n\nnext"
        result = strip_images(md)
        assert "pic.png" not in result
        assert "para" in result and "next" in result

    def test_keeps_text_when_no_images(self):
        md = "# Heading\n\nplain"
        assert strip_images(md) == md


class TestFormatError:
    def test_conversion_error_text_mode(self):
        err = NotionifyConversionError("bad token at line 5")
        out = format_error(err, json_mode=False)
        assert "bad token" in out
        assert "ConversionError" in out or "conversion" in out.lower()

    def test_any_error_json_mode(self):
        err = NotionifyConversionError("oops")
        out = format_error(err, json_mode=True)
        import json as _json
        payload = _json.loads(out)
        assert payload["ok"] is False
        assert payload["message"] == "oops"
        assert "error_type" in payload
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_common.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `_common.py`**

`src/notionify/cli/_common.py`:

```python
"""Shared CLI helpers."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class InvalidIdError(ValueError):
    """Raised when an ID string cannot be parsed as a Notion UUID."""


_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
_UUID_HYPHEN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def parse_id(raw: str) -> str:
    """Parse a Notion ID from a UUID, raw hex, or Notion URL.

    Returns the canonical hyphenated UUID string.
    """
    if not raw:
        raise InvalidIdError("empty id")

    m = _UUID_HYPHEN.search(raw)
    if m:
        return m.group(0).lower()

    m = _HEX32.search(raw)
    if m:
        h = m.group(0).lower()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    raise InvalidIdError(f"could not extract a Notion id from {raw!r}")


def read_markdown(path: str | Path) -> str:
    """Read a markdown file as UTF-8."""
    return Path(path).read_text(encoding="utf-8")


_INLINE_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def strip_images(markdown: str) -> str:
    """Remove inline image syntax from markdown.

    Used by ``--no-images`` for fast text-only debugging.
    """
    cleaned = _INLINE_IMG.sub("", markdown)
    # Drop lines that became blank-only after stripping
    return "\n".join(
        line for i, line in enumerate(cleaned.splitlines())
        if line.strip() or _line_is_separator(cleaned.splitlines(), i)
    )


def _line_is_separator(lines: list[str], idx: int) -> bool:
    # Preserve blank lines that surround real content (paragraph separators).
    has_before = any(line.strip() for line in lines[:idx])
    has_after = any(line.strip() for line in lines[idx + 1:])
    return has_before and has_after


def format_error(err: BaseException, *, json_mode: bool) -> str:
    """Format an exception for CLI display."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_common.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/notionify/cli/_common.py tests/unit/cli/test_common.py
git commit -m "feat(cli): add shared helpers — id parser, md loader, error formatter"
```

---

## Task 3: Config loader

**Files:**
- Create: `src/notionify/cli/config.py`
- Test: `tests/unit/cli/test_config.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/cli/test_config.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from notionify.cli.config import CLIConfig, ConfigError, load_config


def _ns(**kw):
    base = {"token": None, "config_path": None, "profile": "default"}
    base.update(kw)
    return argparse.Namespace(**base)


class TestLoadConfig:
    def test_token_flag_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOTION_TOKEN", "from_env")
        cfg = load_config(_ns(token="from_flag"))
        assert cfg.token == "from_flag"

    def test_explicit_config_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        f = tmp_path / "n.toml"
        f.write_text(
            '[default]\ntoken = "from_file"\ndefault_parent = "abc"\n',
            encoding="utf-8",
        )
        cfg = load_config(_ns(config_path=str(f)))
        assert cfg.token == "from_file"
        assert cfg.default_parent == "abc"

    def test_profile_selects_section(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        f = tmp_path / "n.toml"
        f.write_text(
            '[default]\ntoken = "d"\n\n[work]\ntoken = "w"\n',
            encoding="utf-8",
        )
        cfg = load_config(_ns(config_path=str(f), profile="work"))
        assert cfg.token == "w"

    def test_env_used_when_no_flag_no_explicit_config(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("NOTION_TOKEN", "from_env")
        monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.notionify.toml
        cfg = load_config(_ns())
        assert cfg.token == "from_env"

    def test_explicit_config_no_fallback_to_home(
        self, monkeypatch, tmp_path: Path
    ):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".notionify.toml").write_text(
            '[default]\ntoken = "home_token"\n', encoding="utf-8"
        )
        monkeypatch.setenv("HOME", str(home))

        explicit = tmp_path / "elsewhere.toml"
        explicit.write_text('[default]\n', encoding="utf-8")  # no token
        with pytest.raises(ConfigError):
            load_config(_ns(config_path=str(explicit)))

    def test_default_parent_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOTION_TOKEN", "t")
        monkeypatch.setenv("NOTION_DEFAULT_PARENT", "page-xyz")
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = load_config(_ns())
        assert cfg.default_parent == "page-xyz"

    def test_missing_token_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(ConfigError) as exc_info:
            load_config(_ns())
        assert "no Notion token" in str(exc_info.value).lower() or \
               "no notion token" in str(exc_info.value).lower()

    def test_missing_profile_section_raises(
        self, monkeypatch, tmp_path: Path
    ):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        f = tmp_path / "n.toml"
        f.write_text('[default]\ntoken = "x"\n', encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(_ns(config_path=str(f), profile="ghost"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_config.py -v`
Expected: FAIL — `notionify.cli.config` missing.

- [ ] **Step 3: Implement config loader**

`src/notionify/cli/config.py`:

```python
"""CLI configuration loading."""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Py 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigError(RuntimeError):
    """Raised when config cannot be loaded."""


@dataclass(frozen=True)
class CLIConfig:
    token: str
    default_parent: str | None


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _section(data: dict[str, Any], profile: str, source: str) -> dict[str, Any]:
    if profile not in data:
        raise ConfigError(
            f"profile {profile!r} not found in {source}"
        )
    section = data[profile]
    if not isinstance(section, dict):
        raise ConfigError(f"profile {profile!r} in {source} is not a table")
    return section


def load_config(args: argparse.Namespace) -> CLIConfig:
    """Resolve token + default_parent from CLI flags, env, and config files.

    Precedence (highest first):
      1. ``--token`` flag
      2. ``-c/--config PATH`` file + ``--profile``
      3. ``NOTION_TOKEN`` env
      4. ``~/.notionify.toml`` + ``--profile`` (only when ``-c`` not given)
      5. ``NOTION_DEFAULT_PARENT`` env (for ``default_parent`` only)
    """
    profile: str = getattr(args, "profile", "default") or "default"
    explicit_config: str | None = getattr(args, "config_path", None)

    explicit_data: dict[str, Any] | None = None
    home_data: dict[str, Any] | None = None

    if explicit_config:
        path = Path(explicit_config).expanduser()
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        explicit_data = _section(_read_toml(path), profile, str(path))
    else:
        home_cfg = Path(os.path.expanduser("~/.notionify.toml"))
        if home_cfg.exists():
            raw = _read_toml(home_cfg)
            if profile in raw:
                home_data = _section(raw, profile, str(home_cfg))

    # Token precedence: --token > -c file > NOTION_TOKEN > ~/.notionify.toml
    flag_token: str | None = getattr(args, "token", None)
    explicit_token: str | None = explicit_data.get("token") if explicit_data else None
    env_token: str | None = os.environ.get("NOTION_TOKEN")
    home_token: str | None = home_data.get("token") if home_data else None

    token: str | None = flag_token or explicit_token or env_token or home_token

    if not token:
        raise ConfigError(
            "no Notion token found. Set NOTION_TOKEN, pass --token, "
            "or configure ~/.notionify.toml"
        )

    # default_parent precedence: -c file > ~/.notionify.toml > NOTION_DEFAULT_PARENT
    default_parent: str | None = None
    if explicit_data is not None:
        default_parent = explicit_data.get("default_parent")
    if default_parent is None and home_data is not None:
        default_parent = home_data.get("default_parent")
    if default_parent is None:
        default_parent = os.environ.get("NOTION_DEFAULT_PARENT")

    return CLIConfig(token=token, default_parent=default_parent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_config.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/notionify/cli/config.py tests/unit/cli/test_config.py
git commit -m "feat(cli): add config loader with precedence (flag > -c > env > home)"
```

---

## Task 4: Reporter (output / logging)

**Files:**
- Create: `src/notionify/cli/output.py`
- Test: `tests/unit/cli/test_output.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/cli/test_output.py`:

```python
from __future__ import annotations

import json

from notionify.cli.output import Reporter


def test_step_silent_at_v0(capsys):
    r = Reporter(verbosity=0, json_mode=False)
    r.step("hello")
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out == ""


def test_step_visible_at_v1(capsys):
    r = Reporter(verbosity=1, json_mode=False)
    r.step("hello")
    out = capsys.readouterr()
    assert "hello" in out.err
    assert out.out == ""


def test_detail_only_at_v2(capsys):
    r = Reporter(verbosity=1, json_mode=False)
    r.detail({"x": 1})
    assert capsys.readouterr().err == ""

    r2 = Reporter(verbosity=2, json_mode=False)
    r2.detail({"x": 1})
    assert "x" in capsys.readouterr().err


def test_warn_always_visible(capsys):
    r = Reporter(verbosity=0, json_mode=False)
    r.warn("careful")
    assert "careful" in capsys.readouterr().err


def test_result_text_mode(capsys):
    r = Reporter(verbosity=0, json_mode=False)
    r.result({"page_id": "abc", "url": "http://n/abc"})
    out = capsys.readouterr()
    assert "abc" in out.out
    assert "http://n/abc" in out.out


def test_result_json_mode(capsys):
    r = Reporter(verbosity=0, json_mode=True)
    r.result({"page_id": "abc"})
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["page_id"] == "abc"


def test_fail_returns_nonzero(capsys):
    r = Reporter(verbosity=0, json_mode=False)
    rc = r.fail(ValueError("nope"), exit_code=4)
    assert rc == 4
    assert "nope" in capsys.readouterr().err


def test_fail_json_mode(capsys):
    r = Reporter(verbosity=0, json_mode=True)
    r.fail(ValueError("oops"), exit_code=3)
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert payload["message"] == "oops"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_output.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement Reporter**

`src/notionify/cli/output.py`:

```python
"""CLI output: Reporter for verbosity-aware logging and result rendering."""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from notionify.cli._common import format_error


class Reporter:
    """Verbosity-aware printer.

    - Logs (step / detail / warn) → stderr
    - Results → stdout
    - --json mode renders payloads as JSON
    """

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

    def step(self, msg: str) -> None:
        if self.verbosity >= 1:
            print(f"• {msg}", file=self._err)

    def detail(self, payload: Any) -> None:
        if self.verbosity >= 2:
            text = (
                payload if isinstance(payload, str)
                else json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            )
            print(text, file=self._err)

    def warn(self, msg: str) -> None:
        print(f"warning: {msg}", file=self._err)

    def result(self, payload: dict[str, Any]) -> None:
        if self.json_mode:
            data = {"ok": True, **payload}
            print(json.dumps(data, ensure_ascii=False, default=str), file=self._out)
        else:
            for key, value in payload.items():
                print(f"{key}: {value}", file=self._out)

    def write_raw(self, text: str) -> None:
        """Write raw text to stdout (e.g. pulled markdown)."""
        self._out.write(text)
        if not text.endswith("\n"):
            self._out.write("\n")

    def fail(self, err: BaseException, *, exit_code: int = 1) -> int:
        rendered = format_error(err, json_mode=self.json_mode)
        print(rendered, file=self._err)
        return exit_code
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_output.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/notionify/cli/output.py tests/unit/cli/test_output.py
git commit -m "feat(cli): add Reporter with verbosity levels and json mode"
```

---

## Task 5: Add `plan_page_update` SDK helper

The `diff` and `sync --dry-run` commands need a way to compute a diff plan without executing it. The current `update_page_from_markdown` does both in one call. Add a public method that returns the plan.

**Files:**
- Modify: `src/notionify/client.py`
- Test: `tests/unit/test_plan_page_update.py` (new)

- [ ] **Step 1: Write failing test**

`tests/unit/test_plan_page_update.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

from notionify import NotionifyClient
from notionify.models import DiffOpType


def _make_client_with_mocked_api():
    client = NotionifyClient(token="secret_test")
    # Replace internals with mocks
    client._pages = MagicMock()
    client._blocks = MagicMock()
    client._pages.retrieve.return_value = {
        "id": "page-1",
        "last_edited_time": "2026-01-01T00:00:00Z",
        "properties": {},
    }
    client._blocks.get_children.return_value = []
    return client


def test_plan_page_update_returns_ops_for_added_paragraph():
    client = _make_client_with_mocked_api()
    plan = client.plan_page_update("page-1", "Hello World")
    # An empty page plus new paragraph => at least one INSERT op
    assert any(op.op_type == DiffOpType.INSERT for op in plan.ops)
    assert plan.warnings == [] or all(isinstance(w, str) for w in plan.warnings) or \
           all(hasattr(w, "message") for w in plan.warnings)


def test_plan_page_update_does_not_call_executor():
    client = _make_client_with_mocked_api()
    client._diff_executor = MagicMock()
    client.plan_page_update("page-1", "x")
    client._diff_executor.execute.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plan_page_update.py -v`
Expected: FAIL — `AttributeError: ... 'plan_page_update'`.

- [ ] **Step 3: Implement `plan_page_update`**

In `src/notionify/client.py`, locate `update_page_from_markdown` (line ~366) and add a sibling method just before it. First, add a tiny dataclass at the top of `client.py` (after existing imports):

```python
@dataclass(frozen=True)
class PlanResult:
    """Returned by :meth:`NotionifyClient.plan_page_update`."""

    ops: list[DiffOp]
    warnings: list[ConversionWarning]
    images_to_upload: int
```

Required imports (`client.py` currently has none of these — add them):

```python
from dataclasses import dataclass
```

And in the existing `from notionify.models import (...)` block, add `DiffOp` to the list (`ConversionWarning` is already imported).

Then add the method to the `NotionifyClient` class:

```python
def plan_page_update(
    self,
    page_id: str,
    markdown: str,
) -> PlanResult:
    """Compute a diff plan for updating ``page_id`` with ``markdown``.

    Does **not** execute the plan or upload images. Use this for
    dry-run inspection or CLI ``diff`` rendering. The plan is computed
    against the page's current children at call time.
    """
    existing_blocks = self._blocks.get_children(page_id)
    conversion = self._converter.convert(markdown)
    new_blocks = conversion.blocks
    ops = self._diff_planner.plan(existing_blocks, new_blocks)
    return PlanResult(
        ops=list(ops),
        warnings=list(conversion.warnings),
        images_to_upload=len(conversion.images),
    )
```

Re-export at module level: in `src/notionify/__init__.py` add `PlanResult` to the imports from `notionify.client` and to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_plan_page_update.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run full test suite to ensure no regressions**

Run: `uv run pytest -q`
Expected: previously-green suite still green, plus 2 new passing tests.

- [ ] **Step 6: Commit**

```bash
git add src/notionify/client.py src/notionify/__init__.py \
        tests/unit/test_plan_page_update.py
git commit -m "feat(client): add plan_page_update for diff dry-run support"
```

---

## Task 6: Shared CLI test fixtures

**Files:**
- Create: `tests/unit/cli/conftest.py`

- [ ] **Step 1: Write the conftest**

`tests/unit/cli/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def md_file(tmp_path: Path) -> Path:
    """A simple markdown fixture file."""
    f = tmp_path / "doc.md"
    f.write_text("# Title\n\nHello world.\n", encoding="utf-8")
    return f


_CLIENT_USERS = (
    "notionify.cli.commands.push",
    "notionify.cli.commands.sync",
    "notionify.cli.commands.pull",
    "notionify.cli.commands.inspect",
)


@pytest.fixture
def fake_client(monkeypatch):
    """Patch ``NotionifyClient`` in command modules with a MagicMock factory.

    Modules that haven't been created yet are silently skipped — earlier
    tasks can use this fixture even before later command modules exist.
    Also patches the context-manager protocol so ``with NotionifyClient(...)
    as client`` returns the mock instance.
    """
    import importlib

    instance = MagicMock(name="NotionifyClient_instance")
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    factory = MagicMock(name="NotionifyClient_factory", return_value=instance)

    for module_path in _CLIENT_USERS:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        if hasattr(mod, "NotionifyClient"):
            monkeypatch.setattr(f"{module_path}.NotionifyClient", factory)
    return instance


@pytest.fixture
def env_token(monkeypatch):
    """Provide a token so commands needing one can construct a client."""
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.delenv("NOTION_DEFAULT_PARENT", raising=False)
    return "secret_test"
```

- [ ] **Step 2: Commit**

```bash
git add tests/unit/cli/conftest.py
git commit -m "test(cli): shared fixtures for command tests"
```

---

## Task 7: `convert` command

`convert` is the simplest — pure local, no token, no client.

**Files:**
- Create: `src/notionify/cli/commands/convert.py`
- Modify: `src/notionify/cli/main.py`
- Test: `tests/unit/cli/test_convert.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/cli/test_convert.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from notionify.cli.main import main


def test_convert_to_stdout(md_file: Path, capsys):
    rc = main(["convert", str(md_file)])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert any(b.get("type") == "heading_1" for b in payload)


def test_convert_to_file(md_file: Path, tmp_path: Path):
    target = tmp_path / "blocks.json"
    rc = main(["convert", str(md_file), "--out", str(target)])
    assert rc == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert any(b.get("type") == "heading_1" for b in payload)


def test_convert_missing_file_returns_error(tmp_path: Path, capsys):
    rc = main(["convert", str(tmp_path / "nope.md")])
    err = capsys.readouterr().err
    assert rc != 0
    assert "FileNotFound" in err or "not found" in err.lower() or "No such" in err


def test_convert_strip_images(tmp_path: Path, capsys):
    f = tmp_path / "img.md"
    f.write_text("Hello ![a](x.png)\n", encoding="utf-8")
    rc = main(["convert", str(f), "--no-images"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    # image blocks should not appear
    assert not any(b.get("type") == "image" for b in payload)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_convert.py -v`
Expected: FAIL — convert command not registered.

- [ ] **Step 3: Implement `convert.py`**

`src/notionify/cli/commands/convert.py`:

```python
"""`notionify-cli convert` — md → Notion blocks JSON, no API calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from notionify.cli._common import read_markdown, strip_images
from notionify.cli.output import Reporter
from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter


def add_parser(
    subparsers: argparse._SubParsersAction,
    global_parser: argparse.ArgumentParser,
) -> None:
    p = subparsers.add_parser(
        "convert",
        help="Convert markdown to Notion blocks JSON (no API calls).",
        parents=[global_parser],
    )
    p.add_argument("file", help="Path to a markdown file.")
    p.add_argument("--out", help="Output file (default: stdout).")
    p.add_argument("--no-images", action="store_true",
                   help="Strip image syntax before converting.")
    p.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter) -> int:
    md = read_markdown(args.file)
    if args.no_images:
        md = strip_images(md)

    reporter.step(f"converting {args.file}")
    converter = MarkdownToNotionConverter(NotionifyConfig(token="dummy"))
    conversion = converter.convert(md)
    blocks_json = json.dumps(conversion.blocks, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(blocks_json + "\n", encoding="utf-8")
        reporter.result({"output": args.out, "blocks": len(conversion.blocks)})
    else:
        reporter.write_raw(blocks_json)
    return 0
```

- [ ] **Step 4: Wire it up in `main.py`**

Replace `src/notionify/cli/main.py` with the full dispatcher:

```python
"""Top-level argparse parser and dispatch."""
from __future__ import annotations

import argparse
from typing import Sequence

from notionify.cli._common import InvalidIdError
from notionify.cli.commands import convert as cmd_convert
from notionify.cli.config import ConfigError, load_config
from notionify.cli.output import Reporter
from notionify.errors import (
    NotionifyAuthError,
    NotionifyConversionError,
    NotionifyError,
    NotionifyNetworkError,
    NotionifyRetryExhaustedError,
)


# Commands that don't need a token / config loaded.
_NO_CONFIG_COMMANDS: set[str] = {"convert"}


def build_global_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--token")
    p.add_argument("-c", "--config", dest="config_path")
    p.add_argument("--profile", default="default")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("--json", dest="json_mode", action="store_true")
    return p


def build_parser() -> argparse.ArgumentParser:
    global_parser = build_global_parser()
    parser = argparse.ArgumentParser(
        prog="notionify-cli",
        description="Debug CLI for the notionify SDK.",
        parents=[global_parser],
    )
    parser.set_defaults(command=None, _command=None)
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    cmd_convert.add_parser(subparsers, global_parser)
    # later tasks register their commands here
    return parser


def _classify(err: BaseException) -> int:
    if isinstance(err, ConfigError):
        return 2
    if isinstance(err, (NotionifyAuthError, NotionifyNetworkError,
                        NotionifyRetryExhaustedError)):
        return 3
    if isinstance(err, NotionifyConversionError):
        return 4
    if isinstance(err, NotionifyError):
        return 3
    if isinstance(err, (InvalidIdError, FileNotFoundError, ValueError)):
        return 1
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1

    reporter = Reporter(verbosity=args.verbose, json_mode=args.json_mode)
    handler = getattr(args, "_command", None)
    if handler is None:  # pragma: no cover - argparse guards this
        return reporter.fail(
            RuntimeError(f"command not implemented: {args.command}")
        )

    try:
        if args.command in _NO_CONFIG_COMMANDS:
            return int(handler(args, reporter))
        config = load_config(args)
        return int(handler(args, reporter, config))
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return reporter.fail(e, exit_code=_classify(e))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_convert.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/notionify/cli/commands/convert.py \
        src/notionify/cli/main.py \
        tests/unit/cli/test_convert.py
git commit -m "feat(cli): add convert command (markdown -> Notion blocks JSON)"
```

---

## Task 8: `inspect` command

**Files:**
- Create: `src/notionify/cli/commands/inspect.py`
- Modify: `src/notionify/cli/main.py`
- Test: `tests/unit/cli/test_inspect.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/cli/test_inspect.py`:

```python
from __future__ import annotations

import json

from notionify.cli.main import main


def test_inspect_page_only(env_token, fake_client, capsys):
    fake_client._pages.retrieve.return_value = {"id": "abc", "object": "page"}
    rc = main(
        ["inspect", "12345678-1234-1234-1234-123456789abc", "--json"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["page"]["id"] == "abc"
    assert "children" not in payload


def test_inspect_with_children(env_token, fake_client, capsys):
    fake_client._pages.retrieve.return_value = {"id": "abc"}
    fake_client._blocks.get_children.return_value = [
        {"id": "b1", "type": "paragraph"}
    ]
    rc = main([
        "inspect", "12345678-1234-1234-1234-123456789abc",
        "--children", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["children"] == [{"id": "b1", "type": "paragraph"}]


def test_inspect_invalid_id(env_token, capsys):
    rc = main(["inspect", "not-an-id"])
    assert rc != 0
    assert "id" in capsys.readouterr().err.lower()


def test_inspect_no_token_returns_config_error(monkeypatch, capsys):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")
    rc = main(["inspect", "12345678-1234-1234-1234-123456789abc"])
    assert rc == 2
    assert "token" in capsys.readouterr().err.lower()
```

`inspect.py` reaches into `client._pages.retrieve` / `client._blocks.get_children` — this is intentional debug-only access (see Notes section).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_inspect.py -v`
Expected: FAIL — inspect command not registered.

- [ ] **Step 3: Implement `inspect.py`**

`src/notionify/cli/commands/inspect.py`:

```python
"""`notionify-cli inspect` — fetch a page's raw JSON."""
from __future__ import annotations

import argparse

from notionify import NotionifyClient
from notionify.cli._common import parse_id
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter


def add_parser(
    subparsers: argparse._SubParsersAction,
    global_parser: argparse.ArgumentParser,
) -> None:
    p = subparsers.add_parser(
        "inspect",
        help="Fetch a page's raw JSON (and optionally its children).",
        parents=[global_parser],
    )
    p.add_argument("page", help="Page ID or Notion URL.")
    p.add_argument("--children", action="store_true",
                   help="Also fetch and dump first-level child blocks.")
    p.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    page_id = parse_id(args.page)
    reporter.step(f"fetching page {page_id}")

    with NotionifyClient(token=config.token) as client:
        page = client._pages.retrieve(page_id)
        payload: dict = {"page": page}
        if args.children:
            reporter.step("fetching children")
            children = client._blocks.get_children(page_id)
            payload["children"] = list(children)

    reporter.result(payload)
    return 0
```

- [ ] **Step 4: Register in `main.py`**

In `src/notionify/cli/main.py`, add to the imports:

```python
from notionify.cli.commands import inspect as cmd_inspect
```

And in `build_parser()` after `cmd_convert.add_parser(subparsers, global_parser)`:

```python
cmd_inspect.add_parser(subparsers, global_parser)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_inspect.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/notionify/cli/commands/inspect.py src/notionify/cli/main.py \
        tests/unit/cli/test_inspect.py
git commit -m "feat(cli): add inspect command for raw page/children JSON"
```

---

## Task 9: `pull` command

**Files:**
- Create: `src/notionify/cli/commands/pull.py`
- Modify: `src/notionify/cli/main.py`
- Test: `tests/unit/cli/test_pull.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/cli/test_pull.py`:

```python
from __future__ import annotations

from pathlib import Path

from notionify.cli.main import main


def test_pull_to_stdout(env_token, fake_client, capsys):
    fake_client.page_to_markdown.return_value = "# Pulled\n\nbody\n"
    rc = main(["pull", "12345678-1234-1234-1234-123456789abc"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "# Pulled" in out
    fake_client.page_to_markdown.assert_called_once()
    args, kwargs = fake_client.page_to_markdown.call_args
    # recursive defaults to True for CLI
    assert kwargs.get("recursive") is True or (len(args) >= 2 and args[1] is True)


def test_pull_to_file(env_token, fake_client, tmp_path: Path):
    fake_client.page_to_markdown.return_value = "stuff\n"
    out_path = tmp_path / "out.md"
    rc = main([
        "pull", "12345678-1234-1234-1234-123456789abc",
        "--out", str(out_path),
    ])
    assert rc == 0
    assert out_path.read_text(encoding="utf-8") == "stuff\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_pull.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `pull.py`**

`src/notionify/cli/commands/pull.py`:

```python
"""`notionify-cli pull` — Notion page → markdown."""
from __future__ import annotations

import argparse
from pathlib import Path

from notionify import NotionifyClient
from notionify.cli._common import parse_id
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter


def add_parser(
    subparsers: argparse._SubParsersAction,
    global_parser: argparse.ArgumentParser,
) -> None:
    p = subparsers.add_parser(
        "pull", help="Pull a Notion page as markdown.",
        parents=[global_parser],
    )
    p.add_argument("page", help="Page ID or Notion URL.")
    p.add_argument("--out", help="Output file (default: stdout).")
    p.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    page_id = parse_id(args.page)
    reporter.step(f"pulling page {page_id}")
    with NotionifyClient(token=config.token) as client:
        md = client.page_to_markdown(page_id, recursive=True)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        reporter.result({"output": args.out, "bytes": len(md.encode("utf-8"))})
    else:
        reporter.write_raw(md)
    return 0
```

- [ ] **Step 4: Register in `main.py`**

Add `from notionify.cli.commands import pull as cmd_pull` and `cmd_pull.add_parser(subparsers, global_parser)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_pull.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/notionify/cli/commands/pull.py src/notionify/cli/main.py \
        tests/unit/cli/test_pull.py
git commit -m "feat(cli): add pull command (page -> markdown)"
```

---

## Task 10: `push` command

**Files:**
- Create: `src/notionify/cli/commands/push.py`
- Modify: `src/notionify/cli/main.py`
- Test: `tests/unit/cli/test_push.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/cli/test_push.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from notionify.cli.main import main
from notionify.models import PageCreateResult


def _stub_create_result(page_id="page-xyz"):
    return PageCreateResult(
        page_id=page_id,
        url=f"https://notion.so/{page_id}",
        blocks_created=1,
        images_uploaded=0,
        warnings=[],
    )


def test_push_creates_page(env_token, fake_client, md_file: Path, capsys):
    fake_client.create_page_with_markdown.return_value = _stub_create_result()
    rc = main([
        "push", str(md_file),
        "--parent", "12345678-1234-1234-1234-123456789abc",
        "--title", "Doc",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["page_id"] == "page-xyz"
    fake_client.create_page_with_markdown.assert_called_once()
    _, kwargs = fake_client.create_page_with_markdown.call_args
    assert kwargs["title"] == "Doc"
    assert kwargs["markdown"].startswith("# Title")


def test_push_uses_default_parent_env(
    monkeypatch, fake_client, md_file: Path, capsys
):
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setenv("NOTION_DEFAULT_PARENT",
                       "12345678-1234-1234-1234-123456789abc")
    fake_client.create_page_with_markdown.return_value = _stub_create_result()
    rc = main(["push", str(md_file), "--title", "Doc"])
    assert rc == 0


def test_push_dry_run_does_not_call_create(
    env_token, fake_client, md_file: Path, capsys
):
    rc = main([
        "push", str(md_file),
        "--parent", "12345678-1234-1234-1234-123456789abc",
        "--dry-run",
    ])
    assert rc == 0
    fake_client.create_page_with_markdown.assert_not_called()
    out = capsys.readouterr().out
    assert "blocks" in out.lower()


def test_push_no_parent_errors(env_token, md_file: Path, capsys, monkeypatch):
    monkeypatch.delenv("NOTION_DEFAULT_PARENT", raising=False)
    rc = main(["push", str(md_file)])
    assert rc != 0
    assert "parent" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_push.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `push.py`**

`src/notionify/cli/commands/push.py`:

```python
"""`notionify-cli push` — create a new page from markdown."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from notionify import NotionifyClient
from notionify.cli._common import parse_id, read_markdown, strip_images
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter
from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter

# NotionifyConfig is used only by the dry-run path (no token needed for
# local conversion). The non-dry-run path passes kwargs straight to
# NotionifyClient(token=..., **kwargs); see client.py:93.


def add_parser(
    subparsers: argparse._SubParsersAction,
    global_parser: argparse.ArgumentParser,
) -> None:
    p = subparsers.add_parser(
        "push", help="Create a new Notion page from markdown.",
        parents=[global_parser],
    )
    p.add_argument("file", help="Path to a markdown file.")
    p.add_argument("--parent", help="Parent page or database ID. "
                                    "Falls back to NOTION_DEFAULT_PARENT.")
    p.add_argument("--parent-type", choices=("page", "database"), default="page")
    p.add_argument("--title", help="Page title (default: filename stem).")
    p.add_argument("--upload-remote-images", action="store_true",
                   help="Download remote URLs and re-upload them.")
    p.add_argument("--no-images", action="store_true",
                   help="Strip image syntax before converting.")
    p.add_argument("--dry-run", action="store_true",
                   help="Convert only; do not call the Notion API.")
    p.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    parent_raw = args.parent or config.default_parent
    if not parent_raw:
        return reporter.fail(
            ValueError("no --parent given and NOTION_DEFAULT_PARENT is unset"),
            exit_code=2,
        )
    parent_id = parse_id(parent_raw)
    title = args.title or Path(args.file).stem
    md = read_markdown(args.file)
    if args.no_images:
        md = strip_images(md)

    if args.dry_run:
        return _dry_run(md, reporter)

    reporter.step(f"creating page under {parent_id}")
    with NotionifyClient(
        token=config.token,
        remote_image_upload=args.upload_remote_images,
        image_base_dir=str(Path(args.file).resolve().parent),
    ) as client:
        result = client.create_page_with_markdown(
            parent_id=parent_id,
            title=title,
            markdown=md,
            parent_type=args.parent_type,
        )
    reporter.result({
        "page_id": result.page_id,
        "url": result.url,
        "blocks_created": result.blocks_created,
        "images_uploaded": result.images_uploaded,
        "warnings": [str(w) for w in result.warnings],
    })
    return 0


def _dry_run(md: str, reporter: Reporter) -> int:
    converter = MarkdownToNotionConverter(NotionifyConfig(token="dummy"))
    conversion = converter.convert(md)
    outline = [b.get("type", "?") for b in conversion.blocks[:20]]
    reporter.result({
        "blocks": len(conversion.blocks),
        "outline_first_20": outline,
        "warnings": [str(w) for w in conversion.warnings],
    })
    reporter.detail(json.dumps(conversion.blocks, ensure_ascii=False, indent=2))
    return 0
```

**Note:** `NotionifyClient.__init__(token, **kwargs)` forwards every kwarg to `NotionifyConfig`. Pass `remote_image_upload` and `image_base_dir` directly — never a `config=` kwarg. Verified at `src/notionify/client.py:93`.

- [ ] **Step 4: Register in `main.py`**

Add `from notionify.cli.commands import push as cmd_push` and `cmd_push.add_parser(subparsers, global_parser)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_push.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/notionify/cli/commands/push.py src/notionify/cli/main.py \
        tests/unit/cli/test_push.py
git commit -m "feat(cli): add push command (markdown -> new Notion page)"
```

---

## Task 11: `sync` command

**Files:**
- Create: `src/notionify/cli/commands/sync.py`
- Modify: `src/notionify/cli/main.py`
- Test: `tests/unit/cli/test_sync.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/cli/test_sync.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from notionify.cli.main import main


def test_sync_executes(env_token, fake_client, md_file: Path, capsys):
    from notionify.models import UpdateResult
    update_result = UpdateResult(
        strategy_used="diff",
        blocks_kept=2,
        blocks_inserted=1,
        blocks_deleted=0,
        blocks_replaced=0,
        images_uploaded=0,
        warnings=[],
    )
    fake_client.update_page_from_markdown.return_value = update_result

    rc = main([
        "sync", str(md_file),
        "--page", "12345678-1234-1234-1234-123456789abc",
        "--json",
    ])
    assert rc == 0
    fake_client.update_page_from_markdown.assert_called_once()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["strategy_used"] == "diff"
    assert payload["blocks_inserted"] == 1


def test_sync_dry_run_calls_plan_only(env_token, fake_client, md_file: Path):
    plan = MagicMock()
    plan.ops = []
    plan.warnings = []
    plan.images_to_upload = 0
    fake_client.plan_page_update.return_value = plan
    rc = main([
        "sync", str(md_file),
        "--page", "12345678-1234-1234-1234-123456789abc",
        "--dry-run",
    ])
    assert rc == 0
    fake_client.plan_page_update.assert_called_once()
    fake_client.update_page_from_markdown.assert_not_called()


def test_sync_invalid_page_id(env_token, md_file: Path, capsys):
    rc = main(["sync", str(md_file), "--page", "garbage"])
    assert rc != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/cli/test_sync.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `sync.py`**

`src/notionify/cli/commands/sync.py`:

```python
"""`notionify-cli sync` — incrementally update an existing page."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from notionify import NotionifyClient
from notionify.cli._common import parse_id, read_markdown, strip_images
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter


def add_parser(
    subparsers: argparse._SubParsersAction,
    global_parser: argparse.ArgumentParser,
) -> None:
    p = subparsers.add_parser(
        "sync", help="Incrementally update an existing page from markdown.",
        parents=[global_parser],
    )
    p.add_argument("file")
    p.add_argument("--page", required=True, help="Page ID or Notion URL.")
    p.add_argument("--upload-remote-images", action="store_true")
    p.add_argument("--no-images", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute the diff plan without applying it.")
    p.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    page_id = parse_id(args.page)
    md = read_markdown(args.file)
    if args.no_images:
        md = strip_images(md)

    with NotionifyClient(
        token=config.token,
        remote_image_upload=args.upload_remote_images,
        image_base_dir=str(Path(args.file).resolve().parent),
    ) as client:
        if args.dry_run:
            reporter.step(f"planning diff for {page_id}")
            plan = client.plan_page_update(page_id, md)
            counts = Counter(op.op_type.value for op in plan.ops)
            reporter.result({
                "page_id": page_id,
                "total_ops": len(plan.ops),
                "by_op": dict(counts),
                "images_to_upload": plan.images_to_upload,
                "warnings": [str(w) for w in plan.warnings],
            })
            for op in plan.ops:
                reporter.detail({
                    "op": op.op_type.value,
                    "existing_id": op.existing_id,
                    "depth": op.depth,
                })
            return 0

        reporter.step(f"syncing {page_id}")
        result = client.update_page_from_markdown(page_id, md)
        reporter.result({
            "page_id": page_id,
            "strategy_used": result.strategy_used,
            "blocks_kept": result.blocks_kept,
            "blocks_inserted": result.blocks_inserted,
            "blocks_deleted": result.blocks_deleted,
            "blocks_replaced": result.blocks_replaced,
            "images_uploaded": result.images_uploaded,
            "warnings": [str(w) for w in result.warnings],
        })
    return 0
```

- [ ] **Step 4: Register in `main.py`**

Add `from notionify.cli.commands import sync as cmd_sync` and `cmd_sync.add_parser(subparsers, global_parser)`. Inside `build_parser`, retrieve `global_parser` via the local variable already created.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/cli/test_sync.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/notionify/cli/commands/sync.py src/notionify/cli/main.py \
        tests/unit/cli/test_sync.py
git commit -m "feat(cli): add sync command (markdown -> diff -> Notion)"
```

---

## Task 12: `diff` command

`diff` is `sync --dry-run` with a different name for clarity.

**Files:**
- Create: `src/notionify/cli/commands/diff.py`
- Modify: `src/notionify/cli/main.py`
- Test: `tests/unit/cli/test_diff.py`

- [ ] **Step 1: Write failing test**

`tests/unit/cli/test_diff.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from notionify.cli.main import main


def test_diff_calls_plan_and_does_not_execute(
    env_token, fake_client, md_file: Path, capsys
):
    plan = MagicMock()
    plan.ops = []
    plan.warnings = []
    plan.images_to_upload = 0
    fake_client.plan_page_update.return_value = plan

    rc = main([
        "diff", str(md_file),
        "--page", "12345678-1234-1234-1234-123456789abc",
    ])
    assert rc == 0
    fake_client.plan_page_update.assert_called_once()
    fake_client.update_page_from_markdown.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/cli/test_diff.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `diff.py`**

`src/notionify/cli/commands/diff.py`:

```python
"""`notionify-cli diff` — alias for `sync --dry-run`."""
from __future__ import annotations

import argparse

from notionify.cli.commands.sync import run as sync_run


def add_parser(
    subparsers: argparse._SubParsersAction,
    global_parser: argparse.ArgumentParser,
) -> None:
    p = subparsers.add_parser(
        "diff",
        help="Show the diff plan for syncing markdown to a page (no execution).",
        parents=[global_parser],
    )
    p.add_argument("file")
    p.add_argument("--page", required=True, help="Page ID or Notion URL.")
    p.add_argument("--no-images", action="store_true")
    p.set_defaults(_command=run, dry_run=True, upload_remote_images=False)


def run(
    args: argparse.Namespace,
    reporter: "Reporter",
    config: "CLIConfig",
) -> int:
    # Delegate to sync's run with dry_run forced True
    args.dry_run = True
    args.upload_remote_images = False
    return sync_run(args, reporter, config)
```

Add the missing imports at the top of `diff.py`:

```python
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter
```

- [ ] **Step 4: Register in `main.py`**

Add `from notionify.cli.commands import diff as cmd_diff` and `cmd_diff.add_parser(subparsers, global_parser)`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/cli/test_diff.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add src/notionify/cli/commands/diff.py src/notionify/cli/main.py \
        tests/unit/cli/test_diff.py
git commit -m "feat(cli): add diff command (sync dry-run alias)"
```

---

## Task 13: README docs + CHANGELOG

**Files:**
- Modify: `README.md` (add CLI section)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add CLI section to README**

After the SDK quickstart section in `README.md`, append:

````markdown
## Debug CLI

The `notionify-cli` command (and `python -m notionify.cli`) wraps the SDK for debugging:

```bash
# Convert markdown to Notion blocks JSON (no API call):
notionify-cli convert doc.md

# Create a new page:
NOTION_TOKEN=secret_xxx \
  notionify-cli push doc.md --parent <parent_id> --title "Doc"

# Incremental sync:
notionify-cli sync doc.md --page <page_id>

# Show the diff plan without applying:
notionify-cli diff doc.md --page <page_id>

# Pull a page back to markdown:
notionify-cli pull <page_id> --out out.md

# Inspect a page's raw JSON:
notionify-cli inspect <page_id> --children --json
```

Configure with env (`NOTION_TOKEN`, `NOTION_DEFAULT_PARENT`) or
`~/.notionify.toml`:

```toml
[default]
token = "secret_xxx"
default_parent = "abc123..."
```

Use `-c PATH` for a non-default config file and `--profile NAME` to pick a section.
````

- [ ] **Step 2: Update CHANGELOG**

Prepend to `CHANGELOG.md`:

```markdown
## [Unreleased]

### Added
- `notionify-cli` debug CLI with `push`, `sync`, `pull`, `convert`, `inspect`,
  and `diff` subcommands.
- `NotionifyClient.plan_page_update()` for computing diff plans without
  executing them.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document notionify-cli debug command"
```

---

## Task 14: End-to-end smoke + full suite verification

**Files:**
- (no source changes)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -q`
Expected: all green; new CLI tests + existing 2900+ all pass.

- [ ] **Step 2: Run lint and type checks**

Run: `uv run ruff check src/notionify/cli tests/unit/cli`
Run: `uv run mypy src/notionify/cli`
Expected: clean. Fix any reported issues inline.

- [ ] **Step 3: Coverage check for new code**

Run: `uv run pytest --cov=notionify.cli --cov-branch --cov-report=term-missing tests/unit/cli`
Expected: ≥80% line + branch coverage on `notionify.cli.*`.

- [ ] **Step 4: Manual smoke (no commit)**

```bash
echo "# Hi" > /tmp/sample.md
uv run notionify-cli convert /tmp/sample.md
uv run python -m notionify.cli convert /tmp/sample.md
uv run notionify-cli --help
```

Expected: JSON blocks output, both invocations work, help text lists all 6 subcommands.

- [ ] **Step 5: Final commit if any cleanup happened**

```bash
git status
# If any uncommitted polish, commit it:
# git add -p && git commit -m "chore(cli): polish based on lint/coverage feedback"
```

---

## Notes for the Implementer

**SDK contract (verified against current source):**

- `NotionifyClient(token, **kwargs)` forwards every kwarg to `NotionifyConfig`. Pass `remote_image_upload=...` and `image_base_dir=...` directly. **Never** pass a `config=` kwarg — `NotionifyConfig` has no such field. (`src/notionify/client.py:93`)
- `PageCreateResult` fields: `page_id`, `url`, `blocks_created`, `images_uploaded`, `warnings`. (`src/notionify/models.py:240`)
- `UpdateResult` fields: `strategy_used`, `blocks_kept`, `blocks_inserted`, `blocks_deleted`, `blocks_replaced`, `images_uploaded`, `warnings`. (`src/notionify/models.py:284`)
- `ConversionResult.images` (NOT `pending_images`) is the list of `PendingImage`s awaiting upload. (`src/notionify/models.py:148`)
- `DiffOp` exposes `op_type` (`DiffOpType` enum), `existing_id`, `new_block`, `position_after`, `depth`. (`src/notionify/models.py:157`)

**Image base directory:** `push` and `sync` always pass `image_base_dir=str(Path(args.file).resolve().parent)` so relative image paths in the markdown resolve relative to the markdown file, not the CWD. Without this, `![](pic.png)` next to the markdown file fails when the user runs the CLI from a different directory. (`src/notionify/client.py:790` reads this from `NotionifyConfig.image_base_dir`.)

**Inspect uses private SDK access on purpose.** `inspect.py` reaches into `client._pages` / `client._blocks` to dump raw JSON — that's the whole point of the command. Document with a comment in the file: this command is a debug-only escape hatch, not a normal SDK consumer. If/when the SDK adds public accessors, swap to them.

**Global flags use the parent-parser pattern.** Every subcommand's `add_parser` accepts a `global_parser: argparse.ArgumentParser` and passes `parents=[global_parser]`. This makes `notionify-cli inspect <id> --json` work the same as `notionify-cli --json inspect <id>`. Without this pattern, argparse rejects subcommand-level `--json` / `--token` / `-v` as unknown args.

**`HOME` env in tests:** tests use `monkeypatch.setenv("HOME", ...)`. `os.path.expanduser("~/.notionify.toml")` honors `HOME` on POSIX. Project CI is Linux/macOS; Windows is out of scope.

**Strict mypy:** `pyproject.toml` enables `[tool.mypy] strict = true`. Every function — including `main()`, command `run()` functions, and helpers — needs full type annotations. The plan's snippets include them; do not omit them when implementing.
