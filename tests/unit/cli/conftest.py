from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def md_file(tmp_path: Path) -> Path:
    path = tmp_path / "doc.md"
    path.write_text("# Title\n\nBody\n", encoding="utf-8")
    return path


@pytest.fixture
def env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret-token")


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client._pages.retrieve.return_value = {"id": "page-1"}
    client._blocks.get_children.return_value = []

    for module_name in (
        "notionify.cli.commands.inspect",
        "notionify.cli.commands.pull",
        "notionify.cli.commands.push",
        "notionify.cli.commands.sync",
    ):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        monkeypatch.setattr(module, "NotionifyClient", MagicMock(return_value=client))

    return client
