# 修改记录:
#   2026-07-25  Claude  新建：集中管理与 spring 数据库的读契约(表名/视图名/枚举/必需列)，
#                       作为跨仓库 schema 契约的单一出处，供 server.py 与契约测试共同引用
#   2026-09-12  Claude  STOCK_DAILY 契约补 pre_close/tradestatus(停牌日识别)；
#                       新增 DEPRECATED_TABLES / deprecation_note，声明不该被消费的对象
"""与 spring 数据库的只读契约(single source of truth)。

server.py 的所有 Tool 只依赖本文件声明的表 / 视图与列。spring 侧变更物理表结构时，
只要保证这些名称与 REQUIRED_COLUMNS 中的列(名称+含义)对外稳定，本服务即可零改动。
契约测试(tests/test_contract.py)会依据本文件断言库中结构是否满足契约。
"""

import re


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

# ---- 不应被消费的对象 ----
# 生产库里与契约对象并存的历史遗留：已废弃的数据源留痕表，以及人工备份表。
# 它们结构与正式表几乎一致、数据却是过期的，模型做自省式探索时一旦挑中就会静默
# 算错，故 list_tables 必须把它们与契约对象区分开。
DEPRECATED_TABLES: dict[str, str] = {
    "ADJ_FACTOR_RAW": (
        "已废弃(spring 2026-09-10)：baostock 复权源留痕表，只进不出、不再喂 "
        "ADJ_FACTOR 稠密表，仅供对账；取复权因子请用 ADJ_FACTOR"
    ),
}

# 人工备份表命名约定：<正式表名>_BAK_YYYYMMDD / _BACKUP_YYYYMMDD
_BACKUP_TABLE_RE = re.compile(r"_(BAK|BACKUP)_\d{8}$", re.IGNORECASE)
_BACKUP_TABLE_NOTE = "人工备份表(_BAK_/_BACKUP_ + 日期)，是某次变更前的快照，不是生产数据出口"


def deprecation_note(table_name: str) -> str | None:
    """返回该对象「不该被消费」的原因；契约对象与其他正常表返回 None。"""
    note = DEPRECATED_TABLES.get(table_name.upper())
    if note:
        return note
    if _BACKUP_TABLE_RE.search(table_name):
        return _BACKUP_TABLE_NOTE
    return None


# ---- 交易所枚举 ----
EXCHANGES = ("SH", "SZ", "BJ")

# ---- 每张表 / 视图的必需列(Tool 实际依赖的列;契约测试据此断言) ----
# 只列 Tool 会读到 / 过滤 / 排序用到的列;spring 可自由新增其他列。
REQUIRED_COLUMNS: dict[str, set[str]] = {
    TB_STOCK_INFO: {"code", "symbol", "exchange", "name"},
    TB_TRADE_CAL: {"cal_date", "is_open"},
    # pre_close / tradestatus 是识别停牌日的唯一依据：停牌行(tradestatus=0)的 close
    # 是前收结转的正数，不加这一列就无法与正常交易日区分(spring docs/bug1.md BUG-014)。
    TB_STOCK_DAILY: {
        "code", "date", "open", "high", "low", "close",
        "pre_close", "tradestatus", "volume", "amount",
    },
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
