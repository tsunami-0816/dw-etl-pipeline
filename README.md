# dw-etl-pipeline

模拟 SaaS 经营分析的四层数仓与 ETL 链路：MySQL 8 存储过程完成 ODS→DWD→DWS→ADS，PySpark/pandas 重算 90 天 DWS 并逐日对账，附带指标字典、字段血缘、质量规则和慢查询取证。所有业务数据均为固定种子生成的模拟数据，无 HTTP 服务、无前端、无真实客户数据。

## 技术栈与资源边界

- MySQL 8.0.36（Docker，宿主 `53306`）、Python 3.11、PyMySQL、SQLAlchemy、pandas、pytest。
- 可选 PySpark 3.5.3 + Java 17：`local[2]`、driver 2g、shuffle 8；Windows 失败时显式使用 pandas，不伪称 Spark 成功。
- 不引入 Hadoop/Flink/Kafka/Airflow/BI 前端；单机 16GB 内存可运行。

## 一键启动

在项目根目录执行（PowerShell 示例）：

```powershell
Copy-Item .env.example .env
# 编辑 .env：填入本机 root、dw_etl、dw_reader、造数账号口令；不要提交 .env
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d
.\.venv\Scripts\python.exe 05_代码/backend/scripts/run_sql.py --init
.\.venv\Scripts\python.exe 05_代码/backend/scripts/gen_data.py --days 90 --seed 42 --dirty-rate 0.03 --mode full
.\.venv\Scripts\python.exe 05_代码/backend/scripts/run_daily.py --date 2026-11-23
```

最后一个命令退出 0 即完成单日最小闭环；`reports/run_summary.json` 是机器断言入口。MySQL 容器只在**首次创建空数据卷**时自动执行挂载的初始化 SQL；已有卷必须显式执行上面的 `run_sql.py --init`，不要靠删卷解决所有问题。

模拟数据覆盖 `2026-08-31`～`2026-11-30`（`gen_data.py --days 90 --seed 42`）。跑该范围外的新日期（如 `2026-12-01`）前须先扩造数范围，否则当日 0 单、各层均为空，`skipped`/空结果是预期而非故障。

## 90 天全链路与对账

```powershell
cd 05_代码/backend
..\..\.venv\Scripts\python.exe scripts/backfill.py --start 2026-08-26 --end 2026-11-23
..\..\.venv\Scripts\python.exe spark/spark_rebuild_dws.py --start-date 2026-08-26 --end-date 2026-11-23 --mode both --engine spark
# Spark 失败时，明确改用：
..\..\.venv\Scripts\python.exe spark/spark_rebuild_dws.py --start-date 2026-08-26 --end-date 2026-11-23 --mode both --engine pandas
```

也可在单日调度加 `--with-spark`：Spark 返回运行失败时自动显式降级 pandas，报告记录真实引擎。对账金额容差 0.01，缺行或计数不等也算差异；差异退出 4。

## 常用命令

```powershell
python scripts/run_daily.py --date 2026-11-23             # 0/2/3/4/5：成功/P0/ETL失败/对账差异/报告失败
python scripts/backfill.py --start 2026-11-01 --end 2026-11-23 --retry-failed
python quality/run_rules.py --date 2026-11-23             # 0 无 P0；2 有 P0
python scripts/perf_benchmark.py --start-date 2026-08-26 --end-date 2026-11-23
..\..\.venv\Scripts\python.exe -m pytest -q
```

`run_daily.py` 同日重跑由 `GET_LOCK`、成功层带行数预检、按日删写/唯一键 upsert 保证幂等。并发第二个任务失败；异常修复后从失败层续跑。

## 分层粒度与依赖

| 层 | 表与粒度 | 更新方式 |
|---|---|---|
| ODS | 4 表；实体在一个 `dt` 的原样快照，含 `batch_id/load_time` | 按 `dt` 删写 |
| DWD | 订单号/日一条清洗明细；用户 ID 一行最新画像 | 订单按日删写，画像 upsert |
| DIM | 用户 SCD2 有效期；商品/渠道当前属性；日期维 | 用户关旧开新，其他 upsert |
| DWS | 日×渠道、日×品类聚合 | 唯一键 upsert |
| ADS | 日×一级指标、日×渠道 ROI、日×渠道日报、日×品类 | 唯一键 upsert |

每层只依赖上一层及维表。DWD/DWS 按月 `RANGE COLUMNS(dt)` 分区并保留 `pmax`；唯一键包含分区列。星型关联、索引与 ADR 的唯一权威是架构 v1.1。

## 治理、报告与契约

- `governance/metrics.yml`：M01～M12 共 12 个指标；`lineage.md`：ADS 字段反查至 ODS/biz。
- `quality/dq_rules.yml`：DQ01～DQ05；P0 阻断，P1 告警，P2 记录。正常造数 3% 脏订单不触发 P0，单独注入 10% 空渠道用于 P0 验证。
- `reports/`：日报、质量、对账、性能、造数与 `run_summary.json`，均由真实命令生成；未运行时不伪造报告。
- 对外契约是 5 个存储过程、CLI 退出码和报告文件，不适用 OpenAPI。详细参数见 `后端开发实现文档.md`。
- 五类故障排查见 `ops.md`。

## 诚实边界

Spark 与 pandas 是两种执行引擎；若本机 Spark 失败，只能在 README/报告/验收记录写 pandas 降级。性能报告记录实测中位数，不保证小数据量下所有查询都达到目标倍数。模拟数据不冒充生产经验。
