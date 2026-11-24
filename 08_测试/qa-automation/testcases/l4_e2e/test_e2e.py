"""L4 端到端主路径：run_daily 一键跑通 + 跨层一致性断言。依赖本机 MySQL。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from common.db import HAS_DB

no_db = pytest.mark.skipif(not HAS_DB, reason="本机 MySQL 53306 不可达，E2E 跳过")

BACKEND = Path(__file__).resolve().parents[4] / "05_代码" / "backend"
INTEG_ASSERT = Path(__file__).resolve().parents[4] / "07_联调" / "scripts" / "integ_assert.py"


@no_db
@pytest.mark.l4
@pytest.mark.p0
def test_full_pipeline_e2e(baseline_date):
    """E2E 主路径：run_daily --date 基线日 → exit 0 → 分层单调 + 指标产出；integ_assert 跨层一致性 exit 0。"""
    dt = baseline_date
    r = subprocess.run([sys.executable, str(BACKEND / "scripts" / "run_daily.py"), "--date", dt],
                       cwd=BACKEND, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"run_daily 退出码 {r.returncode}\nstderr: {r.stderr[-800:]}"
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    # 幂等重跑已跑过的日期返回 skipped（exit 0），首次跑返回 success；二者均为正常终态
    assert payload["status"] in ("success", "skipped"), f"状态 {payload['status']} 非 success/skipped"
    assert payload["exit_code"] == 0
    layers = payload["layers"]
    assert int(layers.get("ODS", 0)) >= int(layers.get("DWD", 0)), "ODS 应 >= DWD"
    assert int(layers.get("DWD", 0)) >= int(layers.get("DWS", 0)), "DWD 应 >= DWS"
    metrics = payload["metrics"]
    assert "GMV" in metrics and "NEW_USERS" in metrics, "ADS 应产出一级指标"
    assert float(metrics["GMV"]) > 0, "GMV 应大于 0"

    # 跨层一致性（dwd→dws→ads 全链路 + SCD2 版本链）
    a = subprocess.run([sys.executable, str(INTEG_ASSERT), "--date", dt],
                       capture_output=True, text=True, timeout=300)
    assert a.returncode == 0, f"integ_assert 退出码 {a.returncode}\nstdout: {a.stdout[-800:]}"
