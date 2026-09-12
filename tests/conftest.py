# 修改记录:
#   2026-07-25  Claude  新建：pytest 夹具——构造最小契约库、以隔离方式加载 server 模块
#   2026-07-26  Claude  提高 fixture 保真度：STOCK_SW_INDUSTRY_VIEW 改建为真视图(生产库即为视图，
#                       原先建成表会让 list_tables 只列 BASE TABLE 的缺陷测不出来)；
#                       STOCK_INFO 补 DECIMAL 列，覆盖 JSON 序列化；新增 load_server_with_env
#   2026-09-12  Claude  跟随 spring 2026-09 语义：STOCK_DAILY 补 pre_close/tradestatus 与停牌样本行
"""测试夹具。

- `fixture_db`：一个临时 DuckDB 文件，含 schema.TABLES 全部对象与少量样本数据。
- `srv`：以 fixture_db 加载的独立 server 模块实例。
- `empty_db`：一个空库（无任何表），用于反例（缺表应报错）。
- `load_server`：以指定库路径加载全新 server 模块的工厂。

server 模块在导入时即从环境变量 QUANT_DB_PATH 读取库路径，故每次用唯一模块名
重新 exec，可让不同测试指向不同库而互不干扰。
"""
import importlib.util
import os
import uuid
from pathlib import Path

import duckdb
import pytest
import quant_mcp

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "src" / "quant_mcp" / "server.py"


def load_server(db_path: str, **env: str):
    """以指定库路径加载一个全新、独立的 server 模块实例。

    env: 额外的环境变量(如 MAX_ROWS / ALLOW_RAW_QUERY)，仅在本次加载期间生效，
    加载完成后恢复原值，避免污染其他测试。
    """
    os.environ["QUANT_DB_PATH"] = str(db_path)
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        name = f"quant_mcp.server_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# 建表 + 样本数据（日期类列用 VARCHAR ISO 字符串，便于与字符串参数做 BETWEEN 比较）
_DDL = [
    # JSON 序列化的“毒列”集合，全部按实测挑选(duckdb 1.4.3 + pandas 2.3.3)：
    #   delist_date NULL -> NaT；listing_gap INTERVAL -> pd.Timedelta；
    #   raw_blob BLOB -> bytearray；tags LIST -> np.ndarray；row_id UUID -> uuid.UUID。
    #   total_share DECIMAL 实际已被 fetchdf 转成 float64，留着确认它不会回退。
    """CREATE TABLE STOCK_INFO(
        code VARCHAR, symbol VARCHAR, exchange VARCHAR, name VARCHAR,
        list_date DATE, delist_date DATE, created_at TIMESTAMP,
        total_share DECIMAL(18,4), listing_gap INTERVAL,
        raw_blob BLOB, tags VARCHAR[], row_id UUID)""",
    """INSERT INTO STOCK_INFO VALUES
        ('300085.SZ','300085','SZ','银之杰', DATE '2010-05-26', NULL,
         TIMESTAMP '2026-01-18 15:59:24', 706123456.7800, INTERVAL 2 MINUTE,
         BLOB 'ab', ['创业板','软件'], UUID '00000000-0000-0000-0000-000000000001')""",

    """CREATE TABLE TRADE_CAL(cal_date VARCHAR, is_open INTEGER)""",
    """INSERT INTO TRADE_CAL VALUES
        ('2026-07-01',1),('2026-07-02',1),('2026-07-03',0),('2026-07-06',1),
        ('2026-07-07',1)""",

    # 列序与生产库 STOCK_DAILY 对齐，含 pre_close / tradestatus。
    # 07-07 为停牌日(tradestatus=0)：生产数据里停牌行的 close 是前收结转的正数，
    # 不是 0 也不是 NULL(spring docs/bug1.md BUG-014 已实证)，fixture 必须同构，
    # 否则「停牌日被当成正常交易日」的缺陷测不出来。
    """CREATE TABLE STOCK_DAILY(
        code VARCHAR, date VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, pre_close DOUBLE, tradestatus INTEGER,
        volume DOUBLE, amount DOUBLE)""",
    """INSERT INTO STOCK_DAILY VALUES
        ('300085.SZ','2026-07-01',10.0,10.5,9.8,10.2,9.9,1,1000000,10200000),
        ('300085.SZ','2026-07-02',10.2,10.8,10.1,10.6,10.2,1,1200000,12720000),
        ('300085.SZ','2026-07-06',10.6,11.0,10.4,10.9,10.6,1,1500000,16350000),
        ('300085.SZ','2026-07-07',10.9,10.9,10.9,10.9,10.9,0,0,0)""",

    """CREATE TABLE DAILY_BASIC(
        code VARCHAR, trade_date VARCHAR, turnover_rate DOUBLE, is_st INTEGER)""",
    """INSERT INTO DAILY_BASIC VALUES
        ('300085.SZ','2026-07-01',1.23,0),
        ('300085.SZ','2026-07-02',1.45,0)""",

    """CREATE TABLE ADJ_FACTOR(
        code VARCHAR, trade_date VARCHAR,
        fore_factor DOUBLE, back_factor DOUBLE, adjust_factor DOUBLE)""",
    # 07-02 的 fore_factor 置 NULL：数值列的 NULL 在序列化时必须变成 None 而非 NaN
    """INSERT INTO ADJ_FACTOR VALUES
        ('300085.SZ','2026-07-01',1.0,2.5,2.5),
        ('300085.SZ','2026-07-02',NULL,2.5,2.5)""",

    # 生产库里行业对象是 VIEW，fixture 必须同构，否则测不出 list_tables 漏列视图
    """CREATE TABLE SW_INDUSTRY_BASE(
        symbol VARCHAR, start_date VARCHAR, sw_version VARCHAR,
        sw_l1_code VARCHAR, sw_l1_name VARCHAR,
        sw_l2_code VARCHAR, sw_l2_name VARCHAR,
        sw_l3_code VARCHAR, sw_l3_name VARCHAR,
        industry_code VARCHAR, update_time VARCHAR, updated_at VARCHAR)""",
    """INSERT INTO SW_INDUSTRY_BASE VALUES
        ('300085','2026-01-01','2021','270000','计算机','270200','软件开发',
         '270201','行业应用软件','850831','2026-01-01','2026-01-01')""",
    """CREATE VIEW STOCK_SW_INDUSTRY_VIEW AS SELECT * FROM SW_INDUSTRY_BASE""",

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

    # 生产库里与契约对象并存的「不该被消费」的对象：已废弃的数据源留痕表 +
    # 人工备份表。fixture 必须同构，否则 list_tables 的打标逻辑测不出来。
    """CREATE TABLE ADJ_FACTOR_RAW(
        code VARCHAR, trade_date VARCHAR, back_factor DOUBLE)""",
    """CREATE TABLE ADJ_FACTOR_BAK_20260906(
        code VARCHAR, trade_date VARCHAR, back_factor DOUBLE)""",

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


@pytest.fixture(scope="session")
def srv_raw(fixture_db):
    """开放了原始 SQL 工具的 server 实例。"""
    return load_server(fixture_db, ALLOW_RAW_QUERY="1")


@pytest.fixture(scope="session")
def srv_small(fixture_db):
    """MAX_ROWS=2 的 server 实例，用于验证环境变量真的收紧了返回行数。"""
    return load_server(fixture_db, MAX_ROWS="2")


@pytest.fixture
def empty_db(tmp_path) -> str:
    p = tmp_path / "empty.duckdb"
    duckdb.connect(str(p)).close()
    return str(p)
