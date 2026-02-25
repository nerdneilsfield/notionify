# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-02-25

### Added

- **Bidirectional conversion** — full Markdown-to-Notion and Notion-to-Markdown
  pipelines with rich text annotations, tables, math, code, images, and more.
- **Diff engine** — LCS-based incremental page updates with
  `KEEP`/`UPDATE`/`REPLACE`/`INSERT`/`DELETE` operations and configurable
  `min_match_ratio` fallback.
- **Conflict detection** — `detect_conflict()` compares `PageSnapshot` objects
  to detect concurrent modifications before applying diffs. Configurable
  `on_conflict` policy (`"raise"` or `"overwrite"`).
- **Image upload pipeline** — local file and data-URI images are validated,
  uploaded (single-part or multi-part), and attached to blocks automatically.
  Configurable `image_fallback` policy (`"raise"`, `"placeholder"`, `"skip"`).
- **Async client** — `AsyncNotionifyClient` mirrors all sync client methods as
  coroutines, with shared async rate limiting and concurrent image uploads.
- **Structured error taxonomy** — `NotionifyError` base class with typed
  subclasses (`AuthError`, `RateLimitError`, `DiffConflictError`, etc.) and
  machine-readable `ErrorCode` enum.
- **Rate limiting & retry** — token-bucket rate limiter (sync and async),
  exponential backoff with jitter, `Retry-After` header support.
- **Observability** — `MetricsHook` protocol, structured logging, debug
  artifact dumps (`debug_dump_ast`, `debug_dump_payload`, `debug_dump_diff`),
  and `redact()` for token/bytes/base64 safety.
- **Configuration** — `NotionifyConfig` dataclass with all tuneable knobs:
  Markdown strategies, image settings, retry parameters, debug flags.
- **Security** — token redaction in all log paths, directory traversal
  prevention for local image paths, MIME allowlist enforcement.
- **Test suite** — 1100+ unit tests, golden fixture tests, property/fuzz
  tests, performance benchmarks, 100% line coverage.
- **CI pipeline** — GitHub Actions with lint (ruff), type-check (mypy),
  tests with coverage gate (>=90%), security audit (pip-audit), and
  performance benchmarks.

### Changed

- Complete rewrite from v2.x with a new architecture based on dispatch
  tables, AST normalisation, and a modular pipeline design.
- Uses `httpx` for HTTP transport (replacing `requests`).
- Uses `mistune` v3 for Markdown parsing.

### Migration from v2.x

- `NotionifyClient` constructor now requires `token` as first positional arg.
- Image handling is automatic by default; set `image_upload=False` to disable.
- The diff engine is used by default in `update_page_from_markdown`;
  pass `strategy="overwrite"` for the old full-replace behaviour.
