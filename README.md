# quant-mcp

只读 **MCP (Model Context Protocol)** 服务：将本地 [DuckDB](https://duckdb.org/) 量化数据库以标准化 Tool 暴露给大模型（如 Claude），供 AI 智能体做量化研究与数据检索。

本项目从 [spring](https://github.com/rshun/spring) 项目剥离而来，作为**独立的只读数据读取门面**，供多个不同项目共用。数据库文件由 spring 的 ETL 生产与维护，本服务只读取、不写入。

## 特性

- **只读保护**：内部固定 `read_only=True`，并在应用层拦截所有 DDL/DML/管理类语句；原始 SQL 一律被子查询包裹后强制加 `LIMIT`。
- **短连接**：每次请求 Connect-Per-Request，避免锁表与多线程死锁。
- **零反向依赖**：不依赖 spring 的任何 Python 代码，库路径由环境变量 `QUANT_DB_PATH` 注入。

## 安装（独立虚拟环境）

本项目使用**自己的虚拟环境**（`.venv`），与其它项目隔离。

Windows（PowerShell）：

```powershell
# 1. 在项目根目录创建虚拟环境
python -m venv .venv

# 2. 激活（仅本地开发/跑测试时需要；MCP 客户端不需要激活，见下文）
.venv\Scripts\Activate.ps1

# 3. 安装依赖
pip install -r requirements.txt
# 或以可编辑方式安装，并获得 `quant-mcp` 命令入口：
pip install -e .
```

Linux / macOS：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

服务通过环境变量读取库路径，**必须设置 `QUANT_DB_PATH`**：

| 环境变量 | 必填 | 默认 | 说明 |
|----------|------|------|------|
| `QUANT_DB_PATH` | ✅ | — | DuckDB 库文件的完整路径 |
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
$env:QUANT_DB_PATH="<PATH_TO>/quant.db"; python server.py
```

## 在客户端中接入

各消费项目在自己的 MCP 客户端配置（如 `.mcp.json`）中引用本服务，**多个项目连同一份代码即可**。

**关键**：`command` 必须指向 **venv 里的 python 解释器绝对路径**——MCP 客户端直接把 server 拉起为子进程，不经过 shell、不会激活 venv，写裸 `python` 会用到系统 Python 而缺少依赖。

```jsonc
{
  "mcpServers": {
    "quant-readonly": {
      // Windows: 指向 .venv 的解释器；Linux/macOS 用 .venv/bin/python
      "command": "<QUANT_MCP_DIR>/.venv/Scripts/python.exe",
      "args": ["<QUANT_MCP_DIR>/server.py"],
      "env": {
        "QUANT_DB_PATH": "<PATH_TO>/quant.db"
      }
    }
  }
}
```

> 若执行过 `pip install -e .`，也可以更简洁地把 `command` 直接指向入口脚本
> `<QUANT_MCP_DIR>/.venv/Scripts/quant-mcp.exe`（Linux/macOS 为 `.venv/bin/quant-mcp`），此时无需再写 `args`。

完整示例见 [examples/mcp.json](examples/mcp.json)。

> 开发/测试时建议将 `QUANT_DB_PATH` 指向**测试库**，避免只读进程占用生产库导致 spring 的 ETL 写锁冲突。

## 提供的 Tool

| Tool | 说明 |
|------|------|
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
