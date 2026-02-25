# Quickstart

## Installation

```bash
pip install notionify
```

## Prerequisites

1. Create a [Notion integration](https://www.notion.so/my-integrations) and copy the **Internal Integration Token**.
2. Share a Notion page with your integration (click "..." on the page, then "Add connections").
3. Copy the page ID from the page URL (the 32-character hex string after the page title).

## Basic Usage

### Create a Page

```python
from notionify import NotionifyClient

client = NotionifyClient(token="secret_xxx")

result = client.create_page_with_markdown(
    parent_id="<your_page_id>",
    title="My First Page",
    markdown="# Hello\n\nThis is a **test** with *italic* and `code`.",
)
print(f"Created: {result.page_id}")
print(f"URL: {result.url}")
print(f"Blocks: {result.blocks_created}")
```

### Export a Page to Markdown

```python
md = client.page_to_markdown(result.page_id)
print(md)
```

### Update a Page (Diff Strategy)

```python
result = client.update_page_from_markdown(
    page_id="<page_id>",
    markdown="# Updated\n\nNew content here.",
    strategy="diff",  # Only changes are sent to the API
)
print(f"Kept: {result.blocks_kept}, Inserted: {result.blocks_inserted}")
```

### Append Content

```python
result = client.append_markdown(
    target_id="<page_id>",
    markdown="## New Section\n\nAppended content.",
)
print(f"Appended: {result.blocks_appended} blocks")
```

## Async Usage

```python
import asyncio
from notionify import AsyncNotionifyClient

async def main():
    async with AsyncNotionifyClient(token="secret_xxx") as client:
        result = await client.create_page_with_markdown(
            parent_id="<page_id>",
            title="Async Page",
            markdown="# Async\n\nCreated asynchronously.",
        )
        print(result.page_id)

asyncio.run(main())
```

## Context Manager

Both clients support context managers for automatic resource cleanup:

```python
with NotionifyClient(token="secret_xxx") as client:
    result = client.create_page_with_markdown(
        parent_id="<page_id>",
        title="My Page",
        markdown="# Hello",
    )
# Transport is automatically closed
```

## Configuration

All configuration is passed as keyword arguments to the client constructor:

```python
client = NotionifyClient(
    token="secret_xxx",
    math_strategy="equation",       # "equation", "code", or "latex_text"
    image_upload=True,               # Enable local image uploads
    image_fallback="placeholder",    # "skip", "placeholder", or "raise"
    retry_max_attempts=5,            # Max retries for transient errors
    rate_limit_rps=3.0,             # Client-side rate limiting
)
```

See the [API Reference](api_reference.md) for all configuration options.

## Error Handling

```python
from notionify import (
    NotionifyClient,
    NotionifyAuthError,
    NotionifyRetryExhaustedError,
)

try:
    client = NotionifyClient(token="secret_xxx")
    result = client.create_page_with_markdown(
        parent_id="<page_id>",
        title="My Page",
        markdown="# Hello",
    )
except NotionifyAuthError:
    print("Invalid token — check your integration settings")
except NotionifyRetryExhaustedError as e:
    print(f"API unavailable after {e.context['attempts']} attempts")
```

See the [Error Cookbook](error_cookbook.md) for more patterns.

## Next Steps

- [API Reference](api_reference.md) — Full method signatures and configuration
- [Conversion Matrix](conversion_matrix.md) — What Markdown/Notion constructs are supported
- [Error Cookbook](error_cookbook.md) — Handling errors and edge cases
- [FAQ](faq.md) — Common questions and answers
