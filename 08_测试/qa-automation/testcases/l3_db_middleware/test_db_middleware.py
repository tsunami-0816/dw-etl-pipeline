"""L3 DB 中间件集成测试：GET_LOCK 互斥（不碰业务数据，不污染基线）。依赖本机 MySQL。"""
from __future__ import annotations

import pytest

from common.db import HAS_DB, get_connection

no_db = pytest.mark.skipif(not HAS_DB, reason="本机 MySQL 53306 不可达，中间件测试跳过")

LOCK_NAME = "qa_middleware_lock_test"


@no_db
@pytest.mark.l3
def test_get_lock_mutual_exclusion(db_conn):
    """GET_LOCK 互斥：A 持锁时 B 立即拿不到（返回 0）；A 释放后 B 可拿到。验证调度锁互斥语义。"""
    with db_conn.cursor() as c:
        c.execute("SELECT GET_LOCK(%s, 0)", (LOCK_NAME,))
        got_a = c.fetchone()[0]
    assert got_a == 1, "连接 A 应成功获取锁"

    other = get_connection(database="dw", root=True)
    try:
        with other.cursor() as c:
            c.execute("SELECT GET_LOCK(%s, 0)", (LOCK_NAME,))
            got_b = c.fetchone()[0]
        assert got_b == 0, f"A 持锁时 B 应拿不到锁（期望 0，实际 {got_b}）"
    finally:
        other.close()

    # A 释放锁
    with db_conn.cursor() as c:
        c.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
    db_conn.commit()

    # 新连接 B2 应能拿到
    other2 = get_connection(database="dw", root=True)
    try:
        with other2.cursor() as c:
            c.execute("SELECT GET_LOCK(%s, 0)", (LOCK_NAME,))
            got_b2 = c.fetchone()[0]
        assert got_b2 == 1, "A 释放后 B 应能获取锁"
        with other2.cursor() as c:
            c.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
        other2.commit()
    finally:
        other2.close()
