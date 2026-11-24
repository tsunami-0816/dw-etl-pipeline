from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pymysql
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parents[1]
REPORTS_DIR = BACKEND_DIR / "reports"


def load_environment() -> None:
    """加载项目与后端本地配置；已存在的进程环境变量优先。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(BACKEND_DIR / ".env", override=False)


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"缺少环境变量: {name}")
    return value or ""


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"日期必须为 YYYY-MM-DD 且真实存在: {value!r}") from exc


def validate_date_range(start_date: date, end_date: date, max_days: int = 90) -> int:
    days = (end_date - start_date).days + 1
    if days < 1:
        raise ValueError("开始日期不能晚于结束日期")
    if days > max_days:
        raise ValueError(f"单次窗口不能超过 {max_days} 天，实际 {days} 天")
    return days


def connect(database: str | None = "dw", *, root: bool = False, seed: bool = False):
    """建立本机 MySQL 连接；日常 ETL、造数、初始化账号显式分离。"""
    load_environment()
    if root:
        user = "root"
        password = env("MYSQL_ROOT_PASSWORD", required=True)
    elif seed:
        user = env("MYSQL_SEED_USER", "root")
        password = env("MYSQL_SEED_PASSWORD", env("MYSQL_ROOT_PASSWORD", required=True))
    else:
        user = env("MYSQL_USER", "dw_etl")
        password = env("MYSQL_PASSWORD", required=True)
    return pymysql.connect(
        host=env("MYSQL_HOST", "127.0.0.1"),
        port=int(env("MYSQL_PORT", "53306")),
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        connect_timeout=int(env("MYSQL_CONNECT_TIMEOUT", "10")),
        read_timeout=int(env("MYSQL_READ_TIMEOUT", "120")),
        write_timeout=int(env("MYSQL_WRITE_TIMEOUT", "120")),
        autocommit=False,
    )


def sa_engine(database: str | None = "dw", *, root: bool = False, seed: bool = False):
    """返回 SQLAlchemy engine，供 pandas read_sql 使用（避免 DBAPI 连接告警）。"""
    from sqlalchemy import URL, create_engine

    load_environment()
    if root:
        user = "root"
        password = env("MYSQL_ROOT_PASSWORD", required=True)
    elif seed:
        user = env("MYSQL_SEED_USER", "root")
        password = env("MYSQL_SEED_PASSWORD", env("MYSQL_ROOT_PASSWORD", required=True))
    else:
        user = env("MYSQL_USER", "dw_etl")
        password = env("MYSQL_PASSWORD", required=True)
    # 用 URL.create 组装连接串（内部转义账号口令），避免手拼 DSN 触发密钥扫描误报
    url = URL.create(
        "mysql+pymysql",
        username=user,
        password=password,
        host=env("MYSQL_HOST", "127.0.0.1"),
        port=int(env("MYSQL_PORT", "53306")),
        database=database or None,
        query={"charset": "utf8mb4"},
    )
    return create_engine(url, pool_pre_ping=True, future=True)


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "__str__"):
        return str(value)
    raise TypeError(type(value).__name__)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")
    temp.replace(path)
