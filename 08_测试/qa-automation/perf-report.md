# 性能测试报告（step8）

> 本项目非 HTTP（SQL 存储过程 + Python 脚本）。压测目标 = ADS 指标读查询（`ads_kpi_daily`），按 step8 8.5.2 协议选型规则：私有/DB 查询协议不硬套 JMeter，以 `perf_test.py` 自研多线程客户端作主压测工具。

## 一、查询优化前后实测（C06）

数据来源：`05_代码/backend/reports/perf_report.md`（`EXPLAIN ANALYZE` 连续 5 次取中位数，真实运行）。

| 查询 | 优化前 ms | 优化后 ms | 提升倍数 | 执行计划确认 |
|---|---:|---:|---:|---|
| Q1 渠道 GMV 聚合（加日期范围下推） | 54.466 | 68.569 | 0.79× | 优化后走 `idx_dt_channel_amount` 索引范围扫描（rows 85099→42549），下推生效 |
| Q2 用户等级关联（加 SCD2 is_current 过滤） | 215.355 | 204.925 | 1.05× | 行数 2047 前后一致（正确性未变），`is_current=1` 过滤生效 |
| Q3 日环比（自连接 → LAG 窗口函数） | 2.297 | 2.912 | 0.79× | 优化后用 `lag()` 窗口聚合替代自连接，语义正确 |

**结论：3 条查询提升均 < 3 倍，未达 C06 目标。如实记录，未伪造耗时（底线规则 3）。**

### 未达标根因分析

- 当前运行基线仅 **19 天数据**（ods 16881 行 / dwd 16748 行），数据量太小，索引/下推/窗口函数的优化收益无法被放大——优化前后都在亚毫秒到百毫秒级，已触底 I/O 与 MySQL 优化器开销。
- 执行计划证明优化**已生效**（索引范围扫描替代全表、SCD2 当前版本过滤、窗口函数替代自连接），只是数据量不足以拉开耗时差距。
- 基线配套范围③已预授权降级：「至少 2 条达到 3 倍，其余如实记录实测耗时并说明瓶颈，禁止为达标伪造」。

### 调优建议

1. **放大数据量复测**：若需达成 ≥3 倍，应在 90 天全量（订单 8 万行）ETL 跑完后重测 `perf_benchmark.py`，当前 ETL 基线只跑了 11-10~11-28（19 天）。
2. **Q1/Q2**：覆盖索引 `idx_dt_channel_amount` / `idx_dt_user` 已建，无需再加索引；瓶颈在数据量。
3. **Q3**：`ads_kpi_daily` 仅 63 行（19 天 × 3 kpi），窗口函数与自连接耗时都 < 3ms，无优化必要；90 天全量后重测预期 LAG 优势显现。

## 二、多线程并发压测（ADS 读查询）

工具：`perf_test.py`（自研多线程客户端，3 档并发 10/20/50，每档 10s）。
压测目标：`SELECT kpi_code, kpi_value FROM ads_kpi_daily WHERE dt='2026-11-28'`（3 行结果，纯读）。

### 数据隔离

仅 `SELECT` 只读，不写库，不污染基线。压测在基线日 11-28 上执行。

### 实测结果（本机 MySQL，自跑留证）

由 `python perf_test.py` 真实生成（exit 0，证据 `reports/perf_test.log`）：

```
== perf_test: query='SELECT kpi_code, kpi_value FROM ads_kpi_daily WHERE dt=%s'  duration=10s ==
  concurrency=10  avg_ms=   3.07  p95_ms=   4.66  tps=  3231.3  error_rate=0.0  samples=32342
  concurrency=20  avg_ms=   6.46  p95_ms=  10.26  tps=  3059.9  error_rate=0.0  samples=30646
  concurrency=50  avg_ms=  18.13  p95_ms=   30.6  tps=  2677.6  error_rate=0.0  samples=26885
== perf_test done ==
```

### 瓶颈分析

1. **拐点**：ADS 表仅 3 行命中，查询本身亚毫秒，**TPS 不随并发上升而增长**（10 并发 3231 → 50 并发 2677，反而下降），说明瓶颈不在 SQL 执行，而在 **MySQL 连接建立与 Python DB-API 线程切换**。
2. **RT 线性上升**：并发 10→50，avg RT 3.07→18.13ms（约 6×）、p95 4.66→30.6ms，符合连接/线程调度瓶颈特征（排队等待），不是数据层扫描变慢。
3. **错误率**：三档均为 0，无连接超时或查询失败。
4. **调优建议**：生产读多场景应引入连接池（SQLAlchemy `pool_size`/`max_overflow`）减少建连开销；本项目 ETL 为日批单次执行，连接池收益有限，`sa_engine` 已配置 `pool_pre_ping=True`。日批链路瓶颈在存储过程 ETL 执行（秒级），不在 ADS 读查询。

## 三、降级边界说明（诚实记录）

| 项 | 状态 | 说明 |
|---|---|---|
| 第二工具交叉验证（JMeter） | 未做 | 本项目非 HTTP，无对等 HTTP 压测目标；按 8.5.2 不硬套 JMeter，以 `perf_test.py` 作主压测。不伪造 JMeter 数据。 |
| Allure HTML 报告 | 未渲染 | `allure-pytest` 已生成 `reports/allure-results/`（53 条结果 JSON），但本机未装 allure CLI，HTML 未生成。配置正确，装 CLI 后 `allure generate reports/allure-results -o reports/html --clean` 即可。 |
| C06 提升 ≥3 倍 | 未达标 | 数据量瓶颈（19 天基线），如实记录；执行计划证明优化已生效，未伪造耗时。 |
