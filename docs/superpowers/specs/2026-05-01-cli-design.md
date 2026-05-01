# notionify CLI 设计

**日期**: 2026-05-01
**状态**: Draft (待实现)

## 目标

为 notionify SDK 提供一个调试用 CLI，覆盖库的核心能力：双向 markdown↔Notion 转换、API 访问、diff 同步。尽量少依赖：CLI 解析使用标准库 `argparse`，TOML 配置在 Python 3.11+ 使用标准库 `tomllib`，Python 3.10 使用条件依赖 `tomli`。

## 范围

### 子命令

| 命令 | 用途 | 需要 token |
|------|------|-----------|
| `push` | 把本地 markdown 作为新页面创建到指定 parent (page/database) | 是 |
| `sync` | 把本地 markdown 增量同步到已有 page（走 diff planner/executor） | 是 |
| `pull` | 把 Notion page 拉回来转成 markdown 写到本地或 stdout | 是 |
| `convert` | 纯本地：md → Notion blocks JSON，不调 API | 否 |
| `inspect` | 拉取 page / 子 blocks 的原始 JSON（调试 API/diff signature） | 是 |
| `diff` | 给定本地 md + remote page ID，仅打印 diff plan（等价于 `sync --dry-run`） | 是 |

### CLI 表面

```
notionify-cli push <markdown_file> --parent <id_or_url> [--title TITLE]
                   [--upload-remote-images] [--no-images] [--dry-run]
notionify-cli sync <markdown_file> --page <id_or_url>
                   [--upload-remote-images] [--no-images] [--dry-run]
notionify-cli pull <id_or_url> [--out FILE]
notionify-cli convert <markdown_file> [--out FILE]
notionify-cli inspect <id_or_url> [--children]
notionify-cli diff <markdown_file> --page <id_or_url>
```

**全局参数**（每个子命令均可用）:
- `--token TOKEN` — 覆盖 env / 配置文件
- `-c, --config PATH` — 显式指定配置文件
- `--profile NAME` — 选 profile section
- `-v` / `-vv` — verbose 等级
- `--json` — 机读输出

全局参数必须同时支持放在子命令名前或子命令名后，例如
`notionify-cli --json inspect <id>` 和 `notionify-cli inspect <id> --json`
等价。

### 入口

- `pyproject.toml` 注册 `[project.scripts] notionify-cli = "notionify.cli:main"`
- `python -m notionify.cli` 通过 `src/notionify/cli/__main__.py` 启用

## 架构

### 文件布局

```
src/notionify/cli/
├── __init__.py          # exports main()
├── __main__.py          # `python -m notionify.cli` 入口
├── main.py              # 顶层 argparse parser + 子命令 dispatch
├── config.py            # token/profile 解析
├── output.py            # 分级 logger + --json 渲染器
├── commands/
│   ├── __init__.py
│   ├── push.py
│   ├── sync.py
│   ├── pull.py
│   ├── convert.py
│   ├── inspect.py
│   └── diff.py
└── _common.py           # 共享：load markdown / parse_id / format_error
```

每个 command 文件暴露 `add_parser(subparsers, global_parser)`，`main.py` 只负责装配。需要 Notion token 的命令暴露 `run(args, reporter, config) -> int`；纯本地 `convert` 暴露 `run(args, reporter) -> int`，由 `main.py` 的 `_NO_CONFIG_COMMANDS` 分派。`global_parser` 通过 `parents=[global_parser]` 挂到顶层 parser 和每个 subparser；所有全局参数必须使用 `default=argparse.SUPPRESS`，再由 `main.py` 补默认值，避免 subparser 默认值覆盖子命令名前的全局参数。

### 配置加载

**优先级**（高 → 低）:

1. CLI flag `--token`
2. `-c/--config PATH` 指定的配置文件 + `--profile`
3. 环境变量 `NOTION_TOKEN`
4. `~/.notionify.toml` + `--profile`（默认配置文件）
5. `NOTION_DEFAULT_PARENT` env（仅 `default_parent` 使用，token 不走这条）

`-c PATH` 给定时不再回落到 `~/.notionify.toml`（显式优先于隐式）。

**配置文件格式**:

```toml
[default]
token = "secret_xxx"
default_parent = "abc123..."

[work]
token = "secret_yyy"
default_parent = "def456..."
```

**接口**:

```python
@dataclass(frozen=True)
class CLIConfig:
    token: str
    default_parent: str | None

def load_config(args) -> CLIConfig: ...   # 抛 ConfigError 友好报错
```

token 缺失时打印:
```
error: no Notion token found. Set NOTION_TOKEN, pass --token, or configure ~/.notionify.toml
```

**Python 3.10 注**: 标准库 `tomllib` 仅 3.11+。`pyproject.toml` 增加条件依赖 `tomli; python_version < "3.11"` 作为 fallback，所有 Python 版本均可读配置文件。

### ID 解析

`--parent` / `--page` / `<page_id>` 都接受裸 UUID（带或不带连字符）**或** Notion URL（浏览器粘贴的 `https://notion.so/...-<id>`）。统一由 `_common.parse_id()` 处理。

### 数据流

**push**:
```
md = read_markdown(args.file)
if args.no_images:
    md = strip_images(md)
if dry_run:
    conversion = MarkdownToNotionConverter(NotionifyConfig(token="dummy")).convert(md)
    reporter.result({"blocks": len(conversion.blocks), "outline": [...]})
else:
    client = NotionifyClient(
        token=config.token,
        remote_image_upload=args.upload_remote_images,
        image_base_dir=str(Path(args.file).resolve().parent),
    )
    page = client.create_page_with_markdown(
        parent_id=parent_id,
        title=title,
        markdown=md,
        parent_type=args.parent_type,
    )
    reporter.result({"page_id": page.page_id, "url": page.url})
```

**sync**:
```
md = read_markdown(args.file)
if args.no_images:
    md = strip_images(md)
client = NotionifyClient(
    token=config.token,
    remote_image_upload=args.upload_remote_images,
    image_base_dir=str(Path(args.file).resolve().parent),
)
if dry_run:
    plan = client.plan_page_update(page_id, md)
    reporter.result({"total_ops": len(plan.ops), "by_op": {...}})
else:
    result = client.update_page_from_markdown(page_id, md)
    reporter.result({
        "strategy_used": result.strategy_used,
        "blocks_inserted": result.blocks_inserted,
        "blocks_deleted": result.blocks_deleted,
        "blocks_replaced": result.blocks_replaced,
    })
```

**pull**: `client.page_to_markdown(page_id, recursive=True)` → stdout 或 `--out`。

**convert**: 直接调 converter（无需 client/token），输出 Notion blocks JSON。

**inspect**: 调试命令，故意使用私有 SDK escape hatch：`client._pages.retrieve(page_id)` + 可选 `client._blocks.get_children(page_id)`，输出 JSON。若 SDK 以后暴露 public inspection API，再切换过去。

**diff**: 等价 `sync --dry-run`。

**SDK 补丁**: 增加 `NotionifyClient.plan_page_update(page_id, markdown)`，返回 `PlanResult(ops, warnings, images_to_upload)`，不执行 diff、不上传图片。

### 输出与日志

**Reporter** 接口:

```python
class Reporter:
    def __init__(self, verbosity: int, json_mode: bool): ...
    def step(self, msg): ...        # -v
    def detail(self, obj): ...      # -vv
    def warn(self, msg): ...        # always to stderr
    def result(self, payload: dict[str, Any]): ...  # final result to stdout
    def fail(self, err: BaseException, *, exit_code: int = 1) -> int: ...
```

**约定**:
- 进度/日志 → **stderr**；最终结果（`pull` 的 markdown、`convert` 的 JSON、`result()` 的 payload）→ **stdout**
- `-vv` 挂上库的 observability hook，透传 transport request/response

**退出码**:

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 一般错误 |
| 2 | 配置错误（无 token、坏 profile） |
| 3 | 网络/API 错误（`NotionifyError` 的 API/transport 子类，如 auth/network/retry/rate-limit/not-found） |
| 4 | 转换错误（NotionifyConversionError） |

**错误格式化**（`_common.format_error`）:
- `NotionifyError` → `code` + `message` + 可选 context
- `NotionifyNetworkError` → underlying cause
- `NotionifyConversionError` → 位置（行号若有）
- `--json` 模式: `{"ok": false, "error_type": "...", "message": "...", "code": "..."}`

### 图片处理默认值

- 默认: 本地文件 → 上传；远程 URL → `external`（不下载）
- `--upload-remote-images`: 开启远程 URL 下载并上传
- `--no-images`: 跳过所有图片处理（纯文本调试，最快）

## 测试策略

按项目规范（80%+ 覆盖、TDD）。

### 1. 单元测试 (`tests/unit/cli/`)

- `test_config.py` — 优先级矩阵（env / `-c` / `--profile` / `--token` 各种组合，含 token 缺失报错）
- `test_common.py` — `parse_id()` 喂裸 UUID、Notion URL、带连字符 UUID、坏值
- `test_output.py` — Reporter 在 `-v`/`-vv`/`--json` 各组合下的输出（capsys）
- 每个 command 一个文件：用 `monkeypatch` 把 `NotionifyClient` 替换成 mock，断言调用参数、stdout/stderr、退出码

### 2. 集成测试 (`tests/integration/cli/`)

- 标 `@pytest.mark.integration`，无 `NOTION_TOKEN` 时 skip
- 每个子命令一条端到端冒烟（push → sync → pull → cleanup）

### 3. CLI invocation 测试

- `subprocess.run(["python", "-m", "notionify.cli", ...])` 跑一条 happy path
- 用 `importlib.metadata` 验证 `notionify-cli` 脚本注册

### Mock 策略

- 不 mock httpx 层（库内部已有 respx 测试）
- CLI 测试只 mock `NotionifyClient` / `AsyncNotionifyClient` 的公开方法
- `convert` 命令不 mock（纯本地，直接断言输出 JSON 结构）

### TDD 顺序

1. `parse_id` + `config` 优先级
2. `Reporter`
3. `convert`（最简，无 client）
4. `inspect`（最简 client 调用）
5. `pull`
6. `push`
7. `sync`（含 dry-run）
8. `diff`

## 非目标

- 不做交互式 REPL
- 不做配置加密 / keyring 集成（环境变量足够调试场景）
- 不做 shell 自动补全（YAGNI；可后续加）
- 不做并发批量操作（push/sync 多个文件）

## 风险与注意

- Python 3.10 无 `tomllib` → 加 `tomli` 作为 conditional dep
- SDK 补一个 `plan_page_update()` 公共方法（diff dry-run 用）
- 退出码与 `--json` 错误结构成为对外契约，后续变更需谨慎
- `inspect` 故意使用 `client._pages` / `client._blocks`（私有 API）作为调试 escape hatch
