"""qa-automation 全局 fixture：DB 连接 + 无 DB 时集成用例自动跳过 + 隔离日造数清理。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# 让 testcases 下的用例能 import common 与 backend 脚本
ROOT = Path(__file__).resolve().parents[2]  # qa-automation
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.db import BACKEND, HAS_DB, get_connection  # noqa: E402

# 隔离测试日：在基线窗口（11-10~11-28）之外，造数/注入/清理均不影响干净基线
ISOLATED_DAY = "2026-11-29"
# 隔离日涉及的所有层表（清理时按 dt 删）
_DAY_TABLES = [
    "ods_order", "ods_user", "ods_product", "ods_channel",
    "dwd_order_detail",
    "dws_daily_channel", "dws_daily_category",
    "ads_channel_daily", "ads_channel_roi", "ads_category_sales", "ads_kpi_daily",
]

# 无 DB 时所有 integration/p0/l2/l3/l4 用例跳过（CI 环境无中间件，仅 collect-only 绿）
no_db = pytest.mark.skipif(not HAS_DB, reason="本机 MySQL 53306 不可达，集成/E2E 用例跳过")


@pytest.fixture(scope="session")
def db_conn():
    """session 级 DB 连接（root，可读写各层）；无 DB 时 fixture 本身不可用（用例已 skip）。"""
    conn = get_connection(database="dw", root=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def baseline_date() -> str:
    return "2026-11-28"


@pytest.fixture(scope="session")
def baseline_gmv() -> float:
    return 397131.32


def _clean_day(dt: str) -> None:
    """清理隔离日在各层/日志/治理表的痕迹，不触碰基线窗口数据。"""
    if not HAS_DB:
        return
    conn = get_connection(database="dw", root=True)
    try:
        with conn.cursor() as c:
            for t in _DAY_TABLES:
                c.execute(f"DELETE FROM {t} WHERE dt=%s", (dt,))
            c.execute("DELETE FROM etl_run_log WHERE dt=%s", (dt,))
            c.execute("DELETE FROM dq_report WHERE dt=%s", (dt,))
            c.execute("DELETE FROM dq_alert WHERE dt=%s", (dt,))
            c.execute("DELETE FROM dq_issue WHERE dt=%s", (dt,))
            c.execute("DELETE FROM dim_user WHERE start_date=%s", (dt,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def isolated_day():
    """隔离日 11-29：造单日增量数据 → 初始化跑通 → 用例注入 → 清理。全程不碰 11-10~11-28 基线。"""
    _clean_day(ISOLATED_DAY)
    subprocess.run([sys.executable, str(BACKEND / "scripts" / "gen_data.py"),
                    "--mode", "incremental", "--days", "1", "--end-date", ISOLATED_DAY],
                   cwd=BACKEND, capture_output=True, text=True, timeout=300)
    subprocess.run([sys.executable, str(BACKEND / "scripts" / "run_daily.py"),
                    "--date", ISOLATED_DAY, "--skip-quality"],
                   cwd=BACKEND, capture_output=True, text=True, timeout=300)
    yield ISOLATED_DAY
    _clean_day(ISOLATED_DAY)
