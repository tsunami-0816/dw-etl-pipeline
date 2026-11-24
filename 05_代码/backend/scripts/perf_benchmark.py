from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from dw_common import BACKEND_DIR, REPORTS_DIR, connect, parse_date  # noqa: E402


def median_ms(values: list[float]) -> float:
    if not values:
        raise ValueError("至少需要一个耗时样本")
    return float(statistics.median(values))


def speedup(before_ms: Decimal | float, after_ms: Decimal | float) -> Decimal | None:
    after = Decimal(str(after_ms))
    if after == 0:
        return None
    return (Decimal(str(before_ms)) / after).quantize(Decimal("0.01"))


def run_timed(cursor, sql: str, params: dict, repeats: int = 5) -> tuple[list[float], list[str]]:
    timings: list[float] = []
    plan: list[str] = []
    for _ in range(repeats):
        started = time.perf_counter()
        cursor.execute("EXPLAIN ANALYZE " + sql, params)
        rows = cursor.fetchall()
        timings.append((time.perf_counter() - started) * 1000)
        if not plan:
            plan = [str(row[0]) for row in rows]
    return timings, plan


def render_report(results: list[dict], slow_log_count: int | None) -> str:
    lines = ["# 查询性能实测报告", "", "> 每次执行 EXPLAIN ANALYZE，连续 5 次取中位数；本文件仅由真实运行生成。", "", "| 查询 | 优化前 ms | 优化后 ms | 提升倍数 |", "|---|---:|---:|---:|"]
    for item in results:
        ratio = item["speedup"]
        lines.append(f"| {item['name']} | {item['before_ms']:.3f} | {item['after_ms']:.3f} | {ratio if ratio is not None else 'N/A'} |")
    lines += ["", f"mysql.slow_log 当前查询结果行数：{slow_log_count if slow_log_count is not None else '未查询（账号无权限）'}。", ""]
    for item in results:
        lines += [f"## {item['name']} 执行计划", "", "### 优化前", "", "```text", *item["before_plan"], "```", "", "### 优化后", "", "```text", *item["after_plan"], "```", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Q1/Q2/Q3 各跑 5 次 EXPLAIN ANALYZE 并生成真实性能报告")
    parser.add_argument("--start-date", default="2026-08-26")
    parser.add_argument("--end-date", default="2026-11-23")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats 必须为正数")
    try:
        params = {"start_date": parse_date(args.start_date), "end_date": parse_date(args.end_date)}
        connection = connect(database="dw")
        results = []
        try:
            with connection.cursor() as cursor:
                for name in ("q1", "q2", "q3"):
                    before_sql = (BACKEND_DIR / "sql" / "perf" / f"{name}_before.sql").read_text(encoding="utf-8").strip().rstrip(";")
                    after_sql = (BACKEND_DIR / "sql" / "perf" / f"{name}_after.sql").read_text(encoding="utf-8").strip().rstrip(";")
                    before, before_plan = run_timed(cursor, before_sql, params, args.repeats)
                    after, after_plan = run_timed(cursor, after_sql, params, args.repeats)
                    before_ms, after_ms = median_ms(before), median_ms(after)
                    results.append({"name": name.upper(), "before_ms": before_ms, "after_ms": after_ms, "speedup": speedup(before_ms, after_ms), "before_plan": before_plan, "after_plan": after_plan})
                try:
                    cursor.execute("SELECT COUNT(*) FROM mysql.slow_log")
                    slow_count = cursor.fetchone()[0]
                except Exception:
                    slow_count = None
        finally:
            connection.close()
        (REPORTS_DIR / "perf_report.md").write_text(render_report(results, slow_count), encoding="utf-8")
        print(f"performance report: {REPORTS_DIR / 'perf_report.md'}")
        return 0
    except Exception as exc:
        print(f"performance benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
