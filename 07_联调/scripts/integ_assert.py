"""第 7 步联调专项断言：跨层一致性 + SCD2 版本链正确性（冒烟仅断 dws==ads gmv，这里补齐）。

用法：python integ_assert.py --date 2026-11-29
退出码：0 全部通过；1 存在失败断言。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "05_代码" / "backend"
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
from dw_common import connect, load_environment  # noqa: E402

TOL = Decimal("0.01")


def _q(cursor, sql, args=()):
    cursor.execute(sql, args)
    return cursor.fetchall()


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    dt = datetime.strptime(args.date, "%Y-%m-%d").date()
    load_environment()

    print(f"== integ assertions for {dt} ==")
    ok = True
    conn = connect(database="dw")
    try:
        with conn.cursor() as c:
            # ---- L-02 / C-01: dwd 聚合 == dws_daily_channel（同口径：gmv 仅 paid/refunded，orders 全量）----
            # 口径对齐 sp_build_dws：gmv=SUM(amount WHERE order_status IN ('paid','refunded'))；orders=COUNT(*)
            dwd = _q(c, """SELECT COALESCE(SUM(CASE WHEN order_status IN ('paid','refunded') THEN amount ELSE 0 END),0),
                                  COUNT(*)
                           FROM dwd_order_detail WHERE dt=%s""", (dt,))[0]
            dws = _q(c, "SELECT COALESCE(SUM(gmv),0), COALESCE(SUM(orders),0) FROM dws_daily_channel WHERE dt=%s", (dt,))[0]
            dwd_gmv, dwd_cnt = (Decimal(str(dwd[0])), dwd[1])
            dws_gmv, dws_orders = (Decimal(str(dws[0])), dws[1])
            ok &= check("C-01 dwd(paid/refunded).SUM(amount)==dws.SUM(gmv)（容差0.01）",
                        abs(dwd_gmv - dws_gmv) <= TOL,
                        f"dwd={dwd_gmv} dws={dws_gmv} diff={dwd_gmv - dws_gmv}")
            ok &= check("C-01 dwd 行数==dws.SUM(orders)",
                        dwd_cnt == dws_orders,
                        f"dwd_rows={dwd_cnt} dws_orders={dws_orders}")

            # ---- L-02 / C-02: dws_daily_category == ads_category_sales（按 category 全外连接）----
            diff = _q(c, """
                SELECT COALESCE(d.category_id,a.category_id) cat,
                       COALESCE(d.gmv,0) dws_gmv, COALESCE(a.gmv,0) ads_gmv,
                       COALESCE(d.orders,0) dws_orders, COALESCE(a.orders,0) ads_orders
                FROM (SELECT category_id, gmv, orders FROM dws_daily_category WHERE dt=%s) d
                LEFT JOIN (SELECT category_id, gmv, orders FROM ads_category_sales WHERE dt=%s) a
                  ON d.category_id=a.category_id
                UNION
                SELECT COALESCE(d.category_id,a.category_id), COALESCE(d.gmv,0), COALESCE(a.gmv,0),
                       COALESCE(d.orders,0), COALESCE(a.orders,0)
                FROM (SELECT category_id, gmv, orders FROM dws_daily_category WHERE dt=%s) d
                RIGHT JOIN (SELECT category_id, gmv, orders FROM ads_category_sales WHERE dt=%s) a
                  ON d.category_id=a.category_id
            """, (dt, dt, dt, dt))
            bad = [r for r in diff if abs(Decimal(str(r[1])) - Decimal(str(r[2]))) > TOL or r[3] != r[4]]
            ok &= check("C-02 dws_category==ads_category（gmv 容差0.01 / orders 相等）",
                        len(bad) == 0,
                        f"checked={len(diff)} mismatches={len(bad)}" + (f" sample={bad[:2]}" if bad else ""))

            # ---- L-02 / C-03: dws_daily_channel == ads_channel_daily（gmv/orders/new_users/refund_orders）----
            ch = _q(c, """
                SELECT COALESCE(d.channel_id,a.channel_id) ch,
                       COALESCE(d.gmv,0) dg, COALESCE(a.gmv,0) ag,
                       COALESCE(d.orders,0) dord, COALESCE(a.orders,0) aord,
                       COALESCE(d.new_users,0) dnu, COALESCE(a.new_users,0) anu,
                       COALESCE(d.refund_orders,0) dro, COALESCE(a.refund_orders,0) aro
                FROM (SELECT channel_id,gmv,orders,new_users,refund_orders FROM dws_daily_channel WHERE dt=%s) d
                LEFT JOIN (SELECT channel_id,gmv,orders,new_users,refund_orders FROM ads_channel_daily WHERE dt=%s) a
                  ON d.channel_id=a.channel_id
            """, (dt, dt))
            bad_ch = [r for r in ch
                      if abs(Decimal(str(r[1])) - Decimal(str(r[2]))) > TOL
                      or r[3] != r[4] or r[5] != r[6] or r[7] != r[8]]
            ok &= check("C-03 dws_channel==ads_channel_daily（gmv容差0.01 / orders/new_users/refund相等）",
                        len(bad_ch) == 0,
                        f"checked={len(ch)} mismatches={len(bad_ch)}" + (f" sample={bad_ch[:2]}" if bad_ch else ""))

            # ---- L-02 / C-05: SCD2 dim_user 版本链正确性 ----
            # (a) 同一 user_id + start_date 唯一
            dup_start = _q(c, "SELECT user_id, start_date, COUNT(*) c FROM dim_user GROUP BY user_id, start_date HAVING COUNT(*)>1 LIMIT 5")
            ok &= check("SCD2-a 同 user 同一 start_date 唯一", len(dup_start) == 0, f"dups={len(dup_start)}")
            # (b) 每个 user 最多 1 个 is_current=1
            multi_cur = _q(c, "SELECT user_id, COUNT(*) c FROM dim_user WHERE is_current=1 GROUP BY user_id HAVING COUNT(*)>1 LIMIT 5")
            ok &= check("SCD2-b 每 user is_current=1 行数≤1", len(multi_cur) == 0, f"multi={len(multi_cur)}")
            # (c) 版本区间不重叠：同 user 两版本区间相交（end_date 视为含当天，用 < 比较避免相邻相接误报）
            overlap = _q(c, """
                SELECT a.user_id, a.version, b.version FROM dim_user a
                JOIN dim_user b ON a.user_id=b.user_id AND a.version<>b.version
                WHERE a.start_date < b.end_date AND b.start_date < a.end_date
                LIMIT 5
            """)
            ok &= check("SCD2-c 版本区间不重叠（同 user 无相交区间）", len(overlap) == 0, f"overlaps={len(overlap)}")

            # ---- dim_date 覆盖当日 ----
            dd = _q(c, "SELECT COUNT(*) FROM dim_date WHERE dt=%s", (dt,))[0][0]
            ok &= check("dim_date 覆盖当日", dd == 1, f"rows={dd}")

    finally:
        conn.close()

    print("== ALL PASS ==" if ok else "== HAS FAILURES ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
