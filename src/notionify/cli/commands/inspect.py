"""``notionify-cli inspect``: raw page JSON."""

from __future__ import annotations

import argparse
from typing import Any

from notionify import NotionifyClient
from notionify.cli._common import parse_id
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter


def add_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    global_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "inspect",
        help="Fetch a page's raw JSON and optionally its first-level children.",
        parents=[global_parser],
    )
    parser.add_argument("page", help="Page ID or Notion URL.")
    parser.add_argument("--children", action="store_true", help="Fetch first-level children.")
    parser.set_defaults(_command=run)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    page_id = parse_id(args.page)
    reporter.step(f"fetching page {page_id}")
    with NotionifyClient(token=config.token) as client:
        # Debug-only escape hatch: the SDK does not expose raw page payloads.
        page = client._pages.retrieve(page_id)
        payload: dict[str, Any] = {"page": page}
        if args.children:
            payload["children"] = list(client._blocks.get_children(page_id))
    reporter.result(payload)
    return 0
