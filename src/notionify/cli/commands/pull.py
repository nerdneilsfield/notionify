"""``notionify-cli pull``: Notion page to Markdown."""

from __future__ import annotations

import argparse
from pathlib import Path

from notionify import NotionifyClient
from notionify.cli._common import parse_id
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter


def add_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    global_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "pull",
        help="Pull a Notion page as markdown.",
        parents=[global_parser],
    )
    parser.add_argument("page", help="Page ID or Notion URL.")
    parser.add_argument("--out", help="Output file. Defaults to stdout.")
    parser.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    page_id = parse_id(args.page)
    reporter.step(f"pulling page {page_id}")
    with NotionifyClient(token=config.token) as client:
        markdown = client.page_to_markdown(page_id, recursive=True)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        reporter.result({"output": args.out, "bytes": len(markdown.encode("utf-8"))})
    else:
        reporter.write_raw(markdown)
    return 0
