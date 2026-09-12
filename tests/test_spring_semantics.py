# 修改记录:
#   2026-09-12  Claude  新建：跟随 spring 2026-09 语义变更的回归测试
#                       (ADJ_FACTOR.fore_factor 锚定口径、停牌日可识别、废弃表标记)
"""与 spring 数据语义对齐的回归测试。

spring 在 2026-09 做了三处影响本服务消费方的变更，本文件逐项固化：

1. `ADJ_FACTOR` 主源由 baostock 改为本地自算，`fore_factor` 变成「以该股最新
   事件为锚的累计前复权因子」——整列会随新事件平移(spring docs/bug1.md BUG-007)。
2. `STOCK_DAILY.tradestatus=0` 的停牌行 close 是前收结转的正数，不是 0/NULL
   (spring docs/bug1.md BUG-014)，模型必须能看见该标记。
3. 生产库中并存已废弃表与人工备份表，自省时必须能与契约对象区分。
"""


# ---------- 1. fore_factor 锚定口径必须写进模型可见的 Tool 描述 ----------
# MCP 把 docstring 当作 Tool description 下发给模型，它就是对外 API 的一部分。
# 缺了这句话，模型会把 fore_factor 当成可跨时间比较的稳定序列(数值都贴着 1.0，
# 算错了也极难察觉)。
def test_get_adj_factor_doc_warns_fore_factor_is_anchored(srv):
    doc = srv.get_adj_factor.__doc__ or ""
    assert "最新事件" in doc, "未说明 fore_factor 以该股最新事件为锚"
    assert "back_factor" in doc, "未指引模型改用稳定的 back_factor"


# ---------- 2. 停牌日必须对模型可见，且不产生假的当日收益率 ----------
def test_stock_daily_contract_requires_tradestatus(srv):
    from quant_mcp import schema

    assert {"pre_close", "tradestatus"}.issubset(
        schema.REQUIRED_COLUMNS[schema.TB_STOCK_DAILY]
    ), "停牌标记与前收盘价未纳入读契约，spring 改结构时不会被契约测试拦住"


def test_get_stock_daily_default_fields_expose_tradestatus(srv):
    r = srv.get_stock_daily("300085.SZ", "2026-07-01", "2026-07-07")
    assert "tradestatus" in r["columns"], "默认字段看不到停牌标记，模型会把停牌日当正常交易日"
    assert "pre_close" in r["columns"]


def test_calc_indicators_exposes_tradestatus(srv):
    r = srv.calc_indicators("300085.SZ", "2026-07-01", "2026-07-07", ma_windows=[2])
    assert "tradestatus" in r["columns"]


def test_calc_indicators_suspended_day_has_no_return(srv):
    # 停牌日 close 是前收结转，算出来的 ret_1d 恒为 0——那是「没涨没跌」的假信号
    r = srv.calc_indicators("300085.SZ", "2026-07-01", "2026-07-07", ma_windows=[2])
    by_date = {row["date"]: row for row in r["rows"]}
    assert by_date["2026-07-07"]["tradestatus"] == 0
    assert by_date["2026-07-07"]["ret_1d"] is None


def test_calc_indicators_normal_day_keeps_return(srv):
    # 反例：正常交易日的收益率不能被一起抹掉
    r = srv.calc_indicators("300085.SZ", "2026-07-01", "2026-07-07", ma_windows=[2])
    by_date = {row["date"]: row for row in r["rows"]}
    assert by_date["2026-07-02"]["ret_1d"] is not None


# ---------- 3. 自省时必须能区分契约对象与「不该被消费」的对象 ----------
def _by_name(payload):
    return {row["table_name"]: row for row in payload["rows"]}


def test_list_tables_marks_deprecated_source_table(srv):
    # ADJ_FACTOR_RAW 自 2026-09-10 起只进不出，不再喂 ADJ_FACTOR；
    # 模型探索时挑中它就会静默拿到过期因子
    row = _by_name(srv.list_tables())["ADJ_FACTOR_RAW"]
    assert row["deprecated"] is True
    assert row["note"]


def test_list_tables_marks_backup_table(srv):
    row = _by_name(srv.list_tables())["ADJ_FACTOR_BAK_20260906"]
    assert row["deprecated"] is True
    assert row["note"]


def test_list_tables_keeps_contract_objects_usable(srv):
    from quant_mcp import schema

    rows = _by_name(srv.list_tables())
    for tb in schema.TABLES:
        assert rows[tb]["deprecated"] is False, f"契约对象被误标为废弃: {tb}"
        assert rows[tb]["note"] is None
