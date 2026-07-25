# 修改记录:
#   2026-07-25  Claude  新建：pytest 夹具——构造最小契约库、以隔离方式加载 server 模块
"""测试夹具。

- `fixture_db`：一个临时 DuckDB 文件，含 schema.TABLES 全部对象与少量样本数据。
- `srv`：以 fixture_db 加载的独立 server 模块实例。
- `empty_db`：一个空库（无任何表），用于反例（缺表应报错）。
- `load_server`：以指定库路径加载全新 server 模块的工厂。

server.py 在导入时即从环境变量 QUANT_DB_PATH 读取库路径，故每次用唯一模块名
重新 exec，可让不同测试指向不同库而互不干扰。
"""
import importlib.util
import os
import uuid
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server.py"


def load_server(db_path: str):
    """以指定库路径加载一个全新、独立的 server 模块实例。"""
    os.environ["QUANT_DB_PATH"] = str(db_path)
    name = f"server_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 建表 + 样本数据（日期类列用 VARCHAR ISO 字符串，便于与字符串参数做 BETWEEN 比较）
_DDL = [
    # 含 datetime 列，且 delist_date 为 NULL(-> NaT)，复现 JSON 序列化问题
    """CREATE TABLE STOCK_INFO(
        code VARCHAR, symbol VARCHAR, exchange VARCHAR, name VARCHAR,
        list_date DATE, delist_date DATE, created_at TIMESTAMP)""",
    """INSERT INTO STOCK_INFO VALUES
        ('300085.SZ','300085','SZ','银之杰', DATE '2010-05-26', NULL, TIMESTAMP '2026-01-18 15:59:24')""",

    """CREATE TABLE TRADE_CAL(cal_date VARCHAR, is_open INTEGER)""",
    """INSERT INTO TRADE_CAL VALUES
        ('2026-07-01',1),('2026-07-02',1),('2026-07-03',0),('2026-07-06',1)""",

    """CREATE TABLE STOCK_DAILY(
        code VARCHAR, date VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, volume DOUBLE, amount DOUBLE)""",
    """INSERT INTO STOCK_DAILY VALUES
        ('300085.SZ','2026-07-01',10.0,10.5,9.8,10.2,1000000,10200000),
        ('300085.SZ','2026-07-02',10.2,10.8,10.1,10.6,1200000,12720000),
        ('300085.SZ','2026-07-06',10.6,11.0,10.4,10.9,1500000,16350000)""",

    """CREATE TABLE DAILY_BASIC(
        code VARCHAR, trade_date VARCHAR, turnover_rate DOUBLE, is_st INTEGER)""",
    """INSERT INTO DAILY_BASIC VALUES
        ('300085.SZ','2026-07-01',1.23,0),
        ('300085.SZ','2026-07-02',1.45,0)""",

    """CREATE TABLE ADJ_FACTOR(
        code VARCHAR, trade_date VARCHAR,
        fore_factor DOUBLE, back_factor DOUBLE, adjust_factor DOUBLE)""",
    """INSERT INTO ADJ_FACTOR VALUES
        ('300085.SZ','2026-07-01',1.0,2.5,2.5),
        ('300085.SZ','2026-07-02',1.0,2.5,2.5)""",

    """CREATE TABLE STOCK_SW_INDUSTRY_VIEW(
        symbol VARCHAR, start_date VARCHAR, sw_version VARCHAR,
        sw_l1_code VARCHAR, sw_l1_name VARCHAR,
        sw_l2_code VARCHAR, sw_l2_name VARCHAR,
        sw_l3_code VARCHAR, sw_l3_name VARCHAR,
        industry_code VARCHAR, update_time VARCHAR, updated_at VARCHAR)""",
    """INSERT INTO STOCK_SW_INDUSTRY_VIEW VALUES
        ('300085','2026-01-01','2021','270000','计算机','270200','软件开发',
         '270201','行业应用软件','850831','2026-01-01','2026-01-01')""",

    """CREATE TABLE MARGIN_DETAIL_DAILY(
        trade_date VARCHAR, exchange_code VARCHAR, symbol VARCHAR, code VARCHAR,
        margin_buy_amount DOUBLE, margin_repay_amount DOUBLE, margin_balance DOUBLE,
        short_sell_volume DOUBLE, short_repay_volume DOUBLE,
        short_balance_volume DOUBLE, short_balance_amount DOUBLE,
        margin_short_balance DOUBLE, created_at VARCHAR, updated_at VARCHAR)""",
    """INSERT INTO MARGIN_DETAIL_DAILY VALUES
        ('2026-07-01','SZ','300085','300085.SZ',
         100.0,50.0,500.0,10.0,5.0,20.0,200.0,700.0,'2026-07-01','2026-07-01')""",

    """CREATE TABLE MARGIN_SUMMARY_DAILY(
        trade_date VARCHAR, exchange_code VARCHAR,
        margin_buy_amount DOUBLE, margin_repay_amount DOUBLE, margin_balance DOUBLE,
        short_sell_volume DOUBLE, short_repay_volume DOUBLE,
        short_balance_volume DOUBLE, short_balance_amount DOUBLE,
        margin_short_balance DOUBLE, created_at VARCHAR, updated_at VARCHAR)""",
    """INSERT INTO MARGIN_SUMMARY_DAILY VALUES
        ('2026-07-01','SZ',1000.0,500.0,5000.0,100.0,50.0,200.0,2000.0,7000.0,'2026-07-01','2026-07-01'),
        ('2026-07-01','SH',2000.0,900.0,9000.0,150.0,70.0,300.0,3000.0,12000.0,'2026-07-01','2026-07-01')""",

    """CREATE TABLE CAPITAL_DETAIL(
        code VARCHAR, date VARCHAR, category VARCHAR)""",
    """INSERT INTO CAPITAL_DETAIL VALUES
        ('300085.SZ','2020-06-15','除权除息'),
        ('300085.SZ','2021-05-20','送配股上市')""",
]


def _build_fixture_db(path: str) -> None:
    con = duckdb.connect(path)
    try:
        for stmt in _DDL:
            con.execute(stmt)
    finally:
        con.close()


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory) -> str:
    p = tmp_path_factory.mktemp("quant") / "fixture.duckdb"
    _build_fixture_db(str(p))
    return str(p)


@pytest.fixture(scope="session")
def srv(fixture_db):
    return load_server(fixture_db)


@pytest.fixture
def empty_db(tmp_path) -> str:
    p = tmp_path / "empty.duckdb"
    duckdb.connect(str(p)).close()
    return str(p)
