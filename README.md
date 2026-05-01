# notionify

High-fidelity bidirectional Markdown ↔ Notion conversion and synchronization SDK for Python 3.10+.

[![CI](https://github.com/notionify/notionify/actions/workflows/ci.yml/badge.svg)](https://github.com/notionify/notionify/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Features

- **Markdown → Notion** — Parse Markdown and create Notion pages with headings, lists, code blocks, tables, math equations, images, and more
- **Notion → Markdown** — Export Notion pages back to clean Markdown
- **Diff sync** — Update pages incrementally with minimal API calls using LCS-based diffing
- **Image pipeline** — Automatic upload of local files, data URIs, and external URLs via the Notion file upload API
- **Async support** — Full async client for high-throughput workflows
- **Typed errors** — Every failure has a specific error class with actionable context
- **Retry & rate limiting** — Built-in exponential backoff, jitter, and rate-limit compliance

## Installation

```bash
pip install notionify
```

## Quick Start

### Create a page from Markdown

```python
from notionify import NotionifyClient

with NotionifyClient(token="ntn_xxxxx") as client:
    result = client.create_page_with_markdown(
        parent_id="<page-or-database-id>",
        title="Meeting Notes",
        markdown="# Agenda\n\n- Review Q4 goals\n- **Action items**\n\n```python\nprint('hello')\n```",
    )
    print(result.page_id)
```

### Append content to an existing page

```python
client.append_markdown(
    target_id="<page-id>",
    markdown="## New Section\n\nAppended content with *formatting*.",
)
```

### Update a page with diff sync

Only the changed blocks are updated — unchanged content is left untouched:

```python
result = client.update_page_from_markdown(
    page_id="<page-id>",
    markdown=updated_markdown,
    strategy="diff",       # default; use "overwrite" to replace everything
)
print(f"Kept {result.blocks_kept}, inserted {result.blocks_inserted}, "
      f"deleted {result.blocks_deleted}")
```

### Export a Notion page to Markdown

```python
markdown = client.page_to_markdown("<page-id>", recursive=True)
print(markdown)
```

### Async client

```python
import asyncio
from notionify import AsyncNotionifyClient

async def main():
    async with AsyncNotionifyClient(token="ntn_xxxxx") as client:
        result = await client.create_page_with_markdown(
            parent_id="<page-id>",
            title="Async Page",
            markdown="# Created asynchronously\n\nWith full image support.",
        )
        print(result.page_id)

asyncio.run(main())
```

## Debug CLI

The `notionify-cli` command wraps the SDK for local conversion, Notion page
push/pull/sync flows, raw API inspection, and diff dry-runs. It is also
available as `python -m notionify.cli`.

### CLI quickstart

```bash
# Convert Markdown to Notion blocks JSON without an API call
notionify-cli convert doc.md

# Preview a new page conversion without token, parent, or API calls
notionify-cli push doc.md --dry-run

# Create a new page under a page or database
NOTION_TOKEN=secret_xxx \
  notionify-cli push doc.md --parent <parent_id> --title "Doc"

# Incrementally sync an existing page from Markdown
notionify-cli sync doc.md --page <page_id>

# Show the diff plan without applying it
notionify-cli diff doc.md --page <page_id>

# Pull a page back to markdown
notionify-cli pull <page_id> --out out.md

# Inspect a page's raw JSON
notionify-cli inspect <page_id> --children --json
```

Global flags can be placed before or after the subcommand:

```bash
notionify-cli --json --profile work inspect <page_id> --children
notionify-cli inspect <page_id> --children --json --profile work
```

Useful global flags:

- `--token TOKEN` — Notion integration token
- `-c, --config PATH` — use a specific TOML config file
- `--profile NAME` — select a TOML section, defaults to `default`
- `-v` / `-vv` — show progress or detailed diagnostics
- `--json` — emit machine-readable result/error payloads

### CLI configuration

For one-off commands, use environment variables:

```bash
export NOTION_TOKEN="secret_xxx"
export NOTION_DEFAULT_PARENT="<page-or-database-id>"

notionify-cli push doc.md --title "Doc"
```

For repeated use, create `~/.notionify.toml`:

```toml
[default]
token = "secret_xxx"
default_parent = "12345678-1234-1234-1234-123456789abc"

[work]
token = "secret_work_xxx"
default_parent = "abcdefab-cdef-abcd-efab-cdefabcdefab"

[personal]
token = "secret_personal_xxx"
default_parent = "11111111-2222-3333-4444-555555555555"
```

Then select profiles with `--profile`:

```bash
notionify-cli --profile work push docs/release.md --title "Release Notes"
notionify-cli --profile personal pull <page_id> --out notes.md
```

Use `-c PATH` for a project-local config file. The repository includes
[examples/notionify.test.toml](examples/notionify.test.toml) as a safe
template with placeholder tokens:

```bash
notionify-cli -c examples/notionify.test.toml --profile staging \
  sync doc.md --page <page_id>
```

Token precedence is:

1. `--token`
2. `-c PATH` selected profile
3. `NOTION_TOKEN`
4. `~/.notionify.toml` selected profile, only when `-c` is not used

`default_parent` precedence is:

1. `-c PATH` selected profile
2. `~/.notionify.toml` selected profile, only when `-c` is not used
3. `NOTION_DEFAULT_PARENT`

When `-c PATH` is provided, notionify treats that file as explicit and does
not fall back to `~/.notionify.toml`.

### CLI command reference

```bash
# convert: Markdown -> Notion block JSON, no token required
notionify-cli convert doc.md --out blocks.json
notionify-cli convert doc.md --no-images

# push: create a new Notion page
notionify-cli push doc.md --parent <parent_id> --title "Doc"
notionify-cli push doc.md --parent <database_id> --parent-type database
notionify-cli push doc.md --upload-remote-images
notionify-cli push doc.md --dry-run --json

# sync: update an existing page
notionify-cli sync doc.md --page <page_id>
notionify-cli sync doc.md --page <page_id> --dry-run --json

# diff: alias for sync --dry-run
notionify-cli diff doc.md --page <page_id>

# pull: export Notion page Markdown
notionify-cli pull <page_id>
notionify-cli pull <page_id> --out out.md
notionify-cli pull <page_id> --json

# inspect: dump raw page JSON for debugging
notionify-cli inspect <page_id> --children --json
```

## Configuration

All options are passed as keyword arguments to the client constructor:

```python
client = NotionifyClient(
    token="ntn_xxxxx",
    math_strategy="equation",           # "equation" | "code" | "latex_text"
    image_upload=True,                   # auto-upload local/data-URI images
    image_fallback="skip",              # "skip" | "placeholder" | "raise"
    image_base_dir="/safe/path",        # restrict local image reads to this dir
    enable_tables=True,                  # convert Markdown tables to Notion tables
    retry_max_attempts=5,               # max retries on transient failures
    rate_limit_rps=3.0,                 # requests per second limit
    timeout_seconds=30.0,               # per-request timeout
)
```

See the [API Reference](docs/api_reference.md) for the full list of options.

## Error Handling

All errors inherit from `NotionifyError` and carry structured context:

```python
from notionify import NotionifyClient, NotionifyRateLimitError, NotionifyError

with NotionifyClient(token="ntn_xxxxx") as client:
    try:
        client.create_page_with_markdown(
            parent_id="<page-id>",
            title="Test",
            markdown="# Hello",
        )
    except NotionifyRateLimitError as e:
        print(f"Rate limited, retry after: {e.context}")
    except NotionifyError as e:
        print(f"[{e.code}] {e.message}")
```

## Documentation

- [Quickstart](docs/quickstart.md) — Installation, first page, async usage
- [API Reference](docs/api_reference.md) — Full method signatures, config, result types
- [Conversion Matrix](docs/conversion_matrix.md) — Markdown/Notion compatibility table
- [Error Cookbook](docs/error_cookbook.md) — Handling rate limits, images, conflicts
- [Migration Guide](docs/migration_guide.md) — Upgrading from v1.x/v2.x
- [FAQ](docs/faq.md) — Common questions and answers

## Development

```bash
# Clone and install dev dependencies
git clone https://github.com/notionify/notionify.git
cd notionify
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint and type-check
ruff check src/ tests/
mypy src/notionify  # strict mode via pyproject.toml

# Performance benchmarks
pytest tests/perf/ -v -s
```

## License

[MIT](LICENSE)
