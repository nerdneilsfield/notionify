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
