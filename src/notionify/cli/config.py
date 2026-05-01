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
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib


class ConfigError(RuntimeError):
    """Raised when CLI configuration cannot be resolved."""


@dataclass(frozen=True)
class CLIConfig:
    token: str
    default_parent: str | None


def load_config(args: argparse.Namespace) -> CLIConfig:
    """Resolve token and default parent from CLI args, TOML config, and env."""
    profile = _profile(args)
    explicit_config = _optional_str(getattr(args, "config_path", None))
    explicit_section: dict[str, Any] | None = None
    home_section: dict[str, Any] | None = None

    home_path = Path(os.path.expanduser("~/.notionify.toml"))
    if home_path.exists():
        home_data = _read_toml(home_path)
        if profile in home_data:
            home_section = _profile_section(home_data, profile, home_path)

    if explicit_config:
        explicit_path = Path(explicit_config).expanduser()
        if not explicit_path.exists():
            raise ConfigError(f"config file not found: {explicit_path}")
        explicit_section = _profile_section(_read_toml(explicit_path), profile, explicit_path)

    flag_token = _optional_str(getattr(args, "token", None))
    explicit_token = _section_str(explicit_section, "token")
    if explicit_section is not None and not (flag_token or explicit_token):
        raise ConfigError(
            f"No Notion token found in explicit config profile {profile!r}. "
            "Pass --token or add token to the selected config profile."
        )
    token = flag_token or explicit_token or os.environ.get("NOTION_TOKEN") or _section_str(
        home_section,
        "token",
    )
    if not token:
        raise ConfigError(
            "No Notion token found. Set NOTION_TOKEN, pass --token, "
            "or configure ~/.notionify.toml."
        )

    default_parent = (
        _section_str(explicit_section, "default_parent")
        or _section_str(home_section, "default_parent")
        or os.environ.get("NOTION_DEFAULT_PARENT")
    )
    return CLIConfig(token=token, default_parent=default_parent)


def _profile(args: argparse.Namespace) -> str:
    return _optional_str(getattr(args, "profile", None)) or "default"


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        data = tomllib.load(file)
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain TOML tables: {path}")
    return data


def _profile_section(data: dict[str, Any], profile: str, path: Path) -> dict[str, Any]:
    if profile not in data:
        raise ConfigError(f"profile {profile!r} not found in {path}")

    section = data[profile]
    if not isinstance(section, dict):
        raise ConfigError(f"profile {profile!r} in {path} is not a TOML table")
    return section


def _section_str(section: dict[str, Any] | None, key: str) -> str | None:
    if section is None:
        return None
    return _optional_str(section.get(key))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"expected string value, got {type(value).__name__}")
    return value or None
