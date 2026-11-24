"""轻量多线程压测脚本（非 HTTP 项目：压测目标 = ADS 指标读查询）。

三档并发 10 / 20 / 50，每档持续 10 秒，输出平均 RT、P95 RT、TPS、错误率。
本项目无 HTTP 接口，按 step8 8.5.2 协议选型规则：私有/DB 查询协议不硬套 JMeter，
以自研多线程客户端作主压测工具；第二工具交叉验证未做（无 HTTP 对等工具），降级边界在 perf-report.md 写明。

用法：python perf_test.py
退出码 0 = 压测完成（含数据）；非 0 = 无 DB 或异常。
"""
from __future__ import annotations

import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent / "05_代码" / "backend" / "scripts"))

from common.db import HAS_DB, get_connection  # noqa: E402

QUERY = "SELECT kpi_code, kpi_value FROM ads_kpi_daily WHERE dt=%s"
PARAMS = ("2026-11-28",)
DURATION = 10  # 秒


def worker(stop_event: threading.Event, results: list, errors: list) -> None:
    conn = get_connection(database="dw", root=True)
    try:
        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                with conn.cursor() as c:
                    c.execute(QUERY, PARAMS)
                    c.fetchall()
                results.append((time.perf_counter() - t0) * 1000)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
    finally:
        conn.close()


def run_level(concurrency: int) -> dict:
    results: list[float] = []
    errors: list[str] = []
    stop = threading.Event()
    threads = [threading.Thread(target=worker, args=(stop, results, errors)) for _ in range(concurrency)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(DURATION)
    stop.set()
    for t in threads:
        t.join(timeout=5)
    elapsed = time.perf_counter() - t0
    if not results:
        return {"concurrency": concurrency, "avg_ms": 0, "p95_ms": 0, "tps": 0, "error_rate": 1.0, "samples": 0}
    results.sort()
    p95 = results[int(len(results) * 0.95)] if len(results) >= 20 else results[-1]
    total = len(results) + len(errors)
    return {
        "concurrency": concurrency,
        "avg_ms": round(statistics.mean(results), 2),
        "p95_ms": round(p95, 2),
        "tps": round(len(results) / elapsed, 1),
        "error_rate": round(len(errors) / total, 4) if total else 0,
        "samples": len(results),
    }


def main() -> int:
    if not HAS_DB:
        print("SKIP: 本机 MySQL 53306 不可达，压测跳过（CI 无 DB）")
        return 0
    print(f"== perf_test: query={QUERY!r}  duration={DURATION}s ==")
    summary = []
    for c in (10, 20, 50):
        row = run_level(c)
        summary.append(row)
        print(f"  concurrency={row['concurrency']:>2}  avg_ms={row['avg_ms']:>7}  p95_ms={row['p95_ms']:>7}  "
              f"tps={row['tps']:>8}  error_rate={row['error_rate']}  samples={row['samples']}")
    print("== perf_test done ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
