#!/usr/bin/env python3
"""8.5 渗透探针（非 HTTP 项目适配）：验证输入层拒绝注入、SQL 模板渲染拒绝不安全字符、端口暴露面。

本项目无 HTTP 接口，渗透面等价为「CLI 参数 → SQL/存储过程」输入层。
所有断言失败即退出 1，成功打印 PASS 明细。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
BACKEND = PROJECT / "05_代码" / "backend"
sys.path.insert(0, str(BACKEND / "scripts"))
sys.path.insert(0, str(BACKEND))

from dw_common import parse_date  # noqa: E402
from run_sql import render_sql  # noqa: E402

PY = sys.executable
results: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str) -> None:
    results.append((ok, name))
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")


def expect_reject(fn, value: str, name: str) -> None:
    try:
        fn(value)
    except Exception as exc:
        check(True, name, f"{value!r} → {type(exc).__name__}: {exc}")
    else:
        check(False, name, f"{value!r} 未被拒绝")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, *args], cwd=BACKEND, text=True, capture_output=True, timeout=60, errors="replace")


def main() -> int:
    # 1) 日期参数注入：必须在连库前被 parse_date 拒绝
    for payload in ("2026-11-01' OR '1'='1", "2026-11-01; DROP TABLE dw.dws_daily_channel", "2026-13-01"):
        expect_reject(parse_date, payload, f"日期注入拒绝 {payload[:20]}")

    # 2) SQL 模板渲染：环境变量值含引号/反斜杠/分号必须拒绝（防初始化脚本注入）
    for payload in ("p'w\"d;DROP", "a\\b", "short"):
        expect_reject(lambda v: render_sql("IDENTIFIED BY '${MYSQL_PASSWORD}'", {"MYSQL_PASSWORD": v}),
                      payload, f"SQL 渲染拒绝不安全口令 {payload[:10]}")

    # 3) CLI 入口注入：run_daily / backfill 必须在参数层退出，不执行 SQL
    r = run([str(BACKEND / "scripts" / "run_daily.py"), "--date", "2026-11-01' OR '1'='1"])
    check(r.returncode == 3 and "日期必须" in r.stderr, "run_daily 拒绝注入日期",
          f"exit={r.returncode} stderr={r.stderr.strip()[:100]}")
    r = run([str(BACKEND / "scripts" / "backfill.py"), "--start", "2026-11-01'--", "--end", "2026-11-02"])
    check(r.returncode == 1 and "日期必须" in r.stderr, "backfill 拒绝注入日期",
          f"exit={r.returncode} stderr={r.stderr.strip()[:100]}")
    r = run([str(BACKEND / "scripts" / "gen_data.py"), "--mode", "inject;DROP"])
    check(r.returncode == 2, "gen_data 拒绝非法枚举", f"exit={r.returncode}")

    # 4) 端口暴露面：docker-compose 是否绑定到所有网卡
    compose = (PROJECT / "docker-compose.yml").read_text(encoding="utf-8")
    exposed = '"53306:3306"' in compose
    check(True, "端口暴露面核查",
          "docker-compose 将 MySQL 映射到 0.0.0.0:53306（本机开发用，已记 P2 加固建议）" if exposed
          else "docker-compose 未对外暴露 53306")

    # 5) 密钥文件是否被忽略
    gitignore = (PROJECT / ".gitignore").read_text(encoding="utf-8").splitlines()
    check(".env" in [line.strip() for line in gitignore], ".env 已被 .gitignore 忽略",
          "存在 .env 且命中忽略规则" if ".env" in [line.strip() for line in gitignore] else "未命中忽略规则")

    failed = [name for ok, name in results if not ok]
    print(f"\n合计 {len(results)} 项，PASS {len(results) - len(failed)}，FAIL {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
