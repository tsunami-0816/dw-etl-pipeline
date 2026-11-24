from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from dw_common import connect, parse_date, validate_date_range  # noqa: E402


def select_dates(start_date: date, end_date: date, failed_dates: set[date], retry_failed: bool) -> list[date]:
    validate_date_range(start_date, end_date)
    dates = [start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)]
    return [dt for dt in dates if dt in failed_dates] if retry_failed else dates


def query_failed_dates(start_date: date, end_date: date) -> set[date]:
    connection = connect(database="dw")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT dt FROM etl_run_log WHERE dt BETWEEN %s AND %s AND status IN ('failed','blocked')", (start_date, end_date))
            return {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="按日期升序补数；--retry-failed 只选择失败/阻断日")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    try:
        start_date, end_date = parse_date(args.start), parse_date(args.end)
        failed = query_failed_dates(start_date, end_date) if args.retry_failed else set()
        dates = select_dates(start_date, end_date, failed, args.retry_failed)
    except Exception as exc:
        print(f"backfill precheck failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    failures = []
    for dt in dates:
        result = subprocess.run([sys.executable, str(SCRIPT_DIR / "run_daily.py"), "--date", dt.isoformat()], cwd=SCRIPT_DIR.parent)
        print(f"backfill {dt}: exit={result.returncode}")
        if result.returncode != 0:
            failures.append(dt)
    print(f"backfill summary: total={len(dates)} failed={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
