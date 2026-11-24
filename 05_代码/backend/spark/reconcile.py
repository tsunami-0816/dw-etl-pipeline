from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

# Spark/pandas 聚合口径与 sp_build_dws 完全一致（D-27 / D-29）：
# gmv = 支付订单 amount 合计；refund_* 按 is_refund；new_users 来自 dwd_user_profile；cost 来自 dim_channel。
AGG_COLS = ["dt", "channel_id", "new_users", "orders", "paid_orders", "gmv",
            "refund_orders", "refund_amount", "refund_rate", "cost", "roi"]
COMPARE_COLS = ["orders", "paid_orders", "gmv", "refund_orders", "refund_amount"]


def aggregate_channel(orders: pd.DataFrame, profile: pd.DataFrame, channels: pd.DataFrame) -> pd.DataFrame:
    """按 (dt, channel_id) 聚合，与存储过程 sp_build_dws 同口径。"""
    if orders.empty:
        return pd.DataFrame(columns=AGG_COLS)
    o = orders.copy()
    o["amount"] = pd.to_numeric(o["amount"], errors="coerce").fillna(0)
    paid = o["order_status"].isin(["paid", "refunded"])
    o["_paid"] = paid.astype(int)
    o["_gmv"] = o["amount"].where(paid, 0)
    o["_refund_amt"] = o["amount"].where(o["is_refund"] == 1, 0)
    g = o.groupby(["dt", "channel_id"], as_index=False).agg(
        orders=("order_id", "count"),
        paid_orders=("_paid", "sum"),
        gmv=("_gmv", "sum"),
        refund_orders=("is_refund", "sum"),
        refund_amount=("_refund_amt", "sum"),
    )
    paid_orders = g["paid_orders"].astype(float)
    g["refund_rate"] = (g["refund_orders"].astype(float) / paid_orders.where(paid_orders != 0)).fillna(0)

    p = profile.copy()
    p = p[(p["is_orphan"] == 0) & p["register_date"].notna()]
    nu = p.groupby(["register_date", "channel_id"], as_index=False).agg(new_users=("user_id", "nunique"))
    nu = nu.rename(columns={"register_date": "dt"})

    cost = channels[["channel_id", "daily_cost"]].rename(columns={"daily_cost": "cost"})
    cost["cost"] = pd.to_numeric(cost["cost"], errors="coerce").fillna(0)
    m = g.merge(nu, on=["dt", "channel_id"], how="left").merge(cost, on="channel_id", how="left")
    m["new_users"] = m["new_users"].fillna(0).astype(int)
    m["cost"] = m["cost"].fillna(0).astype(float)
    m["roi"] = (m["gmv"].astype(float) / m["cost"].where(m["cost"] != 0)).fillna(0)
    m = m[AGG_COLS].sort_values(["dt", "channel_id"]).reset_index(drop=True)
    for c in ["orders", "paid_orders", "refund_orders"]:
        m[c] = m[c].astype(int)
    return m


def reconcile(built: pd.DataFrame, dws: pd.DataFrame, tol: float = 0.01) -> tuple[pd.DataFrame, int]:
    """逐日 full outer join；缺行、金额差 >tol 或计数不等均判差异。返回 (差异明细, 差异行数)。"""
    a = built[["dt", "channel_id"] + COMPARE_COLS].copy() if not built.empty else pd.DataFrame(columns=["dt", "channel_id"] + COMPARE_COLS)
    b = dws[["dt", "channel_id"] + COMPARE_COLS].copy() if not dws.empty else pd.DataFrame(columns=["dt", "channel_id"] + COMPARE_COLS)
    m = a.merge(b, on=["dt", "channel_id"], how="outer", suffixes=("_built", "_dws"), indicator=True)
    diffs = []
    for _, row in m.iterrows():
        reason = []
        if row["_merge"] != "both":
            reason.append("侧缺失" if row["_merge"] == "left_only" else "DWS 缺失")
        else:
            for c in COMPARE_COLS:
                va, vb = row[f"{c}_built"], row[f"{c}_dws"]
                if c == "gmv":
                    if abs(float(va) - float(vb)) > tol:
                        reason.append(f"{c} 差 {float(va) - float(vb):.4f}")
                elif int(va) != int(vb):
                    reason.append(f"{c} 不等({va}!={vb})")
        if reason:
            diffs.append({"dt": row["dt"], "channel_id": row["channel_id"], "reason": "; ".join(reason)})
    diff_df = pd.DataFrame(diffs, columns=["dt", "channel_id", "reason"])
    return diff_df, len(diff_df)


def render_report(start_date: date, end_date: date, engine: str, diff_count: int, diff_df: pd.DataFrame) -> str:
    lines = [
        "# Spark/pandas 与 DWS 逐日对账报告", "",
        f"- 对账区间：{start_date} ～ {end_date}",
        f"- 重算引擎：{engine}",
        f"- 差异总行数：{diff_count}",
        f"- 结论：{'通过（0 差异）' if diff_count == 0 else '存在差异，退出码 4'}", "",
    ]
    if diff_count:
        lines += ["| dt | channel_id | 差异原因 |", "|---|---:|---|"]
        for _, r in diff_df.iterrows():
            lines.append(f"| {r['dt']} | {r['channel_id']} | {r['reason']} |")
    else:
        lines.append("> 逐日 full outer join 后无缺行且金额/计数一致（金额容差 0.01）。")
    return "\n".join(lines) + "\n"
