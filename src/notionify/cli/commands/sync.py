"""``notionify-cli sync``: Markdown to existing Notion page."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from notionify import NotionifyClient
from notionify.cli._common import parse_id, read_markdown, strip_images
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter


def add_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    global_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "sync",
        help="Incrementally update an existing page from markdown.",
        parents=[global_parser],
    )
    parser.add_argument("file", help="Path to a markdown file.")
    parser.add_argument("--page", required=True, help="Page ID or Notion URL.")
    parser.add_argument("--upload-remote-images", action="store_true")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not update Notion.")
    parser.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    page_id = parse_id(args.page)
    markdown = read_markdown(args.file)
    if args.no_images:
        markdown = strip_images(markdown)

    with NotionifyClient(
        token=config.token,
        remote_image_upload=args.upload_remote_images,
        image_base_dir=str(Path(args.file).resolve().parent),
    ) as client:
        if args.dry_run:
            plan = client.plan_page_update(page_id, markdown)
            counts = Counter(op.op_type.value for op in plan.ops)
            reporter.result(
                {
                    "page_id": page_id,
                    "total_ops": len(plan.ops),
                    "by_op": dict(counts),
                    "images_to_upload": plan.images_to_upload,
                    "warnings": [str(w) for w in plan.warnings],
                }
            )
            for op in plan.ops:
                reporter.detail(
                    {
                        "op": op.op_type.value,
                        "existing_id": op.existing_id,
                        "depth": op.depth,
                    }
                )
            return 0

        result = client.update_page_from_markdown(page_id, markdown)

    reporter.result(
        {
            "page_id": page_id,
            "strategy_used": result.strategy_used,
            "blocks_kept": result.blocks_kept,
            "blocks_inserted": result.blocks_inserted,
            "blocks_deleted": result.blocks_deleted,
            "blocks_replaced": result.blocks_replaced,
            "images_uploaded": result.images_uploaded,
            "warnings": [str(w) for w in result.warnings],
        }
    )
    return 0
