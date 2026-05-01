from __future__ import annotations

import pytest

from notionify.cli.main import main


def test_no_command_prints_help(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main([])

    assert rc == 1
    assert "usage:" in capsys.readouterr().out


def test_global_flags_before_subcommand(md_file, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main(["--json", "-v", "convert", str(md_file)])

    assert rc == 0
    assert capsys.readouterr().out.startswith("[")


def test_global_flags_after_subcommand(md_file, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main(["convert", str(md_file), "--json", "-v"])

    assert rc == 0
    assert capsys.readouterr().out.startswith("[")


def test_unknown_command_exits() -> None:
    with pytest.raises(SystemExit):
        main(["missing"])
