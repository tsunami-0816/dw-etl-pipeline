from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
sys.path.insert(0, str(BACKEND_DIR / "spark"))
from dw_common import REPORTS_DIR, connect, parse_date, sa_engine  # noqa: E402
from reconcile import aggregate_channel, reconcile, render_report  # noqa: E402

ENGINE = "pandas"
STG_TABLE = "stg_dws_daily_channel_spark"
DWS_TABLE = "dws_daily_channel"


def _to_decimal(value, places: int = 2) -> Decimal:
    return Decimal(str(round(float(value), places)))


def _fetch_batch_id(conn, start_date: date, end_date: date) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT batch_id FROM etl_run_log WHERE dt BETWEEN %s AND %s ORDER BY start_at DESC LIMIT 1",
            (start_date, end_date),
        )
        row = cur.fetchone()
    return row[0] if row else "BRECONCILE"


def _read_dwd(start_date: date, end_date: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    engine = sa_engine("dw")
    try:
        sql = (f"SELECT dt, channel_id, order_id, order_status, amount, is_refund "
               f"FROM dwd_order_detail WHERE dt BETWEEN %s AND %s")
        chunks = pd.read_sql(sql, engine, params=(start_date, end_date), chunksize=20000)
        orders = pd.concat(list(chunks), ignore_index=True)
        profile = pd.read_sql(
            "SELECT user_id, channel_id, register_date, is_orphan FROM dwd_user_profile", engine)
        channels = pd.read_sql("SELECT channel_id, daily_cost FROM dim_channel", engine)
        return orders, profile, channels
    finally:
        engine.dispose()


def _write_stg(conn, df: pd.DataFrame, start_date: date, end_date: date, batch_id: str, engine: str) -> None:
    with conn.cursor() as cur:
        # stg 主键为 (dt, channel_id) 不含 engine：跨引擎重跑必须按日期窗口整体清空，
        # 否则前一引擎的残留行与本次 INSERT 撞主键（冒烟实测：pandas 残留 → spark 重跑报 1062）。
        cur.execute(f"DELETE FROM {STG_TABLE} WHERE dt BETWEEN %s AND %s",
                    (start_date, end_date))
        rows = [
            (r.dt, int(r.channel_id), int(r.new_users), int(r.orders), int(r.paid_orders),
             _to_decimal(r.gmv), int(r.refund_orders), _to_decimal(r.refund_amount),
             _to_decimal(r.refund_rate, 4), _to_decimal(r.cost), _to_decimal(r.roi, 4), engine)
            for r in df.itertuples(index=False)
        ]
        if rows:
            cur.executemany(
                f"INSERT INTO {STG_TABLE}(dt, channel_id, new_users, orders, paid_orders, gmv, "
                f"refund_orders, refund_amount, refund_rate, cost, roi, engine) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )


def _upsert_dws(conn, df: pd.DataFrame, batch_id: str) -> None:
    with conn.cursor() as cur:
        for r in df.itertuples(index=False):
            cur.execute(
                f"INSERT INTO {DWS_TABLE}(dt, channel_id, new_users, orders, paid_orders, gmv, "
                f"refund_orders, refund_amount, refund_rate, cost, roi, batch_id) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                f"ON DUPLICATE KEY UPDATE new_users=VALUES(new_users), orders=VALUES(orders), "
                f"paid_orders=VALUES(paid_orders), gmv=VALUES(gmv), refund_orders=VALUES(refund_orders), "
                f"refund_amount=VALUES(refund_amount), refund_rate=VALUES(refund_rate), cost=VALUES(cost), "
                f"roi=VALUES(roi), batch_id=VALUES(batch_id)",
                (r.dt, int(r.channel_id), int(r.new_users), int(r.orders), int(r.paid_orders),
                 _to_decimal(r.gmv), int(r.refund_orders), _to_decimal(r.refund_amount),
                 _to_decimal(r.refund_rate, 4), _to_decimal(r.cost), _to_decimal(r.roi, 4), batch_id),
            )


def _log_reconcile(conn, start_date: date, end_date: date, diff_count: int, batch_id: str, status: str, engine: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO etl_run_log(batch_id, dt, layer, proc_name, start_at, end_at, rows_out, status, error_msg) "
            "VALUES (%s,%s,%s,%s,NOW(3),NOW(3),%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE end_at=NOW(3), rows_out=VALUES(rows_out), status=VALUES(status), "
            "error_msg=VALUES(error_msg)",
            (batch_id, end_date, "RECONCILE", f"rebuild_{engine}", diff_count, status,
             f"{start_date}~{end_date} diff={diff_count}"),
        )


def persist_and_reconcile(conn, built: pd.DataFrame, start_date: date, end_date: date,
                          mode: str, engine: str) -> int:
    """写 stg + upsert dws + 对账（Spark/pandas 共用，口径由 reconcile.aggregate_channel 保证一致）。"""
    batch_id = _fetch_batch_id(conn, start_date, end_date)
    if mode in ("rebuild", "both"):
        _write_stg(conn, built, start_date, end_date, batch_id, engine)
        _upsert_dws(conn, built, batch_id)
        conn.commit()
    if mode in ("reconcile", "both"):
        db_engine = sa_engine("dw")
        try:
            dws = pd.read_sql(
                f"SELECT dt, channel_id, new_users, orders, paid_orders, gmv, refund_orders, "
                f"refund_amount, refund_rate, cost, roi FROM {DWS_TABLE} WHERE dt BETWEEN %s AND %s",
                db_engine, params=(start_date, end_date))
        finally:
            db_engine.dispose()
        diff_df, diff_count = reconcile(built, dws)
        report = render_report(start_date, end_date, engine, diff_count, diff_df)
        (REPORTS_DIR / f"reconcile_{engine}_{start_date}_{end_date}.md").write_text(report, encoding="utf-8")
        status = "success" if diff_count == 0 else "failed"
        _log_reconcile(conn, start_date, end_date, diff_count, batch_id, status, engine)
        conn.commit()
        print({"engine": engine, "mode": mode, "built_rows": int(len(built)),
               "diff_count": diff_count, "dws_rows": int(len(dws))})
        return 4 if diff_count else 0
    print({"engine": engine, "mode": mode, "built_rows": int(len(built))})
    return 0


def run(start_date: date, end_date: date, mode: str = "both") -> int:
    conn = connect(database="dw")
    try:
        orders, profile, channels = _read_dwd(start_date, end_date)
        built = aggregate_channel(orders, profile, channels)
        return persist_and_reconcile(conn, built, start_date, end_date, mode, ENGINE)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="pandas 引擎重算 DWS 并对账（Spark 不可用时的降级路径）")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--mode", choices=["reconcile", "rebuild", "both"], default="both")
    args = parser.parse_args()
    try:
        return run(parse_date(args.start_date), parse_date(args.end_date), args.mode)
    except Exception as exc:
        print(f"pandas rebuild failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
