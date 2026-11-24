"""DB 连接与可达性探测。本项目无 HTTP/token，集成测试依赖本机 MySQL 53306。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 定位 backend 目录（db.py → common → qa-automation → 08_测试 → dw-etl-pipeline → 05_代码/backend）
BACKEND = Path(__file__).resolve().parents[3] / "05_代码" / "backend"
if str(BACKEND / "scripts") not in sys.path:
    sys.path.insert(0, str(BACKEND / "scripts"))

import dw_common  # noqa: E402

# 优先加载 backend/.env，让 CI 无 .env 时也能快速失败（不依赖默认值挂 10s）
_ENV = BACKEND / ".env"
HAS_DB = False
if _ENV.exists():
    dw_common.load_environment()
    try:
        _conn = dw_common.connect(database="dw", root=True)
        _conn.close()
        HAS_DB = True
    except Exception:
        HAS_DB = False


def get_connection(database: str = "dw", *, root: bool = True):
    """返回一个新连接；调用方负责关闭。"""
    return dw_common.connect(database=database, root=root)
