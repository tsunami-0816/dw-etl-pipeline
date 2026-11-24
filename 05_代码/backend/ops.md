# dw-etl-pipeline 运维手册

> 数据全部为模拟数据。排障先在 `dw.etl_run_log`、`dw.dq_report`、`dw.dq_alert` 取证，再修复；禁止用删日志代替修复。

## 通用入口

```sql
SELECT batch_id,dt,layer,status,rows_in,rows_out,error_code,error_msg
FROM dw.etl_run_log WHERE dt='2026-11-23' ORDER BY log_id;
SELECT * FROM dw.dq_report WHERE dt='2026-11-23' ORDER BY rule_code;
SELECT * FROM dw.dq_alert WHERE dt='2026-11-23' AND status='open';
```

修复后执行 `python scripts/run_daily.py --date YYYY-MM-DD`；总控只跳过“日志成功且目标当日非空”的层。区间失败日用 `python scripts/backfill.py --start ... --end ... --retry-failed` 按日期升序重跑。

## 1. 源表延迟或缺失

- **现象**：ODS 行数低于预期、`sp_load_ods` 报 1146，或当日订单为 0。
- **排查**：`SELECT MAX(pay_time),COUNT(*) FROM biz.order_info WHERE pay_time >= '2026-11-23';`；查 `etl_run_log.error_code`。
- **处置**：恢复源表/补齐模拟增量后重跑当日；失败事务已回滚，不手工补 DWD/DWS/ADS。
- **预防**：调度前核对源表计数和最大时间；连接失败保持非 0 退出。

## 2. 主键冲突或重复订单

- **现象**：`E_SQL_1062`，或 DQ02 重复率升高；超过 5% 时 P0 阻断。
- **排查**：`SELECT order_no,COUNT(*) FROM dw.ods_order WHERE dt='2026-11-23' GROUP BY order_no HAVING COUNT(*)>1;`；查 `dq_issue` 的 DQ02 明细。
- **处置**：源库允许重复订单号，正常小比例由 DWD 保留最新一条；若为目标表结构被改坏，恢复唯一键后从失败层重跑。
- **预防**：不要给源表 `order_no` 错加唯一约束；目标层保留 `(dt,order_no)` 唯一键和按日删写。

## 3. Spark OOM、winutils 或 JDBC 失败

- **现象**：`OutOfMemoryError`、Java 进程退出、winutils/Hadoop 原生库错误，报告未生成成功结论。
- **排查**：确认 `local[2]`、`spark.driver.memory=2g`、shuffle 分区 8；检查 Java 17、53306 JDBC 和错误原文。
- **处置**：运行 `python spark/spark_rebuild_dws.py ... --engine pandas` 显式降级。报告与 `run_summary.json` 必须写 `pandas`，不得声称 Spark 成功。
- **预防**：窗口最多 90 天，pandas 按 20000 行分块；不起多节点集群，不将驱动内存调到本机无法承受的值。

## 4. 磁盘满或报告不可写

- **现象**：MySQL 写 binlog/数据文件失败，或日报/质量/对账报告写入报错；`run_daily.py` 报告阶段退出 5。
- **排查**：检查 Docker Desktop 磁盘、卷空间和 `05_代码/backend/reports/` 写权限；`docker system df` 只看用量，不擅自清理。
- **处置**：人工确认后释放空间/修正目录权限，再重跑；失败时不生成“成功”报告。
- **预防**：定期复盘慢日志和模拟数据卷；保留报告为小文本，不提交容器数据卷。

## 5. 时区或日期错位

- **现象**：订单落到错误 `dt`、中文/日期边界对账差异、`mom_rate` 异常。
- **排查**：`SELECT @@global.time_zone,@@session.time_zone,@@character_set_server;`；比对 `biz.order_info.pay_time`、`ods_order.dt`、`dwd_order_detail.dt`。
- **处置**：容器固定 `+08:00`，Python 使用 ISO `DATE` 参数；修正配置后从 ODS 失败层重跑受影响日期。
- **预防**：不在代码中使用本地隐式时区转换；JDBC URL 固定 `serverTimezone=Asia/Shanghai`，数据库统一 utf8mb4。

## 6. MySQL 连接失败或端口不通

- **现象**：`run_daily.py` 退出 3，报 `2003 Can't connect to MySQL server on '127.0.0.1' (WinError 10061)` 或 `pymysql.err.OperationalError`。
- **排查**：① `docker compose ps` 看 `dw-etl-mysql` 是否 `Up (healthy)`；② `Test-NetConnection 127.0.0.1 -Port 53306`；③ 核对 `.env` 的 `MYSQL_HOST/MYSQL_PORT` 与 compose 端口映射 `53306:3306`；④ `docker compose logs --tail 50 dw-mysql` 看容器启动错误。
- **处置**：容器未起先 `docker compose up -d`；端口/口令漂移按 `.env` 修正后重跑。脚本必须保持非 0 退出，不得生成“成功”报告。
- **预防**：`.env` 不入库不提交；容器 healthy 后再跑 ETL；改端口同时改 `.env` 与 compose。

## 告警闭环

```sql
UPDATE dw.dq_alert SET status='acked',acked_at=NOW()
WHERE dt='2026-11-23' AND rule_code='DQ01' AND status='open';
```

P0 未处理前不得跳过质量规则强行验收。告警落表口径（与 `quality/run_rules.py`、`07_procedures.sql` 实现一致）：P0 阻断写 `dq_alert` 并在 `sp_build_dwd` 内 `SIGNAL` 中止下游；DQ05（GMV 日环比异动）写 `dq_alert`（P2，不阻断）；P1 只记 `dq_report`/`dq_issue`，不写 `dq_alert`；其余 P2 只记录。
