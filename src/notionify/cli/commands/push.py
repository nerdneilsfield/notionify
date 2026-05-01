"""``notionify-cli push``: Markdown to a new Notion page."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from notionify import NotionifyClient
from notionify.cli._common import parse_id, read_markdown, strip_images
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter
from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter


def add_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    global_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "push",
        help="Create a new Notion page from markdown.",
        parents=[global_parser],
    )
    parser.add_argument("file", help="Path to a markdown file.")
    parser.add_argument("--parent", help="Parent page/database ID or URL.")
    parser.add_argument("--parent-type", choices=("page", "database"), default="page")
    parser.add_argument("--title", help="Page title. Defaults to filename stem.")
    parser.add_argument("--upload-remote-images", action="store_true")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Convert only; do not call Notion.")
    parser.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    parent_raw = args.parent or config.default_parent
    if not parent_raw:
        return reporter.fail(
            ValueError("no --parent given and NOTION_DEFAULT_PARENT is unset"),
            exit_code=2,
        )
    parent_id = parse_id(parent_raw)
    markdown = read_markdown(args.file)
    if args.no_images:
        markdown = strip_images(markdown)
    if args.dry_run:
        return _dry_run(markdown, reporter)

    with NotionifyClient(
        token=config.token,
        remote_image_upload=args.upload_remote_images,
        image_base_dir=str(Path(args.file).resolve().parent),
    ) as client:
        result = client.create_page_with_markdown(
            parent_id=parent_id,
            title=args.title or Path(args.file).stem,
            markdown=markdown,
            parent_type=args.parent_type,
        )
    reporter.result(
        {
            "page_id": result.page_id,
            "url": result.url,
            "blocks_created": result.blocks_created,
            "images_uploaded": result.images_uploaded,
            "warnings": [str(w) for w in result.warnings],
        }
    )
    return 0


def _dry_run(markdown: str, reporter: Reporter) -> int:
    conversion = MarkdownToNotionConverter(NotionifyConfig(token="dummy")).convert(markdown)
    reporter.result(
        {
            "blocks": len(conversion.blocks),
            "outline_first_20": [block.get("type", "?") for block in conversion.blocks[:20]],
            "pending_images": len(conversion.images),
            "warnings": [str(w) for w in conversion.warnings],
        }
    )
    reporter.detail(json.dumps(conversion.blocks, ensure_ascii=False, indent=2))
    return 0
