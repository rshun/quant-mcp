# 修改记录:
#   2026-07-25  Claude  新建：跨仓库 schema 读契约测试(正例:表/列齐全;反例:缺表/坏输入/空数据)
"""契约测试。

依据 schema.py 声明的读契约，断言库中结构满足要求（正例）；
并验证缺表、非法输入等异常场景能正确报错或安全降级（反例）。

注意：本测试对着最小 fixture 库跑，只能保证「契约定义与 server 的 SQL 依赖一致」。
真实生产库是否满足契约，应由 spring 项目侧针对真实库运行同类断言来保证。
"""
import pytest

import schema
from conftest import load_server


# ---------- 正例：契约对象与必需列齐全 ----------
def test_all_contract_tables_exist(srv):
    for tb in schema.TABLES:
        assert srv.table_exists(tb), f"契约表/视图缺失: {tb}"


@pytest.mark.parametrize("table", list(schema.TABLES))
def test_required_columns_present(srv, table):
    r = srv.describe_table(table)
    cols = {row["column_name"] for row in r["rows"]}
    missing = schema.REQUIRED_COLUMNS[table] - cols
    assert not missing, f"{table} 缺少契约必需列: {sorted(missing)}"


# ---------- 反例：缺表应报错(契约被破坏时要“吵闹地”失败) ----------
def test_missing_table_raises(empty_db):
    srv_empty = load_server(empty_db)
    with pytest.raises(RuntimeError):
        srv_empty.get_stock_daily("300085.SZ", "2026-07-01", "2026-07-02")


# ---------- 反例：非法输入 ----------
def test_invalid_code_raises(srv):
    with pytest.raises(ValueError):
        srv.get_stock_daily("BADCODE", "2026-07-01", "2026-07-02")


def test_invalid_date_format_raises(srv):
    with pytest.raises(ValueError):
        srv.get_stock_daily("300085.SZ", "2026/07/01", "2026-07-02")


def test_reversed_span_raises(srv):
    with pytest.raises(ValueError):
        srv.get_stock_daily("300085.SZ", "2026-07-06", "2026-07-01")


def test_invalid_exchange_raises(srv):
    with pytest.raises(ValueError):
        srv.get_margin_summary("2026-07-01", "2026-07-01", exchange_code="XX")


# ---------- 反例：无数据应安全降级为空结果，而非报错 ----------
def test_unknown_code_returns_empty(srv):
    r = srv.get_stock_daily("000001.SZ", "2026-07-01", "2026-07-06")
    assert r["rowcount"] == 0


def test_calc_indicators_empty_safe(srv):
    r = srv.calc_indicators("000001.SZ", "2026-07-01", "2026-07-06")
    assert r["rowcount"] == 0
