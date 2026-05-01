"""``notionify-cli convert``: Markdown to Notion blocks JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from notionify.cli._common import read_markdown, strip_images
from notionify.cli.output import Reporter
from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter

_LOCAL_CONVERSION_PLACEHOLDER = "notionify-local-conversion"


def add_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    global_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "convert",
        help="Convert markdown to Notion blocks JSON without API calls.",
        parents=[global_parser],
    )
    parser.add_argument("file", help="Path to a markdown file.")
    parser.add_argument("--out", help="Output file. Defaults to stdout.")
    parser.add_argument("--no-images", action="store_true", help="Strip image syntax first.")
    parser.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter) -> int:
    markdown = read_markdown(args.file)
    if args.no_images:
        markdown = strip_images(markdown)

    reporter.step(f"converting {args.file}")
    config = NotionifyConfig(token=_LOCAL_CONVERSION_PLACEHOLDER)
    conversion = MarkdownToNotionConverter(config).convert(markdown)
    blocks_json = json.dumps(conversion.blocks, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(f"{blocks_json}\n", encoding="utf-8")
        reporter.result({"output": args.out, "blocks": len(conversion.blocks)})
    else:
        reporter.write_raw(blocks_json)
    return 0
