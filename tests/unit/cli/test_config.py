from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from notionify.cli.config import ConfigError, load_config


def _args(
    *,
    token: str | None = None,
    config_path: str | None = None,
    profile: str = "default",
) -> argparse.Namespace:
    return argparse.Namespace(token=token, config_path=config_path, profile=profile)


def test_load_config_prefers_flag_token_over_file_and_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "notionify.toml"
    config.write_text("[default]\ntoken = \"file-token\"\n", encoding="utf-8")
    monkeypatch.setenv("NOTION_TOKEN", "env-token")

    loaded = load_config(_args(token="flag-token", config_path=str(config)))

    assert loaded.token == "flag-token"


def test_load_config_default_parent_prefers_explicit_file_over_home_and_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".notionify.toml").write_text(
        "[default]\ntoken = \"home-token\"\ndefault_parent = \"home-parent\"\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "explicit.toml"
    explicit.write_text(
        "[default]\ntoken = \"file-token\"\ndefault_parent = \"file-parent\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NOTION_DEFAULT_PARENT", "env-parent")

    loaded = load_config(_args(config_path=str(explicit)))

    assert loaded.token == "file-token"
    assert loaded.default_parent == "file-parent"


def test_load_config_with_explicit_file_falls_back_to_home_then_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".notionify.toml").write_text(
        "[default]\ntoken = \"home-token\"\ndefault_parent = \"home-parent\"\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[default]\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NOTION_TOKEN", "env-token")
    monkeypatch.setenv("NOTION_DEFAULT_PARENT", "env-parent")

    loaded = load_config(_args(config_path=str(explicit)))

    assert loaded.token == "env-token"
    assert loaded.default_parent == "home-parent"


def test_load_config_uses_env_token_when_files_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NOTION_TOKEN", "env-token")

    loaded = load_config(_args())

    assert loaded.token == "env-token"


def test_load_config_missing_token_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NOTION_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="token"):
        load_config(_args())
