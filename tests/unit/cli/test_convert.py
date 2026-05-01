from __future__ import annotations

import json
from pathlib import Path

from notionify.cli.main import main


def test_convert_to_stdout(md_file: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main(["convert", str(md_file)])
    out = capsys.readouterr().out

    assert rc == 0
    payload = json.loads(out)
    assert any(block.get("type") == "heading_1" for block in payload)


def test_convert_to_file(md_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "blocks.json"

    rc = main(["convert", str(md_file), "--out", str(target)])

    assert rc == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert any(block.get("type") == "heading_1" for block in payload)


def test_convert_strip_images(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "img.md"
    path.write_text("Hello ![a](x.png)\n", encoding="utf-8")

    rc = main(["convert", str(path), "--no-images"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert not any(block.get("type") == "image" for block in payload)
