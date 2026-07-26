# 修改记录:
#   2026-07-25  Claude  从 spring 项目 mcp_server/server.py 剥离为独立仓库 quant-mcp；
#                       去除对 spring util.myutil 的依赖，改由环境变量 QUANT_DB_PATH 注入库路径
#   2026-07-25  Claude  表名/视图名/交易所枚举/服务名统一改为引用 schema.py，消除散落的写死字面量
#   2026-07-25  Claude  修复 df_to_payload 对 datetime/Timestamp/NaT 列的 JSON 序列化失败
#                       (search_stock/get_stock_info 等对含 datetime 列的表报错)
#   2026-07-26  Claude  代码走查修复 7 项:
#                       1) add_limit_if_missing -> enforce_limit，改为子查询包裹，避免行注释吞掉 LIMIT
#                       2) list_tables 纳入 VIEW，否则行业视图对自省不可见
#                       3) df_to_payload 增加 sql_limit，SQL 层截断也标记 truncated
#                       4) df_to_payload 补齐 INTERVAL/BLOB/LIST/UUID 的 JSON 安全转换
#                       5) parse_code 返回规范化 code，各 Tool 用它入 SQL(原先带空白会查空)
#                       6) MAX_ROWS/MAX_DAYS 环境变量真正生效，与 README 对齐
#                       7) get_stock_info 返回 found 标志，不再静默降级
import os
import sys
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

# Fix Windows GBK encoding issues
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import duckdb
import pandas as pd

import schema

try:
    from mcp.server.fastmcp import FastMCP
except Exception as e:
    raise RuntimeError(
        "Cannot import FastMCP. Please ensure MCP Python SDK is installed and import path is correct."
    ) from e

# 库路径由环境变量注入，不再反向依赖 spring 项目代码。
# 客户端配置示例(.mcp.json):
#   "env": { "QUANT_DB_PATH": "<PATH_TO>/quant.db" }
DB_PATH = os.environ.get("QUANT_DB_PATH", "").strip()
if not DB_PATH:
    raise RuntimeError(
        "环境变量 QUANT_DB_PATH 未设置：请指定 DuckDB 库文件的完整路径。"
    )

# Safety / stability guards
# MAX_ROWS 既是各 Tool 返回行数的默认值，也是其上限(与 README 表述一致)；
# ABS_MAX_ROWS 是绝对硬顶，防止 MAX_ROWS 被配得过大而拖垮进程。
ABS_MAX_ROWS = 20000
MAX_ROWS_DEFAULT = max(1, min(int(os.environ.get("MAX_ROWS", "2000")), ABS_MAX_ROWS))
MAX_DAYS_DEFAULT = int(os.environ.get("MAX_DAYS", "800"))  # 行情类 Tool 的日期跨度上限
# 两个不受 MAX_DAYS 约束的例外——语义上就需要长跨度，故单独取名而非散落的字面量：
TRADE_CAL_MAX_DAYS = 5000   # 交易日历是低频小表，按年查很常见
CAPITAL_MAX_DAYS = 20000    # 股本变动按「全部历史」查询
ALLOW_RAW_QUERY = os.environ.get("ALLOW_RAW_QUERY", "0").strip() == "1"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# DuckDB connection:
# Removed global CON and LOCK to implement short connections (connect-per-request).

mcp = FastMCP(schema.SERVER_NAME)

# -----------------------------
# Helpers
# -----------------------------
_EXCH_ALT = "|".join(schema.EXCHANGES)  # SH|SZ|BJ
_CODE_RE = re.compile(rf"^\s*(\d{{6}})\.({_EXCH_ALT})\s*$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DANGEROUS_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|COPY|EXPORT|IMPORT|PRAGMA|CALL|SET|LOAD|INSTALL)\b",
    re.IGNORECASE,
)

_DANGEROUS_TABLE_FUNCTION_RE = re.compile(
    r"\b(read_[A-Za-z0-9_]*|glob|filename|parquet_scan|csv_scan|sqlite_scan|postgres_scan|httpfs)\s*\(",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    # stderr logging is best for stdio MCP servers; but keep it minimal
    if LOG_LEVEL in ("DEBUG", "INFO"):
        print(f"[{LOG_LEVEL}] {msg}", file=sys.stderr)


def parse_code(code: str) -> tuple[str, str]:
    """
    "300085.SZ" -> ("300085","SZ")
    """
    m = _CODE_RE.match(code or "")
    if not m:
        raise ValueError("code must be like '300085.SZ' (6 digits + .SZ/.SH/.BJ)")
    symbol = m.group(1)
    exch = m.group(2).upper()
    return symbol, exch


def normalize_code(code: str) -> str:
    """
    " 300085.sz " -> "300085.SZ"

    _CODE_RE 容忍首尾空白与小写后缀，但 SQL 里 code 是按字面量比较的，
    必须用规范化后的值入参，否则校验通过却静默查空。
    """
    symbol, exch = parse_code(code)
    return f"{symbol}.{exch}"


def clamp_rows(n: int) -> int:
    """把请求行数收敛到 [1, MAX_ROWS_DEFAULT]。"""
    return max(1, min(int(n), MAX_ROWS_DEFAULT))


def validate_date(d: str) -> str:
    if not _DATE_RE.match(d or ""):
        raise ValueError("date must be YYYY-MM-DD")
    datetime.strptime(d, "%Y-%m-%d")
    return d


def ensure_span(start: str, end: str, max_days: int) -> None:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    if e < s:
        raise ValueError("end_date must be >= start_date")
    days = (e - s).days + 1
    if days > max_days:
        raise ValueError(f"date span too large: {days} days > max_days={max_days}")


def _json_safe(v: Any) -> Any:
    """把 DuckDB 取回的非 JSON 原生类型转成可序列化值。

    实测(duckdb 1.4.3 + pandas 2.3.3) fetchdf 后仍会 json.dumps 失败的类型：
    INTERVAL->pd.Timedelta、BLOB->bytearray、LIST/ARRAY->np.ndarray、UUID->uuid.UUID。
    DECIMAL/HUGEINT 已被转成 float64，此处的 Decimal 分支只作防御。

    注意顺序：pd.Timedelta 既是 datetime.timedelta 子类又带 isoformat()，
    必须先判 timedelta，否则会输出 'P0DT0H2M0S' 这种 ISO 时长串而非秒数。
    """
    if isinstance(v, timedelta):           # INTERVAL -> 秒
        return v.total_seconds()
    if hasattr(v, "isoformat"):            # datetime / date / Timestamp
        return v.isoformat()
    if isinstance(v, Decimal):             # DECIMAL(防御) —— 与 DOUBLE 列同为数值
        return float(v)
    if isinstance(v, (bytes, bytearray)):  # BLOB
        return v.hex()
    if isinstance(v, uuid.UUID):           # UUID
        return str(v)
    if hasattr(v, "tolist"):               # np.ndarray(LIST/ARRAY) 及 numpy 标量
        return v.tolist()
    return v


def df_to_payload(df: pd.DataFrame, max_rows: int, sql_limit: int | None = None) -> dict[str, Any]:
    """把 DataFrame 转为 Tool 返回体。

    sql_limit: 调用方在 SQL 里已用 LIMIT 截断时传入该值。行数正好顶到 limit
    说明后面可能还有数据，同样要标记 truncated——否则调用方(大模型)会把
    截断结果当成全量。
    """
    truncated = sql_limit is not None and len(df) >= sql_limit
    if len(df) > max_rows:
        df = df.iloc[:max_rows].copy()
        truncated = True
    # make JSON-safe:
    #   1) NaN/NaT/None 统一置 None
    #   2) datetime/Decimal/timedelta/bytes 等转可序列化值——否则 MCP 无法序列化
    #      (NaT 底层为 float，会触发 "'float' object cannot be interpreted as an integer")
    mask = df.notnull()
    df = df.astype(object).where(mask, None)
    df = df.map(_json_safe)
    return {
        "columns": list(df.columns),
        "rows": df.to_dict(orient="records"),
        "rowcount": int(len(df)),
        "truncated": truncated,
    }


def run_sql(sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    # Short connection implementation: Open -> Execute -> Close
    _log(f"SQL: {sql} | params={params}")
    with duckdb.connect(DB_PATH, read_only=True) as con:
        if params:
            return con.execute(sql, params).fetchdf()
        return con.execute(sql).fetchdf()


def table_exists(table: str) -> bool:
    df = run_sql(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = ?
        LIMIT 1
        """,
        [table],
    )
    return len(df) > 0


def resolve_stock(code: str) -> tuple[dict[str, Any], bool]:
    """
    code: '300085.SZ' (primary key in STOCK_INFO)

    返回 (股票信息, 是否命中)。查不到时第二个值为 False，调用方据此区分
    「查到但字段为空」与「根本没这只股票」，不再静默降级。
    """
    code = normalize_code(code)
    if table_exists(schema.TB_STOCK_INFO):
        df = run_sql(
            f"""
            SELECT *
            FROM {schema.TB_STOCK_INFO}
            WHERE UPPER(code) = UPPER(?)
            LIMIT 1
            """,
            [code],
        )
        if len(df) == 1:
            out = df_to_payload(df, 1)
            return out["rows"][0], True

        # optional fallback: derive symbol/exchange and try (symbol, exchange)
        symbol, exch = parse_code(code)
        df2 = run_sql(
            f"""
            SELECT *
            FROM {schema.TB_STOCK_INFO}
            WHERE symbol = ? AND UPPER(exchange) = ?
            LIMIT 1
            """,
            [symbol, exch],
        )
        if len(df2) == 1:
            out = df_to_payload(df2, 1)
            return out["rows"][0], True

    return {"code": code}, False


def enforce_limit(sql: str, max_rows: int) -> str:
    """用子查询包裹后统一加 LIMIT（原 add_limit_if_missing，语义已从「缺则补」改为「总是加」）。

    不再用「检测到 LIMIT 就跳过」的启发式：那样既会被子查询/字符串里的
    LIMIT 字样误判，追加的 LIMIT 也会被结尾的 `--` 行注释整体吞掉，
    导致全表被 fetch 进 pandas。包裹后外层 LIMIT 总是生效，且对
    SELECT 与 WITH...SELECT 都语义等价(只是额外收紧行数)。
    """
    q = sql.rstrip().rstrip(";")
    return f"SELECT * FROM (\n{q}\n) AS _q LIMIT {int(max_rows)}"


def validate_raw_query(sql: str) -> None:
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    if len(statements) != 1:
        raise ValueError("only one SQL statement is allowed.")

    q = statements[0]
    if not re.match(r"^(SELECT|WITH)\b", q, re.IGNORECASE):
        raise ValueError("only SELECT/WITH queries are allowed.")

    if _DANGEROUS_SQL_RE.search(q):
        raise ValueError("DDL/DML/admin statements are not allowed in read-only server.")

    if _DANGEROUS_TABLE_FUNCTION_RE.search(q):
        raise ValueError("file/network table functions are not allowed in raw query.")


# -----------------------------
# Core MCP Tools
# -----------------------------
@mcp.tool()
def list_tables() -> dict[str, Any]:
    """
    List all base tables and views in DuckDB.
    """
    # 必须含 VIEW：契约对象里 STOCK_SW_INDUSTRY_VIEW 是视图，
    # 只列 BASE TABLE 会让行业数据在自省时“不存在”。
    df = run_sql(
        """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_type IN ('BASE TABLE', 'VIEW')
        ORDER BY table_schema, table_name
        """
    )
    return df_to_payload(df, MAX_ROWS_DEFAULT)


@mcp.tool()
def describe_table(table: str) -> dict[str, Any]:
    """
    Describe columns for a given table.
    """
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table or ""):
        raise ValueError("invalid table name")
    df = run_sql(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = ?
        ORDER BY ordinal_position
        """,
        [table],
    )
    return df_to_payload(df, MAX_ROWS_DEFAULT)


@mcp.tool()
def search_stock(keyword: str, limit: int = 20) -> dict[str, Any]:
    """
    Returns STOCK_INFO rows; canonical code is STOCK_INFO.code (already has suffix).
    """
    if not table_exists(schema.TB_STOCK_INFO):
        raise RuntimeError(f"{schema.TB_STOCK_INFO} table not found.")
    kw = (keyword or "").strip()
    if not kw:
        raise ValueError("keyword is required")
    limit = min(clamp_rows(limit), 200)

    if _CODE_RE.match(kw):
        df = run_sql(
            f"""
            SELECT *
            FROM {schema.TB_STOCK_INFO}
            WHERE UPPER(code) = UPPER(?)
            LIMIT 1
            """,
            [normalize_code(kw)],
        )
        if len(df) > 0:
            return df_to_payload(df, limit)

    df = run_sql(
        f"""
        SELECT *
        FROM {schema.TB_STOCK_INFO}
        WHERE
            UPPER(code) LIKE UPPER(?) OR
            symbol LIKE ? OR
            name LIKE ?
        ORDER BY symbol
        LIMIT ?
        """,
        [f"{kw}%", f"{kw}%", f"%{kw}%", limit],
    )
    return df_to_payload(df, limit, sql_limit=limit)


@mcp.tool()
def get_stock_info(code: str) -> dict[str, Any]:
    """
    Get single stock info by code (e.g., 300085.SZ).
    found=false 表示 STOCK_INFO 中没有这只股票(而非查到了但字段为空)。
    """
    info, found = resolve_stock(code)
    return {"stock": info, "found": found}


@mcp.tool()
def get_trade_days(
    start_date: str,
    end_date: str,
    open_only: bool = True,
    limit: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    TRADE_CAL(cal_date, is_open)
    """
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, max_days=TRADE_CAL_MAX_DAYS)
    limit = clamp_rows(limit)

    if not table_exists(schema.TB_TRADE_CAL):
        raise RuntimeError(f"{schema.TB_TRADE_CAL} table not found.")

    if open_only:
        df = run_sql(
            f"""
            SELECT cal_date, is_open
            FROM {schema.TB_TRADE_CAL}
            WHERE cal_date BETWEEN ? AND ?
              AND is_open = 1
            ORDER BY cal_date
            LIMIT ?
            """,
            [start_date, end_date, limit],
        )
    else:
        df = run_sql(
            f"""
            SELECT cal_date, is_open
            FROM {schema.TB_TRADE_CAL}
            WHERE cal_date BETWEEN ? AND ?
            ORDER BY cal_date
            LIMIT ?
            """,
            [start_date, end_date, limit],
        )
    return df_to_payload(df, limit, sql_limit=limit)


def _query_stock_daily(
    code: str,
    start_date: str,
    end_date: str,
    fields: list[str] | None,
) -> pd.DataFrame:
    """取日线原始 DataFrame（不做行数截断）。

    行数由 MAX_DAYS 的跨度上限间接约束。calc_indicators 直接复用本函数，
    避免「先按 max_rows 截断、再在截断数据上算均线」而算出错误指标。
    """
    code = normalize_code(code)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists(schema.TB_STOCK_DAILY):
        raise RuntimeError(f"{schema.TB_STOCK_DAILY} table not found.")

    default_fields = ["date", "open", "high", "low", "close", "volume", "amount"]
    use_fields = fields if fields else default_fields

    clean_fields = []
    for c in use_fields:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", c):
            raise ValueError(f"invalid field: {c}")
        clean_fields.append(c)
    cols = ", ".join(clean_fields)

    return run_sql(
        f"""
        SELECT {cols}
        FROM {schema.TB_STOCK_DAILY}
        WHERE UPPER(code) = UPPER(?)
          AND date BETWEEN ? AND ?
        ORDER BY date
        """,
        [code, start_date, end_date],
    )


@mcp.tool()
def get_stock_daily(
    code: str,
    start_date: str,
    end_date: str,
    fields: list[str] | None = None,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    STOCK_DAILY(code, date, open, high, low, close, volume, amount)
    """
    df = _query_stock_daily(code, start_date, end_date, fields)
    return df_to_payload(df, clamp_rows(max_rows))


@mcp.tool()
def get_daily_basic(
    code: str,
    start_date: str,
    end_date: str,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    DAILY_BASIC(code, trade_date, turnover_rate, ... is_st)
    """
    code = normalize_code(code)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists(schema.TB_DAILY_BASIC):
        raise RuntimeError(f"{schema.TB_DAILY_BASIC} table not found.")

    max_rows = clamp_rows(max_rows)

    df = run_sql(
        f"""
        SELECT *
        FROM {schema.TB_DAILY_BASIC}
        WHERE UPPER(code) = UPPER(?)
          AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [code, start_date, end_date],
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def calc_indicators(
    code: str,
    start_date: str,
    end_date: str,
    ma_windows: list[int] | None = None,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    Calculate simple indicators from STOCK_DAILY:
    - returns (pct)
    - MA(close) for given windows
    - VOL_MA(volume) for given windows
    """
    if ma_windows is None or len(ma_windows) == 0:
        ma_windows = [5, 10, 20, 60]

    # 指标必须在完整区间上计算，故直接取原始 DataFrame，截断留到最后一步
    df = _query_stock_daily(code, start_date, end_date, ["date", "close", "volume"])
    if len(df) == 0:
        return {"columns": [], "rows": [], "rowcount": 0, "truncated": False}

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    df["ret_1d"] = df["close"].pct_change()

    for w in ma_windows:
        w = int(w)
        if w <= 1 or w > 400:
            continue
        df[f"ma_{w}"] = df["close"].rolling(w, min_periods=max(2, w // 2)).mean()
        df[f"vol_ma_{w}"] = df["volume"].rolling(w, min_periods=max(2, w // 2)).mean()

    # keep JSON-friendly
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df_to_payload(df, clamp_rows(max_rows))


@mcp.tool()
def get_adj_factor(
    code: str,
    start_date: str,
    end_date: str,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    ADJ_FACTOR(code, trade_date, fore_factor, back_factor, adjust_factor)
    获取复权因子，用于计算前/后复权价格。
    """
    code = normalize_code(code)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists(schema.TB_ADJ_FACTOR):
        raise RuntimeError(f"{schema.TB_ADJ_FACTOR} table not found.")

    max_rows = clamp_rows(max_rows)

    df = run_sql(
        f"""
        SELECT code, trade_date, fore_factor, back_factor, adjust_factor
        FROM {schema.TB_ADJ_FACTOR}
        WHERE UPPER(code) = UPPER(?)
          AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [code, start_date, end_date],
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def get_stock_industry(
    code: str,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """
    查询股票的申万行业分类（一/二/三级）。
    如果指定 trade_date，返回该日期生效的行业归属；否则返回最新记录。
    """
    code = normalize_code(code)  # 结果里的 `? AS code` 需要回显规范化后的值
    symbol, _ = parse_code(code)

    if not table_exists(schema.VW_SW_INDUSTRY):
        raise RuntimeError(f"{schema.VW_SW_INDUSTRY} view not found.")

    if trade_date:
        trade_date = validate_date(trade_date)
        df = run_sql(
            f"""
            SELECT
                ? AS code,
                symbol,
                start_date,
                sw_version,
                sw_l1_code,
                sw_l1_name,
                sw_l2_code,
                sw_l2_name,
                sw_l3_code,
                sw_l3_name,
                industry_code,
                update_time,
                updated_at
            FROM {schema.VW_SW_INDUSTRY}
            WHERE symbol = ?
              AND start_date <= ?
            ORDER BY start_date DESC
            LIMIT 1
            """,
            [code, symbol, trade_date],
        )
    else:
        df = run_sql(
            f"""
            SELECT
                ? AS code,
                symbol,
                start_date,
                sw_version,
                sw_l1_code,
                sw_l1_name,
                sw_l2_code,
                sw_l2_name,
                sw_l3_code,
                sw_l3_name,
                industry_code,
                update_time,
                updated_at
            FROM {schema.VW_SW_INDUSTRY}
            WHERE symbol = ?
            ORDER BY start_date DESC
            LIMIT 1
            """,
            [code, symbol],
        )
    return df_to_payload(df, 1)


@mcp.tool()
def get_stock_industry_history(
    code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    查询股票申万行业分类历史（一/二/三级展开）。
    可选 start_date / end_date 按计入日期过滤。
    """
    code = normalize_code(code)  # 结果里的 `? AS code` 需要回显规范化后的值
    symbol, _ = parse_code(code)

    if start_date:
        start_date = validate_date(start_date)
    if end_date:
        end_date = validate_date(end_date)
    if start_date and end_date:
        ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists(schema.VW_SW_INDUSTRY):
        raise RuntimeError(f"{schema.VW_SW_INDUSTRY} view not found.")

    max_rows = clamp_rows(max_rows)

    filters = ["symbol = ?"]
    params: list[Any] = [symbol]
    if start_date:
        filters.append("start_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("start_date <= ?")
        params.append(end_date)

    df = run_sql(
        f"""
        SELECT
            ? AS code,
            symbol,
            start_date,
            sw_version,
            sw_l1_code,
            sw_l1_name,
            sw_l2_code,
            sw_l2_name,
            sw_l3_code,
            sw_l3_name,
            industry_code,
            update_time,
            updated_at
        FROM {schema.VW_SW_INDUSTRY}
        WHERE {' AND '.join(filters)}
        ORDER BY start_date
        """,
        [code] + params,
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def get_margin_detail(
    code: str,
    start_date: str,
    end_date: str,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    MARGIN_DETAIL_DAILY(trade_date, exchange_code, symbol, code,
        margin_buy_amount, margin_repay_amount, margin_balance,
        short_sell_volume, short_repay_volume,
        short_balance_volume, short_balance_amount, margin_short_balance, ...)
    获取指定股票的融资融券明细数据。
    """
    code = normalize_code(code)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists(schema.TB_MARGIN_DETAIL):
        raise RuntimeError(f"{schema.TB_MARGIN_DETAIL} table not found.")

    max_rows = clamp_rows(max_rows)

    df = run_sql(
        f"""
        SELECT trade_date, exchange_code, symbol, code,
               margin_buy_amount, margin_repay_amount, margin_balance,
               short_sell_volume, short_repay_volume,
               short_balance_volume, short_balance_amount,
               margin_short_balance, created_at, updated_at
        FROM {schema.TB_MARGIN_DETAIL}
        WHERE UPPER(code) = UPPER(?)
          AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [code, start_date, end_date],
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def get_margin_summary(
    start_date: str,
    end_date: str,
    exchange_code: str | None = None,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    MARGIN_SUMMARY_DAILY(trade_date, exchange_code,
        margin_buy_amount, margin_repay_amount, margin_balance,
        short_sell_volume, short_repay_volume,
        short_balance_volume, short_balance_amount, margin_short_balance, ...)
    获取交易所级融资融券每日汇总数据。
    exchange_code 可选: SH / SZ / BJ；不传则返回所有交易所。
    """
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists(schema.TB_MARGIN_SUMMARY):
        raise RuntimeError(f"{schema.TB_MARGIN_SUMMARY} table not found.")

    filters = ["trade_date BETWEEN ? AND ?"]
    params: list[Any] = [start_date, end_date]
    if exchange_code:
        ex = exchange_code.upper()
        if ex not in schema.EXCHANGES:
            raise ValueError(f"exchange_code must be one of {'/'.join(schema.EXCHANGES)}")
        filters.append("exchange_code = ?")
        params.append(ex)

    max_rows = clamp_rows(max_rows)

    df = run_sql(
        f"""
        SELECT trade_date, exchange_code,
               margin_buy_amount, margin_repay_amount, margin_balance,
               short_sell_volume, short_repay_volume,
               short_balance_volume, short_balance_amount,
               margin_short_balance, created_at, updated_at
        FROM {schema.TB_MARGIN_SUMMARY}
        WHERE {' AND '.join(filters)}
        ORDER BY trade_date, exchange_code
        """,
        params,
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def get_capital_detail(
    code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict[str, Any]:
    """
    CAPITAL_DETAIL (GBBQ 股本变动/权息资料)
    获取除权除息、送配股、股本变化等记录。
    category 可选: 除权除息 / 股本变化 / 送配股上市
    若不传日期则返回该股票全部历史记录。
    """
    code = normalize_code(code)

    if not table_exists(schema.TB_CAPITAL_DETAIL):
        raise RuntimeError(f"{schema.TB_CAPITAL_DETAIL} table not found.")

    max_rows = clamp_rows(max_rows)

    conditions = ["UPPER(code) = UPPER(?)"]
    params: list[Any] = [code]

    if start_date:
        start_date = validate_date(start_date)
        conditions.append("date >= ?")
        params.append(start_date)

    if end_date:
        end_date = validate_date(end_date)
        conditions.append("date <= ?")
        params.append(end_date)

    if start_date and end_date:
        ensure_span(start_date, end_date, CAPITAL_MAX_DAYS)

    if category:
        conditions.append("category = ?")
        params.append(category)

    where = " AND ".join(conditions)
    df = run_sql(
        f"""
        SELECT *
        FROM {schema.TB_CAPITAL_DETAIL}
        WHERE {where}
        ORDER BY date
        """,
        params,
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def query(sql: str, max_rows: int = MAX_ROWS_DEFAULT) -> dict[str, Any]:
    """
    Raw SQL query (read-only). DDL/DML is blocked. Result is always LIMIT-capped.
    """
    if not ALLOW_RAW_QUERY:
        raise RuntimeError("Raw query tool is disabled by server policy (ALLOW_RAW_QUERY=0).")

    q = (sql or "").strip()
    if not q:
        raise ValueError("sql is required")

    validate_raw_query(q)

    max_rows = clamp_rows(max_rows)
    q2 = enforce_limit(q, max_rows)

    df = run_sql(q2)
    return df_to_payload(df, max_rows, sql_limit=max_rows)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
