from __future__ import annotations

import json
from pathlib import Path

from notionify.cli.main import main
from notionify.models import PageCreateResult


def _stub_create_result(page_id: str = "page-xyz") -> PageCreateResult:
    return PageCreateResult(
        page_id=page_id,
        url=f"https://notion.so/{page_id}",
        blocks_created=1,
        images_uploaded=0,
        warnings=[],
    )


def test_push_creates_page(env_token, fake_client, md_file: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    fake_client.create_page_with_markdown.return_value = _stub_create_result()

    rc = main(
        [
            "push",
            str(md_file),
            "--parent",
            "12345678-1234-1234-1234-123456789abc",
            "--title",
            "Doc",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["page_id"] == "page-xyz"
    _, kwargs = fake_client.create_page_with_markdown.call_args
    assert kwargs["title"] == "Doc"
    assert kwargs["markdown"].startswith("# Title")


def test_push_uses_default_parent_env(
    monkeypatch,
    fake_client,
    md_file: Path,
) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("NOTION_DEFAULT_PARENT", "12345678-1234-1234-1234-123456789abc")
    fake_client.create_page_with_markdown.return_value = _stub_create_result()

    rc = main(["push", str(md_file), "--title", "Doc"])

    assert rc == 0


def test_push_dry_run_does_not_call_create(
    env_token,
    fake_client,
    md_file: Path,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    rc = main(
        [
            "push",
            str(md_file),
            "--parent",
            "12345678-1234-1234-1234-123456789abc",
            "--dry-run",
        ]
    )

    assert rc == 0
    fake_client.create_page_with_markdown.assert_not_called()
    assert "blocks" in capsys.readouterr().out.lower()


def test_push_dry_run_does_not_require_token_or_parent(
    monkeypatch,
    fake_client,
    md_file: Path,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DEFAULT_PARENT", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")

    rc = main(["push", str(md_file), "--dry-run"])

    assert rc == 0
    fake_client.create_page_with_markdown.assert_not_called()
    assert "blocks" in capsys.readouterr().out.lower()
