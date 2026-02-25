# API Reference

## NotionifyClient

Synchronous client for the notionify SDK.

### Constructor

```python
NotionifyClient(token: str, **kwargs)
```

Creates a new synchronous client. The `token` parameter is required. All additional keyword arguments are forwarded to `NotionifyConfig`.

**Example:**

```python
from notionify import NotionifyClient

client = NotionifyClient(
    token="secret_xxx",
    math_strategy="equation",
    image_upload=True,
)
```

### Methods

#### `create_page_with_markdown`

```python
def create_page_with_markdown(
    self,
    parent_id: str,
    title: str,
    markdown: str,
    parent_type: str = "page",
    properties: dict | None = None,
    title_from_h1: bool = False,
) -> PageCreateResult
```

Create a new Notion page from Markdown content.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `parent_id` | `str` | required | ID of the parent page or database |
| `title` | `str` | required | Page title |
| `markdown` | `str` | required | Raw Markdown text to convert |
| `parent_type` | `str` | `"page"` | `"page"` or `"database"` |
| `properties` | `dict \| None` | `None` | Extra page properties (merged with title) |
| `title_from_h1` | `bool` | `False` | Extract title from first H1 heading |

**Returns:** `PageCreateResult`

**Raises:** `ValueError` if `parent_type` is not `"page"` or `"database"`.

---

#### `append_markdown`

```python
def append_markdown(
    self,
    target_id: str,
    markdown: str,
    target_type: str = "page",
) -> AppendResult
```

Append Markdown content to a page or after a block.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target_id` | `str` | required | ID of the page or block to append to |
| `markdown` | `str` | required | Raw Markdown text to convert and append |
| `target_type` | `str` | `"page"` | `"page"` or `"block"` |

**Returns:** `AppendResult`

---

#### `overwrite_page_content`

```python
def overwrite_page_content(
    self,
    page_id: str,
    markdown: str,
) -> UpdateResult
```

Full overwrite: archive all existing children, write new blocks.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page_id` | `str` | required | The Notion page ID |
| `markdown` | `str` | required | New Markdown content |

**Returns:** `UpdateResult`

---

#### `update_page_from_markdown`

```python
def update_page_from_markdown(
    self,
    page_id: str,
    markdown: str,
    strategy: str = "diff",
    on_conflict: str = "raise",
) -> UpdateResult
```

Update page with diff or overwrite strategy. The diff strategy computes the minimal set of API calls needed.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page_id` | `str` | required | The Notion page ID to update |
| `markdown` | `str` | required | The desired Markdown content |
| `strategy` | `str` | `"diff"` | `"diff"` or `"overwrite"` |
| `on_conflict` | `str` | `"raise"` | `"raise"` or `"overwrite"` |

**Returns:** `UpdateResult`

**Raises:**
- `ValueError` if `strategy` or `on_conflict` is invalid
- `NotionifyDiffConflictError` if the page was modified concurrently and `on_conflict="raise"`

---

#### `update_block`

```python
def update_block(
    self,
    block_id: str,
    markdown_fragment: str,
) -> BlockUpdateResult
```

Update a single block with a markdown fragment.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `block_id` | `str` | required | The UUID of the block to update |
| `markdown_fragment` | `str` | required | Markdown text for the block's new content |

**Returns:** `BlockUpdateResult`

---

#### `delete_block`

```python
def delete_block(
    self,
    block_id: str,
    archive: bool = True,
) -> None
```

Delete (archive) a block.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `block_id` | `str` | required | The UUID of the block to delete |
| `archive` | `bool` | `True` | Archive rather than permanently delete |

---

#### `insert_after`

```python
def insert_after(
    self,
    block_id: str,
    markdown_fragment: str,
) -> InsertResult
```

Insert new blocks after a given block.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `block_id` | `str` | required | The UUID of the block after which to insert |
| `markdown_fragment` | `str` | required | Markdown text to convert and insert |

**Returns:** `InsertResult`

---

#### `page_to_markdown`

```python
def page_to_markdown(
    self,
    page_id: str,
    recursive: bool = False,
    max_depth: int | None = None,
) -> str
```

Export a Notion page to Markdown.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page_id` | `str` | required | The Notion page ID |
| `recursive` | `bool` | `False` | Recursively fetch children of child blocks |
| `max_depth` | `int \| None` | `None` | Maximum recursion depth (`None` = unlimited) |

**Returns:** `str` — The rendered Markdown text.

---

#### `block_to_markdown`

```python
def block_to_markdown(
    self,
    block_id: str,
    recursive: bool = True,
    max_depth: int | None = 3,
) -> str
```

Export a block subtree to Markdown.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `block_id` | `str` | required | The UUID of the root block |
| `recursive` | `bool` | `True` | Recursively fetch children |
| `max_depth` | `int \| None` | `3` | Maximum recursion depth |

**Returns:** `str` — The rendered Markdown text.

---

#### `close`

```python
def close(self) -> None
```

Close the HTTP transport. Also called automatically when using the client as a context manager.

---

## AsyncNotionifyClient

Async counterpart of `NotionifyClient`. All methods have identical signatures but are `async` and must be `await`ed.

```python
from notionify import AsyncNotionifyClient

async with AsyncNotionifyClient(token="secret_xxx") as client:
    result = await client.create_page_with_markdown(...)
    md = await client.page_to_markdown(result.page_id)
```

All methods listed above for `NotionifyClient` are available with `async`/`await` semantics.

---

## NotionifyConfig

All configuration is passed as keyword arguments to the client constructor.

### Core

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `token` | `str` | `""` | Notion integration token. **Required.** Never logged. |
| `notion_version` | `str` | `"2025-09-03"` | Notion-Version header value |
| `base_url` | `str` | `"https://api.notion.com/v1"` | API root URL |

### Math

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `math_strategy` | `"equation" \| "code" \| "latex_text"` | `"equation"` | How to convert LaTeX math to Notion blocks |
| `math_overflow_inline` | `"split" \| "code" \| "text"` | `"code"` | Fallback when inline equation exceeds 1000 chars |
| `math_overflow_block` | `"split" \| "code" \| "text"` | `"code"` | Fallback when block equation exceeds 1000 chars |
| `detect_latex_code` | `bool` | `True` | Treat `language="latex"` code blocks as math on export |

### Images

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `image_upload` | `bool` | `True` | Enable upload pipeline for local/data-URI images |
| `image_max_concurrent` | `int` | `4` | Max parallel upload tasks (async only) |
| `image_fallback` | `"skip" \| "placeholder" \| "raise"` | `"skip"` | Behaviour when image cannot be processed |
| `image_expiry_warnings` | `bool` | `True` | Annotate Notion-hosted URLs with expiry warning |
| `image_allowed_mimes_upload` | `list[str]` | `DEFAULT_UPLOAD_MIMES` | MIME types for uploads |
| `image_allowed_mimes_external` | `list[str]` | `DEFAULT_EXTERNAL_MIMES` | MIME types for external URLs |
| `image_max_size_bytes` | `int` | `5242880` (5 MiB) | Max upload file size |
| `image_verify_external` | `bool` | `False` | HEAD-check external image URLs |
| `image_base_dir` | `str \| None` | `None` | Base directory for local images (path traversal protection) |

### Tables

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_tables` | `bool` | `True` | Convert Markdown tables to Notion table blocks |
| `table_fallback` | `"paragraph" \| "comment" \| "raise"` | `"comment"` | Fallback when tables are disabled or fail |

### Headings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `heading_overflow` | `"downgrade" \| "paragraph"` | `"downgrade"` | How to handle H4/H5/H6 (Notion supports H1-H3 only) |

### Export

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `unsupported_block_policy` | `"comment" \| "skip" \| "raise"` | `"comment"` | How to render unsupported Notion blocks on export |

### Retry & Rate Limiting

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `retry_max_attempts` | `int` | `5` | Max retries per request |
| `retry_base_delay` | `float` | `1.0` | Base delay (seconds) for exponential backoff |
| `retry_max_delay` | `float` | `60.0` | Upper cap (seconds) on backoff delay |
| `retry_jitter` | `bool` | `True` | Add random jitter to backoff |
| `rate_limit_rps` | `float` | `3.0` | Target requests per second (token bucket) |

### HTTP

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timeout_seconds` | `float` | `30.0` | HTTP request timeout |
| `http_proxy` | `str \| None` | `None` | HTTP/HTTPS proxy URL |

### Observability

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `metrics` | `MetricsHook \| None` | `None` | Custom metrics hook implementation |

### Debug

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `debug_dump_ast` | `bool` | `False` | Write normalised AST to stderr |
| `debug_dump_payload` | `bool` | `False` | Write redacted API payload to stderr |
| `debug_dump_diff` | `bool` | `False` | Write diff operation plan to stderr |

---

## Result Types

### PageCreateResult

Returned by `create_page_with_markdown`.

| Field | Type | Description |
|-------|------|-------------|
| `page_id` | `str` | ID of the created page |
| `url` | `str` | URL of the created page |
| `blocks_created` | `int` | Total blocks appended |
| `images_uploaded` | `int` | Images processed through upload pipeline |
| `warnings` | `list[ConversionWarning]` | Non-fatal issues |

### AppendResult

Returned by `append_markdown`.

| Field | Type | Description |
|-------|------|-------------|
| `blocks_appended` | `int` | Number of blocks appended |
| `images_uploaded` | `int` | Number of images uploaded |
| `warnings` | `list[ConversionWarning]` | Non-fatal issues |

### UpdateResult

Returned by `update_page_from_markdown` and `overwrite_page_content`.

| Field | Type | Description |
|-------|------|-------------|
| `strategy_used` | `str` | `"diff"` or `"overwrite"` |
| `blocks_kept` | `int` | Unchanged blocks (diff only) |
| `blocks_inserted` | `int` | New blocks added |
| `blocks_deleted` | `int` | Blocks archived |
| `blocks_replaced` | `int` | Blocks whose type changed |
| `images_uploaded` | `int` | Images uploaded |
| `warnings` | `list[ConversionWarning]` | Non-fatal issues |

### BlockUpdateResult

Returned by `update_block`.

| Field | Type | Description |
|-------|------|-------------|
| `block_id` | `str` | The updated block's ID |
| `warnings` | `list[ConversionWarning]` | Non-fatal issues |

### InsertResult

Returned by `insert_after`.

| Field | Type | Description |
|-------|------|-------------|
| `inserted_block_ids` | `list[str]` | IDs of created blocks, in order |
| `warnings` | `list[ConversionWarning]` | Non-fatal issues |

### ConversionWarning

| Field | Type | Description |
|-------|------|-------------|
| `code` | `str` | Machine-readable warning code |
| `message` | `str` | Human-readable description |
| `context` | `dict` | Structured diagnostic data |

---

## Error Hierarchy

All errors inherit from `NotionifyError`.

```
NotionifyError
├── NotionifyValidationError      (400 — invalid payload)
├── NotionifyAuthError            (401 — bad token)
├── NotionifyPermissionError      (403 — no access)
├── NotionifyNotFoundError        (404 — resource missing)
├── NotionifyRateLimitError       (429 — rate limited)
├── NotionifyRetryExhaustedError  (all retries used)
├── NotionifyNetworkError         (timeout, DNS, connection)
├── NotionifyConversionError
│   ├── NotionifyUnsupportedBlockError
│   ├── NotionifyTextOverflowError
│   └── NotionifyMathOverflowError
├── NotionifyImageError
│   ├── NotionifyImageNotFoundError
│   ├── NotionifyImageTypeError
│   ├── NotionifyImageSizeError
│   └── NotionifyImageParseError
├── NotionifyUploadError
│   ├── NotionifyUploadExpiredError
│   └── NotionifyUploadTransportError
└── NotionifyDiffConflictError
```

Every error carries:

| Attribute | Type | Description |
|-----------|------|-------------|
| `code` | `str` | Value from `ErrorCode` enum |
| `message` | `str` | Human-readable description |
| `context` | `dict` | Structured diagnostic data |
| `cause` | `Exception \| None` | Chained exception |

See the [Error Cookbook](error_cookbook.md) for handling patterns.

---

## Enums

### ImageSourceType

| Value | Description |
|-------|-------------|
| `EXTERNAL_URL` | HTTP/HTTPS URL |
| `LOCAL_FILE` | Local filesystem path |
| `DATA_URI` | Inline `data:` URI |
| `UNKNOWN` | Could not be classified |

### UploadState

| Value | Description |
|-------|-------------|
| `PENDING` | Upload not started |
| `UPLOADING` | Transfer in progress |
| `UPLOADED` | Bytes sent, block not attached |
| `ATTACHED` | Block appended to Notion |
| `FAILED` | Unrecoverable error |
| `EXPIRED` | Attachment window passed |

### DiffOpType

| Value | Description |
|-------|-------------|
| `KEEP` | Block unchanged |
| `UPDATE` | Content changed, same type |
| `REPLACE` | Type changed (archive + insert) |
| `INSERT` | New block |
| `DELETE` | Block to archive |

### ErrorCode

Machine-readable codes for all error types. Values match the error class names (e.g., `ErrorCode.AUTH_ERROR` for `NotionifyAuthError`).

---

## Utility Functions

### `detect_conflict`

```python
def detect_conflict(before: PageSnapshot, after: PageSnapshot) -> bool
```

Compare two page snapshots to detect concurrent modifications.

### `take_snapshot`

```python
def take_snapshot(page_id: str, page: dict, blocks: list[dict]) -> PageSnapshot
```

Create a `PageSnapshot` from a page response and its block children.
