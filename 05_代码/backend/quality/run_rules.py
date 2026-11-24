from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
from dw_common import REPORTS_DIR, connect, parse_date  # noqa: E402

RULES_FILE = SCRIPT_DIR / "dq_rules.yml"
MONEY = Decimal("0.0001")


def load_rules() -> dict:
    """加载并校验 5 类规则：缺规则或非数值阈值拒绝。"""
    data = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = data.get("rules") or {}
    required = {"DQ01", "DQ02", "DQ03", "DQ04", "DQ05"}
    missing = required - set(rules)
    if missing:
        raise ValueError(f"dq_rules.yml 缺少规则: {sorted(missing)}")
    for code, rule in rules.items():
        if not isinstance(rule, dict) or "name" not in rule:
            raise ValueError(f"规则 {code} 缺少 name")
        if "p0_threshold" in rule and not isinstance(rule["p0_threshold"], (int, float)):
            raise ValueError(f"规则 {code} 的 p0_threshold 必须为数值")
        if "threshold" in rule and not isinstance(rule["threshold"], (int, float)):
            raise ValueError(f"规则 {code} 的 threshold 必须为数值")
    return rules


def level_for_rate(rate: float, rule: dict) -> str:
    """空渠道/重复率超过 p0_threshold 返回 P0，否则返回 base_level。"""
    if rate > float(rule.get("p0_threshold", 1)):
        return rule.get("p0_level", "P0")
    return rule.get("base_level", "P2")


def compute_results(cursor, dt: date, rules: dict) -> list[dict]:
    """独立复跑 5 类规则（与过程内联 SQL 双实现对账），返回每行含 report/issue 数据。"""
    results = []
    # DQ01 / DQ02 / DQ03 基于 ods_order 当日
    cursor.execute("SELECT COUNT(*) FROM ods_order WHERE dt=%s", (dt,))
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM ods_order WHERE dt=%s AND channel_id IS NULL", (dt,))
    null_ch = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) - COUNT(DISTINCT order_no) FROM ods_order WHERE dt=%s", (dt,))
    dup = cursor.fetchone()[0]
    # 与 sp_build_dwd 内联口径一致：负金额按去重后落地计数（dwd.dq_flag 位 1 = amount<0）
    cursor.execute("SELECT COUNT(*) FROM dwd_order_detail WHERE dt=%s AND (dq_flag & 1) = 1", (dt,))
    neg = cursor.fetchone()[0]

    null_rate = (null_ch / total) if total else 0.0
    dup_rate = (dup / total) if total else 0.0

    results.append(_row("DQ01", rules["DQ01"], total, null_ch, null_rate,
                        level_for_rate(null_rate, rules["DQ01"])))
    results.append(_row("DQ02", rules["DQ02"], total, dup, dup_rate,
                        level_for_rate(dup_rate, rules["DQ02"])))
    results.append(_row("DQ03", rules["DQ03"], total, neg,
                        (neg / total) if total else 0.0, rules["DQ03"]["level"]))

    # DQ04 与内联口径一致：失败数=落地孤儿行数，分母=ODS 当日总行数 total
    cursor.execute("SELECT COUNT(*) FROM dwd_order_detail WHERE dt=%s AND user_id = -1", (dt,))
    orphan = cursor.fetchone()[0]
    results.append(_row("DQ04", rules["DQ04"], total, orphan,
                        (orphan / total) if total else 0.0, rules["DQ04"]["level"]))

    # DQ05 GMV 日环比
    cursor.execute("SELECT kpi_value FROM ads_kpi_daily WHERE dt=%s AND kpi_code='GMV'", (dt,))
    row = cursor.fetchone()
    today = Decimal(str(row[0])) if row and row[0] is not None else Decimal(0)
    cursor.execute(
        "SELECT kpi_value FROM ads_kpi_daily WHERE dt=DATE_SUB(%s, INTERVAL 1 DAY) AND kpi_code='GMV'",
        (dt,),
    )
    yrow = cursor.fetchone()
    yesterday = Decimal(str(yrow[0])) if yrow and yrow[0] is not None else None
    threshold = Decimal(str(rules["DQ05"]["threshold"]))
    triggered = yesterday is not None and yesterday > 0 and abs(today - yesterday) / yesterday > threshold
    results.append({
        "rule_code": "DQ05", "rule_name": rules["DQ05"]["name"],
        "level": rules["DQ05"]["level"], "table_name": rules["DQ05"]["table"],
        "total_rows": 1, "failed_rows": 1 if triggered else 0,
        "pass_rate": Decimal(0) if triggered else Decimal(1),
        "status": "warn" if triggered else "pass",
        "detail": f"GMV 环比波动超 {threshold}: 今日={today} 昨日={yesterday}" if triggered
                  else "GMV 环比波动在阈值以内",
    })
    return results


def _row(code: str, rule: dict, total: int, failed: int, rate: float, level: str) -> dict:
    blocked = level == "P0"
    return {
        "rule_code": code, "rule_name": rule["name"], "level": level,
        "table_name": rule["table"], "total_rows": total, "failed_rows": failed,
        "pass_rate": (Decimal(1) - Decimal(str(rate)).quantize(MONEY)) if total else Decimal(1),
        "status": "blocked" if blocked else ("warn" if failed else "pass"),
        "detail": f"失败 {failed}/{total}（{Decimal(str(rate)).quantize(MONEY)}）",
    }


def write_report(cursor, dt: date, results: list[dict], batch_id: str) -> None:
    for r in results:
        cursor.execute(
            """INSERT INTO dq_report(dt, rule_code, rule_name, level, table_name, total_rows, failed_rows,
                   pass_rate, status, detail, batch_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE rule_name=VALUES(rule_name), level=VALUES(level),
                   table_name=VALUES(table_name), total_rows=VALUES(total_rows),
                   failed_rows=VALUES(failed_rows), pass_rate=VALUES(pass_rate),
                   status=VALUES(status), detail=VALUES(detail), batch_id=VALUES(batch_id)""",
            (dt, r["rule_code"], r["rule_name"], r["level"], r["table_name"], r["total_rows"],
             r["failed_rows"], r["pass_rate"], r["status"], r["detail"], batch_id),
        )


def write_alerts(cursor, dt: date, results: list[dict]) -> None:
    # 先清当日旧告警再写，保证重跑（含修复后）状态归一，与过程内 delete-then-insert 模式一致
    cursor.execute("DELETE FROM dq_alert WHERE dt=%s", (dt,))
    # 与 sp_build_dwd/sp_build_ads 内联一致：仅 P0 blocked 与 DQ05(P2) 触发告警；P1 warn 留痕不告警
    for r in results:
        if r["status"] == "blocked" or (r["rule_code"] == "DQ05" and r["status"] == "warn"):
            cursor.execute(
                """INSERT INTO dq_alert(dt, rule_code, level, message)
                   VALUES (%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE message=VALUES(message), level=VALUES(level)""",
                (dt, r["rule_code"], r["level"], f"{r['rule_name']}：{r['detail']}"),
            )


def render_markdown(dt: date, results: list[dict]) -> str:
    lines = [f"# 质量报告 {dt}", "", "> 由 quality/run_rules.py 独立复跑生成；与存储过程内联结果双实现对账。", "",
             "| 规则 | 名称 | 级别 | 总数 | 失败 | 通过率 | 状态 | 说明 |",
             "|---|---|---|---:|---:|---:|---|---|"]
    for r in results:
        lines.append(f"| {r['rule_code']} | {r['rule_name']} | {r['level']} | {r['total_rows']} | "
                     f"{r['failed_rows']} | {r['pass_rate']} | {r['status']} | {r['detail']} |")
    p0 = [r for r in results if r["level"] == "P0" and r["status"] == "blocked"]
    lines += ["", f"- P0 阻断：{len(p0)} 条", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="复跑 DQ01～DQ05 并写入治理表与 Markdown 报告")
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    try:
        dt = parse_date(args.date)
        rules = load_rules()
    except Exception as exc:
        print(f"quality precheck failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    try:
        connection = connect(database="dw")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT batch_id FROM etl_run_log WHERE dt=%s ORDER BY start_at DESC LIMIT 1", (dt,))
                row = cursor.fetchone()
                batch_id = row[0] if row else f"B{dt:%Y%m%d}-QUALITY"
                results = compute_results(cursor, dt, rules)
                write_report(cursor, dt, results, batch_id)
                write_alerts(cursor, dt, results)
            connection.commit()
        finally:
            connection.close()
        (REPORTS_DIR / f"dq_report_{dt}.md").write_text(render_markdown(dt, results), encoding="utf-8")
        p0_blocked = any(r["level"] == "P0" and r["status"] == "blocked" for r in results)
        summary = {r["rule_code"]: {"level": r["level"], "status": r["status"], "failed": r["failed_rows"]} for r in results}
        print({"dt": dt.isoformat(), "rules": summary, "p0_blocked": p0_blocked})
        return 2 if p0_blocked else 0
    except Exception as exc:
        print(f"quality run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
