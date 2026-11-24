from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SPARK_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SPARK_DIR.parent
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
sys.path.insert(0, str(SPARK_DIR))
from dw_common import connect, parse_date, sa_engine  # noqa: E402

try:  # pandas 引擎不需要 pyspark，延迟导入
    from reconcile import reconcile, render_report  # noqa: E402
except Exception:  # pragma: no cover - 仅在路径异常时
    reconcile = None
    render_report = None


def _aggregate_spark(orders_sdf, profile_sdf, channels_sdf):
    """Spark 聚合，口径与 reconcile.aggregate_channel / sp_build_dws 完全一致（D-27）。"""
    from pyspark.sql import functions as F

    paid = F.col("order_status").isin(["paid", "refunded"])
    o = orders_sdf.withColumn("_paid", paid.cast("int")) \
        .withColumn("_gmv", F.when(paid, F.col("amount")).otherwise(F.lit(0))) \
        .withColumn("_refund_amt", F.when(F.col("is_refund") == 1, F.col("amount")).otherwise(F.lit(0)))

    g = o.groupBy("dt", "channel_id").agg(
        F.count("order_id").alias("orders"),
        F.sum("_paid").alias("paid_orders"),
        F.sum("_gmv").alias("gmv"),
        F.sum("is_refund").alias("refund_orders"),
        F.sum("_refund_amt").alias("refund_amount"),
    ).withColumn("refund_rate",
                 F.when(F.col("paid_orders") == 0, F.lit(0))
                  .otherwise(F.col("refund_orders") / F.col("paid_orders")))

    p = profile_sdf.filter((F.col("is_orphan") == 0) & F.col("register_date").isNotNull())
    nu = p.groupBy(F.col("register_date").alias("dt"), "channel_id") \
        .agg(F.countDistinct("user_id").alias("new_users"))

    cost = channels_sdf.select("channel_id", F.col("daily_cost").alias("cost"))
    m = g.join(nu, on=["dt", "channel_id"], how="left").join(F.broadcast(cost), on="channel_id", how="left")
    m = m.fillna({"new_users": 0, "cost": 0}) \
        .withColumn("new_users", F.col("new_users").cast("int")) \
        .withColumn("roi", F.when(F.col("cost") == 0, F.lit(0)).otherwise(F.col("gmv") / F.col("cost")))
    return m.select("dt", "channel_id", "new_users", "orders", "paid_orders", "gmv",
                    "refund_orders", "refund_amount", "refund_rate", "cost", "roi")


def run_spark(start_date, end_date, mode: str) -> int:
    try:
        from pyspark.sql import SparkSession
    except ImportError:
        print("pyspark not importable; caller should retry with --engine pandas", file=sys.stderr)
        return 1
    import pandas as pd
    from rebuild_dws_pandas import persist_and_reconcile

    # Windows 本地 pyspark 常因 IPv4/IPv6 localhost 不匹配导致 "Python worker failed to connect back"。
    # 仅在 win32 强制 IPv4 + 驱动绑回环，使本地模式开箱即用；Linux/集群不受影响。
    if sys.platform == "win32":
        os.environ.setdefault("_JAVA_OPTIONS", "-Djava.net.preferIPv4Stack=true")
    builder = (SparkSession.builder.appName("dw_etl_rebuild_dws")
               .config("spark.sql.session.timeZone", "Asia/Shanghai")
               .config("spark.driver.memory", "1g"))
    if sys.platform == "win32":
        builder = (builder.config("spark.driver.host", "127.0.0.1")
                   .config("spark.driver.bindAddress", "127.0.0.1"))
    spark = builder.getOrCreate()
    try:
        engine = sa_engine("dw")
        try:
            sql = ("SELECT dt, channel_id, order_id, order_status, amount, is_refund "
                   "FROM dwd_order_detail WHERE dt BETWEEN %s AND %s")
            orders_pd = pd.read_sql(sql, engine, params=(start_date, end_date))
            profile_pd = pd.read_sql(
                "SELECT user_id, channel_id, register_date, is_orphan FROM dwd_user_profile", engine)
            channels_pd = pd.read_sql("SELECT channel_id, daily_cost FROM dim_channel", engine)
        finally:
            engine.dispose()
        # 生产环境可改为 SparkSession.read.jdbc(...)，此处用 pandas→createDataFrame 避免依赖 JDBC 驱动 jar
        orders_sdf = spark.createDataFrame(orders_pd)
        profile_sdf = spark.createDataFrame(profile_pd)
        channels_sdf = spark.createDataFrame(channels_pd)
        built_sdf = _aggregate_spark(orders_sdf, profile_sdf, channels_sdf)
        built = built_sdf.toPandas()
        conn = connect(database="dw")
        try:
            return persist_and_reconcile(conn, built, start_date, end_date, mode, "spark")
        finally:
            conn.close()
    finally:
        spark.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Spark/pandas 重算 DWS 并与 SQL 结果逐日对账")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--mode", choices=["reconcile", "rebuild", "both"], default="both")
    parser.add_argument("--engine", choices=["spark", "pandas"], default="spark")
    args = parser.parse_args()
    try:
        start, end = parse_date(args.start_date), parse_date(args.end_date)
        if args.engine == "pandas":
            from rebuild_dws_pandas import run
            return run(start, end, args.mode)
        return run_spark(start, end, args.mode)
    except Exception as exc:
        print(f"spark rebuild failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
