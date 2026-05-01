"""``notionify-cli diff``: sync dry-run alias."""

from __future__ import annotations

import argparse

from notionify.cli.commands.sync import run as sync_run
from notionify.cli.config import CLIConfig
from notionify.cli.output import Reporter


def add_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    global_parser: argparse.ArgumentParser,
) -> None:
    parser = subparsers.add_parser(
        "diff",
        help="Show the sync diff plan without applying it.",
        parents=[global_parser],
    )
    parser.add_argument("file", help="Path to a markdown file.")
    parser.add_argument("--page", required=True, help="Page ID or Notion URL.")
    parser.add_argument("--no-images", action="store_true")
    parser.set_defaults(_command=run, dry_run=True, upload_remote_images=False)


def run(args: argparse.Namespace, reporter: Reporter, config: CLIConfig) -> int:
    args.dry_run = True
    args.upload_remote_images = False
    return sync_run(args, reporter, config)
