# 修改记录:
#   2026-07-25  Claude  新建：每个 MCP Tool 的正例(happy path)冒烟测试
#   2026-07-25  Claude  新增：返回值 JSON 可序列化断言(datetime/NaT 列曾导致序列化失败)
"""正例：各 Tool 在正常输入下返回预期结果。"""
import json

import pytest


def test_list_tables(srv):
    r = srv.list_tables()
    names = {row["table_name"] for row in r["rows"]}
    # 契约中的 9 个对象都应存在
    from quant_mcp import schema
    assert set(schema.TABLES).issubset(names)


def test_describe_table(srv):
    r = srv.describe_table("STOCK_INFO")
    cols = {row["column_name"] for row in r["rows"]}
    assert {"code", "symbol", "name"}.issubset(cols)


def test_search_stock_by_keyword(srv):
    r = srv.search_stock("300085")
    assert r["rowcount"] >= 1
    assert r["rows"][0]["code"] == "300085.SZ"


def test_search_stock_by_full_code(srv):
    r = srv.search_stock("300085.SZ")
    assert r["rowcount"] == 1
    assert r["rows"][0]["name"] == "银之杰"


def test_get_stock_info(srv):
    r = srv.get_stock_info("300085.SZ")
    assert r["stock"]["code"] == "300085.SZ"


def test_get_trade_days_open_only(srv):
    r = srv.get_trade_days("2026-07-01", "2026-07-06", open_only=True)
    # 07-03 收市，排除 -> 3 个交易日
    assert r["rowcount"] == 3


def test_get_trade_days_all(srv):
    r = srv.get_trade_days("2026-07-01", "2026-07-06", open_only=False)
    assert r["rowcount"] == 4


def test_get_stock_daily(srv):
    r = srv.get_stock_daily("300085.SZ", "2026-07-01", "2026-07-06")
    assert r["rowcount"] == 3
    assert "close" in r["columns"]


def test_get_daily_basic(srv):
    r = srv.get_daily_basic("300085.SZ", "2026-07-01", "2026-07-02")
    assert r["rowcount"] == 2


def test_get_adj_factor(srv):
    r = srv.get_adj_factor("300085.SZ", "2026-07-01", "2026-07-02")
    assert r["rowcount"] == 2
    assert "adjust_factor" in r["columns"]


def test_calc_indicators(srv):
    r = srv.calc_indicators("300085.SZ", "2026-07-01", "2026-07-06", ma_windows=[2])
    assert r["rowcount"] == 3
    assert "ret_1d" in r["columns"]
    assert "ma_2" in r["columns"]


def test_get_stock_industry(srv):
    r = srv.get_stock_industry("300085.SZ")
    assert r["rowcount"] == 1
    assert r["rows"][0]["sw_l1_name"] == "计算机"


def test_get_stock_industry_history(srv):
    r = srv.get_stock_industry_history("300085.SZ")
    assert r["rowcount"] >= 1


def test_get_margin_detail(srv):
    r = srv.get_margin_detail("300085.SZ", "2026-07-01", "2026-07-02")
    assert r["rowcount"] == 1
    assert "margin_balance" in r["columns"]


def test_get_margin_summary_all(srv):
    r = srv.get_margin_summary("2026-07-01", "2026-07-01")
    assert r["rowcount"] == 2  # SH + SZ


def test_get_margin_summary_filtered(srv):
    r = srv.get_margin_summary("2026-07-01", "2026-07-01", exchange_code="SZ")
    assert r["rowcount"] == 1
    assert r["rows"][0]["exchange_code"] == "SZ"


def test_get_capital_detail_all(srv):
    r = srv.get_capital_detail("300085.SZ")
    assert r["rowcount"] == 2


def test_get_capital_detail_by_category(srv):
    r = srv.get_capital_detail("300085.SZ", category="除权除息")
    assert r["rowcount"] == 1


def test_query_disabled_by_default(srv):
    # ALLOW_RAW_QUERY 未开启 -> 原始 SQL 工具应被拒绝
    with pytest.raises(RuntimeError):
        srv.query("SELECT 1")


# ---- 返回值必须可 JSON 序列化(datetime/NaT 列曾导致 MCP 序列化失败) ----
def test_search_stock_json_serializable(srv):
    r = srv.search_stock("300085")
    json.dumps(r)  # STOCK_INFO 含 Timestamp/NaT 列，不得抛异常


def test_get_stock_info_json_serializable(srv):
    r = srv.get_stock_info("300085.SZ")
    json.dumps(r)


def test_datetime_column_serialized_as_string(srv):
    r = srv.search_stock("300085.SZ")
    row = r["rows"][0]
    # list_date 应为 ISO 字符串；delist_date(NULL)应为 None
    assert isinstance(row["list_date"], str)
    assert row["delist_date"] is None
