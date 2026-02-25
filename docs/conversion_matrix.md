# Conversion Compatibility Matrix

## Markdown to Notion

| Markdown Construct | Notion Block Type | Support | Notes |
|--------------------|-------------------|---------|-------|
| `# Heading 1` | `heading_1` | Full | |
| `## Heading 2` | `heading_2` | Full | |
| `### Heading 3` | `heading_3` | Full | |
| `#### Heading 4` | `heading_3` or `paragraph` | Lossy | Controlled by `heading_overflow` config |
| `##### Heading 5` | `heading_3` or `paragraph` | Lossy | Same as H4 |
| `###### Heading 6` | `heading_3` or `paragraph` | Lossy | Same as H4 |
| Paragraph | `paragraph` | Full | |
| `**bold**` | `bold` annotation | Full | |
| `*italic*` | `italic` annotation | Full | |
| `` `inline code` `` | `code` annotation | Full | |
| `~~strikethrough~~` | `strikethrough` annotation | Full | |
| `[text](url)` | `text` with `href` | Full | |
| `![alt](url)` | `image` block | Full | External URLs embedded directly |
| `![alt](local.png)` | `image` block (uploaded) | Full | Requires `image_upload=True` |
| `![alt](data:...)` | `image` block (uploaded) | Full | Data URIs decoded and uploaded |
| Unordered list (`- item`) | `bulleted_list_item` | Full | Nesting supported |
| Ordered list (`1. item`) | `numbered_list_item` | Full | Nesting supported |
| Task list (`- [x] item`) | `to_do` | Full | Checked/unchecked preserved |
| Blockquote (`> text`) | `quote` | Full | |
| Code block (fenced) | `code` | Full | Language preserved |
| Horizontal rule (`---`) | `divider` | Full | |
| Table | `table` + `table_row` | Full | Requires `enable_tables=True` |
| `$inline math$` | `equation` (inline) | Full | With `math_strategy="equation"` |
| `$$block math$$` | `equation` (block) | Full | With `math_strategy="equation"` |
| `$math$` (>1000 chars) | Depends on overflow config | Lossy | See `math_overflow_inline/block` |
| HTML tags | Unsupported | None | Stripped during parsing |
| Footnotes | Unsupported | None | Not supported by Notion |
| Definition lists | Unsupported | None | Rendered as paragraphs |

## Notion to Markdown

| Notion Block Type | Markdown Output | Support | Notes |
|-------------------|-----------------|---------|-------|
| `paragraph` | Paragraph text | Full | |
| `heading_1` | `# Heading` | Full | |
| `heading_2` | `## Heading` | Full | |
| `heading_3` | `### Heading` | Full | |
| `bulleted_list_item` | `- item` | Full | Nesting indented |
| `numbered_list_item` | `1. item` | Full | Nesting indented |
| `to_do` | `- [x] item` / `- [ ] item` | Full | |
| `toggle` | `- item` + indented children | Full | |
| `code` | Fenced code block | Full | Language preserved |
| `quote` | `> text` | Full | |
| `divider` | `---` | Full | |
| `equation` | `$$expression$$` | Full | |
| `table` | Markdown table | Full | Header row supported |
| `image` (external) | `![caption](url)` | Full | |
| `image` (file) | `![caption](url)` | Full | Expiry warning optional |
| `callout` | `> icon text` | Lossy | Icon + text merged |
| `child_page` | `[Page: title](url)` | Lossy | Link only, no content |
| `child_database` | `[Database: title](url)` | Lossy | Link only |
| `embed` | `[Embed](url)` | Lossy | URL preserved |
| `bookmark` | `[title](url)` + `> description` | Lossy | |
| `link_preview` | `[url](url)` | Lossy | |
| `video` | `[Video](url)` | Lossy | URL preserved |
| `file` | `[filename](url)` | Lossy | URL preserved |
| `pdf` | `[PDF](url)` | Lossy | URL preserved |
| `audio` | `[Audio](url)` | Lossy | URL preserved |
| `column_list` | Children concatenated | Lossy | Layout information lost |
| `column` | Children concatenated | Lossy | Layout information lost |
| `synced_block` | Children rendered | Full | |
| `template` | Children rendered | Full | |
| `breadcrumb` | Omitted | None | No Markdown equivalent |
| `table_of_contents` | Omitted | None | Auto-generated in Markdown tools |
| Unsupported types | Per `unsupported_block_policy` | Config | `"comment"`, `"skip"`, or `"raise"` |

## Inline Annotations

| Annotation | Markdown to Notion | Notion to Markdown |
|------------|--------------------|--------------------|
| Bold | `**text**` | `**text**` |
| Italic | `*text*` | `_text_` |
| Strikethrough | `~~text~~` | `~~text~~` |
| Inline code | `` `text` `` | `` `text` `` |
| Underline | N/A | `<u>text</u>` (HTML fallback) |
| Color | N/A | Not rendered (Notion-only) |
| Link | `[text](url)` | `[text](url)` |
| Inline equation | `$expr$` | `$expr$` |

## Round-Trip Fidelity

The following constructs are preserved through a full MD -> Notion -> MD round-trip:

- Headings (H1-H3)
- Paragraphs with inline formatting (bold, italic, code, strikethrough)
- Links
- Bulleted and numbered lists (including nesting)
- Task lists with checked/unchecked state
- Blockquotes
- Code blocks with language
- Horizontal rules
- Tables
- Images (external URLs)
- Math expressions (with `math_strategy="equation"`)

**Known lossy cases:**
- H4/H5/H6 headings are downgraded to H3
- Notion colors and underline annotations have no standard Markdown equivalent
- Column layout is flattened
- Callout icons are merged into blockquote text
