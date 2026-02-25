# Frequently Asked Questions

## General

### Why do my Notion images expire?

Notion-hosted file URLs have a TTL (typically 1 hour). When exporting pages, set `image_expiry_warnings=True` (the default) to get markers in the output indicating which image URLs may expire.

If you need persistent image URLs, re-upload images to your own storage before using the exported URLs.

### Can I use a database as a parent?

Yes. Pass `parent_type="database"` and optionally provide a `properties` dict matching your database schema:

```python
result = client.create_page_with_markdown(
    parent_id="<database_id>",
    title="New Entry",
    markdown="# Content",
    parent_type="database",
    properties={
        "Status": {"select": {"name": "Draft"}},
    },
)
```

### Does round-trip preserve everything?

Supported constructs (headings, paragraphs, lists, code, tables, math, images) are preserved through MD -> Notion -> MD conversion. Some information is lossy:

- H4/H5/H6 headings are downgraded to H3
- Notion colors and underline have no standard Markdown equivalent
- Column layouts are flattened
- Callout icons are merged into text

See the [Conversion Matrix](conversion_matrix.md) for the full compatibility table.

### What happens to H4, H5, and H6 headings?

Notion only supports heading levels 1-3. By default, H4/H5/H6 are downgraded to `heading_3`. Set `heading_overflow="paragraph"` to render them as bold paragraphs instead:

```python
client = NotionifyClient(
    token="secret_xxx",
    heading_overflow="paragraph",  # or "downgrade" (default)
)
```

### Is there a maximum page size?

The Notion API limits individual `append_children` requests to 100 blocks. The SDK handles this automatically by batching blocks into groups of 100. There is no hard limit on total page size, but very large pages may be slow to create or export.

## Async Usage

### How do I use the async client?

```python
import asyncio
from notionify import AsyncNotionifyClient

async def main():
    async with AsyncNotionifyClient(token="secret_xxx") as client:
        result = await client.create_page_with_markdown(
            parent_id="<page_id>",
            title="Async Page",
            markdown="# Hello from async",
        )
        print(result.page_id)

asyncio.run(main())
```

All methods on `AsyncNotionifyClient` mirror `NotionifyClient` but are `async` and must be `await`ed.

### What is `image_max_concurrent` for?

The `image_max_concurrent` config (default: 4) limits how many images the async client uploads in parallel. This prevents overwhelming the Notion API when a document contains many images. The sync client processes images sequentially.

## Images

### What image formats are supported?

**For uploads (local files, data URIs):** JPEG, PNG, GIF, WebP, SVG (configurable via `image_allowed_mimes_upload`).

**For external URLs:** JPEG, PNG, GIF, WebP, SVG, BMP, TIFF (configurable via `image_allowed_mimes_external`).

### How large can uploaded images be?

The default limit is 5 MiB (`image_max_size_bytes=5242880`). You can adjust this in the config. Images larger than the configured limit raise `NotionifyImageSizeError` (or are handled per `image_fallback`).

### How does path traversal protection work?

Set `image_base_dir` to restrict where local image files can be loaded from:

```python
client = NotionifyClient(
    token="secret_xxx",
    image_base_dir="/path/to/allowed/dir",
)
```

Any image path that resolves outside this directory is rejected, preventing `../../etc/passwd` style attacks when processing untrusted Markdown.

## Security

### Is the token stored anywhere?

No. The token is only used in `Authorization` headers and is never logged. The `NotionifyConfig.__repr__()` masks the token (showing only the last 4 characters). The `redact()` utility strips tokens from debug dumps.

### Can I plug in my own metrics?

Yes. Implement the `MetricsHook` protocol and pass it to the constructor:

```python
class MyMetrics:
    def increment(self, name: str, value: int = 1, tags: dict | None = None):
        # Send to your metrics system
        ...

    def timing(self, name: str, value_ms: float, tags: dict | None = None):
        # Send to your metrics system
        ...

client = NotionifyClient(token="secret_xxx", metrics=MyMetrics())
```

## Diff Engine

### How does the diff strategy work?

When you call `update_page_from_markdown(strategy="diff")`, the SDK:

1. Fetches the current page blocks
2. Converts your new Markdown to blocks
3. Computes block signatures (type, content hash, structure hash)
4. Runs LCS (Longest Common Subsequence) matching to find unchanged blocks
5. Generates a minimal operation plan (KEEP, UPDATE, INSERT, DELETE, REPLACE)
6. Executes only the necessary API calls

This minimizes API calls and preserves block IDs for unchanged content.

### What is conflict detection?

Before executing diff operations, the SDK takes a snapshot of the page and re-checks it before applying changes. If the page was modified by another user or process between these two checks, a `NotionifyDiffConflictError` is raised (or the SDK falls back to overwrite if `on_conflict="overwrite"`).

## Troubleshooting

### I'm getting rate limited

The SDK includes client-side pacing (default: 3 requests/second) and automatic retry with exponential backoff. If you still hit rate limits:

1. Lower `rate_limit_rps` (e.g., `1.0`)
2. Increase `retry_max_attempts` (e.g., `10`)
3. Consider batching operations across time

### My math expressions are not rendering as equations

Ensure `math_strategy="equation"` (the default). If your math contains `$...$` inline or `$$...$$` blocks, the SDK parses these via the mistune math plugin and converts them to Notion equation objects.

### Debug output

Enable debug dumps to inspect the conversion pipeline:

```python
client = NotionifyClient(
    token="secret_xxx",
    debug_dump_ast=True,      # Print normalised AST to stderr
    debug_dump_payload=True,  # Print redacted API payload to stderr
    debug_dump_diff=True,     # Print diff operation plan to stderr
)
```
