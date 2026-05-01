from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from notionify.cli.main import main
from notionify.models import UpdateResult


def test_sync_executes(env_token, fake_client, md_file: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    fake_client.update_page_from_markdown.return_value = UpdateResult(
        strategy_used="diff",
        blocks_kept=2,
        blocks_inserted=1,
        blocks_deleted=0,
        blocks_replaced=0,
        images_uploaded=0,
        warnings=[],
    )

    rc = main(
        [
            "sync",
            str(md_file),
            "--page",
            "12345678-1234-1234-1234-123456789abc",
            "--json",
        ]
    )

    assert rc == 0
    fake_client.update_page_from_markdown.assert_called_once()
    payload = json.loads(capsys.readouterr().out)
    assert payload["strategy_used"] == "diff"
    assert payload["blocks_inserted"] == 1


def test_sync_dry_run_calls_plan_only(env_token, fake_client, md_file: Path) -> None:
    plan = MagicMock()
    plan.ops = []
    plan.warnings = []
    plan.images_to_upload = 0
    fake_client.plan_page_update.return_value = plan

    rc = main(
        [
            "sync",
            str(md_file),
            "--page",
            "12345678-1234-1234-1234-123456789abc",
            "--dry-run",
        ]
    )

    assert rc == 0
    fake_client.plan_page_update.assert_called_once()
    fake_client.update_page_from_markdown.assert_not_called()
