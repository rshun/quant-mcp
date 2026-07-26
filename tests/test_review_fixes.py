# 修改记录:
#   2026-07-26  Claude  新建：代码走查 7 项修复的回归测试
"""代码走查修复的回归测试。

每个 test 对应走查报告中的一项，命名里带编号便于回溯。
"""
import json

import pytest


# ---------- 1. LIMIT 必须真正生效，不能被行注释吞掉 ----------
# 注意：断言必须落在「库里实际取回多少行」上。payload 层的 max_rows 截断会把
# 缺陷掩盖成同样的 rowcount，但内存已经被全表打爆——那才是这条修复要防的事。
def test_limit_survives_trailing_line_comment(srv_raw):
    # 旧实现拼出 `... -- 全部日线 LIMIT 2`，LIMIT 被整体注释掉
    df = srv_raw.run_sql(srv_raw.enforce_limit("SELECT * FROM STOCK_DAILY -- 全部日线", 2))
    assert len(df) == 2


def test_limit_not_skipped_by_inner_limit(srv_raw):
    # 旧实现检测到子查询里的 LIMIT 字样就整个跳过，不加外层 LIMIT
    sql = "SELECT * FROM (SELECT * FROM STOCK_DAILY LIMIT 3) AS t"
    df = srv_raw.run_sql(srv_raw.enforce_limit(sql, 1))
    assert len(df) == 1


def test_limit_applies_to_cte(srv_raw):
    sql = "WITH x AS (SELECT * FROM STOCK_DAILY) SELECT * FROM x"
    df = srv_raw.run_sql(srv_raw.enforce_limit(sql, 2))
    assert len(df) == 2


def test_query_marks_truncated_at_limit(srv_raw):
    r = srv_raw.query("SELECT * FROM STOCK_DAILY", max_rows=2)
    assert r["rowcount"] == 2
    assert r["truncated"] is True


# ---------- 2. list_tables 必须包含视图 ----------
def test_list_tables_includes_views(srv):
    import schema

    r = srv.list_tables()
    names = {row["table_name"] for row in r["rows"]}
    assert schema.VW_SW_INDUSTRY in names, "行业视图未出现在 list_tables 中，模型自省时会认为它不存在"
    assert "table_type" in r["columns"]
    types = {row["table_name"]: row["table_type"] for row in r["rows"]}
    assert types[schema.VW_SW_INDUSTRY] == "VIEW"


# ---------- 3. SQL 层 LIMIT 截断也要标记 truncated ----------
def test_trade_days_sql_limit_marks_truncated(srv):
    r = srv.get_trade_days("2026-07-01", "2026-07-06", open_only=False, limit=2)
    assert r["rowcount"] == 2
    assert r["truncated"] is True


def test_trade_days_not_truncated_when_all_returned(srv):
    r = srv.get_trade_days("2026-07-01", "2026-07-06", open_only=False, limit=100)
    assert r["rowcount"] == 4
    assert r["truncated"] is False


def test_search_stock_sql_limit_marks_truncated(srv):
    r = srv.search_stock("300085", limit=1)
    assert r["rowcount"] == 1
    assert r["truncated"] is True


# ---------- 4. INTERVAL/BLOB/LIST/UUID 必须可 JSON 序列化 ----------
def test_poison_columns_json_serializable(srv):
    r = srv.search_stock("300085.SZ")
    json.dumps(r)  # 旧实现遇到这几列直接 TypeError
    row = r["rows"][0]
    assert row["listing_gap"] == 120.0                                  # INTERVAL -> 秒
    assert row["raw_blob"] == "6162"                                    # BLOB -> hex
    assert row["tags"] == ["创业板", "软件"]                              # LIST -> list
    assert row["row_id"] == "00000000-0000-0000-0000-000000000001"      # UUID -> str
    assert isinstance(row["total_share"], float)                        # DECIMAL 已是 float64


def test_json_safe_timedelta_takes_precedence_over_isoformat(srv):
    import pandas as pd

    # pd.Timedelta 既是 datetime.timedelta 子类又带 isoformat()；
    # 分支顺序写反会输出 'P0DT0H2M0S' 而不是秒数
    assert srv._json_safe(pd.Timedelta("2min")) == 120.0


def test_json_safe_covers_non_native_types(srv):
    import uuid as _uuid
    from datetime import timedelta
    from decimal import Decimal

    assert srv._json_safe(Decimal("1.50")) == 1.5
    assert srv._json_safe(timedelta(minutes=2)) == 120.0
    assert srv._json_safe(b"\x00\xff") == "00ff"
    assert srv._json_safe(_uuid.UUID(int=1)) == "00000000-0000-0000-0000-000000000001"
    assert srv._json_safe("plain") == "plain"


# ---------- 5. code 必须规范化后再进 SQL ----------
@pytest.mark.parametrize("raw", [" 300085.SZ ", "300085.sz", " 300085.sz "])
def test_code_normalized_before_query(srv, raw):
    # 旧实现校验通过但把带空白/小写的原值拼进 SQL，静默返回 0 行
    assert srv.get_stock_daily(raw, "2026-07-01", "2026-07-06")["rowcount"] == 3
    assert srv.get_daily_basic(raw, "2026-07-01", "2026-07-02")["rowcount"] == 2
    assert srv.get_adj_factor(raw, "2026-07-01", "2026-07-02")["rowcount"] == 2
    assert srv.get_margin_detail(raw, "2026-07-01", "2026-07-02")["rowcount"] == 1
    assert srv.get_capital_detail(raw)["rowcount"] == 2


def test_industry_echoes_normalized_code(srv):
    r = srv.get_stock_industry("300085.sz")
    assert r["rows"][0]["code"] == "300085.SZ"
    h = srv.get_stock_industry_history(" 300085.sz ")
    assert h["rows"][0]["code"] == "300085.SZ"


def test_normalize_code_rejects_bad_input(srv):
    with pytest.raises(ValueError):
        srv.normalize_code("BADCODE")


# ---------- 6. MAX_ROWS 环境变量必须真正生效 ----------
def test_max_rows_env_caps_result(srv_small):
    assert srv_small.MAX_ROWS_DEFAULT == 2
    r = srv_small.get_stock_daily("300085.SZ", "2026-07-01", "2026-07-06")
    assert r["rowcount"] == 2
    assert r["truncated"] is True


def test_max_rows_env_caps_explicit_argument(srv_small):
    # 调用方显式传大值也不能突破 MAX_ROWS
    r = srv_small.get_stock_daily("300085.SZ", "2026-07-01", "2026-07-06", max_rows=1000)
    assert r["rowcount"] == 2


def test_query_stock_daily_not_row_clamped(srv_small):
    # 指标要在完整区间上计算，取数环节不受 MAX_ROWS 影响
    df = srv_small._query_stock_daily("300085.SZ", "2026-07-01", "2026-07-06", None)
    assert len(df) == 3


# ---------- 7. 查不到股票时不能静默降级 ----------
def test_get_stock_info_found_true(srv):
    r = srv.get_stock_info("300085.SZ")
    assert r["found"] is True
    assert r["stock"]["name"] == "银之杰"


def test_get_stock_info_found_false(srv):
    r = srv.get_stock_info("999999.SZ")
    assert r["found"] is False
    assert r["stock"] == {"code": "999999.SZ"}
