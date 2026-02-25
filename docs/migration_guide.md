# Migration Guide

## From v1.x to v2+

### Constructor Changes

**Before (v1.x):**

```python
from notion_markdown import NotionMarkdownClient

client = NotionMarkdownClient(token="secret_xxx", math="equation")
```

**After (v2+):**

```python
from notionify import NotionifyClient

client = NotionifyClient(token="secret_xxx", math_strategy="equation")
```

### Method Renames

| v1.x Method | v2+ Method | Notes |
|-------------|------------|-------|
| `write_markdown()` | `create_page_with_markdown()` | Returns `PageCreateResult` |
| `update_markdown()` | `update_page_from_markdown()` | Returns `UpdateResult` |
| `read_markdown()` | `page_to_markdown()` | Returns `str` |
| N/A | `append_markdown()` | New in v2 |
| N/A | `overwrite_page_content()` | New in v2 |
| N/A | `update_block()` | New in v2 |
| N/A | `delete_block()` | New in v2 |
| N/A | `insert_after()` | New in v2 |
| N/A | `block_to_markdown()` | New in v2 |

### Configuration Changes

| v1.x Parameter | v2+ Parameter | Notes |
|----------------|---------------|-------|
| `math` | `math_strategy` | Same values: `"equation"`, `"code"`, `"latex_text"` |
| N/A | `math_overflow_inline` | New: controls overflow fallback |
| N/A | `math_overflow_block` | New: controls overflow fallback |
| N/A | `image_fallback` | New: `"skip"`, `"placeholder"`, `"raise"` |
| N/A | `retry_max_attempts` | New: built-in retry |
| N/A | `rate_limit_rps` | New: client-side pacing |
| N/A | `unsupported_block_policy` | New: export control |

### Behavior Changes

#### Images

**v1.x:** Images that failed to process were silently skipped with no indication.

**v2+:** Configurable via `image_fallback`:
- `"skip"` (default) — silently omit, same as v1 behavior
- `"placeholder"` — insert a text block `[image: <src>]`
- `"raise"` — raise a specific `NotionifyImageError` subclass

#### Math Overflow

**v1.x:** Equations exceeding the 1000-character limit raised an untyped error.

**v2+:** Configurable via `math_overflow_block` and `math_overflow_inline`:
- `"split"` — split across multiple equation blocks/objects
- `"code"` (default) — fall back to a code block
- `"text"` — render as plain text with `$`/`$$` delimiters

#### Retry and Rate Limiting

**v1.x:** No built-in retry. Users needed manual retry wrappers.

**v2+:** Automatic retry with exponential backoff and jitter. Respects `Retry-After` headers. Client-side token bucket pacing at `rate_limit_rps` (default 3.0) requests per second.

#### Error Types

**v1.x:** Generic `Exception` or `ValueError` for most errors.

**v2+:** Full typed error hierarchy. All errors carry `code`, `message`, `context`, and optional `cause`. See the [Error Cookbook](error_cookbook.md).

#### Async Support

**v1.x:** Sync-only.

**v2+:** Full async client with identical API:

```python
from notionify import AsyncNotionifyClient

async with AsyncNotionifyClient(token="secret_xxx") as client:
    result = await client.create_page_with_markdown(
        parent_id="<page_id>",
        title="My Page",
        markdown="# Hello",
    )
```

### Migration Checklist

1. Update import: `from notionify import NotionifyClient`
2. Rename `math=` to `math_strategy=` in constructor
3. Rename method calls per the table above
4. Update error handling to use typed exceptions
5. Remove manual retry wrappers (now built-in)
6. Remove external rate-limiting code (now built-in)
7. Consider using `image_fallback="placeholder"` for better diagnostics
8. Update result handling (methods now return typed dataclasses)
