# 独立代码 Review 报告（step5.5）

> 写实现者不得自审：另起 reviewer、新上下文、只审不改。
> 本报告按四个维度逐条给结论；问题回流项目根目录 `_问题台账.md`（来源阶段=5.5）。

## 一、Review 范围

- 被审代码目录 / 模块：
  - `05_代码/backend/sql/init/07_procedures.sql`（5 个存储过程：sp_etl_daily / sp_load_ods / sp_build_dwd / sp_build_dws / sp_build_ads）
  - `05_代码/backend/spark/reconcile.py`（pandas 聚合与对账，Spark 聚合 `spark_rebuild_dws.py::_aggregate_spark` 口径对齐）
  - `05_代码/backend/quality/run_rules.py` + `quality/dq_rules.yml`（DQ 双实现的 Python 复跑侧）
  - `05_代码/backend/scripts/run_daily.py`（退出码协议 + Spark 降级）
  - `05_代码/backend/scripts/dw_common.py`（连接/口令/SQLAlchemy engine）
- Review 人（reviewer 上下文）：5A 实现完成后独立审查，对照架构 v1.1 与映射表，只审不改。
- 对照基线：`01_项目基线说明书.md`、`02_架构设计/第一轮技术架构设计文档.md`、`_设计实施映射表.md`

## 二、分维度检查

| 维度 | 检查要点 | 发现 | 严重级别 | 台账 ID |
|---|---|---|---|---|
| 需求正确性 | 实现是否与基线/契约一致，有无漏做、错做 | D-36 要求「SQL 内联 + Python 复跑双实现，结果不一致即缺陷」。审查发现 3 处口径不一致：① `run_rules.py` DQ03 负金额计数基于 `ods_order` 全量（含重复行），而 SQL 内联基于去重后 `rn=1` 集合（`tmp_ods_dedup`），脏数据类型重叠时计数会分歧；② DQ04 通过率分母：SQL 用 ODS 总行数 `v_total`，Python 用 DWD 行数 `dwd_total`；③ `dq_alert` 触发范围：Python 对所有 warn（含 DQ03/DQ04 P1）都写告警，SQL 内联仅对 P0 blocked 与 DQ05 P2 写告警。当前 3% 非重叠造数下结果恰好一致，故未在运行中暴露，但属于真实缺陷。 | P2 | 5.5-01 |
| 需求正确性 | D-01～D-53 其余项 | 与映射表一致，无漏做、错做；阈值/级别在 `dq_rules.yml` 与 SQL 硬编码间一致（DQ01/02 p0=0.05、DQ05=0.5，级别 P0/P1/P2）。 | 通过 | — |
| 系统一致性 | 命名/分层/复用/配置口径 | `reconcile.aggregate_channel` 与 `sp_build_dws` 口径逐字段对齐（gmv/refund/new_users/cost/roi 除零处理一致），已由 14 天 + 90 天逐日 full outer join 对账 diff=0 机器校验。 | 通过 | — |
| 回归 | 改动是否影响既有链路 | 修复 5.5-01 后回归：`pytest -q` 48 passed 无警告；DQ01～DQ04 与存储过程内联逐字段比对一致（5 天样本）；`dq_alert` 归一化后仅含真实 DQ05 告警，无 DQ03/DQ04/陈旧 P0 残留；既有 14 天链路与对账 diff=0 未受影响。 | 通过 | — |
| 健壮性 | 异常分支、边界、空值、并发 | `sp_etl_daily` 并发锁只判 `GET_LOCK = 0`，未覆盖返回 NULL（连接异常）分支，理论上可能无锁继续；低概率，建议改 `<> 1`。 | P3 | 5.5-02 |
| 健壮性 | 事务/证据/锁释放 | 每层自管事务，`etl_run_log` 独立提交；P0 分支先 COMMIT 治理证据（dq_report/dq_alert/dq_issue）再 SIGNAL，外层 ROLLBACK 不丢证据（已实测 11-25）；锁在 success/skipped/failed/blocked 各路径均 `RELEASE_LOCK`。 | 通过 | — |
| 健壮性 | 游标/handler | `sp_build_dwd` 的 `CONTINUE HANDLER FOR NOT FOUND` 仅由游标触发，过程内标量子查询返回 NULL 不发 NOT FOUND，无串扰；游标循环 `IF v_done THEN LEAVE` 正确。 | 通过 | — |
| 安全 | 注入/越权/口令 | Python 侧全部参数化查询（`%s`），表名/文件名为代码内常量白名单；口令仅从 `.env` 读取，示例仅占位符，真实 `.env` 被 `.gitignore` 忽略；`01_databases.sql` 密码占位符经 `SAFE_SECRET` 正则校验。 | 通过 | — |

## 三、回归与结论

- 回归验证方式（命令 / 用例 / 截图证据）：
  1. 修复 5.5-01 后：`pytest -q` → 48 passed，无警告；
  2. 对 2026-11-10/15/20/23/25 五天运行 DQ 双实现比对脚本：DQ01～DQ03 逐字段（total/failed/level/status）完全一致，DQ04 失败数与落地 `user_id=-1` 行数一致、分母已对齐 ODS 总数；
  3. 重跑 `run_rules` 归一化全 16 天：`dq_alert` 最终仅含 1 条真实 DQ05 告警，无 DQ03/DQ04 及修复前陈旧 P0 残留；
  4. 既有 14 天链路、pandas 对账 diff=0、幂等不受影响。
- Review 结论：**通过** — P2 台账 5.5-01（DQ 双实现 3 处口径不一致）已在 5A 修复并完成上述回归，`run_rules.py` 现与 `07_procedures.sql` 内联口径一致；P3 台账 5.5-02（GET_LOCK NULL 分支）为低概率健壮性边界，已记入 `_问题台账.md` 并 deferred 到迭代线，不阻塞 5.5 放行。
- 遗留项归属：5.5-01 → 已修复 verified；5.5-02 → P3 deferred（`_问题台账.md` Q-002，迭代线改判 `v_lock <> 1`）。
