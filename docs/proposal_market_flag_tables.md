# 接入建议：spring 新增的 `SUSPENSION_DAILY` / `LIMIT_POOL_DAILY`

- 提出日期：2026-09-13
- 提出方：spring 侧重构（核对模块引入第三方停牌 / 涨跌停交叉核对）
- 状态：**待评估**——本文档只描述建议，未改动本仓库任何代码
- 结论先行：**本服务无需改动即可使用这两张表**，下面是可选的契约硬化建议

---

## 一、背景

spring 新增两张表，均为纯新增（`CREATE TABLE IF NOT EXISTS`），不改动任何既有表结构：

| 表 | 主键 | 内容 |
|---|---|---|
| `SUSPENSION_DAILY` | `(code, trade_date)` | 每日停牌名单（akshare 东财源） |
| `LIMIT_POOL_DAILY` | `(code, trade_date, limit_type)` | 每日涨跌停股池，`limit_type` 取 `'U'`（涨停）/ `'D'`（跌停） |

两张表的 `code` 均为标准代码（`600519.SH`），与 `STOCK_DAILY` / `DAILY_BASIC` 一致，
可直接等值 JOIN（**不是** `CAPITAL_DETAIL` 那种 6 位裸码）。

`trade_date` 是**交易日**语义，与 `DAILY_BASIC.trade_date` 一致。

---

## 二、为什么本服务无需改动

`server.py` 的 `list_tables` 是从 `information_schema.tables` **实时枚举**的：

```sql
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_type IN ('BASE TABLE', 'VIEW')
ORDER BY table_schema, table_name
```

不是白名单。所以两张新表**自动出现在 `list_tables` 结果里、可被 `describe_table` 自省、
可被 `execute_sql` 查询**，零改动。

`src/quant_mcp/schema.py` 的 `TABLES` 元组，其注释写明用途是
「契约涉及的全部对象（供契约测试遍历）」——它不拦访问，只决定契约测试覆盖哪些对象。

---

## 三、可选：纳入契约测试覆盖

如果希望这两张表也受契约测试保护（表结构被 spring 改动时本仓库能红），
把它们加进 `TABLES`：

```python
TB_SUSPENSION_DAILY = "SUSPENSION_DAILY"
TB_LIMIT_POOL_DAILY = "LIMIT_POOL_DAILY"

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
    TB_SUSPENSION_DAILY,   # 新增
    TB_LIMIT_POOL_DAILY,   # 新增
)
```

**权衡**：加进去之后，契约测试需要 spring 那边已经跑过 `python -m etl.init_db` 建表，
否则会因表不存在而红。两张表目前是新东西，部署面可能还没铺开。
建议等这两张表在生产库稳定运行一段时间后再纳入。

---

## 四、使用这两张表时需要知道的语义

如果后续要基于它们做分析，有两点容易踩：

1. **它们是每日全量快照，成员会变。** spring 侧 ETL 写库是「按日期整体删除后重插」，
   不是 upsert——所以某日的行集就是那天的完整名单，不会残留历史成员。
2. **`SUSPENSION_DAILY` 只含当日真正处于停牌状态的股票。** 上游 akshare 接口
   （`stock_tfp_em`）实际返回的是围绕该日期的停复牌**公告**信息，混着「未来才停牌」和
   「当日已复牌」的记录；spring 侧已在入库时过滤掉，表里留下的是当日实际停牌名单。
   这一点在 2026-09-11 的数据上验证过：接口返回 18 条，过滤后 12 条，
   12 只全部 `STOCK_DAILY.tradestatus = 0` 且 `volume = 0`。

3. **`LIMIT_POOL_DAILY` 一张表装两个池。** 查询时几乎总要带 `limit_type` 条件；
   某天有涨停行不代表有跌停行（大涨的日子全市场可以无跌停）。

---

## 五、相关

spring 侧对应的 ETL 是 `etl.sync_suspension` / `etl.sync_limit_pool`。
执行侧（`etl-quant-mcp`）的接入建议见该仓库的
`docs/proposal_sync_market_flags.md`——那边**确实需要改动**，因为它的
`schema.PROGRAMS` 是安全白名单，不加进去就无法启动这两个 ETL。
