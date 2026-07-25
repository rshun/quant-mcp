# 修改记录:
#   2026-07-25  Claude  新建：集中管理与 spring 数据库的读契约(表名/视图名/枚举/必需列)，
#                       作为跨仓库 schema 契约的单一出处，供 server.py 与契约测试共同引用
"""与 spring 数据库的只读契约(single source of truth)。

server.py 的所有 Tool 只依赖本文件声明的表 / 视图与列。spring 侧变更物理表结构时，
只要保证这些名称与 REQUIRED_COLUMNS 中的列(名称+含义)对外稳定，本服务即可零改动。
契约测试(tests/test_contract.py)会依据本文件断言库中结构是否满足契约。
"""

# ---- MCP 服务标识 ----
SERVER_NAME = "duckdb-quant-readonly"

# ---- 表 / 视图名(与 spring 数据库对齐) ----
TB_STOCK_INFO = "STOCK_INFO"
TB_TRADE_CAL = "TRADE_CAL"
TB_STOCK_DAILY = "STOCK_DAILY"
TB_DAILY_BASIC = "DAILY_BASIC"
TB_ADJ_FACTOR = "ADJ_FACTOR"
VW_SW_INDUSTRY = "STOCK_SW_INDUSTRY_VIEW"
TB_MARGIN_DETAIL = "MARGIN_DETAIL_DAILY"
TB_MARGIN_SUMMARY = "MARGIN_SUMMARY_DAILY"
TB_CAPITAL_DETAIL = "CAPITAL_DETAIL"

# 契约涉及的全部对象(供契约测试遍历)
TABLES = (
    TB_STOCK_INFO,
    TB_TRADE_CAL,
    TB_STOCK_DAILY,
    TB_DAILY_BASIC,
    TB_ADJ_FACTOR,
    VW_SW_INDUSTRY,
    TB_MARGIN_DETAIL,
    TB_MARGIN_SUMMARY,
    TB_CAPITAL_DETAIL,
)

# ---- 交易所枚举 ----
EXCHANGES = ("SH", "SZ", "BJ")

# ---- 每张表 / 视图的必需列(Tool 实际依赖的列;契约测试据此断言) ----
# 只列 Tool 会读到 / 过滤 / 排序用到的列;spring 可自由新增其他列。
REQUIRED_COLUMNS: dict[str, set[str]] = {
    TB_STOCK_INFO: {"code", "symbol", "exchange", "name"},
    TB_TRADE_CAL: {"cal_date", "is_open"},
    TB_STOCK_DAILY: {"code", "date", "open", "high", "low", "close", "volume", "amount"},
    TB_DAILY_BASIC: {"code", "trade_date"},
    TB_ADJ_FACTOR: {"code", "trade_date", "fore_factor", "back_factor", "adjust_factor"},
    VW_SW_INDUSTRY: {
        "symbol", "start_date", "sw_version",
        "sw_l1_code", "sw_l1_name",
        "sw_l2_code", "sw_l2_name",
        "sw_l3_code", "sw_l3_name",
        "industry_code", "update_time", "updated_at",
    },
    TB_MARGIN_DETAIL: {
        "trade_date", "exchange_code", "symbol", "code",
        "margin_buy_amount", "margin_repay_amount", "margin_balance",
        "short_sell_volume", "short_repay_volume",
        "short_balance_volume", "short_balance_amount", "margin_short_balance",
        "created_at", "updated_at",
    },
    TB_MARGIN_SUMMARY: {
        "trade_date", "exchange_code",
        "margin_buy_amount", "margin_repay_amount", "margin_balance",
        "short_sell_volume", "short_repay_volume",
        "short_balance_volume", "short_balance_amount", "margin_short_balance",
        "created_at", "updated_at",
    },
    TB_CAPITAL_DETAIL: {"code", "date", "category"},
}
