"""Top-level parser and dispatch for ``notionify-cli``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from notionify.cli._common import InvalidIdError
from notionify.cli.commands import convert as cmd_convert
from notionify.cli.commands import diff as cmd_diff
from notionify.cli.commands import inspect as cmd_inspect
from notionify.cli.commands import pull as cmd_pull
from notionify.cli.commands import push as cmd_push
from notionify.cli.commands import sync as cmd_sync
from notionify.cli.config import CLIConfig, ConfigError, load_config
from notionify.cli.output import Reporter
from notionify.errors import (
    NotionifyAuthError,
    NotionifyConversionError,
    NotionifyError,
    NotionifyNetworkError,
    NotionifyRetryExhaustedError,
)

_NO_CONFIG_COMMANDS: set[str] = {"convert"}


def build_global_parser() -> argparse.ArgumentParser:
    """Build a reusable parent parser for global options."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--token",
        default=argparse.SUPPRESS,
        help="Notion API token. Overrides config files and NOTION_TOKEN.",
    )
    parser.add_argument(
        "-c",
        "--config",
        dest="config_path",
        default=argparse.SUPPRESS,
        help="Path to a notionify TOML config file.",
    )
    parser.add_argument(
        "--profile",
        default=argparse.SUPPRESS,
        help="Profile name in the config file.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=argparse.SUPPRESS,
        help="Increase verbosity. Use -vv for detailed diagnostics.",
    )
    parser.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit machine-readable JSON output.",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    global_parser = build_global_parser()
    parser = argparse.ArgumentParser(
        prog="notionify-cli",
        description="Debug CLI for the notionify SDK.",
        parents=[global_parser],
    )
    parser.set_defaults(command=None, _command=None)

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    cmd_convert.add_parser(subparsers, global_parser)
    cmd_inspect.add_parser(subparsers, global_parser)
    cmd_pull.add_parser(subparsers, global_parser)
    cmd_push.add_parser(subparsers, global_parser)
    cmd_sync.add_parser(subparsers, global_parser)
    cmd_diff.add_parser(subparsers, global_parser)

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    return _normalise_global_defaults(parser.parse_args(raw))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    args = _normalise_global_defaults(parser.parse_args(raw))

    if args.command is None:
        parser.print_help()
        return 1

    reporter = Reporter(verbosity=args.verbose, json_mode=args.json_mode)
    handler = getattr(args, "_command", None)
    if handler is None:
        return reporter.fail(RuntimeError(f"command not implemented: {args.command}"))

    try:
        if args.command == "push" and getattr(args, "dry_run", False):
            return int(handler(args, reporter, CLIConfig(token="", default_parent=None)))
        if args.command in _NO_CONFIG_COMMANDS:
            return int(handler(args, reporter))
        config = load_config(args)
        return int(handler(args, reporter, config))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return reporter.fail(exc, exit_code=_classify(exc))


def _normalise_global_defaults(args: argparse.Namespace) -> argparse.Namespace:
    if not hasattr(args, "token"):
        args.token = None
    if not hasattr(args, "config_path"):
        args.config_path = None
    if not hasattr(args, "profile"):
        args.profile = "default"
    if not hasattr(args, "verbose"):
        args.verbose = 0
    if not hasattr(args, "json_mode"):
        args.json_mode = False
    return args


def _classify(err: BaseException) -> int:
    if isinstance(err, ConfigError):
        return 2
    if isinstance(err, NotionifyConversionError):
        return 4
    if isinstance(
        err,
        (
            NotionifyAuthError,
            NotionifyNetworkError,
            NotionifyRetryExhaustedError,
            NotionifyError,
        ),
    ):
        return 3
    if isinstance(err, (InvalidIdError, FileNotFoundError, ValueError)):
        return 1
    return 1
