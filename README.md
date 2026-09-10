# quant-mcp

只读 **MCP (Model Context Protocol)** 服务：将本地 [DuckDB](https://duckdb.org/) 量化数据库以标准化 Tool 暴露给大模型（如 Claude），供 AI 智能体做量化研究与数据检索。

本项目从 [spring](https://github.com/rshun/spring) 项目剥离而来，作为**独立的只读数据读取门面**，供多个不同项目共用。数据库文件由 spring 的 ETL 生产与维护，本服务只读取、不写入。

## 特性

- **只读保护**：内部固定 `read_only=True`，并在应用层拦截所有 DDL/DML/管理类语句；原始 SQL 一律被子查询包裹后强制加 `LIMIT`。
- **短连接**：每次请求 Connect-Per-Request，避免锁表与多线程死锁。
- **显式跨仓库契约**：不依赖 spring 的 Python 代码；数据库与 ETL pipeline 路径分别由 `QUANT_DB_PATH`、`SPRING_PIPELINE_PATH` 注入。

## 安装（独立虚拟环境）

本项目使用**自己的虚拟环境**（`.venv`），与其它项目隔离。

Windows（PowerShell）：

```powershell
# 1. 在项目根目录创建虚拟环境
python -m venv .venv

# 2. 激活（仅本地开发/跑测试时需要；MCP 客户端不需要激活，见下文）
.venv\Scripts\Activate.ps1

# 3. 以可编辑方式安装，并生成 `quant-mcp` 命令入口
pip install -e .
```

Linux / macOS：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 配置

服务通过环境变量读取外部路径：

| 环境变量 | 必填 | 默认 | 说明 |
|----------|------|------|------|
| `QUANT_DB_PATH` | ✅ | — | DuckDB 库文件的完整路径 |
| `SPRING_PIPELINE_PATH` | 调用 `get_etl_pipeline` 时必填 | — | Spring `config/pipeline.yaml` 的完整路径；不猜测兄弟仓库位置 |
| `MAX_ROWS` | | `2000` | 各 Tool `max_rows` 参数的**默认值兼上限**；调用方传更大的值会被收敛回该上限。自身受 20000 硬顶保护 |
| `MAX_DAYS` | | `800` | 行情类 Tool（`get_stock_daily` / `get_daily_basic` / `get_adj_factor` / `get_margin_*` / `calc_indicators`）的日期跨度上限（天） |
| `ALLOW_RAW_QUERY` | | `0` | 是否开放 `query` 原始 SQL 工具（`1` 开启） |
| `LOG_LEVEL` | | `INFO` | 日志级别 |

> `get_trade_days`（交易日历，上限 5000 天）和 `get_capital_detail`（股本变动按全部历史查询，上限 20000 天）语义上就需要长跨度，不受 `MAX_DAYS` 约束。
>
> 返回体中的 `truncated` 为 `true` 表示结果被截断（无论截断发生在 SQL 的 `LIMIT` 还是返回前的行数收敛），后面可能还有数据。

## 运行

激活 venv 后直接运行（stdio 传输）：

```powershell
$env:QUANT_DB_PATH="<PATH_TO>/quant.db"
$env:SPRING_PIPELINE_PATH="<SPRING_DIR>/config/pipeline.yaml"
quant-mcp
```

## 在客户端中接入

各消费项目在自己的 MCP 客户端配置（如 `.mcp.json`）中引用本服务，**多个项目连同一份代码即可**。

**关键**：`command` 必须指向安装项目后在 venv 中生成的 `quant-mcp` 入口。MCP 客户端直接拉起子进程，不经过 shell、不会激活 venv。

```jsonc
{
  "mcpServers": {
    "quant-readonly": {
      // Windows 路径；Linux/macOS 使用 <QUANT_MCP_DIR>/.venv/bin/quant-mcp
      "command": "<QUANT_MCP_DIR>/.venv/Scripts/quant-mcp.exe",
      "args": [],
      "env": {
        "QUANT_DB_PATH": "<PATH_TO>/quant.db",
        "SPRING_PIPELINE_PATH": "<SPRING_DIR>/config/pipeline.yaml"
      }
    }
  }
}
```

完整示例见 [examples/mcp.json](examples/mcp.json)。

> 开发/测试时建议将 `QUANT_DB_PATH` 指向**测试库**，避免只读进程占用生产库导致 spring 的 ETL 写锁冲突。

## MCP 客户端迁移手册

本节用于从旧的根目录脚本启动方式迁移到 `src/quant_mcp` 包入口。此次迁移**不保留兼容入口**，仍指向 `<QUANT_MCP_DIR>/server.py` 的客户端会启动失败。

### 1. 在每台客户端主机上更新安装

修改客户端配置前，先备份对应配置文件。然后使用该客户端实际调用的虚拟环境执行 editable install：

```powershell
# Windows PowerShell
<VENV_DIR>\Scripts\python.exe -m pip install -e <QUANT_MCP_DIR>

# 确认新入口已经生成
Test-Path -LiteralPath "<VENV_DIR>\Scripts\quant-mcp.exe"

# 确认导入的是迁移后的包
<VENV_DIR>\Scripts\python.exe -c "import quant_mcp; print(quant_mcp.__file__)"
```

```bash
# Linux / macOS
<VENV_DIR>/bin/python -m pip install -e <QUANT_MCP_DIR>
test -x <VENV_DIR>/bin/quant-mcp
<VENV_DIR>/bin/python -c 'import quant_mcp; print(quant_mcp.__file__)'
```

预期导入路径包含 `src/quant_mcp/__init__.py`。不要直接运行 `quant-mcp` 来做存活测试：stdio MCP server 正常启动后会等待客户端输入，终端看起来会一直阻塞。

所有客户端都执行同一组替换：

| 项目 | 旧值 | 新值 |
|------|------|------|
| `command` | `<VENV_DIR>/Scripts/python.exe` | `<VENV_DIR>/Scripts/quant-mcp.exe` |
| `args` | `["<QUANT_MCP_DIR>/server.py"]` | `[]` |
| `QUANT_DB_PATH` | 保持原值 | 保持原值 |
| `SPRING_PIPELINE_PATH` | 无 | `<SPRING_DIR>/config/pipeline.yaml`；仅 `get_etl_pipeline` Tool 需要 |

Windows JSON/TOML 中建议使用 `/`，或把 `\` 写成 `\\`，避免路径转义错误。

### 2. Codex Desktop / CLI / IDE

Codex Desktop、CLI 和 IDE extension 在同一 Codex 主机上共享 MCP 配置。默认用户配置位于 `~/.codex/config.toml`，可信项目也可以使用项目内 `.codex/config.toml`。先备份实际生效的配置文件，再把对应 server 改为：

```toml
[mcp_servers.quant-readonly]
command = "<VENV_DIR>/Scripts/quant-mcp.exe"
args = []

[mcp_servers.quant-readonly.env]
QUANT_DB_PATH = "<PATH_TO>/quant.db"
SPRING_PIPELINE_PATH = "<SPRING_DIR>/config/pipeline.yaml"
MAX_ROWS = "2000"
MAX_DAYS = "800"
ALLOW_RAW_QUERY = "0"
LOG_LEVEL = "INFO"
```

Linux/macOS 将 `command` 改为 `<VENV_DIR>/bin/quant-mcp`。更新后执行：

```powershell
codex mcp get quant-readonly --json
codex mcp list --json
```

然后完全重启 Codex Desktop，或在 IDE 中执行 Restart extension。CLI/TUI 新开会话后可用 `/mcp` 确认连接状态。Codex 官方说明见 [Model Context Protocol](https://developers.openai.com/codex/mcp)。

### 3. Claude Code

如果多个项目都要使用同一服务，建议配置为 `user` scope。先执行 `claude mcp get quant-readonly` 确认旧配置所在 scope，并备份 `~/.claude.json`；随后针对**同一个 scope**移除旧定义并重新添加：

```powershell
# 示例使用 user scope；如果 get 显示 local/project，请使用对应 scope
claude mcp remove quant-readonly --scope user

claude mcp add-json quant-readonly '{"type":"stdio","command":"<VENV_DIR>/Scripts/quant-mcp.exe","args":[],"env":{"QUANT_DB_PATH":"<PATH_TO>/quant.db","SPRING_PIPELINE_PATH":"<SPRING_DIR>/config/pipeline.yaml","MAX_ROWS":"2000","MAX_DAYS":"800","ALLOW_RAW_QUERY":"0","LOG_LEVEL":"INFO"}}' --scope user

claude mcp get quant-readonly
claude mcp list
```

Claude Code 的配置优先级是 `local` > `project` > `user`。如果 `user` scope 已更新但仍启动旧脚本，应检查并更新或移除同名的 local/project 配置。

若团队通过项目根目录 `.mcp.json` 共享配置，则将其中条目改为：

```jsonc
{
  "mcpServers": {
    "quant-readonly": {
      "type": "stdio",
      "command": "<VENV_DIR>/Scripts/quant-mcp.exe",
      "args": [],
      "env": {
        "QUANT_DB_PATH": "<PATH_TO>/quant.db",
        "SPRING_PIPELINE_PATH": "<SPRING_DIR>/config/pipeline.yaml",
        "MAX_ROWS": "2000",
        "MAX_DAYS": "800",
        "ALLOW_RAW_QUERY": "0",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

首次加载项目级 `.mcp.json` 时，Claude Code 会要求确认该 server。启动 Claude Code 后使用 `/mcp` 检查并批准。官方说明见 [Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp)。

### 4. 其他使用 `mcpServers` JSON 的客户端

对于 Claude Desktop、Cursor、Windsurf 等采用常见 `mcpServers` JSON 结构的客户端，使用 [examples/mcp.json](examples/mcp.json) 中的条目，核心要求仍是：`command` 直接指向 `quant-mcp` 可执行入口、`args` 为空。修改后完全退出并重启客户端。

### 5. 验收与故障定位

逐个客户端迁移，确认一个成功后再处理下一个：

1. 客户端状态中 `quant-readonly` 显示 connected/enabled。
2. 调用 `list_tables`，确认返回 DuckDB 表和视图。
3. 调用一个小范围只读查询，例如 `get_trade_days`。
4. 若配置了 `SPRING_PIPELINE_PATH`，调用 `get_etl_pipeline`，确认能返回 Spring 的程序依赖。

常见错误：

| 现象 | 原因与处理 |
|------|------------|
| 找不到 `quant-mcp.exe` | 尚未在客户端所用 venv 中执行 `pip install -e <QUANT_MCP_DIR>` |
| `ModuleNotFoundError: quant_mcp` | 客户端使用了另一个 Python/venv，或 editable install 仍指向旧 checkout |
| 仍尝试打开根目录 `server.py` | 存在未更新的同名配置；Claude Code 优先检查 local/project scope，Codex 检查用户与项目 `config.toml` |
| `QUANT_DB_PATH 未设置` | 环境变量没有写在该 MCP server 的 `env` 中 |
| `Spring pipeline 配置不存在` | `SPRING_PIPELINE_PATH` 仍指向旧的 `etl/pipeline.yaml`，应改为 `config/pipeline.yaml` |

客户端配置和服务端代码必须一起迁移。若需要回滚，必须同时恢复旧 Git 版本和迁移前备份的客户端配置，不能只恢复其中一边。

## 提供的 Tool

| Tool | 说明 |
|------|------|
| `get_etl_pipeline` | 从 `SPRING_PIPELINE_PATH` 读取并校验 Spring ETL 程序依赖声明 |
| `list_tables` / `describe_table` | 列出表与视图、查看表结构（运行时自省，自动适应 schema） |
| `search_stock` / `get_stock_info` | 按关键字/代码检索股票基础信息（`get_stock_info` 返回 `found` 标志区分「查不到」与「字段为空」） |
| `get_trade_days` | 交易日历 |
| `get_stock_daily` | 日线行情 |
| `get_daily_basic` | 每日基础指标（换手率、市值、is_st 等） |
| `calc_indicators` | 基于日线计算收益率 / MA / 量能均线 |
| `get_adj_factor` | 复权因子 |
| `get_stock_industry` / `get_stock_industry_history` | 申万行业分类（当前 / 历史） |
| `get_margin_detail` / `get_margin_summary` | 融资融券明细 / 交易所汇总 |
| `get_capital_detail` | 股本变动 / 权息资料（GBBQ） |
| `query` | 只读原始 SQL（默认关闭，`ALLOW_RAW_QUERY=1` 开启） |

## Schema 契约

本服务的 Tool 依赖 spring 数据库中的表 / 视图（如 `STOCK_INFO`、`STOCK_DAILY`、`ADJ_FACTOR`、`STOCK_SW_INDUSTRY_VIEW` 等）。这些结构由 spring 生产，二者构成**跨仓库读契约**：spring 侧变更表结构时，需保证契约视图对外输出（列名与含义）稳定，否则本服务对应 Tool 会失效。契约的固化与测试在 spring 项目侧维护。

## License

[MIT](LICENSE)
