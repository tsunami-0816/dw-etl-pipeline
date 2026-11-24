from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from dw_common import BACKEND_DIR, connect, load_environment  # noqa: E402

PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
SAFE_SECRET = re.compile(r"^[A-Za-z0-9_@#%+=.\-]{8,128}$")


def split_sql_statements(sql: str) -> list[str]:
    """按客户端 DELIMITER 指令切分脚本，保留存储过程内部的分号。"""
    statements: list[str] = []
    buffer = ""
    delimiter = ";"
    for line in sql.splitlines(keepends=True):
        stripped = line.strip()
        if not buffer.strip() and stripped.upper().startswith("DELIMITER "):
            delimiter = stripped.split(None, 1)[1]
            continue
        buffer += line
        while True:
            quote: str | None = None
            escaped = False
            end = -1
            for index, char in enumerate(buffer):
                if escaped:
                    escaped = False
                    continue
                if char == "\\" and quote:
                    escaped = True
                    continue
                if char in ("'", '"', "`"):
                    if quote == char:
                        quote = None
                    elif quote is None:
                        quote = char
                    continue
                if quote is None and buffer.startswith(delimiter, index):
                    end = index
                    break
            if end < 0:
                break
            statement = buffer[:end].strip()
            if statement:
                statements.append(statement)
            buffer = buffer[end + len(delimiter):]
    if buffer.strip():
        raise ValueError("SQL 文件末尾存在未闭合语句或缺少 DELIMITER")
    return statements


def render_sql(sql: str, variables: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = variables.get(name, os.getenv(name, ""))
        if not SAFE_SECRET.fullmatch(value):
            raise ValueError(f"{name} 缺失或含不安全字符；密码长度须为 8～128")
        return value
    return PLACEHOLDER.sub(replace, sql)


def execute_sql_file(path: Path, *, database: str | None = None, root: bool = False, variables: dict[str, str] | None = None) -> int:
    sql = render_sql(path.read_text(encoding="utf-8"), variables or {})
    statements = split_sql_statements(sql)
    connection = connect(database=database, root=root)
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
                while cursor.nextset():
                    pass
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(statements)


def initialize_database() -> None:
    init_dir = BACKEND_DIR / "sql" / "init"
    for path in sorted(init_dir.glob("*.sql")):
        count = execute_sql_file(path, root=True)
        print(f"initialized {path.name}: {count} statements")


def main() -> int:
    parser = argparse.ArgumentParser(description="执行 dw-etl-pipeline SQL 文件或按序初始化 01～08")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--database", default=None)
    parser.add_argument("--root", action="store_true")
    parser.add_argument("--init", action="store_true", help="使用 root 初始化全部库表、账号、过程与维表")
    args = parser.parse_args()
    load_environment()
    if args.init == (args.file is not None):
        parser.error("--init 与 --file 必须且只能指定一个")
    try:
        if args.init:
            initialize_database()
        else:
            count = execute_sql_file(args.file, database=args.database, root=args.root)
            print(f"executed {args.file}: {count} statements")
        return 0
    except Exception as exc:
        print(f"SQL execution failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
