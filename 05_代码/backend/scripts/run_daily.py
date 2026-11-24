from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from dw_common import BACKEND_DIR, REPORTS_DIR, connect, json_default, parse_date, write_json_atomic  # noqa: E402

EXIT_CODES = {"success": 0, "skipped": 0, "blocked": 2, "failed": 3}


def exit_code_for_status(status: str) -> int:
    return EXIT_CODES.get(status, 3)


def write_summary(path: Path, *, batch_id: str, dt: date, status: str, layers: dict, exit_code: int, engine: str | None = None) -> None:
    payload = {"batch_id": batch_id, "dt": dt, "status": status, "layers": layers, "exit_code": exit_code}
    if engine:
        payload["reconcile_engine"] = engine
    write_json_atomic(path, payload)


def call_etl(dt: date) -> tuple[str, int, str, dict[str, int]]:
    connection = connect(database="dw")
    try:
        with connection.cursor() as cursor:
            cursor.execute("CALL sp_etl_daily(%s,@p_status,@p_rows)", (dt.isoformat(),))
            while cursor.nextset():
                pass
            cursor.execute("SELECT @p_status,@p_rows")
            status, total_rows = cursor.fetchone()
            cursor.execute("SELECT batch_id FROM etl_run_log WHERE dt=%s ORDER BY start_at DESC LIMIT 1", (dt,))
            row = cursor.fetchone()
            batch_id = row[0] if row else ""
            cursor.execute("SELECT layer,rows_out FROM etl_run_log WHERE dt=%s AND batch_id=%s ORDER BY log_id", (dt, batch_id))
            layers = {layer: rows for layer, rows in cursor.fetchall()}
        connection.commit()
        return status, int(total_rows or 0), batch_id, layers
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def collect_metrics(dt: date) -> dict[str, str]:
    connection = connect(database="dw")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT kpi_code,kpi_value FROM ads_kpi_daily WHERE dt=%s", (dt,))
            metrics = {code: str(value) for code, value in cursor.fetchall()}
            cursor.execute("SELECT COALESCE(SUM(gmv)/NULLIF(SUM(cost),0),0) FROM ads_channel_roi WHERE dt=%s", (dt,))
            metrics["ROI"] = str(cursor.fetchone()[0])
            return metrics
    finally:
        connection.close()


def run_stage(script: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([sys.executable, str(script), *arguments], cwd=BACKEND_DIR,
                            text=True, capture_output=True, timeout=3600, errors="replace")
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result


def render_daily_report(dt: date, batch_id: str, status: str, layers: dict, metrics: dict, quality_counts: dict, elapsed: str) -> str:
    lines = [f"# 指标日报 {dt}", "", "> 模拟业务数据；本报告由 run_daily.py 根据数据库查询结果生成。", "", f"- 批次：`{batch_id}`", f"- 状态：`{status}`", f"- 耗时：{elapsed}", "", "## 分层行数", "", "| 层 | 行数 |", "|---|---:|"]
    lines += [f"| {layer} | {layers.get(layer, 0)} |" for layer in ("ODS", "DWD", "DWS", "ADS")]
    lines += ["", "## 一级指标", "", "| 指标 | 实测值 |", "|---|---:|"]
    lines += [f"| {code} | {metrics.get(code, '无数据')} |" for code in ("NEW_USERS", "GMV", "REFUND_RATE", "ROI")]
    lines += ["", "## 质量计数", "", f"- P0：{quality_counts.get('P0', 0)}", f"- P1：{quality_counts.get('P1', 0)}", f"- P2：{quality_counts.get('P2', 0)}", ""]
    return "\n".join(lines)


def quality_counts(dt: date) -> dict[str, int]:
    connection = connect(database="dw")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT level,COUNT(*) FROM dq_report WHERE dt=%s AND status<>'pass' GROUP BY level", (dt,))
            return {level: count for level, count in cursor.fetchall()}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="运行单日 ODS→DWD→DWS→ADS、质量与可选重算对账")
    parser.add_argument("--date", required=True)
    parser.add_argument("--with-spark", action="store_true", help="先尝试 Spark；运行失败时显式降级 pandas")
    parser.add_argument("--skip-quality", action="store_true")
    args = parser.parse_args()
    started = __import__("time").perf_counter()
    try:
        dt = parse_date(args.date)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 3
    status = "failed"
    batch_id = f"B{dt:%Y%m%d}-PRECHECK"
    layers: dict[str, int] = {}
    metrics: dict[str, str] = {}
    counts: dict[str, int] = {}
    engine = None
    code = 3
    try:
        status, _, batch_id, layers = call_etl(dt)
        code = exit_code_for_status(status)
        if code == 0:
            if not args.skip_quality:
                result = run_stage(BACKEND_DIR / "quality" / "run_rules.py", ["--date", dt.isoformat()])
                if result.returncode == 2:
                    status, code = "blocked", 2
                elif result.returncode != 0:
                    status, code = "failed", 3
            if code == 0 and args.with_spark:
                start = dt - timedelta(days=89)
                common_args = ["--start-date", start.isoformat(), "--end-date", dt.isoformat(), "--mode", "both", "--engine", "spark"]
                result = run_stage(BACKEND_DIR / "spark" / "spark_rebuild_dws.py", common_args)
                engine = "spark"
                if result.returncode == 1:
                    print("Spark 运行失败，显式切换 pandas 降级通道。", file=sys.stderr)
                    engine = "pandas"
                    pandas_args = common_args.copy()
                    pandas_args[-1] = "pandas"
                    result = run_stage(BACKEND_DIR / "spark" / "spark_rebuild_dws.py", pandas_args)
                if result.returncode == 4:
                    status, code = "failed", 4
                elif result.returncode != 0:
                    status, code = "failed", 3
            if code == 0:
                metrics = collect_metrics(dt)
                counts = quality_counts(dt) if not args.skip_quality else {}
    except Exception as exc:
        message = str(exc)
        if "E_DWD_P0" in message:
            status, code = "blocked", 2
        else:
            status, code = "failed", 3
        print(f"ETL failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    elapsed = f"{__import__('time').perf_counter() - started:.3f}s"
    try:
        report = render_daily_report(dt, batch_id, status, layers, metrics, counts, elapsed)
        (REPORTS_DIR / f"daily_{dt}.md").write_text(report, encoding="utf-8")
        write_summary(REPORTS_DIR / "run_summary.json", batch_id=batch_id, dt=dt, status=status, layers=layers, exit_code=code, engine=engine)
    except OSError as exc:
        print(f"report writing failed: {exc}", file=sys.stderr)
        return 5
    print(json.dumps({"dt": dt, "batch_id": batch_id, "status": status, "layers": layers, "metrics": metrics, "exit_code": code}, ensure_ascii=False, default=json_default))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
