from __future__ import annotations

import json

from notionify.cli.main import main


def test_inspect_page_only(env_token, fake_client, capsys) -> None:  # type: ignore[no-untyped-def]
    fake_client._pages.retrieve.return_value = {"id": "abc", "object": "page"}

    rc = main(["inspect", "12345678-1234-1234-1234-123456789abc", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["page"]["id"] == "abc"
    assert "children" not in payload


def test_inspect_with_children(env_token, fake_client, capsys) -> None:  # type: ignore[no-untyped-def]
    fake_client._pages.retrieve.return_value = {"id": "abc"}
    fake_client._blocks.get_children.return_value = [{"id": "b1", "type": "paragraph"}]

    rc = main(
        ["inspect", "12345678-1234-1234-1234-123456789abc", "--children", "--json"]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["children"] == [{"id": "b1", "type": "paragraph"}]


def test_inspect_accepts_global_flags_before_subcommand(
    monkeypatch,
    fake_client,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    fake_client._pages.retrieve.return_value = {"id": "abc", "object": "page"}

    rc = main(
        [
            "--token",
            "secret-from-flag",
            "--json",
            "inspect",
            "12345678-1234-1234-1234-123456789abc",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["page"]["id"] == "abc"
