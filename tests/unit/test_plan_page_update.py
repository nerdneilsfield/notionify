from __future__ import annotations

from unittest.mock import MagicMock

from notionify import models
from notionify.client import NotionifyClient
from notionify.models import (
    ConversionResult,
    ConversionWarning,
    DiffOp,
    DiffOpType,
    ImageSourceType,
    PendingImage,
)


def test_plan_result_is_public_model() -> None:
    assert hasattr(models, "PlanResult")


def test_plan_page_update_returns_ops_without_executing() -> None:
    client = NotionifyClient(token="test-token")
    client._blocks.get_children = MagicMock(return_value=[{"id": "old", "type": "paragraph"}])
    client._converter.convert = MagicMock(
        return_value=ConversionResult(
            blocks=[{"type": "paragraph", "paragraph": {"rich_text": []}}],
            warnings=[ConversionWarning(code="W", message="warn")],
            images=[],
        )
    )
    client._diff_planner.plan = MagicMock(
        return_value=[DiffOp(op_type=DiffOpType.INSERT, new_block={"type": "paragraph"})]
    )
    client._diff_executor.execute = MagicMock()
    client._process_images = MagicMock(return_value=99)

    plan = client.plan_page_update("page-1", "Hello")

    assert isinstance(plan, models.PlanResult)
    assert len(plan.ops) == 1
    assert plan.warnings[0].code == "W"
    assert plan.images_to_upload == 0
    client._blocks.get_children.assert_called_once_with("page-1")
    client._diff_planner.plan.assert_called_once()
    client._diff_executor.execute.assert_not_called()
    client._process_images.assert_not_called()


def test_plan_page_update_counts_pending_images_without_uploading() -> None:
    client = NotionifyClient(token="test-token")
    client._blocks.get_children = MagicMock(return_value=[])
    client._converter.convert = MagicMock(
        return_value=ConversionResult(
            blocks=[{"type": "image"}],
            images=[
                PendingImage(
                    src="image.png",
                    source_type=ImageSourceType.LOCAL_FILE,
                    block_index=0,
                )
            ],
        )
    )
    client._diff_planner.plan = MagicMock(return_value=[])

    plan = client.plan_page_update("page-1", "![alt](image.png)")

    assert plan.images_to_upload == 1
