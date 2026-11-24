"""L2 存储过程 / SQL 层集成测试：sp_etl_daily 正向、幂等、P0 阻断。依赖本机 MySQL，无 DB 自动 skip。"""
from __future__ import annotations

from pathlib import Path

import pytest

from common.assert_util import assert_amount_close, assert_layer_monotonic, assert_status_in
from common.db import HAS_DB, get_connection

no_db = pytest.mark.skipif(not HAS_DB, reason="本机 MySQL 53306 不可达，集成用例跳过")


def _call_etl(dt: str):
    """调用 sp_etl_daily，返回 (status, total_rows, layers)。"""
    conn = get_connection(database="dw", root=True)
    try:
        with conn.cursor() as c:
            c.execute("CALL sp_etl_daily(%s,@p_status,@p_rows)", (dt,))
            while c.nextset():
                pass
            c.execute("SELECT @p_status,@p_rows")
            status, rows = c.fetchone()
            c.execute("SELECT batch_id FROM etl_run_log WHERE dt=%s ORDER BY start_at DESC LIMIT 1", (dt,))
            r = c.fetchone()
            batch_id = r[0] if r else ""
            c.execute("SELECT layer,rows_out FROM etl_run_log WHERE dt=%s AND batch_id=%s ORDER BY log_id", (dt, batch_id))
            layers = {layer: n for layer, n in c.fetchall()}
        conn.commit()
        return status, int(rows or 0), layers
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _kpi_gmv(dt: str):
    conn = get_connection(database="dw", root=True)
    try:
        with conn.cursor() as c:
            c.execute("SELECT kpi_value FROM ads_kpi_daily WHERE dt=%s AND kpi_code='GMV'", (dt,))
            r = c.fetchone()
        return r[0] if r else None
    finally:
        conn.close()


@no_db
@pytest.mark.l2
@pytest.mark.p0
def test_sp_etl_daily_success(baseline_date, baseline_gmv):
    """正向：干净基线日 sp_etl_daily 返回 success/skipped，分层行数单调，ADS 出 GMV 且复现基线值。"""
    dt = baseline_date
    status, rows, layers = _call_etl(dt)
    assert_status_in(status, {"success", "skipped"})
    assert rows > 0, "ETL 应产出数据"
    assert_layer_monotonic(layers)
    gmv = _kpi_gmv(dt)
    assert gmv is not None, "ads_kpi_daily 应产出 GMV"
    assert_amount_close(gmv, baseline_gmv, msg="11-28 GMV 基线值复现")


@no_db
@pytest.mark.l2
@pytest.mark.p0
def test_sp_etl_daily_idempotent(baseline_date):
    """幂等：同日连跑两次，ADS GMV 与各层出数完全一致（不翻倍、不漂移）。"""
    dt = baseline_date
    _call_etl(dt)
    gmv1 = _kpi_gmv(dt)
    snap1 = _layer_snapshot(dt)
    _call_etl(dt)
    gmv2 = _kpi_gmv(dt)
    snap2 = _layer_snapshot(dt)
    assert_amount_close(gmv1, gmv2, msg="幂等 GMV 两次一致")
    assert snap1 == snap2, f"幂等分层行数不一致: {snap1} vs {snap2}"


def _layer_snapshot(dt: str) -> dict:
    conn = get_connection(database="dw", root=True)
    try:
        with conn.cursor() as c:
            c.execute("SELECT layer,rows_out FROM etl_run_log WHERE dt=%s ORDER BY start_at DESC LIMIT 4", (dt,))
            return dict(c.fetchall())
    finally:
        conn.close()


@no_db
@pytest.mark.l2
@pytest.mark.p0
def test_sp_p0_blocks_downstream(isolated_day, db_conn):
    """P0 治理：隔离日注入 100% 空渠道 → sp_etl_daily blocked；DWD 事务回滚不写；dq_report 留 P0 证据。"""
    dt = isolated_day
    with db_conn.cursor() as c:
        for t in ["dwd_order_detail", "dws_daily_channel", "dws_daily_category",
                  "ads_channel_daily", "ads_channel_roi", "ads_category_sales", "ads_kpi_daily"]:
            c.execute(f"DELETE FROM {t} WHERE dt=%s", (dt,))
        c.execute("UPDATE ods_order SET channel_id=NULL WHERE dt=%s", (dt,))
    db_conn.commit()
    status, _rows, _layers = _call_etl(dt)
    assert_status_in(status, {"blocked"})
    with db_conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM dwd_order_detail WHERE dt=%s", (dt,))
        dwd_rows = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM dq_report WHERE dt=%s AND level='P0'", (dt,))
        p0_cnt = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM dq_alert WHERE dt=%s AND level='P0'", (dt,))
        alert_cnt = c.fetchone()[0]
    assert dwd_rows == 0, f"P0 阻断时 DWD 应回滚不写，实际 {dwd_rows} 行"
    assert p0_cnt > 0, "P0 阻断应在 dq_report 留痕"
    assert alert_cnt > 0, "P0 阻断应触发 dq_alert 告警"
