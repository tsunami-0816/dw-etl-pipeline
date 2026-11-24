"""第 6 步冒烟字段值断言：不只断 exit 0，必须断言关键字段值与跨层一致性。

用法：python smoke_assert.py --date 2026-11-26
退出码：0 全部通过；1 存在失败断言。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "05_代码" / "backend"
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
from dw_common import connect, load_environment  # noqa: E402


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

    # 读取 run_summary.json 作为期望值来源（不硬编码金额）
    summary_path = BACKEND_DIR / "reports" / "run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    exp_layers = summary.get("layers", {})
    print(f"== smoke assertions for {dt} ==")
    print(f"run_summary: status={summary.get('status')} exit={summary.get('exit_code')} layers={exp_layers}")

    ok = True
    conn = connect(database="dw")
    try:
        with conn.cursor() as c:
            # A1: etl_run_log 当日 layer=ALL 必须 success；run_summary 状态/退出码一致
            rows = _q(c, "SELECT status, batch_id FROM etl_run_log WHERE dt=%s AND layer='ALL' ORDER BY log_id DESC LIMIT 1", (dt,))
            log_status = rows[0][0] if rows else None
            log_batch = rows[0][1] if rows else None
            ok &= check("A1 etl_run_log(layer=ALL)=success 且 summary exit_code=0",
                        log_status == "success" and summary.get("status") == "success" and summary.get("exit_code") == 0,
                        f"log_status={log_status} summary_status={summary.get('status')} exit={summary.get('exit_code')}")

            # A2: ads_kpi_daily 关键字段值断言（长表 kpi_code 行；非空 + 合法范围，不硬编码目标值）
            rows = _q(c, "SELECT kpi_code, kpi_value FROM ads_kpi_daily WHERE dt=%s", (dt,))
            kpi = {r[0]: Decimal(str(r[1])) for r in rows}
            ok &= check("A2 ads_kpi 覆盖 GMV/NEW_USERS/REFUND_RATE",
                        {"GMV", "NEW_USERS", "REFUND_RATE"} <= set(kpi), f"codes={sorted(kpi)}")
            if "GMV" in kpi:
                ok &= check("A2 GMV > 0", kpi["GMV"] > 0, f"GMV={kpi['GMV']}")
            if "NEW_USERS" in kpi:
                ok &= check("A2 NEW_USERS >= 0", kpi["NEW_USERS"] >= 0, f"NEW_USERS={kpi['NEW_USERS']}")
            if "REFUND_RATE" in kpi:
                ok &= check("A2 REFUND_RATE 在 [0,1]", Decimal("0") <= kpi["REFUND_RATE"] <= Decimal("1"),
                            f"REFUND_RATE={kpi['REFUND_RATE']}")

            # A3: 跨层一致性 —— dws_daily_channel.gmv 汇总 == ads_kpi GMV（容差0.01）
            r = _q(c, "SELECT COALESCE(SUM(gmv),0) FROM dws_daily_channel WHERE dt=%s", (dt,))
            dws_gmv = Decimal(str(r[0][0])) if r and r[0][0] is not None else Decimal(0)
            ads_gmv = kpi.get("GMV", Decimal("-1"))
            ok &= check("A3 dws_daily_channel.gmv 汇总 == ads_kpi GMV（容差0.01）",
                        ads_gmv >= 0 and abs(dws_gmv - ads_gmv) < Decimal("0.01"),
                        f"dws={dws_gmv} ads={ads_gmv}")

            # A3b: ads_channel_roi 当日有行（ROI 视角落地）
            r = _q(c, "SELECT COUNT(*) FROM ads_channel_roi WHERE dt=%s", (dt,))
            roi_cnt = r[0][0] if r else 0
            ok &= check("A3 ads_channel_roi 当日有行", roi_cnt > 0, f"rows={roi_cnt}")

            # A4: dwd_order_detail 行数 == run_summary layers.DWD，且 > 0
            r = _q(c, "SELECT COUNT(*) FROM dwd_order_detail WHERE dt=%s", (dt,))
            dwd_cnt = r[0][0] if r else 0
            ok &= check("A4 dwd_order_detail 行数 == summary.DWD 且 >0",
                        dwd_cnt == exp_layers.get("DWD") and dwd_cnt > 0,
                        f"dwd={dwd_cnt} summary={exp_layers.get('DWD')}")

            # A5: dq_report 当日恰好 DQ01~DQ05 五行，关键规则级别/状态正确
            rows = _q(c, "SELECT rule_code, level, status, failed_rows FROM dq_report WHERE dt=%s ORDER BY rule_code", (dt,))
            codes = [x[0] for x in rows]
            ok &= check("A5 dq_report 覆盖 DQ01-DQ05", codes == ["DQ01", "DQ02", "DQ03", "DQ04", "DQ05"], f"codes={codes}")
            by = {x[0]: x for x in rows}
            if "DQ01" in by:
                ok &= check("A5 DQ01 level=P2", by["DQ01"][1] == "P2", f"got {by['DQ01'][1]}")
            if "DQ04" in by:
                ok &= check("A5 DQ04 status=pass（无 P0 触发）", by["DQ04"][2] == "pass", f"got {by['DQ04'][2]}")

            # A6: dq_alert 当日只允许 DQ05（P2 warn）告警，不得有 DQ01-04 残留
            rows = _q(c, "SELECT rule_code FROM dq_alert WHERE dt=%s", (dt,))
            bad = [x[0] for x in rows if x[0] in ("DQ01", "DQ02", "DQ03", "DQ04")]
            ok &= check("A6 dq_alert 无 DQ01-04 告警（仅 P0/DQ05）", not bad, f"spurious={bad}")
            # 若 DQ05 warn 则应恰好 1 条 DQ05；DQ05 pass 则 0 条
            dq05 = next((x for x in by.values() if x[0] == "DQ05"), None)
            expect_dq05_alert = 1 if (dq05 and dq05[2] == "warn") else 0
            actual_dq05 = sum(1 for x in rows if x[0] == "DQ05")
            ok &= check("A6 dq_alert DQ05 条数与规则状态一致",
                        actual_dq05 == expect_dq05_alert, f"expect={expect_dq05_alert} actual={actual_dq05}")

            # A7: ods_order 当日有数据且 batch_id 与 etl_run_log 一致（复用 A1 的 log_batch）
            r = _q(c, "SELECT COUNT(*), MAX(batch_id) FROM ods_order WHERE dt=%s", (dt,))
            ods_cnt, ods_batch = r[0] if r else (0, None)
            ok &= check("A7 ods_order 行数>0 且 batch_id 一致",
                        ods_cnt > 0 and ods_batch == log_batch,
                        f"ods_cnt={ods_cnt} ods_batch={ods_batch} log_batch={log_batch}")

    finally:
        conn.close()

    print("== " + ("ALL PASS" if ok else "HAS FAILURES") + " ==")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
