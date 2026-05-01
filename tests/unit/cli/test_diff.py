from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from notionify.cli.main import main


def test_diff_calls_plan_and_does_not_execute(env_token, fake_client, md_file: Path) -> None:
    plan = MagicMock()
    plan.ops = []
    plan.warnings = []
    plan.images_to_upload = 0
    fake_client.plan_page_update.return_value = plan

    rc = main(
        [
            "diff",
            str(md_file),
            "--page",
            "12345678-1234-1234-1234-123456789abc",
        ]
    )

    assert rc == 0
    fake_client.plan_page_update.assert_called_once()
    fake_client.update_page_from_markdown.assert_not_called()
