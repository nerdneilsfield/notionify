# notionify — Full Engineering Specification (v3.0)

**Project:** `notionify`
**Type:** Python SDK (sync + async)
**Language:** Python 3.10+
**Purpose:** High-fidelity bidirectional Markdown ↔ Notion conversion and synchronization
**Status:** Implementation-Ready (Full Scope)
**Doc Version:** 3.0.0
**Last Updated:** 2025-07

---

## Table of Contents

```
 0. Executive Summary
 1. Objectives and Success Criteria
 2. Users and Use Cases
 3. Scope (In / Out)
 4. Terminology Glossary
 5. External Constraints
 6. Functional Requirements (full)
 7. Non-Functional Requirements (full)
 8. Public SDK API Contract (full typed)
 9. Configuration Reference (every field)
10. Markdown → Notion Conversion Spec
11. Notion → Markdown Rendering Spec
12. Diff Engine Design
13. Image Upload Architecture
14. Notion API Endpoint Mapping
15. Error Taxonomy (full code enum + context schema)
16. Reliability & Rate-Limit Design
17. Observability
18. Security & Privacy
19. Package Architecture
20. Full Test Matrix
21. Documentation Deliverables
22. Full Jira Breakdown (Epic → Story → Subtask)
23. Complete Python Interface Definitions
24. Development Schedule (by module × person-days)
25. Risks and Mitigations
26. Open Decisions
27. Final Acceptance Checklist
```

---

## 0. Executive Summary

`notionify` is a production-grade Python SDK for bidirectional Markdown ↔ Notion conversion and page synchronization.

**Core value proposition:**
- Write, update, and export Notion pages with confidence.
- Support for math (equation/code/text strategies), nested structures, tables, and media.
- Reliable by default: auto-retry, rate-limit compliance, chunked uploads, typed errors.
- Async-first where performance matters; sync API for simplicity.
- Designed for AI agent workflows, CI pipelines, doc migrations, and knowledge automation.

**Architecture philosophy:**
*Parse once → convert deterministically → execute safely → report clearly.*

---

## 1. Objectives and Success Criteria

### 1.1 Primary Objectives

| # | Objective |
|---|-----------|
| O1 | Convert Markdown to Notion blocks accurately and completely |
| O2 | Sync incremental changes with minimal API calls (diff strategy) |
| O3 | Export Notion page trees to Markdown with deterministic fallback |
| O4 | Handle all image types (URL, local, data URI) with upload lifecycle |
| O5 | Hide Notion API complexity (limits, pagination, retries) behind stable SDK |
| O6 | Provide typed errors with actionable developer messages |
| O7 | Support async for high-throughput workflows |

### 1.2 Release Gate Criteria

| Metric | Threshold |
|--------|-----------|
| P0 test pass rate | 100% |
| Round-trip semantic fidelity (supported blocks) | ≥ 95% |
| Critical regressions on golden fixtures | 0 |
| 429 handling compliance | 100% (Retry-After respected) |
| Upload success rate (valid files, integration) | > 99% |
| Max latency for 100-block page create (sync) | < 10s |
| Typed public API surface coverage | 100% |

---

## 2. Users and Use Cases

### 2.1 Primary Users

| User Type | Description |
|-----------|-------------|
| Backend developer | Syncing documentation from code repos to Notion |
| DevOps / CI | Automating knowledge base updates in pipelines |
| AI agent | Generating and updating Notion content dynamically |
| Data migration | Moving Markdown/MDX repos into Notion |
| Internal tooling | Building Notion-backed content systems |

### 2.2 Detailed Use Cases

**UC-01 — Markdown to New Page**
Write a complete Markdown document as a new Notion page under a given parent.

**UC-02 — Append Fragment**
Add Markdown content to the bottom of an existing page or under a specific block.

**UC-03 — Full Overwrite**
Replace all content of an existing page with new Markdown (archive old, write new).

**UC-04 — Diff Update**
Sync only changed blocks (keep unchanged ones, insert new ones, delete removed ones).

**UC-05 — Block Patch**
Update a specific known block (by ID) with new content.

**UC-06 — Block Insert / Delete**
Insert new blocks after a specific block, or delete a block.

**UC-07 — Page Export**
Export a Notion page (and optionally its sub-pages) to Markdown text.

**UC-08 — Block Export**
Export a specific block subtree to Markdown.

**UC-09 — Image Migration**
Convert Markdown with local images into Notion pages with uploaded media.

**UC-10 — Math Doc Migration**
Convert Markdown with LaTeX math into Notion equation blocks.

---

## 3. Scope

### 3.1 In Scope (v3.0)

- Markdown → Notion: create, append, overwrite, diff
- Notion → Markdown: page export, block export, recursive
- Block-level mutation: update, delete, insert_after
- Equation: inline + block + three strategies + overflow fallback
- Images: external URL, local file, data URI, single-part upload, multi-part upload
- Async API variants for all high-traffic methods
- Rate limit: token bucket + retry-after + exponential backoff
- Pagination: automatic for block children and database queries
- Typed errors with machine-readable codes
- Configurable Notion API version header
- Observability: structured logging + optional metrics hooks

### 3.2 Out of Scope

| Feature | Reason |
|---------|--------|
| OAuth authorization flow | Out of SDK responsibility |
| Notion database schema create/alter | Separate concern |
| Comments / discussion threads | Not in API scope |
| Full WYSIWYG fidelity for Notion-only layouts (columns, synced blocks) | Lossy by nature |
| Docx / PDF / HTML import | Out of scope |
| Real-time collaboration / webhooks | Future phase |

---

## 4. Terminology Glossary

| Term | Definition |
|------|------------|
| AST | Abstract Syntax Tree produced by Mistune v3 parser |
| Block | A single Notion content unit (paragraph, heading, image, etc.) |
| Rich text | Array of Notion text segments with annotations |
| Round-trip | Markdown → Notion → Markdown, expected to preserve semantics |
| Lossy conversion | Semantic degradation due to Notion/Markdown capability mismatch |
| Fallback policy | Deterministic behavior when a construct is unsupported or overflows |
| Diff strategy | Produce minimal set of block operations vs full overwrite |
| Signature | Structural fingerprint of a block used for diff matching |
| Upload lifecycle | `pending → uploading → uploaded → attached` state machine |
| Chunk | A batch of ≤ 100 children for a single append call |
| Overwrite mode | Archive all existing children, write new blocks from scratch |
| Canonical token | The definitive Mistune AST token name (e.g., `block_code` not `code`) |

---

## 5. External Constraints

### 5.1 Notion API Hard Limits

| Constraint | Value | Enforcement |
|------------|-------|-------------|
| `rich_text[].text.content` length | 2000 chars | Split before sending |
| `equation.expression` length | 1000 chars | Split or fallback |
| `append_block_children` batch size | 100 blocks | Auto-chunk |
| Nested depth practical limit | ~8 levels | Guard + warning |
| Rate limit | ~3 req/s average | Token bucket |
| File upload size limit | Plan-dependent | Validate pre-upload |

### 5.2 Mistune v3 Parser Contract

```python
mistune.create_markdown(
    renderer="ast",
    plugins=[
        "strikethrough",
        "table",
        "task_lists",
        "url",
        "math",
        "footnotes",
    ]
)
```

**Canonical token types (authoritative list):**

```
Block-level:
  heading, paragraph, block_quote, list, list_item,
  task_list_item, block_code, table, thematic_break,
  block_math, html_block

Inline-level:
  text, strong, emphasis, codespan, strikethrough,
  link, image, inline_math, softline, hardline, html
```

**Normalizer rule:** All internal conversion logic must use only canonical token names. An `ASTNormalizer` class handles variant-to-canonical mapping.

---

## 6. Functional Requirements

### FR-1 Page Creation

| ID | Requirement |
|----|-------------|
| FR-1.1 | Create a page from Markdown under a page parent |
| FR-1.2 | Create a page from Markdown under a database parent (with optional properties) |
| FR-1.3 | Set page title from argument or first H1 in Markdown (configurable) |
| FR-1.4 | Auto-chunk children into batches of ≤ 100 |
| FR-1.5 | Enforce rich_text and equation limits before sending any request |
| FR-1.6 | Return created page ID and metadata on success |

### FR-2 Append

| ID | Requirement |
|----|-------------|
| FR-2.1 | Append Markdown to a page (append after last child) |
| FR-2.2 | Append Markdown after a specific block ID |
| FR-2.3 | Support appending nested block structures |
| FR-2.4 | Auto-chunk append calls |

### FR-3 Update

| ID | Requirement |
|----|-------------|
| FR-3.1 | Overwrite: archive all existing children, write new blocks |
| FR-3.2 | Diff: compute minimal operations and apply |
| FR-3.3 | Update a single block by ID with a Markdown fragment |
| FR-3.4 | Block type mismatch in diff: replace (archive + insert), not patch |
| FR-3.5 | Delete a block by ID (archive by default, configurable) |
| FR-3.6 | Insert new blocks after a given block ID |

### FR-4 Export (Notion → Markdown)

| ID | Requirement |
|----|-------------|
| FR-4.1 | Export a full page to Markdown string |
| FR-4.2 | Export a specific block subtree to Markdown string |
| FR-4.3 | Support recursive export with configurable `max_depth` |
| FR-4.4 | Handle paginated block children transparently |
| FR-4.5 | Preserve inline annotations (bold, italic, code, links) |
| FR-4.6 | Render unsupported blocks per `unsupported_block_policy` |
| FR-4.7 | Optionally annotate Notion-hosted image URLs with expiry warning |

### FR-5 Math

| ID | Requirement |
|----|-------------|
| FR-5.1 | `equation` strategy: use Notion equation block/inline |
| FR-5.2 | `code` strategy: store as code block with `language="latex"` |
| FR-5.3 | `latex_text` strategy: preserve as plain text with delimiters |
| FR-5.4 | Overflow (>1000): configurable fallback per strategy |
| FR-5.5 | On read-back: equation block → `$$...$$`, inline → `$...$` |
| FR-5.6 | `detect_latex_code=True`: heuristic detect `latex` code blocks and render as math |

### FR-6 Images

| ID | Requirement |
|----|-------------|
| FR-6.1 | Detect source type: URL, local path, data URI |
| FR-6.2 | External URL: embed as `image.external` |
| FR-6.3 | Local path: validate, upload, attach |
| FR-6.4 | Data URI: decode, validate, upload, attach |
| FR-6.5 | Single-part upload for small files |
| FR-6.6 | Multi-part upload for large files |
| FR-6.7 | State machine tracking per upload |
| FR-6.8 | Attach must happen promptly after upload |
| FR-6.9 | Handle expiry during attach gracefully |
| FR-6.10 | Validate MIME and size before upload |
| FR-6.11 | Separate allowlists for URL acceptance vs file-upload MIME |
| FR-6.12 | Fallback on image failure: `skip`, `placeholder`, `raise` |

### FR-7 Async

| ID | Requirement |
|----|-------------|
| FR-7.1 | All page/block write operations have async variants |
| FR-7.2 | All export operations have async variants |
| FR-7.3 | Async image uploads are concurrent and bounded |
| FR-7.4 | Shared rate limiter across all async tasks |

---

## 7. Non-Functional Requirements

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-1 | Python version compatibility | 3.10+ |
| NFR-2 | Type annotations | 100% public API |
| NFR-3 | Determinism | Same input + config → same output |
| NFR-4 | No hidden state | All config explicit via constructor |
| NFR-5 | Retry-safe | Upload/attach operations idempotent |
| NFR-6 | Token never logged | Hard rule, enforced in review |
| NFR-7 | Thread safety | Sync client safe for concurrent calls |
| NFR-8 | Package size | < 5MB installed |
| NFR-9 | Import time | < 500ms |
| NFR-10 | Zero mandatory side effects | No files written without explicit request |

---

## 8. Public SDK API Contract

### 8.1 Sync Client

```python
from notionify import NotionifyClient

client = NotionifyClient(token="secret_xxx", ...)

# ── Write ──────────────────────────────────────────────
result: PageCreateResult = client.create_page_with_markdown(
    parent_id="<page_or_db_id>",
    title="My Page",
    markdown="# Hello\n\nWorld",
    parent_type="page",          # "page" | "database"
    properties=None,             # Notion DB properties dict
    title_from_h1=False,         # override title with first H1
)

result: AppendResult = client.append_markdown(
    target_id="<page_or_block_id>",
    markdown="## Section\n\nContent",
    target_type="page",          # "page" | "block"
)

result: UpdateResult = client.overwrite_page_content(
    page_id="<page_id>",
    markdown="# New content",
)

result: UpdateResult = client.update_page_from_markdown(
    page_id="<page_id>",
    markdown="# Updated",
    strategy="diff",             # "diff" | "overwrite"
    on_conflict="raise",         # "raise" | "overwrite"
)

result: BlockUpdateResult = client.update_block(
    block_id="<block_id>",
    markdown_fragment="**bold text**",
)

client.delete_block(
    block_id="<block_id>",
    archive=True,                # True = archive, False = hard delete
)

result: InsertResult = client.insert_after(
    block_id="<block_id>",
    markdown_fragment="New paragraph",
)

# ── Read ───────────────────────────────────────────────
md: str = client.page_to_markdown(
    page_id="<page_id>",
    recursive=False,
    max_depth=None,
)

md: str = client.block_to_markdown(
    block_id="<block_id>",
    recursive=True,
    max_depth=3,
)
```

### 8.2 Async Client

```python
from notionify import AsyncNotionifyClient

client = AsyncNotionifyClient(token="secret_xxx", ...)

result = await client.create_page_with_markdown(...)
result = await client.append_markdown(...)
result = await client.overwrite_page_content(...)
result = await client.update_page_from_markdown(...)
result = await client.update_block(...)
await   client.delete_block(...)
result = await client.insert_after(...)
md     = await client.page_to_markdown(...)
md     = await client.block_to_markdown(...)
```

### 8.3 Result Types

```python
@dataclass
class PageCreateResult:
    page_id: str
    url: str
    blocks_created: int
    images_uploaded: int
    warnings: list[ConversionWarning]

@dataclass
class AppendResult:
    blocks_appended: int
    images_uploaded: int
    warnings: list[ConversionWarning]

@dataclass
class UpdateResult:
    strategy_used: str               # "diff" | "overwrite"
    blocks_kept: int
    blocks_inserted: int
    blocks_deleted: int
    blocks_replaced: int
    images_uploaded: int
    warnings: list[ConversionWarning]

@dataclass
class BlockUpdateResult:
    block_id: str
    warnings: list[ConversionWarning]

@dataclass
class InsertResult:
    inserted_block_ids: list[str]
    warnings: list[ConversionWarning]

@dataclass
class ConversionWarning:
    code: str
    message: str
    context: dict
```

---

## 9. Configuration Reference

```python
NotionifyClient(
    # ── Core ────────────────────────────────────────────
    token: str,
    # Notion integration token. Required. Never logged.

    notion_version: str = "2025-09-03",
    # Notion-Version header. Update when Notion API changes.

    base_url: str = "https://api.notion.com/v1",
    # Override for proxy/testing environments.

    # ── Math ────────────────────────────────────────────
    math_strategy: Literal["equation", "code", "latex_text"] = "equation",
    # equation: use Notion equation objects (recommended)
    # code: store as code block (language="latex")
    # latex_text: keep as plain text with delimiters

    math_overflow_inline: Literal["split", "code", "text"] = "code",
    # Inline equation > 1000: split (best-effort), code, or plain text

    math_overflow_block: Literal["split", "code", "text"] = "code",
    # Block equation > 1000: split (best-effort), code, or plain text

    detect_latex_code: bool = True,
    # On read-back: treat code blocks with language="latex" as math

    # ── Images ──────────────────────────────────────────
    image_upload: bool = True,
    # Enable upload pipeline for local/data URI images

    image_max_concurrent: int = 4,
    # Max parallel upload tasks (async client)

    image_fallback: Literal["skip", "placeholder", "raise"] = "skip",
    # skip: silently omit failed images
    # placeholder: insert a text block with [image: path]
    # raise: throw NotionifyImageError

    image_expiry_warnings: bool = True,
    # Add comment annotation on Notion-hosted image URLs warning about expiry

    image_allowed_mimes_upload: list[str] = DEFAULT_UPLOAD_MIMES,
    # MIME allowlist for file-upload images
    # Default: ["image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"]

    image_allowed_mimes_external: list[str] = DEFAULT_EXTERNAL_MIMES,
    # MIME allowlist for external URL images (checked via Content-Type if verify=True)

    image_max_size_bytes: int = 5 * 1024 * 1024,
    # Max upload file size (5MB default, plan-dependent)

    image_verify_external: bool = False,
    # HEAD-check external URLs before embedding

    # ── Tables ──────────────────────────────────────────
    enable_tables: bool = True,
    # Convert markdown tables to Notion table blocks

    table_fallback: Literal["paragraph", "comment", "raise"] = "comment",
    # Behavior when enable_tables=False or table conversion fails

    # ── Headings ────────────────────────────────────────
    heading_overflow: Literal["downgrade", "paragraph"] = "downgrade",
    # H4+ behavior: downgrade to H3, or render as paragraph

    # ── Unsupported blocks ───────────────────────────────
    unsupported_block_policy: Literal["comment", "skip", "raise"] = "comment",
    # comment: emit <!-- notion-block: type --> marker
    # skip: silently omit
    # raise: throw NotionifyUnsupportedBlockError

    # ── Retry & Rate ────────────────────────────────────
    retry_max_attempts: int = 5,
    # Max retries per request for retryable errors

    retry_base_delay: float = 1.0,
    # Base delay in seconds for exponential backoff

    retry_max_delay: float = 60.0,
    # Maximum delay cap in seconds

    retry_jitter: bool = True,
    # Add random jitter to backoff intervals

    rate_limit_rps: float = 3.0,
    # Target requests per second (client-side pacing)

    # ── HTTP ─────────────────────────────────────────────
    timeout_seconds: float = 30.0,
    # HTTP request timeout

    http_proxy: str | None = None,
    # Optional HTTP proxy URL

    # ── Debug ────────────────────────────────────────────
    debug_dump_ast: bool = False,
    # Write normalized AST to stderr on each conversion

    debug_dump_payload: bool = False,
    # Write Notion API payload (redacted) to stderr

    debug_dump_diff: bool = False,
    # Write diff plan operations to stderr
)
```

---

## 10. Markdown → Notion Conversion Spec

### 10.1 Complete Block Mapping

| Markdown | Mistune Token | Notion Type | Notes |
|----------|--------------|-------------|-------|
| `# Heading 1` | `heading` level=1 | `heading_1` | |
| `## Heading 2` | `heading` level=2 | `heading_2` | |
| `### Heading 3` | `heading` level=3 | `heading_3` | |
| `#### H4+` | `heading` level≥4 | `heading_3` or `paragraph` | per `heading_overflow` |
| paragraph | `paragraph` | `paragraph` | rich_text split at 2000 |
| `> quote` | `block_quote` | `quote` | nested supported |
| `- item` | `list` bullet | `bulleted_list_item` | |
| `1. item` | `list` ordered | `numbered_list_item` | |
| `- [ ] todo` | `task_list_item` checked=False | `to_do` checked=false | |
| `- [x] done` | `task_list_item` checked=True | `to_do` checked=true | |
| ` ```lang ` | `block_code` | `code` | language preserved |
| `---` | `thematic_break` | `divider` | |
| table | `table` | `table` | if `enable_tables=True` |
| `![alt](url)` | `image` URL | `image.external` | |
| `![alt](./path)` | `image` local | `image.file_upload` | upload pipeline |
| `![alt](data:...)` | `image` data URI | `image.file_upload` | decode + upload |
| `$$...$$` | `block_math` | `equation` block or `code` | per `math_strategy` |
| `$...$` | `inline_math` | equation rich_text or code | per `math_strategy` |
| `**bold**` | inline `strong` | annotation `bold=true` | |
| `_italic_` | inline `emphasis` | annotation `italic=true` | |
| `~~strike~~` | inline `strikethrough` | annotation `strikethrough=true` | |
| `` `code` `` | inline `codespan` | annotation `code=true` | |
| `[text](url)` | inline `link` | rich_text `href` | |
| `\n\n` | hardline | paragraph break | |

### 10.2 Notion Block JSON Samples

#### Heading
```json
{
  "object": "block",
  "type": "heading_2",
  "heading_2": {
    "rich_text": [{ "type": "text", "text": { "content": "Section Title" } }],
    "color": "default",
    "is_toggleable": false
  }
}
```

#### Paragraph with annotations
```json
{
  "object": "block",
  "type": "paragraph",
  "paragraph": {
    "rich_text": [
      { "type": "text", "text": { "content": "This is " } },
      {
        "type": "text",
        "text": { "content": "bold" },
        "annotations": { "bold": true }
      },
      { "type": "text", "text": { "content": " and " } },
      {
        "type": "text",
        "text": { "content": "italic" },
        "annotations": { "italic": true }
      }
    ],
    "color": "default"
  }
}
```

#### Code Block
```json
{
  "object": "block",
  "type": "code",
  "code": {
    "rich_text": [{ "type": "text", "text": { "content": "print('hello')" } }],
    "language": "python",
    "caption": []
  }
}
```

#### Equation Block
```json
{
  "object": "block",
  "type": "equation",
  "equation": {
    "expression": "E = mc^2"
  }
}
```

#### Inline Equation (rich_text)
```json
{
  "type": "equation",
  "equation": { "expression": "\\alpha + \\beta" }
}
```

#### To-Do
```json
{
  "object": "block",
  "type": "to_do",
  "to_do": {
    "rich_text": [{ "type": "text", "text": { "content": "Complete this task" } }],
    "checked": false,
    "color": "default"
  }
}
```

#### Image (External)
```json
{
  "object": "block",
  "type": "image",
  "image": {
    "type": "external",
    "external": { "url": "https://example.com/image.png" }
  }
}
```

#### Image (Uploaded)
```json
{
  "object": "block",
  "type": "image",
  "image": {
    "type": "file",
    "file": { "upload_id": "<upload_id>" }
  }
}
```

#### Table
```json
{
  "object": "block",
  "type": "table",
  "table": {
    "table_width": 3,
    "has_column_header": true,
    "has_row_header": false,
    "children": [
      {
        "type": "table_row",
        "table_row": {
          "cells": [
            [{ "type": "text", "text": { "content": "Name" } }],
            [{ "type": "text", "text": { "content": "Age" } }],
            [{ "type": "text", "text": { "content": "Role" } }]
          ]
        }
      }
    ]
  }
}
```

### 10.3 Rich Text Splitting Rules

```
ALGORITHM split_rich_text(segments, limit=2000):
  output = []
  for segment in segments:
    if len(segment.content) <= limit:
      output.append(segment)
    else:
      # Split preserving annotation
      chunks = split_string(segment.content, limit)
      for chunk in chunks:
        output.append(segment.clone(content=chunk))
  return output
```

**Rule:** Never split in the middle of a multi-byte character or emoji.

### 10.4 Math Strategy Decision Tree

```
inline math token:
  strategy == "equation"  →  rich_text equation object
    len > 1000 → math_overflow_inline:
      "split"  → split expression (best-effort)
      "code"   → annotation code=True
      "text"   → plain text with $...$
  strategy == "code"      →  annotation code=True
  strategy == "latex_text" → plain text with $...$

block math token:
  strategy == "equation"  →  equation block
    len > 1000 → math_overflow_block:
      "split"  → split into multiple equation blocks
      "code"   → code block language="latex"
      "text"   → paragraph with $$...$$
  strategy == "code"      →  code block language="latex"
  strategy == "latex_text" → paragraph with $$...$$
```

---

## 11. Notion → Markdown Rendering Spec

### 11.1 Block Renderers

| Notion Type | Markdown Output | Notes |
|-------------|----------------|-------|
| `heading_1` | `# ...` | |
| `heading_2` | `## ...` | |
| `heading_3` | `### ...` | |
| `paragraph` | `...\n\n` | preserve inline annotations |
| `quote` | `> ...\n\n` | nested → repeated `>` |
| `bulleted_list_item` | `- ...\n` | nested = indented |
| `numbered_list_item` | `1. ...\n` | numbering computed |
| `to_do` | `- [ ] ...` / `- [x] ...` | checked state preserved |
| `code` | ` ```lang\n...\n``` ` | language preserved |
| `divider` | `---\n\n` | |
| `equation` | `$$\n...\n$$\n\n` | expression preserved |
| `table` | GFM table syntax | see §11.3 |
| `image` (external) | `![caption](url)` | |
| `image` (file) | `![caption](url)` | optional expiry warning |
| `callout` | `> 💡 ...\n\n` | icon + text merged |
| `toggle` | `- ...\n  children` | children indented |
| `child_page` | `[Page: title](notion_url)` | link only, no recurse unless requested |
| `child_database` | `[Database: title](notion_url)` | link only |
| `embed` | `[Embed](url)` | |
| `bookmark` | `[title](url)\n> description` | |
| `link_preview` | `[url](url)` | |
| `video` | `[Video](url)` | |
| `file` | `[filename](url)` | |
| `pdf` | `[PDF](url)` | |
| `audio` | `[Audio](url)` | |
| `column_list` | children concatenated | layout lost, semantic preserved |
| `column` | children concatenated | |
| `synced_block` | children rendered | |
| `template` | children rendered | |
| `breadcrumb` | omitted | no markdown equivalent |
| `table_of_contents` | omitted | auto-generated in markdown tools |
| unsupported | per `unsupported_block_policy` | comment/skip/raise |

### 11.2 Inline Annotation Rendering

```
ALGORITHM render_rich_text(segments) -> str:
  output = ""
  for seg in segments:
    text = seg.plain_text
    text = markdown_escape(text)           # escape special chars
    ann  = seg.annotations

    if ann.code:        text = f"`{text}`"
    if ann.bold:        text = f"**{text}**"
    if ann.italic:      text = f"_{text}_"
    if ann.strikethrough: text = f"~~{text}~~"
    if ann.underline:   text = f"<u>{text}</u>"   # HTML fallback

    if seg.type == "equation":
      text = f"${seg.equation.expression}$"

    if seg.href:        text = f"[{text}]({seg.href})"

    output += text
  return output
```

**Annotation combination order (innermost first):**
`code` → `bold` → `italic` → `strikethrough` → `link`

### 11.3 Table Rendering

```
ALGORITHM render_table(block) -> str:
  rows = fetch_children(block.id)   # table_row blocks
  if len(rows) == 0: return ""

  col_count = block.table.table_width
  lines = []

  for i, row in enumerate(rows):
    cells = [render_rich_text(cell) for cell in row.cells]
    lines.append("| " + " | ".join(cells) + " |")
    if i == 0 and block.table.has_column_header:
      lines.append("|" + "|".join(["---"] * col_count) + "|")

  return "\n".join(lines) + "\n\n"
```

### 11.4 Markdown Escaping Rules

```python
ESCAPE_CHARS = r'\`*_{}[]()#+-.!|'

def markdown_escape(text: str, context: str = "inline") -> str:
    """
    context: "inline" | "code" | "url"
    In code context: no escaping.
    In url context: only escape brackets.
    In inline context: escape all ESCAPE_CHARS.
    """
    if context == "code":
        return text
    if context == "url":
        return text.replace("(", "%28").replace(")", "%29")
    return re.sub(r'([\\`*_{}\[\]()#+\-.!|])', r'\\\1', text)
```

### 11.5 Recursion and Pagination

```
ALGORITHM export_blocks(block_id, depth, max_depth) -> str:
  if max_depth is not None and depth > max_depth:
    return "<!-- max_depth reached -->\n"

  output = ""
  cursor = None
  while True:
    resp = api.get_block_children(block_id, start_cursor=cursor)
    for block in resp.results:
      output += render_block(block, depth)
      if block.has_children:
        output += export_blocks(block.id, depth+1, max_depth)
    if not resp.has_more:
      break
    cursor = resp.next_cursor
  return output
```

### 11.6 Unsupported Block Fallback

```python
def render_unsupported(block, policy) -> str:
    if policy == "comment":
        text = extract_plain_text(block)   # best-effort
        if text:
            return f"<!-- notion:{block.type} -->\n{text}\n\n"
        return f"<!-- notion:{block.type} -->\n\n"
    elif policy == "skip":
        return ""
    elif policy == "raise":
        raise NotionifyUnsupportedBlockError(
            code="UNSUPPORTED_BLOCK",
            message=f"Cannot render block type: {block.type}",
            context={"block_id": block.id, "block_type": block.type}
        )
```

---

## 12. Diff Engine Design

### 12.1 Overview

The diff engine computes a **minimal change plan** to update an existing Notion page to match a new Markdown document, minimizing API calls and preserving unchanged blocks.

```
INPUT:  existing_blocks (fetched from Notion)
        new_blocks      (converted from new Markdown)
OUTPUT: operation_plan  (ordered list of ops)
```

### 12.2 Block Signature

```python
@dataclass(frozen=True)
class BlockSignature:
    block_type:          str       # "paragraph", "heading_2", etc.
    rich_text_hash:      str       # MD5 of normalized plain text
    structural_hash:     str       # MD5 of child count + child types
    attrs_hash:          str       # MD5 of type-specific attrs (lang, checked, level)
    nesting_depth:       int       # depth in tree

def compute_signature(block: NotionBlock) -> BlockSignature:
    plain = normalize_rich_text(block)
    attrs = extract_type_attrs(block)        # e.g., {"language": "python"}
    children_shape = summarize_children(block)
    return BlockSignature(
        block_type      = block.type,
        rich_text_hash  = md5(plain),
        structural_hash = md5(children_shape),
        attrs_hash      = md5(json.dumps(attrs, sort_keys=True)),
        nesting_depth   = block.depth,
    )
```

**Design note:** Plain text alone is insufficient. Two paragraphs with identical text but different annotations (bold/italic) must have different signatures.

### 12.3 LCS Matching

```
ALGORITHM lcs_match(existing_sigs, new_sigs):
  # Standard LCS over signatures (full equality)
  # Returns: matched pairs [(existing_idx, new_idx)]
  # Unmatched existing → DELETE ops
  # Unmatched new      → INSERT ops
  # Matched but type differs → REPLACE op (not UPDATE)
  # Matched, same type, content differs → UPDATE op
```

**Fallback:** If LCS score < `min_match_ratio` (default 0.3), switch to full overwrite. Prevents pathological cases where a near-complete rewrite still tries to diff.

### 12.4 Operation Types

```python
class DiffOpType(str, Enum):
    KEEP    = "keep"       # block unchanged, no API call
    UPDATE  = "update"     # same type, content patched
    REPLACE = "replace"    # type changed: delete old + insert new
    INSERT  = "insert"     # new block to create
    DELETE  = "delete"     # existing block to archive/delete

@dataclass
class DiffOp:
    op_type:          DiffOpType
    existing_id:      str | None      # Notion block ID (for DELETE/UPDATE/REPLACE)
    new_block:        dict | None     # Notion block payload (for INSERT/UPDATE/REPLACE)
    position_after:   str | None      # block ID to insert after (INSERT/REPLACE)
    depth:            int
```

### 12.5 Execution Plan

```
ALGORITHM execute_plan(ops):
  for op in ops:
    match op.op_type:
      KEEP:
        pass  # no API call

      UPDATE:
        api.update_block(op.existing_id, op.new_block)

      DELETE:
        api.delete_block(op.existing_id, archive=True)

      INSERT:
        api.append_block_children(
            parent_id     = op.parent_id,
            children      = [op.new_block],
            after         = op.position_after,
        )

      REPLACE:
        api.delete_block(op.existing_id, archive=True)
        api.append_block_children(
            parent_id     = op.parent_id,
            children      = [op.new_block],
            after         = op.position_after,
        )
```

**Batching:** Consecutive INSERT ops at the same level are batched into a single append_block_children call (up to 100 per batch).

### 12.6 Conflict Detection

```python
@dataclass
class PageSnapshot:
    page_id:      str
    last_edited:  datetime
    block_etags:  dict[str, str]    # block_id → last_edited_time

def detect_conflict(snapshot: PageSnapshot, current: PageSnapshot) -> bool:
    return (
        snapshot.last_edited != current.last_edited
        or any(
            snapshot.block_etags.get(bid) != current.block_etags.get(bid)
            for bid in snapshot.block_etags
        )
    )
```

**On conflict:**
- `on_conflict="raise"` → raise `NotionifyDiffConflictError`
- `on_conflict="overwrite"` → fall back to full overwrite

### 12.7 Nested Block Diff

Diff is applied recursively per level. Each block's children are independently diffed if the parent is `KEEP` or `UPDATE`. If parent is `REPLACE`, children are entirely rebuilt.

---

## 13. Image Upload Architecture

### 13.1 Source Detection

```python
def detect_image_source(src: str) -> ImageSourceType:
    if src.startswith("data:"):
        return ImageSourceType.DATA_URI
    if src.startswith("http://") or src.startswith("https://"):
        return ImageSourceType.EXTERNAL_URL
    if Path(src).exists():
        return ImageSourceType.LOCAL_FILE
    return ImageSourceType.UNKNOWN

# UNKNOWN behavior per image_fallback config:
# "skip"        → ConversionWarning, omit block
# "placeholder" → text block "[image: {src}]"
# "raise"       → NotionifyImageNotFoundError
```

### 13.2 Validation Pipeline

```
PIPELINE validate_image(src, source_type):
  1. MIME detection
     - LOCAL_FILE:  python-magic / mimetypes
     - DATA_URI:    parse header (data:image/png;base64,...)
     - EXTERNAL_URL: optional HEAD request if image_verify_external=True

  2. MIME allowlist check
     - upload types:   image_allowed_mimes_upload
     - external types: image_allowed_mimes_external
     → NotionifyImageTypeError if rejected

  3. Size check (upload only)
     - LOCAL_FILE:  os.path.getsize
     - DATA_URI:    len(decoded_bytes)
     → NotionifyImageSizeError if > image_max_size_bytes

  4. Decode (DATA_URI only)
     - base64 decode
     → NotionifyImageParseError if malformed
```

### 13.3 Single-Part Upload Flow

```
STEP 1: POST /v1/file-uploads
  Body: { "name": filename, "content_type": mime }
  Response: { "id": upload_id, "upload_url": url }

STEP 2: PUT {upload_url}
  Headers: Content-Type: {mime}
  Body: raw file bytes
  Response: 200 OK

STEP 3: Build image block with upload_id
  {
    "type": "image",
    "image": {
      "type": "file",
      "file": { "upload_id": upload_id }
    }
  }

STEP 4: Include block in append_block_children call
  (attachment happens when block is appended)
```

### 13.4 Multi-Part Upload Flow

```
STEP 1: POST /v1/file-uploads
  Body: { "name": filename, "content_type": mime, "mode": "multi_part" }
  Response: { "id": upload_id, "part_urls": [...] }

STEP 2: For each chunk (chunk_size = 5MB):
  PUT {part_url}
  Headers: Content-Type: {mime}
  Body: chunk bytes
  Response: { "etag": "..." }
  → collect all ETags

STEP 3: POST /v1/file-uploads/{upload_id}/complete
  Body: { "parts": [{ "part_number": 1, "etag": "..." }, ...] }
  Response: { "id": upload_id, "status": "uploaded" }

STEP 4: Build image block with upload_id (same as single-part)

STEP 5: Include in append_block_children
```

### 13.5 Upload State Machine

```
States:
  PENDING    → initial, not yet started
  UPLOADING  → PUT/chunks in progress
  UPLOADED   → all parts complete, not yet attached
  ATTACHED   → block appended to Notion page
  FAILED     → unrecoverable error
  EXPIRED    → uploaded but not attached within TTL window

Transitions:
  PENDING    → UPLOADING  (on upload start)
  UPLOADING  → UPLOADED   (on completion success)
  UPLOADING  → FAILED     (on upload error, retries exhausted)
  UPLOADED   → ATTACHED   (on successful append)
  UPLOADED   → EXPIRED    (if TTL window passed before attach)
  EXPIRED    → UPLOADING  (retry: re-upload)
  ATTACHED   → (terminal)
  FAILED     → (terminal, unless explicit retry)
```

### 13.6 Concurrent Upload (Async)

```python
async def upload_all_images(
    images: list[PendingImage],
    max_concurrent: int,
) -> list[UploadResult]:
    semaphore = asyncio.Semaphore(max_concurrent)
    async def upload_one(img):
        async with semaphore:
            return await upload_image(img)
    return await asyncio.gather(*[upload_one(img) for img in images],
                                 return_exceptions=True)
```

---

## 14. Notion API Endpoint Mapping

### 14.1 Pages

| Operation | Method | Endpoint | Key Payload Fields |
|-----------|--------|----------|--------------------|
| Create page | POST | `/v1/pages` | `parent`, `properties`, `children` |
| Retrieve page | GET | `/v1/pages/{page_id}` | — |
| Update page properties | PATCH | `/v1/pages/{page_id}` | `properties`, `archived` |
| Archive page | PATCH | `/v1/pages/{page_id}` | `archived: true` |

### 14.2 Blocks

| Operation | Method | Endpoint | Key Payload Fields |
|-----------|--------|----------|--------------------|
| Retrieve block | GET | `/v1/blocks/{block_id}` | — |
| Update block | PATCH | `/v1/blocks/{block_id}` | `{type}: { rich_text, ... }` |
| Delete block | DELETE | `/v1/blocks/{block_id}` | — |
| Get children | GET | `/v1/blocks/{block_id}/children` | `start_cursor`, `page_size` |
| Append children | PATCH | `/v1/blocks/{block_id}/children` | `children[]`, `after` |

### 14.3 File Uploads

| Operation | Method | Endpoint | Key Payload Fields |
|-----------|--------|----------|--------------------|
| Create upload | POST | `/v1/file-uploads` | `name`, `content_type`, `mode` |
| Send single part | PUT | `{upload_url}` | raw bytes |
| Send multi part | PUT | `{part_url}` | chunk bytes |
| Complete upload | POST | `/v1/file-uploads/{id}/complete` | `parts[]` |
| Retrieve upload | GET | `/v1/file-uploads/{id}` | — |

### 14.4 Common Request Headers

```http
Authorization: Bearer {token}
Notion-Version: {notion_version}
Content-Type: application/json
```

### 14.5 Pagination Contract

```python
def paginate(api_call, **kwargs) -> Iterator[Block]:
    cursor = None
    while True:
        resp = api_call(**kwargs, start_cursor=cursor, page_size=100)
        yield from resp["results"]
        if not resp["has_more"]:
            break
        cursor = resp["next_cursor"]
```

### 14.6 Response Error Codes Handled

| HTTP Status | Notion Code | Action |
|-------------|------------|--------|
| 400 | `validation_error` | Raise `NotionifyValidationError`, no retry |
| 401 | `unauthorized` | Raise `NotionifyAuthError`, no retry |
| 403 | `restricted_resource` | Raise `NotionifyPermissionError`, no retry |
| 404 | `object_not_found` | Raise `NotionifyNotFoundError`, no retry |
| 409 | `conflict_error` | Raise `NotionifyDiffConflictError`, no retry |
| 429 | `rate_limited` | Retry with `Retry-After` header |
| 500 | `internal_server_error` | Retry with exponential backoff |
| 502 | — | Retry with exponential backoff |
| 503 | `service_unavailable` | Retry with exponential backoff |
| 504 | — | Retry with exponential backoff |

---

## 15. Error Taxonomy

### 15.1 Base Class

```python
class NotionifyError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        context: dict | None = None,
        cause: Exception | None = None,
    ):
        self.code    = code
        self.message = message
        self.context = context or {}
        self.cause   = cause
        super().__init__(message)
```

### 15.2 Full Error Class Hierarchy

```
NotionifyError
│
├── NotionifyValidationError        VALIDATION_ERROR
│     Context: field, value, constraint
│
├── NotionifyAuthError              AUTH_ERROR
│     Context: token_prefix (last 4 chars only)
│
├── NotionifyPermissionError        PERMISSION_ERROR
│     Context: page_id, operation
│
├── NotionifyNotFoundError          NOT_FOUND
│     Context: resource_type, resource_id
│
├── NotionifyRateLimitError         RATE_LIMITED
│     Context: retry_after_seconds, attempt
│
├── NotionifyRetryExhaustedError    RETRY_EXHAUSTED
│     Context: attempts, last_status_code, last_error_code
│
├── NotionifyNetworkError           NETWORK_ERROR
│     Context: url, attempt
│
├── NotionifyConversionError        CONVERSION_ERROR
│   ├── NotionifyUnsupportedBlockError   UNSUPPORTED_BLOCK
│   │     Context: block_id, block_type
│   ├── NotionifyTextOverflowError       TEXT_OVERFLOW
│   │     Context: content_length, limit, block_type
│   └── NotionifyMathOverflowError       MATH_OVERFLOW
│         Context: expression_length, limit, strategy
│
├── NotionifyImageError             IMAGE_ERROR
│   ├── NotionifyImageNotFoundError      IMAGE_NOT_FOUND
│   │     Context: src, resolved_path
│   ├── NotionifyImageTypeError          IMAGE_TYPE_ERROR
│   │     Context: src, detected_mime, allowed_mimes
│   ├── NotionifyImageSizeError          IMAGE_SIZE_ERROR
│   │     Context: src, size_bytes, max_bytes
│   └── NotionifyImageParseError         IMAGE_PARSE_ERROR
│         Context: src, reason
│
├── NotionifyUploadError            UPLOAD_ERROR
│   ├── NotionifyUploadExpiredError      UPLOAD_EXPIRED
│   │     Context: upload_id, elapsed_seconds
│   └── NotionifyUploadTransportError   UPLOAD_TRANSPORT_ERROR
│         Context: upload_id, part_number, status_code
│
└── NotionifyDiffConflictError      DIFF_CONFLICT
      Context: page_id, snapshot_time, detected_time
```

### 15.3 Error Code Enum

```python
class ErrorCode(str, Enum):
    VALIDATION_ERROR       = "VALIDATION_ERROR"
    AUTH_ERROR             = "AUTH_ERROR"
    PERMISSION_ERROR       = "PERMISSION_ERROR"
    NOT_FOUND              = "NOT_FOUND"
    RATE_LIMITED           = "RATE_LIMITED"
    RETRY_EXHAUSTED        = "RETRY_EXHAUSTED"
    NETWORK_ERROR          = "NETWORK_ERROR"
    CONVERSION_ERROR       = "CONVERSION_ERROR"
    UNSUPPORTED_BLOCK      = "UNSUPPORTED_BLOCK"
    TEXT_OVERFLOW          = "TEXT_OVERFLOW"
    MATH_OVERFLOW          = "MATH_OVERFLOW"
    IMAGE_ERROR            = "IMAGE_ERROR"
    IMAGE_NOT_FOUND        = "IMAGE_NOT_FOUND"
    IMAGE_TYPE_ERROR       = "IMAGE_TYPE_ERROR"
    IMAGE_SIZE_ERROR       = "IMAGE_SIZE_ERROR"
    IMAGE_PARSE_ERROR      = "IMAGE_PARSE_ERROR"
    UPLOAD_ERROR           = "UPLOAD_ERROR"
    UPLOAD_EXPIRED         = "UPLOAD_EXPIRED"
    UPLOAD_TRANSPORT_ERROR = "UPLOAD_TRANSPORT_ERROR"
    DIFF_CONFLICT          = "DIFF_CONFLICT"
```

---

## 16. Reliability & Rate-Limit Design

### 16.1 Token Bucket (Client-Side Pacing)

```python
class TokenBucket:
    def __init__(self, rate_rps: float, burst: int = 10):
        self.rate      = rate_rps       # tokens/second
        self.burst     = burst          # max burst size
        self.tokens    = float(burst)
        self.last_refill = time.monotonic()
        self._lock     = threading.Lock()

    def acquire(self, tokens: int = 1) -> float:
        """Returns wait time in seconds before token available."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.burst,
                              self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0
            else:
                wait = (tokens - self.tokens) / self.rate
                return wait
```

### 16.2 Retry Decision Matrix

```python
def should_retry(response: httpx.Response, attempt: int, max_attempts: int) -> bool:
    if attempt >= max_attempts:
        return False

    retryable_status = {429, 500, 502, 503, 504}
    retryable_network = (httpx.TimeoutException, httpx.NetworkError)

    if isinstance(response, Exception):
        return isinstance(response, retryable_network)

    return response.status_code in retryable_status
```

### 16.3 Backoff Algorithm

```python
def compute_backoff(
    attempt:    int,
    base:       float = 1.0,
    maximum:    float = 60.0,
    jitter:     bool  = True,
    retry_after: float | None = None,
) -> float:
    if retry_after is not None:
        delay = retry_after
    else:
        delay = min(base * (2 ** attempt), maximum)

    if jitter:
        delay *= (0.5 + random.random() * 0.5)    # ±50% jitter

    return delay
```

### 16.4 Full Request Lifecycle

```
REQUEST LIFECYCLE:
  1. Acquire token bucket slot (wait if needed)
  2. Send HTTP request
  3. On response:
     a. 2xx        → return response
     b. 429        → extract Retry-After → sleep → retry
     c. 5xx        → exponential backoff → retry
     d. 4xx (other) → raise immediately (no retry)
     e. Network err → exponential backoff → retry
  4. On max_attempts exceeded:
     → raise NotionifyRetryExhaustedError

ASYNC variant:
  Same logic, use asyncio.sleep instead of time.sleep.
  Shared asyncio.Lock for token bucket.
```

### 16.5 Idempotency Strategy

| Operation | Idempotency Approach |
|-----------|---------------------|
| Create page | Check by title + parent before create (optional) |
| Append blocks | Chunk + ordered append; re-check children on retry |
| Update block | PATCH is naturally idempotent |
| Delete block | Archive is idempotent (already archived = no-op) |
| Upload file | Re-upload on expired; track state to avoid double-attach |

---

## 17. Observability

### 17.1 Structured Log Format

```json
{
  "ts":          "2025-07-01T12:00:00.123Z",
  "level":       "INFO",
  "op":          "append_markdown",
  "page_id":     "abc123",
  "block_id":    null,
  "attempt":     1,
  "duration_ms": 342,
  "status":      "success",
  "blocks":      12,
  "images":      2,
  "warnings":    0
}
```

```json
{
  "ts":           "2025-07-01T12:00:01.456Z",
  "level":        "WARNING",
  "op":           "request",
  "endpoint":     "PATCH /v1/blocks/{id}/children",
  "status_code":  429,
  "retry_after":  2.5,
  "attempt":      2
}
```

### 17.2 Log Levels

| Level | Used For |
|-------|----------|
| DEBUG | AST dumps, payload snippets, diff ops |
| INFO | Operation start/complete, counts |
| WARNING | Retries, rate limits, lossy conversion, image skips |
| ERROR | Failed operations, unrecoverable errors |

### 17.3 Metrics Interface

```python
class MetricsHook(Protocol):
    def increment(self, name: str, value: int = 1, tags: dict = {}) -> None: ...
    def timing(self, name: str, ms: float, tags: dict = {}) -> None: ...
    def gauge(self, name: str, value: float, tags: dict = {}) -> None: ...

# Usage
client = NotionifyClient(token=..., metrics=MyDatadogMetrics())
```

**Emitted metrics:**

| Metric Name | Type | Tags |
|-------------|------|------|
| `notionify.requests_total` | counter | `endpoint`, `status_code` |
| `notionify.retries_total` | counter | `reason`, `endpoint` |
| `notionify.rate_limited_total` | counter | `endpoint` |
| `notionify.request_duration_ms` | timing | `endpoint`, `status_code` |
| `notionify.blocks_created_total` | counter | `block_type` |
| `notionify.upload_success_total` | counter | `mode` (single/multi) |
| `notionify.upload_failure_total` | counter | `reason` |
| `notionify.conversion_warnings_total` | counter | `code` |
| `notionify.diff_ops_total` | counter | `op_type` |
| `notionify.page_export_duration_ms` | timing | `recursive` |

### 17.4 Debug Artifact Dump

```python
# When debug_dump_ast=True:
logger.debug("AST", extra={"ast": json.dumps(normalized_ast, indent=2)})

# When debug_dump_payload=True:
logger.debug("PAYLOAD", extra={"payload": redact(payload)})
# redact() removes: token, file bytes, base64 data

# When debug_dump_diff=True:
logger.debug("DIFF_PLAN", extra={"ops": [op.__dict__ for op in plan]})
```

---

## 18. Security & Privacy

| Rule | Implementation |
|------|---------------|
| Token never logged | `redact()` applied to all log calls; review checklist item |
| No token in error context | `NotionifyAuthError` only includes token prefix (last 4 chars) |
| File bytes never logged | `redact()` strips binary payloads |
| base64 data URIs never logged | Replaced with `<data_uri:N_bytes>` |
| No persistent storage | SDK writes no files unless explicitly requested |
| No directory crawling | Only explicit `src` paths are ever accessed |
| Proxy support | `http_proxy` config for corporate environments |
| TLS validation | Enabled by default; no option to disable globally |
| Dependency audit | `pip-audit` in CI pipeline |

---

## 19. Package Architecture

### 19.1 Full Directory Structure

```
notionify/
│
├── __init__.py                  # public exports
├── client.py                    # NotionifyClient (sync)
├── async_client.py              # AsyncNotionifyClient
├── config.py                    # NotionifyConfig dataclass
├── errors.py                    # full error hierarchy
├── models.py                    # result types, dataclasses
│
├── converter/
│   ├── __init__.py
│   ├── ast_normalizer.py        # Mistune AST → canonical tokens
│   ├── md_to_notion.py          # top-level Markdown → blocks pipeline
│   ├── notion_to_md.py          # top-level Notion → Markdown pipeline
│   ├── block_builder.py         # build individual Notion block dicts
│   ├── rich_text.py             # rich text array builder + splitter
│   ├── math.py                  # math strategy dispatcher
│   ├── tables.py                # table block builder + renderer
│   └── inline_renderer.py       # render rich_text → Markdown string
│
├── diff/
│   ├── __init__.py
│   ├── signature.py             # BlockSignature computation
│   ├── lcs_matcher.py           # LCS algorithm over signatures
│   ├── planner.py               # produce DiffOp list
│   └── executor.py              # apply DiffOp list via API
│
├── notion_api/
│   ├── __init__.py
│   ├── transport.py             # httpx-based HTTP layer (sync + async
### 19.1 Full Directory Structure (continued)

```
notionify/
│
├── __init__.py                  # public exports
├── client.py                    # NotionifyClient (sync)
├── async_client.py              # AsyncNotionifyClient
├── config.py                    # NotionifyConfig dataclass
├── errors.py                    # full error hierarchy
├── models.py                    # result types, dataclasses
│
├── converter/
│   ├── __init__.py
│   ├── ast_normalizer.py        # Mistune AST → canonical tokens
│   ├── md_to_notion.py          # top-level Markdown → blocks pipeline
│   ├── notion_to_md.py          # top-level Notion → Markdown pipeline
│   ├── block_builder.py         # build individual Notion block dicts
│   ├── rich_text.py             # rich text array builder + splitter
│   ├── math.py                  # math strategy dispatcher
│   ├── tables.py                # table block builder + renderer
│   └── inline_renderer.py       # render rich_text → Markdown string
│
├── diff/
│   ├── __init__.py
│   ├── signature.py             # BlockSignature computation
│   ├── lcs_matcher.py           # LCS algorithm over signatures
│   ├── planner.py               # produce DiffOp list
│   └── executor.py              # apply DiffOp list via API
│
├── notion_api/
│   ├── __init__.py
│   ├── transport.py             # httpx sync+async client, retry, rate-limit
│   ├── rate_limit.py            # TokenBucket implementation
│   ├── retries.py               # backoff, retry decision matrix
│   ├── pages.py                 # page-level API wrappers
│   ├── blocks.py                # block-level API wrappers
│   └── files.py                 # file-upload API wrappers
│
├── image/
│   ├── __init__.py
│   ├── detect.py                # source type detection
│   ├── validate.py              # MIME, size, decode validation
│   ├── upload_single.py         # single-part upload flow
│   ├── upload_multi.py          # multi-part upload flow
│   ├── attach.py                # build image block with upload_id
│   └── state.py                 # UploadStateMachine
│
├── observability/
│   ├── __init__.py
│   ├── logger.py                # structured logger setup
│   └── metrics.py               # MetricsHook protocol + noop impl
│
└── utils/
    ├── __init__.py
    ├── chunk.py                 # batch children into ≤100 groups
    ├── text_split.py            # split string at char boundary ≤N
    ├── hashing.py               # MD5 helpers for signatures
    └── redact.py                # token/payload redaction for logs

tests/
├── unit/
│   ├── converter/
│   ├── diff/
│   ├── image/
│   └── utils/
├── integration/
│   ├── conftest.py              # vcr cassette setup, live flag
│   ├── test_create.py
│   ├── test_append.py
│   ├── test_update.py
│   ├── test_export.py
│   ├── test_images.py
│   └── test_retry.py
├── golden/
│   ├── fixtures/                # .md input files
│   └── snapshots/               # .json Notion payload snapshots
└── perf/
    ├── test_large_page.py
    └── test_async_upload.py

docs/
├── quickstart.md
├── api_reference.md
├── conversion_matrix.md
├── error_cookbook.md
├── migration_guide.md
└── faq.md
```

### 19.2 Module Dependency Graph

```
client.py / async_client.py
    │
    ├── config.py
    ├── models.py
    ├── errors.py
    │
    ├── converter/
    │   ├── md_to_notion.py
    │   │   ├── ast_normalizer.py
    │   │   ├── block_builder.py
    │   │   │   ├── rich_text.py
    │   │   │   ├── math.py
    │   │   │   └── tables.py
    │   │   └── image/  (detect, validate, upload_*, attach)
    │   │
    │   └── notion_to_md.py
    │       ├── inline_renderer.py
    │       └── tables.py
    │
    ├── diff/
    │   ├── planner.py
    │   │   ├── signature.py
    │   │   └── lcs_matcher.py
    │   └── executor.py
    │       └── notion_api/blocks.py
    │
    ├── notion_api/
    │   ├── transport.py
    │   │   ├── rate_limit.py
    │   │   └── retries.py
    │   ├── pages.py
    │   ├── blocks.py
    │   └── files.py
    │
    └── observability/
        ├── logger.py
        └── metrics.py
```

### 19.3 Key Class Interfaces

```python
# config.py
@dataclass
class NotionifyConfig:
    token:                      str
    notion_version:             str         = "2025-09-03"
    base_url:                   str         = "https://api.notion.com/v1"
    math_strategy:              str         = "equation"
    math_overflow_inline:       str         = "code"
    math_overflow_block:        str         = "code"
    detect_latex_code:          bool        = True
    image_upload:               bool        = True
    image_max_concurrent:       int         = 4
    image_fallback:             str         = "skip"
    image_expiry_warnings:      bool        = True
    image_allowed_mimes_upload: list[str]   = field(default_factory=lambda: DEFAULT_UPLOAD_MIMES)
    image_allowed_mimes_external:list[str]  = field(default_factory=lambda: DEFAULT_EXTERNAL_MIMES)
    image_max_size_bytes:       int         = 5 * 1024 * 1024
    image_verify_external:      bool        = False
    enable_tables:              bool        = True
    table_fallback:             str         = "comment"
    heading_overflow:           str         = "downgrade"
    unsupported_block_policy:   str         = "comment"
    retry_max_attempts:         int         = 5
    retry_base_delay:           float       = 1.0
    retry_max_delay:            float       = 60.0
    retry_jitter:               bool        = True
    rate_limit_rps:             float       = 3.0
    timeout_seconds:            float       = 30.0
    http_proxy:                 str | None  = None
    metrics:                    MetricsHook | None = None
    debug_dump_ast:             bool        = False
    debug_dump_payload:         bool        = False
    debug_dump_diff:            bool        = False


# notion_api/transport.py
class NotionTransport:
    def __init__(self, config: NotionifyConfig): ...
    def request(self, method, path, **kwargs) -> dict: ...
    def paginate(self, path, **kwargs) -> Iterator[dict]: ...

class AsyncNotionTransport:
    def __init__(self, config: NotionifyConfig): ...
    async def request(self, method, path, **kwargs) -> dict: ...
    async def paginate(self, path, **kwargs) -> AsyncIterator[dict]: ...


# converter/md_to_notion.py
class MarkdownToNotionConverter:
    def __init__(self, config: NotionifyConfig): ...
    def convert(self, markdown: str) -> ConversionResult: ...
    # ConversionResult.blocks      : list[dict]   (Notion block payloads)
    # ConversionResult.images      : list[PendingImage]
    # ConversionResult.warnings    : list[ConversionWarning]


# converter/notion_to_md.py
class NotionToMarkdownRenderer:
    def __init__(self, config: NotionifyConfig): ...
    def render_blocks(self, blocks: list[dict]) -> str: ...
    def render_block(self, block: dict, depth: int = 0) -> str: ...


# diff/planner.py
class DiffPlanner:
    def __init__(self, config: NotionifyConfig): ...
    def plan(
        self,
        existing: list[dict],
        new: list[dict],
    ) -> list[DiffOp]: ...


# diff/executor.py
class DiffExecutor:
    def __init__(self, transport: NotionTransport, config: NotionifyConfig): ...
    def execute(self, page_id: str, ops: list[DiffOp]) -> UpdateResult: ...


# image/state.py
class UploadStateMachine:
    def __init__(self, upload_id: str): ...
    state: UploadState
    def transition(self, new_state: UploadState) -> None: ...
    def assert_can_attach(self) -> None: ...
```

---

## 20. Full Test Matrix

### 20.1 Unit Tests — Converter

| Test ID | Description | Expected |
|---------|-------------|----------|
| U-CV-001 | H1/H2/H3 → heading_1/2/3 | Correct type mapping |
| U-CV-002 | H4+ with `heading_overflow="downgrade"` | → heading_3 |
| U-CV-003 | H4+ with `heading_overflow="paragraph"` | → paragraph |
| U-CV-004 | Paragraph plain text | Single rich_text segment |
| U-CV-005 | Paragraph > 2000 chars | Split into multiple segments, annotations preserved |
| U-CV-006 | Bold + italic combined | Both annotations true on same segment |
| U-CV-007 | Nested bold inside italic | Correct annotation merge |
| U-CV-008 | Inline code | annotation code=true |
| U-CV-009 | Strikethrough | annotation strikethrough=true |
| U-CV-010 | Link in paragraph | href set, text preserved |
| U-CV-011 | Bullet list single item | bulleted_list_item |
| U-CV-012 | Ordered list | numbered_list_item |
| U-CV-013 | Nested list (3 levels) | Correct nesting structure |
| U-CV-014 | Task list unchecked | to_do checked=false |
| U-CV-015 | Task list checked | to_do checked=true |
| U-CV-016 | Block quote plain | quote block |
| U-CV-017 | Nested block quote | quote with children |
| U-CV-018 | Code block with language | code.language preserved |
| U-CV-019 | Code block no language | language="plain text" |
| U-CV-020 | Code block > 2000 chars | Split rich_text segments |
| U-CV-021 | Thematic break | divider block |
| U-CV-022 | Table 3x3 with header | table block + table_rows |
| U-CV-023 | Table with inline formatting in cells | rich_text in cells |
| U-CV-024 | Table with `enable_tables=False` | fallback per config |
| U-CV-025 | Block math `equation` strategy | equation block |
| U-CV-026 | Block math `code` strategy | code block language=latex |
| U-CV-027 | Block math `latex_text` strategy | paragraph with `$$...$$` |
| U-CV-028 | Block math > 1000 chars overflow | fallback per math_overflow_block |
| U-CV-029 | Inline math `equation` strategy | equation rich_text segment |
| U-CV-030 | Inline math `code` strategy | code annotation |
| U-CV-031 | Inline math > 1000 chars overflow | fallback per math_overflow_inline |
| U-CV-032 | Image external URL | image.external block |
| U-CV-033 | Image local path (mocked upload) | image.file block with upload_id |
| U-CV-034 | Image data URI (mocked upload) | image.file block with upload_id |
| U-CV-035 | Image local path + `image_upload=False` | fallback per image_fallback |
| U-CV-036 | Empty markdown | empty block list, no error |
| U-CV-037 | Markdown with only whitespace | empty block list |
| U-CV-038 | Unicode content (CJK, emoji) | Correctly encoded, split at char boundary |
| U-CV-039 | Mixed content (all block types) | All converted correctly |
| U-CV-040 | `detect_latex_code=True`: code block language=latex | rendered as equation |

### 20.2 Unit Tests — Notion → Markdown

| Test ID | Description | Expected |
|---------|-------------|----------|
| U-NM-001 | heading_1/2/3 → `#`/`##`/`###` | Correct prefix |
| U-NM-002 | paragraph with bold | `**bold**` preserved |
| U-NM-003 | paragraph with italic | `_italic_` preserved |
| U-NM-004 | paragraph with link | `[text](url)` |
| U-NM-005 | paragraph with inline equation | `$expr$` |
| U-NM-006 | bulleted_list_item nested | Correct indentation |
| U-NM-007 | numbered_list_item | numbering computed correctly |
| U-NM-008 | to_do checked=false | `- [ ] text` |
| U-NM-009 | to_do checked=true | `- [x] text` |
| U-NM-010 | code block python | ` ```python\n...\n``` ` |
| U-NM-011 | divider | `---` |
| U-NM-012 | equation block | `$$\nexpr\n$$` |
| U-NM-013 | table 3x3 | GFM table with header separator |
| U-NM-014 | image external | `![caption](url)` |
| U-NM-015 | image with expiry warning | comment appended |
| U-NM-016 | callout block | `> icon text` |
| U-NM-017 | unsupported block `comment` policy | `<!-- notion:type -->` |
| U-NM-018 | unsupported block `skip` policy | empty string |
| U-NM-019 | unsupported block `raise` policy | NotionifyUnsupportedBlockError |
| U-NM-020 | markdown_escape special chars | All special chars escaped |
| U-NM-021 | empty rich_text array | empty string, no crash |
| U-NM-022 | nested quote | `> > text` |

### 20.3 Unit Tests — Rich Text & Splitting

| Test ID | Description | Expected |
|---------|-------------|----------|
| U-RT-001 | Split at exactly 2000 | Two segments, no char loss |
| U-RT-002 | Split multi-byte char at boundary | No broken character |
| U-RT-003 | Split emoji at boundary | No broken emoji |
| U-RT-004 | Split preserves annotation | Both parts have same annotation |
| U-RT-005 | Empty content | Empty array returned |
| U-RT-006 | Content exactly 2000 | Single segment, no split |
| U-RT-007 | Content 2001 | Two segments |
| U-RT-008 | Multiple segments, one oversized | Only oversized is split |

### 20.4 Unit Tests — Diff Engine

| Test ID | Description | Expected |
|---------|-------------|----------|
| U-DF-001 | Identical content | All KEEP ops, 0 API calls |
| U-DF-002 | Single paragraph changed | 1 UPDATE op |
| U-DF-003 | Block type changed | 1 REPLACE op (DELETE + INSERT) |
| U-DF-004 | New block added at end | 1 INSERT op |
| U-DF-005 | New block added at start | 1 INSERT op at correct position |
| U-DF-006 | Block deleted | 1 DELETE op |
| U-DF-007 | Two blocks swapped | DELETE + INSERT ops |
| U-DF-008 | Completely different content | Full replace |
| U-DF-009 | LCS score below min_match_ratio | Fallback to overwrite |
| U-DF-010 | Conflict detected | NotionifyDiffConflictError raised |
| U-DF-011 | Conflict with `on_conflict="overwrite"` | Falls back to overwrite |
| U-DF-012 | Consecutive inserts batched | Single append call |
| U-DF-013 | Nested block diff | Children independently diffed |
| U-DF-014 | Signature hash stability | Same input always same hash |
| U-DF-015 | Rich text change only | Signatures differ, UPDATE op |

### 20.5 Unit Tests — Image Pipeline

| Test ID | Description | Expected |
|---------|-------------|----------|
| U-IM-001 | Detect HTTPS URL | ImageSourceType.EXTERNAL_URL |
| U-IM-002 | Detect local path exists | ImageSourceType.LOCAL_FILE |
| U-IM-003 | Detect data URI | ImageSourceType.DATA_URI |
| U-IM-004 | Detect unknown path | ImageSourceType.UNKNOWN |
| U-IM-005 | Unknown + `image_fallback="skip"` | Warning, no block |
| U-IM-006 | Unknown + `image_fallback="placeholder"` | Text block with path |
| U-IM-007 | Unknown + `image_fallback="raise"` | NotionifyImageNotFoundError |
| U-IM-008 | Valid MIME (image/png) | Passes validation |
| U-IM-009 | Invalid MIME (application/pdf) | NotionifyImageTypeError |
| U-IM-010 | File within size limit | Passes validation |
| U-IM-011 | File exceeds size limit | NotionifyImageSizeError |
| U-IM-012 | Valid base64 data URI | Decodes successfully |
| U-IM-013 | Malformed base64 data URI | NotionifyImageParseError |
| U-IM-014 | Upload state: PENDING → UPLOADING | Valid transition |
| U-IM-015 | Upload state: UPLOADED → EXPIRED | Valid transition |
| U-IM-016 | Upload state: EXPIRED → UPLOADING (retry) | Re-upload triggered |
| U-IM-017 | Attach after EXPIRED | NotionifyUploadExpiredError |

### 20.6 Integration Tests

| Test ID | Description | Approach |
|---------|-------------|----------|
| I-001 | Create page from markdown | VCR cassette |
| I-002 | Create page under database | VCR cassette |
| I-003 | Create page with all block types | VCR cassette |
| I-004 | Append markdown to page | VCR cassette |
| I-005 | Append markdown after block | VCR cassette |
| I-006 | Overwrite page content | VCR cassette |
| I-007 | Diff update: minimal changes | VCR cassette |
| I-008 | Diff update: full replace | VCR cassette |
| I-009 | Update single block | VCR cassette |
| I-010 | Delete block (archive) | VCR cassette |
| I-011 | Insert after block | VCR cassette |
| I-012 | Export page flat | VCR cassette |
| I-013 | Export page recursive | VCR cassette |
| I-014 | Export block subtree | VCR cassette |
| I-015 | Export with unsupported blocks | VCR cassette |
| I-016 | Upload single-part image | VCR cassette |
| I-017 | Upload multi-part image | VCR cassette |
| I-018 | Image upload + attach roundtrip | VCR cassette |
| I-019 | 429 retry with Retry-After | Mock 429 → then 200 |
| I-020 | 500 retry with backoff | Mock 500 × N → then 200 |
| I-021 | Retry exhausted → exception | Mock always 500 |
| I-022 | Pagination: > 100 blocks | VCR cassette (paginated) |
| I-023 | Async create page | VCR cassette |
| I-024 | Async parallel image upload | VCR cassette |
| I-025 | Large page > 100 blocks (chunked) | VCR cassette |

### 20.7 Golden Fixture Tests

| Fixture | Description |
|---------|-------------|
| `basic_formatting.md` | bold, italic, code, strikethrough |
| `headings_all_levels.md` | H1–H6 |
| `nested_lists.md` | bullet + ordered + nested 3 levels |
| `task_list.md` | checked + unchecked items |
| `code_blocks.md` | multiple languages |
| `tables.md` | simple + complex tables |
| `math_inline.md` | inline equations |
| `math_block.md` | block equations |
| `math_overflow.md` | equations exceeding 1000 chars |
| `images_external.md` | external URL images |
| `mixed_all.md` | all block types in one document |
| `unicode_cjk.md` | Chinese/Japanese/Korean content |
| `unicode_emoji.md` | emoji in text |
| `empty.md` | empty file |
| `whitespace_only.md` | only newlines/spaces |
| `long_paragraph.md` | paragraph > 2000 chars |
| `deeply_nested.md` | quote > list > list > list |

**Each fixture produces:**
- `{name}.input.md` — Markdown input
- `{name}.notion.json` — Expected Notion API payload
- `{name}.roundtrip.md` — Expected re-exported Markdown

### 20.8 Property / Fuzz Tests

```python
# Using hypothesis library

@given(st.text(min_size=0, max_size=10000))
def test_converter_never_crashes(md: str):
    converter = MarkdownToNotionConverter(config=default_config())
    result = converter.convert(md)
    assert isinstance(result.blocks, list)

@given(st.text(min_size=1, max_size=5000))
def test_rich_text_split_no_loss(text: str):
    segments = split_rich_text([make_segment(text)], limit=2000)
    rejoined = "".join(s["text"]["content"] for s in segments)
    assert rejoined == text

@given(st.text(min_size=1, max_size=5000))
def test_rich_text_split_all_within_limit(text: str):
    segments = split_rich_text([make_segment(text)], limit=2000)
    for seg in segments:
        assert len(seg["text"]["content"]) <= 2000
```

### 20.9 Performance Benchmarks

| Benchmark | Input | Target |
|-----------|-------|--------|
| Convert 1000-block markdown (no images) | 1000 paragraphs | < 500ms |
| Export 1000-block page | 1000 blocks (mocked API) | < 2s |
| Async upload 20 images concurrently | 20 × 100KB PNG | < 5s |
| Diff plan 500-block identical page | 500 paragraphs | < 200ms |
| Diff execute 500-block page (10 changes) | 490 KEEP + 10 UPDATE | < 3s |

---

## 21. Documentation Deliverables

### 21.1 Quickstart (`docs/quickstart.md`)

```markdown
## Installation
pip install notionify

## Basic Usage
from notionify import NotionifyClient

client = NotionifyClient(token="secret_xxx")

# Create a page
result = client.create_page_with_markdown(
    parent_id="<your_page_id>",
    title="My First Page",
    markdown="# Hello\n\nThis is a **test**.",
)
print(result.page_id)

# Export it back
md = client.page_to_markdown(result.page_id)
print(md)
```

### 21.2 API Reference (`docs/api_reference.md`)

Sections:
- `NotionifyClient` constructor + all parameters
- Each public method: signature, parameters, return type, raises, example
- `AsyncNotionifyClient` (same structure)
- All result types with field descriptions
- All config fields with types, defaults, constraints

### 21.3 Conversion Compatibility Matrix (`docs/conversion_matrix.md`)

Full table:
- Markdown construct → Notion block (supported / lossy / unsupported)
- Notion block → Markdown (supported / lossy / unsupported)
- Notes on fallback behavior per construct

### 21.4 Error Handling Cookbook (`docs/error_cookbook.md`)

```markdown
## Handling Rate Limits
try:
    client.create_page_with_markdown(...)
except NotionifyRetryExhaustedError as e:
    print(f"Gave up after {e.context['attempts']} attempts")

## Handling Image Failures
client = NotionifyClient(token=..., image_fallback="placeholder")
# Failed images become "[image: path]" text blocks

## Handling Unsupported Blocks on Export
client = NotionifyClient(token=..., unsupported_block_policy="skip")
md = client.page_to_markdown(page_id)
# Unsupported blocks silently omitted

## Handling Diff Conflicts
try:
    client.update_page_from_markdown(..., strategy="diff")
except NotionifyDiffConflictError as e:
    # Retry with overwrite
    client.update_page_from_markdown(..., strategy="overwrite")
```

### 21.5 Migration Guide (`docs/migration_guide.md`)

```markdown
## From NotionMarkdownClient (v1.x) to NotionifyClient (v2+)

### Constructor
- Old: NotionMarkdownClient(token, math="equation")
- New: NotionifyClient(token, math_strategy="equation")

### Method renames
- write_markdown()        → create_page_with_markdown()
- update_markdown()       → update_page_from_markdown()
- read_markdown()         → page_to_markdown()

### Behavior changes
- Images: previously skipped silently, now configurable via image_fallback
- Math overflow: previously raised, now configurable via math_overflow_block
- Retry: now built-in, no need for manual retry wrappers
- Rate limit: automatic client-side pacing, no external throttle needed
```

### 21.6 FAQ (`docs/faq.md`)

| Question | Answer |
|----------|--------|
| Why do my Notion images expire? | Notion-hosted file URLs have a TTL. Use `image_expiry_warnings=True` to get markers. |
| Can I use a database as parent? | Yes, pass `parent_type="database"` and optional `properties`. |
| Does round-trip preserve everything? | Supported constructs are preserved. See conversion matrix for lossy cases. |
| What happens to H4/H5/H6? | Downgraded to H3 by default, or rendered as paragraph. |
| How do I use async? | `from notionify import AsyncNotionifyClient` and `await` all methods. |
| Can I plug in my own metrics? | Yes, pass a `MetricsHook` implementation to the constructor. |
| Is the token stored anywhere? | No. It is only used in Authorization headers and never logged. |
| What is the max page size? | No SDK-enforced limit. Large pages are auto-chunked into batches of 100. |

---

## 22. Full Jira Breakdown

### Epic Map

```
NTFY-E1   Core Infrastructure
NTFY-E2   Markdown → Notion Converter
NTFY-E3   Notion → Markdown Exporter
NTFY-E4   Diff Engine
NTFY-E5   Image Pipeline
NTFY-E6   Async Client
NTFY-E7   Reliability (Retry / Rate Limit)
NTFY-E8   Observability
NTFY-E9   Test Suite
NTFY-E10  Documentation & Release
```

---

### NTFY-E1 — Core Infrastructure

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-101 | Config & models | - Define `NotionifyConfig` dataclass<br>- Define all result dataclasses<br>- Define `ConversionWarning` |
| NTFY-102 | Error taxonomy | - Implement base `NotionifyError`<br>- Implement all derived error classes<br>- Implement `ErrorCode` enum<br>- Unit tests for error hierarchy |
| NTFY-103 | HTTP transport (sync) | - httpx sync client wrapper<br>- Auth header injection<br>- Notion-Version header injection<br>- Request/response logging |
| NTFY-104 | HTTP transport (async) | - httpx async client wrapper<br>- Same header injection<br>- Async logging |
| NTFY-105 | Pagination helper | - `paginate()` sync iterator<br>- `async_paginate()` async iterator<br>- Unit tests with mock responses |
| NTFY-106 | Chunking utility | - `chunk_children(blocks, size=100)`<br>- Unit tests for exact boundary |

---

### NTFY-E2 — Markdown → Notion Converter

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-201 | AST normalizer | - Mistune v3 parser setup<br>- Token alias → canonical map<br>- Normalizer unit tests |
| NTFY-202 | Rich text builder | - Build rich_text array from inline tokens<br>- Annotation combination logic<br>- Link href handling<br>- 2000-char split logic<br>- Multi-byte safe split<br>- Unit tests (all annotation combos) |
| NTFY-203 | Block builder — basic | - heading_1/2/3 + overflow<br>- paragraph<br>- quote<br>- divider<br>- Unit tests |
| NTFY-204 | Block builder — lists | - bulleted_list_item<br>- numbered_list_item<br>- to_do<br>- Nested list recursion<br>- Unit tests |
| NTFY-205 | Block builder — code | - code block<br>- language mapping<br>- 2000-char split for content<br>- Unit tests |
| NTFY-206 | Math strategy | - equation block + inline<br>- code strategy<br>- latex_text strategy<br>- Overflow dispatcher<br>- detect_latex_code read-back<br>- Unit tests (all strategies × overflow) |
| NTFY-207 | Table builder | - Parse table AST<br>- Build table + table_row blocks<br>- Cell rich_text handling<br>- enable_tables=False fallback<br>- Unit tests |
| NTFY-208 | Top-level pipeline | - `md_to_notion.py` orchestrator<br>- Wire all block builders<br>- Collect PendingImage list<br>- Collect ConversionWarning list<br>- Return `ConversionResult` |

---

### NTFY-E3 — Notion → Markdown Exporter

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-301 | Inline renderer | - Render rich_text array → string<br>- All annotation combos<br>- Equation segment rendering<br>- Markdown escaping rules<br>- Unit tests |
| NTFY-302 | Block renderers — basic | - heading, paragraph, quote<br>- divider, code<br>- equation block<br>- Unit tests |
| NTFY-303 | Block renderers — lists | - bulleted, numbered, to_do<br>- Nested indentation<br>- Counter tracking for ordered<br>- Unit tests |
| NTFY-304 | Block renderers — media | - image (external + file)<br>- Expiry warning annotation<br>- video, file, pdf, audio<br>- embed, bookmark, link_preview<br>- Unit tests |
| NTFY-305 | Block renderers — layout | - callout, toggle<br>- column_list, column<br>- synced_block, template<br>- child_page, child_database<br>- Unit tests |
| NTFY-306 | Table renderer | - table + table_row → GFM<br>- Header row detection<br>- Unit tests |
| NTFY-307 | Unsupported block handler | - comment / skip / raise policies<br>- Plain text extraction best-effort<br>- Unit tests |
| NTFY-308 | Export orchestrator | - `page_to_markdown()` impl<br>- `block_to_markdown()` impl<br>- Pagination in export<br>- Recursion + max_depth<br>- Integration tests |

---

### NTFY-E4 — Diff Engine

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-401 | Block signature | - Signature dataclass<br>- `compute_signature()` impl<br>- Hash stability tests<br>- Edge cases: empty rich_text, no children |
| NTFY-402 | LCS matcher | - LCS algorithm implementation<br>- Matched / unmatched pair extraction<br>- Performance test (500 blocks) |
| NTFY-403 | Diff planner | - Plan generation from LCS output<br>- KEEP/UPDATE/REPLACE/INSERT/DELETE classification<br>- min_match_ratio fallback<br>- Nested block planner recursion<br>- Unit tests (all op types) |
| NTFY-404 | Conflict detection | - `PageSnapshot` model<br>- `detect_conflict()` impl<br>- Integration with planner<br>- on_conflict behavior |
| NTFY-405 | Diff executor | - Execute ordered op list<br>- Batch consecutive INSERTs<br>- API call sequencing<br>- `UpdateResult` population<br>- Integration tests |

---

### NTFY-E5 — Image Pipeline

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-501 | Source detection | - `detect_image_source()` impl<br>- Unit tests (all 4 types) |
| NTFY-502 | Validation pipeline | - MIME detection (magic + mimetypes)<br>- MIME allowlist enforcement<br>- Size check<br>- Data URI decode + validate<br>- Unit tests (all error types) |
| NTFY-503 | Single-part upload | - POST /v1/file-uploads<br>- PUT upload_url<br>- Error handling<br>- Retry integration<br>- Unit tests (mocked HTTP) |
| NTFY-504 | Multi-part upload | - Chunking logic (5MB)<br>- Sequential part uploads<br>- ETag collection<br>- POST complete<br>- Error handling per part<br>- Unit tests |
| NTFY-505 | Upload state machine | - State enum + transitions<br>- `assert_can_attach()` guard<br>- Expiry detection<br>- Re-upload on expiry<br>- Unit tests |
| NTFY-506 | Image block attacher | - `build_image_block(upload_id)` impl<br>- External URL image block<br>- Fallback block builders<br>- Unit tests |
| NTFY-507 | Integration & async | - Wire into converter pipeline<br>- Async concurrent upload<br>- `image_max_concurrent` enforcement<br>- Integration tests |

---

### NTFY-E6 — Async Client

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-601 | AsyncNotionifyClient shell | - Class + constructor<br>- Config reuse from sync |
| NTFY-602 | Async write methods | - `create_page_with_markdown`<br>- `append_markdown`<br>- `overwrite_page_content`<br>- `update_page_from_markdown` |
| NTFY-603 | Async block methods | - `update_block`<br>- `delete_block`<br>- `insert_after` |
| NTFY-604 | Async export methods | - `page_to_markdown`<br>- `block_to_markdown` |
| NTFY-605 | Shared async rate limiter | - asyncio.Lock-based token bucket<br>- Shared across all tasks in session |

---

### NTFY-E7 — Reliability

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-701 | Token bucket (sync) | - `TokenBucket` class<br>- `acquire()` with wait<br>- Unit tests (burst, steady-state) |
| NTFY-702 | Token bucket (async) | - Async-safe variant<br>- asyncio.Lock integration |
| NTFY-703 | Retry logic | - `should_retry()` matrix<br>- `compute_backoff()` with jitter<br>- Retry-After header parsing<br>- Max attempts enforcement |
| NTFY-704 | Request lifecycle | - Wire rate limiter + retry into transport<br>- Integration test: 429 sequence<br>- Integration test: 500 sequence<br>- Integration test: exhaust retries |

---

### NTFY-E8 — Observability

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-801 | Structured logger | - Logger setup + format<br>- Operation start/end logs<br>- Warning logs<br>- Error logs |
| NTFY-802 | Metrics protocol | - `MetricsHook` Protocol definition<br>- NoopMetricsHook default impl<br>- Wire metric calls throughout codebase |
| NTFY-803 | Debug artifacts | - AST dump (debug_dump_ast)<br>- Payload dump + redact (debug_dump_payload)<br>- Diff plan dump (debug_dump_diff) |
| NTFY-804 | Redaction | - `redact()` function<br>- Token, bytes, base64 redaction rules<br>- Unit tests |

---

### NTFY-E9 — Test Suite

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-901 | Unit test infrastructure | - pytest setup<br>- conftest fixtures<br>- Mock factory helpers |
| NTFY-902 | Converter unit tests | - All U-CV-001 to U-CV-040 |
| NTFY-903 | Export unit tests | - All U-NM-001 to U-NM-022 |
| NTFY-904 | Rich text unit tests | - All U-RT-001 to U-RT-008 |
| NTFY-905 | Diff engine unit tests | - All U-DF-001 to U-DF-015 |
| NTFY-906 | Image pipeline unit tests | - All U-IM-001 to U-IM-017 |
| NTFY-907 | Integration test infrastructure | - VCR cassette setup<br>- Live test flag + skip markers<br>- Cassette recording workflow |
| NTFY-908 | Integration tests | - All I-001 to I-025 |
| NTFY-909 | Golden fixture tests | - All 17 fixtures × 3 files<br>- Snapshot approval workflow |
| NTFY-910 | Property / fuzz tests | - hypothesis setup<br>- 3 fuzz test cases |
| NTFY-911 | Performance benchmarks | - pytest-benchmark setup<br>- All 5 benchmark cases<br>- CI threshold enforcement |

---

### NTFY-E10 — Documentation & Release

| Story | Title | Subtasks |
|-------|-------|----------|
| NTFY-1001 | Quickstart | Write + review |
| NTFY-1002 | API reference | Auto-generate from docstrings + manual review |
| NTFY-1003 | Conversion matrix | Table authoring + accuracy review |
| NTFY-1004 | Error cookbook | Write all recipes |
| NTFY-1005 | Migration guide | Write v1→v2 migration steps |
| NTFY-1006 | FAQ | Compile from dev discussions |
| NTFY-1007 | PyPI packaging | - `pyproject.toml` setup<br>- Classifiers, license, dependencies<br>- Build + publish workflow |
| NTFY-1008 | CI/CD pipeline | - GitHub Actions: lint, type-check, test, bench<br>- pip-audit security scan<br>- Coverage gate (≥90%) |
| NTFY-1009 | Changelog | CHANGELOG.md for v3.0.0 |
| NTFY-1010 | Release checklist | Final gate review + tag + publish |

---

## 23. Complete Python Interface Definitions

```python
# ============================================================
# notionify/errors.py
# ============================================================

from __future__ import annotations


class NotionifyError(Exception):
    def __init__(self, code: str, message: str,
                 context: dict | None = None,
                 cause: Exception | None = None):
        self.code    = code
        self.message = message
        self.context = context or {}
        self.cause   = cause
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(code={self.code!r}, context={self.context!r})"


class NotionifyValidationError(NotionifyError): pass
class NotionifyAuthError(NotionifyError): pass
class NotionifyPermissionError(NotionifyError): pass
class NotionifyNotFoundError(NotionifyError): pass
class NotionifyRateLimitError(NotionifyError): pass
class NotionifyRetryExhaustedError(NotionifyError): pass
class NotionifyNetworkError(NotionifyError): pass

class NotionifyConversionError(NotionifyError): pass
class NotionifyUnsupportedBlockError(NotionifyConversionError): pass
class NotionifyTextOverflowError(NotionifyConversionError): pass
class NotionifyMathOverflowError(NotionifyConversionError): pass

class NotionifyImageError(NotionifyError): pass
class NotionifyImageNotFoundError(NotionifyImageError): pass
class NotionifyImageTypeError(NotionifyImageError): pass
class NotionifyImageSizeError(NotionifyImageError): pass
class NotionifyImageParseError(NotionifyImageError): pass

class NotionifyUploadError(NotionifyError): pass
class NotionifyUploadExpiredError(NotionifyUploadError): pass
class NotionifyUploadTransportError(NotionifyUploadError): pass

class NotionifyDiffConflictError(NotionifyError): pass


# ============================================================
# notionify/models.py
# ============================================================

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversionWarning:
    code:    str
    message: str
    context: dict = field(default_factory=dict)


@dataclass
class PageCreateResult:
    page_id:         str
    url:             str
    blocks_created:  int
    images_uploaded: int
    warnings:        list[ConversionWarning] = field(default_factory=list)


@dataclass
class AppendResult:
    blocks_appended: int
    images_uploaded: int
    warnings:        list[ConversionWarning] = field(default_factory=list)


@dataclass
class UpdateResult:
    strategy_used:   str
    blocks_kept:     int
    blocks_inserted: int
    blocks_deleted:  int
    blocks_replaced: int
    images_uploaded: int
    warnings:        list[ConversionWarning] = field(default_factory=list)


@dataclass
class BlockUpdateResult:
    block_id: str
    warnings: list[ConversionWarning] = field(default_factory=list)


@dataclass
class InsertResult:
    inserted_block_ids: list[str]
    warnings:           list[ConversionWarning] = field(default_factory=list)


@dataclass
class PendingImage:
    src:         str
    source_type: str                     # "external_url" | "local_file" | "data_uri"
    mime:        str | None = None
    data:        bytes | None = None
    upload_id:   str | None = None
    block_index: int = 0                 # position in blocks list


@dataclass
class ConversionResult:
    blocks:   list[dict]
    images:   list[PendingImage]
    warnings: list[ConversionWarning]


@dataclass
class PageSnapshot:
    page_id:      str
    last_edited:  str                    # ISO datetime string
    block_etags:  dict[str, str]         # block_id → last_edited_time


# ============================================================
# notionify/config.py
# ============================================================

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

DEFAULT_UPLOAD_MIMES  = ["image/jpeg", "image/png", "image/gif",
                          "image/webp", "image/svg+xml"]
DEFAULT_EXTERNAL_MIMES = ["image/jpeg", "image/png", "image/gif",
                           "image/webp", "image/svg+xml", "image/bmp"]


@dataclass
class NotionifyConfig:
    token:                        str
    notion_version:               str            = "2025-09-03"
    base_url:                     str            = "https://api.notion.com/v1"
    math_strategy:                str            = "equation"
    math_overflow_inline:         str            = "code"
    math_overflow_block:          str            = "code"
    detect_latex_code:            bool           = True
    image_upload:                 bool           = True
    image_max_concurrent:         int            = 4
    image_fallback:               str            = "skip"
    image_expiry_warnings:        bool           = True
    image_allowed_mimes_upload:   list[str]      = field(default_factory=lambda: list(DEFAULT_UPLOAD_MIMES))
    image_allowed_mimes_external: list[str]      = field(default_factory=lambda: list(DEFAULT_EXTERNAL_MIMES))
    image_max_size_bytes:         int            = 5 * 1024 * 1024
    image_verify_external:        bool           = False
    enable_tables:                bool           = True
    table_fallback:               str            = "comment"
    heading_overflow:             str            = "downgrade"
    unsupported_block_policy:     str            = "comment"
    retry_max_attempts:           int            = 5
    retry_base_delay:             float          = 1.0
    retry_max_delay:              float          = 60.0
    retry_jitter:                 bool           = True
    rate_limit_rps:               float          = 3.0
    timeout_seconds:              float          = 30.0
    http_proxy:                   str | None     = None
    metrics:                      Any | None     = None
    debug_dump_ast:               bool           = False
    debug_dump_payload:           bool           = False
    debug_dump_diff:              bool           = False


# ============================================================
# notionify/diff/signature.py
# ============================================================

from __future__ import annotations
import hashlib, json
from dataclasses import dataclass


@dataclass(frozen=True)
class BlockSignature:
    block_type:      str
    rich_text_hash:  str
    structural_hash: str
    attrs_hash:      str
    nesting_depth:   int

def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()

def compute_signature(block: dict, depth: int = 0) -> BlockSignature:
    btype     = block.get("type", "unknown")
    inner     = block.get(btype, {})
    rt        = inner.get("rich_text", [])
    plain     = "".join(seg.get("plain_text", "") for seg in rt)
    children  = block.get("children", [])
    child_sig = json.dumps([b.get("type") for b in children])
    attrs     = {k: v for k, v in inner.items()
                 if k not in ("rich_text", "children")}
    return BlockSignature(
        block_type      = btype,
        rich_text_hash  = md5(plain),
        structural_hash = md5(child_sig),
        attrs_hash      = md5(json.dumps(attrs, sort_keys=True)),
        nesting_depth   = depth,
    )


# ============================================================
# notionify/diff/planner.py
# ============================================================

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any
from .signature import BlockSignature, compute_signature
from .lcs_matcher import lcs_match


class DiffOpType(str, Enum):
    KEEP    = "keep"
    UPDATE  = "update"
    REPLACE = "replace"
    INSERT  = "insert"
    DELETE  = "delete"


@dataclass
class DiffOp:
    op_type:        DiffOpType
    existing_id:    str | None
    new_block:      dict | None
    position_after: str | None
    depth:          int


class DiffPlanner:
    MIN_MATCH_RATIO = 0.30

    def __init__(self, config: Any): ...

    def plan(
        self,
        existing: list[dict],
        new:      list[dict],
        depth:    int = 0,
    ) -> list[DiffOp]: ...

    def _match_ratio(
        self,
        existing: list[dict],
        new:      list[dict],
    ) -> float: ...

    def _plan_level(
        self,
        existing: list[dict],
        new:      list[dict],
        depth:    int,
    ) -> list[DiffOp]: ...


# ============================================================
# notionify/image/state.py
# ============================================================

from __future__ import annotations
from enum import Enum
from ..errors import NotionifyUploadExpiredError


class UploadState(str, Enum):
    PENDING   = "pending"
    UPLOADING = "uploading"
    UPLOADED  = "uploaded"
    ATTACHED  = "attached"
    FAILED    = "failed"
    EXPIRED   = "expired"


VALID_TRANSITIONS: dict[UploadState, set[UploadState]] = {
    UploadState.PENDING:   {UploadState.UPLOADING},
    UploadState.UPLOADING: {UploadState.UPLOADED, UploadState.FAILED},
    UploadState.UPLOADED:  {UploadState.ATTACHED, UploadState.EXPIRED},
    UploadState.EXPIRED:   {UploadState.UPLOADING},
    UploadState.ATTACHED:  set(),
    UploadState.FAILED:    set(),
}


class UploadStateMachine:
    def __init__(self, upload_id: str):
        self.upload_id = upload_id
        self.state     = UploadState.PENDING

    def transition(self, new_state: UploadState) -> None:
        if new_state not in VALID_TRANSITIONS[self.state]:
            raise ValueError(
                f"Invalid transition: {self.state} → {new_state}"
            )
        self.state = new_state

    def assert_can_attach(self) -> None:
        if self.state == UploadState.EXPIRED:
            raise NotionifyUploadExpiredError(
                code="UPLOAD_EXPIRED",
                message=f"Upload {self.upload_id} expired before attach.",
                context={"upload_id": self.upload_id},
            )
        if self.state != UploadState.UPLOADED:
            raise ValueError(
                f"Cannot attach in state: {self.state}"
            )


# ============================================================
# notionify/observability/metrics.py
# ============================================================

from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsHook(Protocol):
    def increment(self, name: str, value: int = 1,
                  tags: dict[str, str] | None = None) -> None: ...
    def timing(self, name: str, ms: float,
               tags: dict[str, str] | None = None) -> None: ...
    def gauge(self, name: str, value: float,
              tags: dict[str, str] | None = None) -> None: ...


class NoopMetricsHook:
    def increment(self, name, value=1, tags=None): pass
    def timing(self, name, ms, tags=None):         pass
    def gauge(self, name, value, tags=None):        pass


# ============================================================
# notionify/__init__.py
# ============================================================

from .client       import NotionifyClient
from .async_client import AsyncNotionifyClient
from .config       import NotionifyConfig
from .models       import (
    PageCreateResult, AppendResult, UpdateResult,
    BlockUpdateResult, InsertResult, ConversionWarning,
)
from .errors import (
    NotionifyError,
    NotionifyValidationError, NotionifyAuthError,
    NotionifyPermissionError, NotionifyNotFoundError,
    NotionifyRateLimitError, NotionifyRetryExhaustedError,
    NotionifyNetworkError,
    NotionifyConversionError, NotionifyUnsupportedBlockError,
    NotionifyTextOverflowError, NotionifyMathOverflowError,
    NotionifyImageError, NotionifyImageNotFoundError,
    NotionifyImageTypeError, NotionifyImageSizeError,
    NotionifyImageParseError,
    NotionifyUploadError, NotionifyUploadExpiredError,
    NotionifyUploadTransportError,
    NotionifyDiffConflictError,
)

__version__ = "3.0.0"
__all__ = [
    "NotionifyClient", "AsyncNotionifyClient", "NotionifyConfig",
    "PageCreateResult", "AppendResult", "UpdateResult",
    "BlockUpdateResult", "InsertResult", "ConversionWarning",
    # errors
    "NotionifyError", "NotionifyValidationError", "NotionifyAuthError",
    "NotionifyPermissionError", "NotionifyNotFoundError",
    "NotionifyRateLimitError", "NotionifyRetryExhaustedError",
    "NotionifyNetworkError",
    "NotionifyConversionError", "NotionifyUnsupportedBlockError",
    "NotionifyTextOverflowError", "NotionifyMathOverflowError",
    "NotionifyImageError", "NotionifyImageNotFoundError",
    "NotionifyImageTypeError", "NotionifyImageSizeError",
    "NotionifyImageParseError",
    "NotionifyUploadError", "NotionifyUploadExpiredError",
    "NotionifyUploadTransportError",
    "NotionifyDiffConflictError",
]
```

---

## 24. Development Schedule

### 24.1 Assumptions
- Team: 2 backend engineers (Eng-A, Eng-B)
- 1 person-day = 6 focused hours
- Sprint = 2 weeks (10 working days)

### 24.2 Full Schedule

| Sprint | Epic | Story IDs | Owner | Days |
|--------|------|-----------|-------|------|
| **S1** | E1 Core Infra | NTFY-101 Config + models | Eng-A | 1 |
| | E1 | NTFY-102 Error taxonomy | Eng-A | 1 |
| | E1 | NTFY-103 HTTP transport sync | Eng-B | 2 |
| | E1 | NTFY-104 HTTP transport async | Eng-B | 1 |
| | E1 | NTFY-105 Pagination helper | Eng-B | 1 |
| | E1 | NTFY-106 Chunking utility | Eng-A | 0.5 |
| | E7 | NTFY-701 Token bucket sync | Eng-A | 1 |
| | E7 | NTFY-702 Token bucket async | Eng-A | 0.5 |
| | E7 | NTFY-703 Retry logic | Eng-B | 1 |
| | E7 | NTFY-704 Request lifecycle wire | Eng-B | 1 |
| | | **S1 Total** | | **10 days** |
| **S2** | E2 | NTFY-201 AST normalizer | Eng-A | 1 |
| | E2 | NTFY-202 Rich text builder | Eng-A | 2 |
| | E2 | NTFY-203 Block builder basic | Eng-A | 1 |
| | E2 | NTFY-204 Block builder lists | Eng-A | 1.5 |
| | E2 | NTFY-205 Block builder code | Eng-B | 1 |
| | E2 | NTFY-206 Math strategy | Eng-B | 2 |
| | E2 | NTFY-207 Table builder | Eng-B | 1.5 |
| | | **S2 Total** | | **10 days** |
| **S3** | E2 | NTFY-208 Top-level MD pipeline | Eng-A | 1.5 |
| | E3 | NTFY-301 Inline renderer | Eng-A | 1.5 |
| | E3 | NTFY-302 Block renderers basic | Eng-A | 1 |
| | E3 | NTFY-303 Block renderers lists | Eng-A | 1 |
| | E3 | NTFY-304 Block renderers media | Eng-B | 1.5 |
| | E3 | NTFY-305 Block renderers layout | Eng-B | 1 |
| | E3 | NTFY-306 Table renderer | Eng-B | 1 |
| | E3 | NTFY-307 Unsupported block handler | Eng-B | 0.5 |
| | E3 | NTFY-308 Export orchestrator | Eng-B | 1 |
| | | **S3 Total** | | **10 days** |
| **S4** | E4 | NTFY-401 Block signature | Eng-A | 1 |
| | E4 | NTFY-402 LCS matcher | Eng-A | 2 |
| | E4 | NTFY-403 Diff planner | Eng-A | 2.5 |
| | E4 | NTFY-404 Conflict detection | Eng-B | 1 |
| | E4 | NTFY-405 Diff executor | Eng-B | 2 |
| | E8 | NTFY-801 Structured logger | Eng-B | 0.5 |
| | E8 | NTFY-802 Metrics protocol | Eng-B | 0.5 |
| | E8 | NTFY-803 Debug artifacts | Eng-A | 0.5 |
| | | **S4 Total** | | **10 days** |
| **S5** | E5 | NTFY-501 Source detection | Eng-A | 0.5 |
| | E5 | NTFY-502 Validation pipeline | Eng-A | 1.5 |
| | E5 | NTFY-503 Single-part upload | Eng-A | 1.5 |
| | E5 | NTFY-504 Multi-part upload | Eng-A | 2 |
| | E5 | NTFY-505 Upload state machine | Eng-B | 1 |
| | E5 | NTFY-506 Image block attacher | Eng-B | 0.5 |
| | E5 | NTFY-507 Integration + async | Eng-B | 2 |
| | E6 | NTFY-601 Async client shell | Eng-B | 0.5 |
| | E6 | NTFY-602 Async write methods | Eng-B | 0.5 |
| | | **S5 Total** | | **10 days** |
| **S6** | E6 | NTFY-603 Async block methods | Eng-A | 0.5 |
| | E6 | NTFY-604 Async export methods | Eng-A | 0.5 |
| | E6 | NTFY-605 Shared async rate limiter | Eng-A | 0.5 |
| | E8 | NTFY-804 Redaction | Eng-A | 0.5 |
| | E9 | NTFY-901 Unit test infra | Eng-B | 0.5 |
| | E9 | NTFY-902~906 All unit tests | Both | 4 |
| | E9 | NTFY-907 Integration infra | Eng-B | 1 |
| | E9 | NTFY-908 Integration tests | Both | 2 |
| | | **S6 Total** | | **9.5 days** |
| **S7** | E9 | NTFY-909 Golden fixture tests | Eng-A | 2 |
| | E9 | NTFY-910 Property/fuzz tests | Eng-A | 1 |
| | E9 | NTFY-911 Performance benchmarks | Eng-B | 1.5 |
| | E10 | NTFY-1001~1006 All docs | Both | 3 |
| | E10 | NTFY-1007 PyPI packaging | Eng-B | 1 |
| | E10 | NTFY-1008 CI/CD pipeline | Eng-B | 1.5 |
| | | **S7 Total** | | **10 days** |
| **S8** | E10 | NTFY-1009 Changelog | Eng-A | 0.5 |
| | | Bug fixes + hardening | Both | 5 |
| | | Final QA + acceptance review | Both | 3 |
| | E10 | NTFY-1010 Release | Eng-B | 1 |
| | | **S8 Total** | | **9.5 days** |

### 24.3 Summary

| Phase | Sprints | Calendar Weeks | Scope |
|-------|---------|---------------|-------|
| Foundation | S1 | 1–2 | Infra, transport, reliability |
| Conversion | S2–S3 | 3–6 | MD→Notion, Notion→MD |
| Diff + Images | S4–S5 | 7–10 | Diff engine, image pipeline |
| Async + Testing | S6–S7 | 11–14 | Async, all tests, docs |
| Release | S8 | 15–16 | Hardening, QA, publish |
| **Total** | **8 sprints** | **~16 weeks** | **Full scope** |

---

## 25. Risks and Mitigations

| # | Risk | Probability | Impact | Mitigation |
|---|------|-------------|--------|------------|
| R1 | Notion API breaking change | Medium | High | `notion_version` config; versioned integration tests; changelog monitoring |
| R2 | Round-trip fidelity gaps discovered late | Medium | Medium | Golden fixtures from day 1; approved lossy markers in snapshot |
| R3 | Upload expiry race condition | Low | High | Immediate attach after upload; state machine guards; re-upload on expiry |
| R4 | Diff false-match (identical text, different type) | Low | Medium | Signature includes block_type; type mismatch always REPLACE |
| R5 | Rate-limit storms in async bulk operations | Medium | Medium | Global shared limiter; max_concurrent cap; backoff |
| R6 | Mistune v3 API change | Low | High | Pin mistune version; parser isolated in ast_normalizer |
| R7 | Large page export timeout | Low | Medium | Paginated streaming; max_depth guard; configurable timeout |
| R8 | Data URI image memory pressure | Low | Medium | Size check before decode; streaming for multi-part |
| R9 | Token accidentally logged | Low | Critical | `redact()` function; mandatory review checklist; CI grep for token patterns |
| R10 | Scope creep (database modeling, OAuth) | High | Medium | Hard scope doc; out-of-scope list in spec; dedicated roadmap item for v4 |

---

## 26. Open Decisions

| # | Decision | Options | Recommendation | Deadline |
|---|----------|---------|----------------|----------|
| OD-1 | Overwrite: archive vs hard-delete old blocks | `archive=True` / `archive=False` | Archive (safer, recoverable) | Before S3 |
| OD-2 | Table fallback when `enable_tables=False` | paragraph / comment / raise | `comment` (informative) | Before S2 |
| OD-3 | Diff conflict default behavior | raise / overwrite | raise (explicit is better) | Before S4 |
| OD-4 | Legacy class alias lifetime | 1 minor / 2 minors / forever | 2 minor versions | Before release |
| OD-5 | Multi-part chunk size | 5MB / 10MB / configurable | 5MB default, configurable | Before S5 |
| OD-6 | min_match_ratio value | 0.2 / 0.3 / 0.5 | 0.3 (tune after benchmarks) | Before S4 |
| OD-7 | Export: include page title as H1 | yes / no / configurable | configurable, default=True | Before S3 |
| OD-8 | Async client: separate class vs unified | separate `AsyncNotionifyClient` / `asyncio.run()` wrapper | Separate class (cleaner API) | Before S6 |

---

## 27. Final Acceptance Checklist

### Functional
- [ ] All FR-1 through FR-7 requirements implemented and tested
- [ ] All P0 unit tests passing (U-CV, U-NM, U-RT, U-DF, U-IM)
- [ ] All integration tests passing (I-001 to I-025)
- [ ] All golden fixtures approved and locked
- [ ] Property/fuzz tests: no crashes on 10,000 random inputs
- [ ] Performance benchmarks within defined thresholds

### API & Types
- [ ] 100% public API type-annotated
- [ ] All result types documented
- [ ] Error taxonomy complete and consistent
- [ ] `__all__` in `__init__.py` is complete and correct
- [ ] No breaking changes from v2.x without migration guide entry

### Reliability
- [ ] 429 Retry-After respected in all paths
- [ ] Retry exhaustion raises `NotionifyRetryExhaustedError`
- [ ] Upload state machine guards attach on wrong state
- [ ] Diff conflict detection functional

### Security
- [ ] Token never appears in logs (grep check in CI)
- [ ] File bytes / base64 redacted in debug dumps
- [ ] `pip-audit` passes with no known vulnerabilities
- [ ] No directory traversal possible from image src

### Documentation
- [ ] Quickstart: install + first page + first export
- [ ] API reference: every public method documented
- [ ] Conversion matrix: accurate and reviewed
- [ ] Error cookbook: all major error classes covered
- [ ] Migration guide: v1→v2→v3 paths documented
- [ ] FAQ: ≥ 10 real questions answered

### Release
- [ ] `pyproject.toml` complete (name, version, classifiers, deps)
- [ ] CI pipeline green (lint + typecheck + test + bench + audit)
- [ ] Test coverage ≥ 90%
- [ ] CHANGELOG.md complete for v3.0.0
- [ ] PyPI package published and installable
- [ ] Git tag `v3.0.0` created

---

**End of notionify Full Engineering Specification v3.0.0**
