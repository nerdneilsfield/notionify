from __future__ import annotations

from pathlib import Path

from notionify.cli.main import main


def test_pull_to_stdout(env_token, fake_client, capsys) -> None:  # type: ignore[no-untyped-def]
    fake_client.page_to_markdown.return_value = "# Pulled\n\nbody\n"

    rc = main(["pull", "12345678-1234-1234-1234-123456789abc"])

    assert rc == 0
    assert "# Pulled" in capsys.readouterr().out
    fake_client.page_to_markdown.assert_called_once_with(
        "12345678-1234-1234-1234-123456789abc",
        recursive=True,
    )


def test_pull_to_file(env_token, fake_client, tmp_path: Path) -> None:
    fake_client.page_to_markdown.return_value = "stuff\n"
    out_path = tmp_path / "out.md"

    rc = main(
        ["pull", "12345678-1234-1234-1234-123456789abc", "--out", str(out_path)]
    )

    assert rc == 0
    assert out_path.read_text(encoding="utf-8") == "stuff\n"
