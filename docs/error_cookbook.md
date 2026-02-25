# Error Handling Cookbook

## Catching All SDK Errors

Every error raised by notionify inherits from `NotionifyError`:

```python
from notionify import NotionifyClient, NotionifyError

try:
    client = NotionifyClient(token="secret_xxx")
    result = client.create_page_with_markdown(
        parent_id="<page_id>",
        title="My Page",
        markdown="# Hello",
    )
except NotionifyError as e:
    print(f"[{e.code}] {e.message}")
    print(f"Context: {e.context}")
    if e.cause:
        print(f"Caused by: {e.cause}")
```

## Handling Rate Limits

The SDK automatically retries on HTTP 429 responses, respecting the `Retry-After` header. If all retries are exhausted, `NotionifyRetryExhaustedError` is raised:

```python
from notionify import NotionifyClient, NotionifyRetryExhaustedError

try:
    client = NotionifyClient(
        token="secret_xxx",
        retry_max_attempts=5,
        retry_base_delay=1.0,
        retry_max_delay=60.0,
    )
    result = client.create_page_with_markdown(
        parent_id="<page_id>",
        title="My Page",
        markdown="# Hello",
    )
except NotionifyRetryExhaustedError as e:
    print(f"Gave up after {e.context['attempts']} attempts")
    print(f"Last status: {e.context.get('last_status_code')}")
```

## Handling Authentication Errors

```python
from notionify import NotionifyClient, NotionifyAuthError

try:
    client = NotionifyClient(token="secret_xxx")
    result = client.create_page_with_markdown(
        parent_id="<page_id>",
        title="Test",
        markdown="# Test",
    )
except NotionifyAuthError as e:
    # Only the last 4 chars of the token are included for diagnostics
    print(f"Auth failed: {e.message}")
    print(f"Token hint: {e.context.get('token_prefix')}")
```

## Handling Image Failures

### Skip Failed Images (Default)

```python
client = NotionifyClient(token="secret_xxx", image_fallback="skip")
result = client.create_page_with_markdown(
    parent_id="<page_id>",
    title="My Page",
    markdown="![photo](missing.png)\n\nSome text.",
)
# The image block is silently omitted; text blocks are still created
```

### Placeholder for Failed Images

```python
client = NotionifyClient(token="secret_xxx", image_fallback="placeholder")
result = client.create_page_with_markdown(
    parent_id="<page_id>",
    title="My Page",
    markdown="![photo](missing.png)\n\nSome text.",
)
# Failed images become "[image: missing.png]" text blocks
```

### Raise on Image Failure

```python
from notionify import (
    NotionifyClient,
    NotionifyImageNotFoundError,
    NotionifyImageTypeError,
    NotionifyImageSizeError,
)

client = NotionifyClient(token="secret_xxx", image_fallback="raise")

try:
    result = client.create_page_with_markdown(
        parent_id="<page_id>",
        title="My Page",
        markdown="![photo](missing.png)",
    )
except NotionifyImageNotFoundError as e:
    print(f"File not found: {e.context.get('src')}")
except NotionifyImageTypeError as e:
    print(f"Bad MIME: {e.context.get('detected_mime')}")
    print(f"Allowed: {e.context.get('allowed_mimes')}")
except NotionifyImageSizeError as e:
    print(f"Too large: {e.context.get('size_bytes')} bytes")
    print(f"Max: {e.context.get('max_bytes')} bytes")
```

## Handling Unsupported Blocks on Export

When exporting a Notion page, some block types have no Markdown equivalent:

```python
# Option 1: HTML comments (default)
client = NotionifyClient(
    token="secret_xxx",
    unsupported_block_policy="comment",
)
md = client.page_to_markdown("<page_id>")
# Unsupported blocks render as: <!-- notion-block: <type> -->

# Option 2: Skip silently
client = NotionifyClient(
    token="secret_xxx",
    unsupported_block_policy="skip",
)
md = client.page_to_markdown("<page_id>")

# Option 3: Raise an error
from notionify import NotionifyUnsupportedBlockError

client = NotionifyClient(
    token="secret_xxx",
    unsupported_block_policy="raise",
)

try:
    md = client.page_to_markdown("<page_id>")
except NotionifyUnsupportedBlockError as e:
    print(f"Block type: {e.context.get('block_type')}")
    print(f"Block ID: {e.context.get('block_id')}")
```

## Handling Diff Conflicts

When using the diff update strategy, the SDK detects concurrent page modifications:

```python
from notionify import NotionifyClient, NotionifyDiffConflictError

client = NotionifyClient(token="secret_xxx")

# Option 1: Raise on conflict (default)
try:
    result = client.update_page_from_markdown(
        page_id="<page_id>",
        markdown="# Updated content",
        strategy="diff",
        on_conflict="raise",
    )
except NotionifyDiffConflictError as e:
    print(f"Page modified: {e.context.get('page_id')}")
    print(f"Snapshot: {e.context.get('snapshot_time')}")
    print(f"Current: {e.context.get('detected_time')}")
    # Retry with overwrite as fallback
    result = client.update_page_from_markdown(
        page_id="<page_id>",
        markdown="# Updated content",
        strategy="overwrite",
    )

# Option 2: Auto-fallback to overwrite on conflict
result = client.update_page_from_markdown(
    page_id="<page_id>",
    markdown="# Updated content",
    strategy="diff",
    on_conflict="overwrite",
)
```

## Handling Math Overflow

Math expressions exceeding 1000 characters are handled according to the overflow config:

```python
client = NotionifyClient(
    token="secret_xxx",
    math_strategy="equation",
    math_overflow_block="code",   # Fall back to code block
    math_overflow_inline="text",  # Fall back to plain text
)

result = client.create_page_with_markdown(
    parent_id="<page_id>",
    title="Math Doc",
    markdown="$$very_long_expression...$$",
)
# Check warnings for overflow notifications
for w in result.warnings:
    if w.code == "MATH_OVERFLOW":
        print(f"Math overflow: {w.message}")
```

## Handling Network Errors

```python
from notionify import NotionifyClient, NotionifyNetworkError

try:
    client = NotionifyClient(token="secret_xxx", timeout_seconds=10.0)
    result = client.create_page_with_markdown(
        parent_id="<page_id>",
        title="Test",
        markdown="# Hello",
    )
except NotionifyNetworkError as e:
    print(f"Network issue: {e.message}")
    print(f"URL: {e.context.get('url')}")
```

## Inspecting Warnings

All result objects carry a `warnings` list with non-fatal issues:

```python
result = client.create_page_with_markdown(
    parent_id="<page_id>",
    title="My Page",
    markdown=content,
)

for w in result.warnings:
    print(f"[{w.code}] {w.message}")
    if w.context:
        print(f"  Details: {w.context}")
```
