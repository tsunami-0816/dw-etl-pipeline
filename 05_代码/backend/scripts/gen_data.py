from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from dw_common import REPORTS_DIR, connect, parse_date, write_json_atomic  # noqa: E402

MONEY = Decimal("0.01")


def build_dataset(
    *,
    days: int = 90,
    seed: int = 42,
    dirty_rate: float = 0.03,
    end_date: date = date(2026, 11, 23),
    user_count: int = 20_000,
    order_count: int = 80_000,
    product_count: int = 500,
    channel_count: int = 20,
) -> dict[str, object]:
    """生成固定种子的模拟源数据；脏订单只打标，不在生成阶段清洗。"""
    if not 1 <= days <= 90:
        raise ValueError("days 必须在 1～90")
    if not 0 <= dirty_rate <= 1:
        raise ValueError("dirty-rate 必须在 0～1")
    if min(user_count, order_count, product_count, channel_count) <= 0:
        raise ValueError("业务表规模必须为正数")
    dirty_count = int(order_count * dirty_rate)
    if dirty_count and dirty_count < 4:
        raise ValueError("非零脏数据至少需 4 条订单以覆盖四类异常")
    rng = random.Random(seed)
    start_date = end_date - timedelta(days=days - 1)
    cities = ["杭州", "上海", "北京", "深圳", "成都", "南京"]

    channels = [{
        "channel_id": index,
        "channel_name": f"渠道{index:02d}",
        "channel_type": ("search", "social", "display", "offline")[index % 4],
        "daily_cost": Decimal(800 + index * 35).quantize(MONEY),
        "status": 1,
        "update_time": datetime.combine(end_date, time(8, 0, 0)),
    } for index in range(1, channel_count + 1)]
    products = [{
        "product_id": index,
        "product_name": f"SaaS 测试商品{index:03d}",
        "category_id": (index - 1) % 10 + 1,
        "category_name": f"品类{(index - 1) % 10 + 1:02d}",
        "price": Decimal(rng.randrange(9900, 99900)).quantize(MONEY) / 100,
        "status": 1,
        "update_time": datetime.combine(end_date, time(8, 0, 0)),
    } for index in range(1, product_count + 1)]
    users = []
    for index in range(1, user_count + 1):
        register_date = start_date + timedelta(days=(index - 1) % days)
        register_time = datetime.combine(register_date, time(rng.randrange(24), rng.randrange(60), rng.randrange(60)))
        name = f"测试用户{index:06d}" if index % 2 else f"O'Neil-{index:06d}"
        users.append({
            "user_id": index,
            "user_name": name,
            "phone": f"13{index:09d}"[:20],
            "city": cities[index % len(cities)],
            "level": rng.choices((1, 2, 3), weights=(70, 20, 10), k=1)[0],
            "channel_id": rng.randint(1, channel_count),
            "register_time": register_time,
            "update_time": register_time + timedelta(hours=rng.randrange(24)),
        })

    orders = []
    for index in range(1, order_count + 1):
        pay_date = start_date + timedelta(days=(index - 1) % days)
        pay_time = datetime.combine(pay_date, time(rng.randrange(24), rng.randrange(60), rng.randrange(60)))
        status = rng.choices(("paid", "refunded", "cancelled"), weights=(80, 10, 10), k=1)[0]
        orders.append({
            "order_id": index,
            "order_no": f"SO{pay_date:%Y%m%d}{index:08d}",
            "user_id": rng.randint(1, user_count),
            "product_id": rng.randint(1, product_count),
            "channel_id": rng.randint(1, channel_count),
            "quantity": rng.randint(1, 3),
            "amount": Decimal(rng.randrange(100, 100000)).quantize(MONEY) / 100,
            "status": status,
            "pay_time": pay_time,
            "refund_time": pay_time + timedelta(hours=2) if status == "refunded" else None,
            "update_time": pay_time + timedelta(minutes=rng.randrange(60)),
        })

    dirty_ids = set(rng.sample(range(1, order_count + 1), dirty_count))
    dirty_orders = []
    kinds = ("duplicate_order", "null_channel", "negative_amount", "orphan_user")
    by_date: dict[date, list[dict]] = {}
    for row in orders:
        by_date.setdefault(row["pay_time"].date(), []).append(row)
    for position, order_id in enumerate(sorted(dirty_ids)):
        row = orders[order_id - 1]
        kind = kinds[position % 4]
        if kind == "duplicate_order":
            candidates = [item for item in by_date[row["pay_time"].date()] if item["order_id"] not in dirty_ids]
            if not candidates:
                raise ValueError("重复订单异常需要同日存在正常订单")
            donor = candidates[(position + seed) % len(candidates)]
            row["order_no"] = donor["order_no"]
            row["update_time"] = max(row["update_time"], donor["update_time"] + timedelta(seconds=1))
        elif kind == "null_channel":
            row["channel_id"] = None
        elif kind == "negative_amount":
            row["amount"] = -abs(row["amount"])
        else:
            row["user_id"] = user_count + 100_000 + order_id
        dirty_orders.append({**row, "dirty_type": kind})
    return {
        "users": users,
        "orders": orders,
        "products": products,
        "channels": channels,
        "dirty_orders": dirty_orders,
        "start_date": start_date,
        "end_date": end_date,
    }


def limit_dataset_to_days(full_data: dict[str, object], days: int) -> dict[str, object]:
    """从同一固定 90 天数据集中截取最近 N 天，保持订单 ID/日期稳定且不删除历史。"""
    if not 1 <= days <= 90:
        raise ValueError("days 必须在 1～90")
    end_date = full_data["end_date"]
    start_date = end_date - timedelta(days=days - 1)
    orders = [row for row in full_data["orders"] if start_date <= row["pay_time"].date() <= end_date]
    dirty_orders = [row for row in full_data["dirty_orders"] if start_date <= row["pay_time"].date() <= end_date]
    return {**full_data, "orders": orders, "dirty_orders": dirty_orders, "start_date": start_date}


TABLE_COLUMNS = {
    "user_info": ("user_id", "user_name", "phone", "city", "level", "channel_id", "register_time", "update_time"),
    "product_info": ("product_id", "product_name", "category_id", "category_name", "price", "status", "update_time"),
    "channel_info": ("channel_id", "channel_name", "channel_type", "daily_cost", "status", "update_time"),
    "order_info": ("order_id", "order_no", "user_id", "product_id", "channel_id", "quantity", "amount", "status", "pay_time", "refund_time", "update_time"),
}


def write_dataset(data: dict[str, object], mode: str) -> dict[str, int]:
    datasets = {"user_info": data["users"], "product_info": data["products"], "channel_info": data["channels"], "order_info": data["orders"]}
    connection = connect(database="biz", seed=True)
    counts = {}
    try:
        with connection.cursor() as cursor:
            if mode == "full":
                delete_statements = {
                    "order_info": "DELETE FROM order_info",
                    "user_info": "DELETE FROM user_info",
                    "product_info": "DELETE FROM product_info",
                    "channel_info": "DELETE FROM channel_info",
                }
                for table, statement in delete_statements.items():
                    cursor.execute(statement)
            for table, rows in datasets.items():
                columns = TABLE_COLUMNS[table]
                values = [tuple(row[column] for column in columns) for row in rows]
                updates = ",".join(f"{column}=VALUES({column})" for column in columns if column != columns[0])
                sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))}) ON DUPLICATE KEY UPDATE {updates}"
                for offset in range(0, len(values), 5000):
                    cursor.executemany(sql, values[offset:offset + 5000])
                counts[table] = len(rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="生成固定种子的 90 天模拟业务数据（默认 3% 脏订单）")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dirty-rate", type=float, default=0.03)
    parser.add_argument("--mode", choices=("full", "incremental"), default="full")
    parser.add_argument("--end-date", default="2026-11-23")
    args = parser.parse_args()
    try:
        data = build_dataset(days=90 if args.mode == "incremental" else args.days, seed=args.seed, dirty_rate=args.dirty_rate, end_date=parse_date(args.end_date))
        if args.mode == "incremental":
            data = limit_dataset_to_days(data, args.days)
        counts = write_dataset(data, args.mode)
        dirty_types = Counter(row["dirty_type"] for row in data["dirty_orders"])
        payload = {"status": "success", "mode": args.mode, "seed": args.seed, "days": args.days, "counts": counts, "dirty_orders": len(data["dirty_orders"]), "dirty_types": dict(dirty_types), "start_date": data["start_date"], "end_date": data["end_date"]}
        write_json_atomic(REPORTS_DIR / f"data_generation_{args.end_date}.json", payload)
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(f"data generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
