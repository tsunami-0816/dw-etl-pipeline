"""D-24 并发锁互斥实测：同时启动两个 run_daily --date <同一天>，
断言恰好一个 success(exit 0)、一个被命名锁拒绝(exit 3)，
且 etl_run_log 留 success + E_LOCK 失败两行，最终当日数据有效。

用法：python concurrency_test.py --date 2026-11-27 [--retries 3]
退出码：0 断言通过；1 未复现互斥。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "05_代码" / "backend"
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
from dw_common import connect, load_environment  # noqa: E402

TARGET_TABLES = [
    "ods_order", "dwd_order_detail", "dws_daily_channel", "dws_daily_category",
    "ads_kpi_daily", "ads_channel_roi", "ads_channel_daily", "ads_category_sales",
]


def reset_day(dt: str) -> None:
    """清空当日所有层数据与运行日志，使当日回到未运行状态。"""
    conn = connect(database="dw")
    try:
        with conn.cursor() as c:
            for t in TARGET_TABLES:
                c.execute(f"DELETE FROM {t} WHERE dt=%s", (dt,))
            c.execute("DELETE FROM etl_run_log WHERE dt=%s", (dt,))
            c.execute("DELETE FROM dq_report WHERE dt=%s", (dt,))
            c.execute("DELETE FROM dq_issue WHERE dt=%s", (dt,))
            c.execute("DELETE FROM dq_alert WHERE dt=%s", (dt,))
        conn.commit()
    finally:
        conn.close()


def run_one(dt: str) -> tuple[int, str]:
    p = subprocess.Popen(
        [sys.executable, str(BACKEND_DIR / "scripts" / "run_daily.py"), "--date", dt],
        cwd=str(BACKEND_DIR),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
    )
    out, _ = p.communicate()
    return p.returncode, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()
    dt = args.date
    load_environment()

    for attempt in range(1, args.retries + 1):
        print(f"\n=== concurrency attempt {attempt}/{args.retries} for {dt} ===")
        reset_day(dt)
        # 同时启动两个进程（列表推导内毫秒级差）
        procs = [subprocess.Popen(
            [sys.executable, str(BACKEND_DIR / "scripts" / "run_daily.py"), "--date", dt],
            cwd=str(BACKEND_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        ) for _ in range(2)]
        outs = [p.communicate()[0] for p in procs]
        codes = [p.returncode for p in procs]
        print(f"returncodes = {codes}")
        for i, (code, out) in enumerate(zip(codes, outs), 1):
            last = [ln for ln in out.splitlines() if ln.strip()][-1:] if out.strip() else []
            print(f"  proc#{i} exit={code} | tail: {last[0] if last else '(no output)'}")

        success = codes.count(0)
        lock_fail = sum(1 for c in codes if c == 3)
        if success == 1 and lock_fail == 1:
            # 查 etl_run_log 证据
            conn = connect(database="dw")
            try:
                with conn.cursor() as c:
                    c.execute("SELECT batch_id, status, error_code, error_msg FROM etl_run_log "
                              "WHERE dt=%s AND layer='ALL' ORDER BY log_id", (dt,))
                    rows = c.fetchall()
                    print("etl_run_log(layer=ALL) rows:")
                    for r in rows:
                        print(f"  batch={r[0]!r} status={r[1]} err_code={r[2]} err_msg={r[3]!r}")
                    has_success = any(r[1] == "success" for r in rows)
                    has_lock = any(r[3] and "LOCK" in (r[3] or "").upper() for r in rows)
                    c.execute("SELECT COUNT(*) FROM dwd_order_detail WHERE dt=%s", (dt,))
                    dwd = c.fetchone()[0]
                    c.execute("SELECT kpi_value FROM ads_kpi_daily WHERE dt=%s AND kpi_code='GMV'", (dt,))
                    gmv = c.fetchone()
            finally:
                conn.close()
            print(f"verdict: has_success={has_success} has_lock_fail={has_lock} dwd_rows={dwd} ads_gmv={gmv[0] if gmv else None}")
            if has_success and has_lock and dwd > 0 and gmv and gmv[0] is not None:
                print("== CONCURRENCY TEST PASS: 命名锁互斥生效，一胜一负，胜者数据有效 ==")
                return 0
        print(f"attempt {attempt}: success={success} lock_fail={lock_fail} — 未复现互斥，重置后重试")

    print(f"== CONCURRENCY TEST FAIL: {args.retries} 次均未复现一胜一负 ==")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
