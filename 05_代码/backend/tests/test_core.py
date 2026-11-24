from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = load_module("dw_common", "scripts/dw_common.py")
gen_data = load_module("gen_data", "scripts/gen_data.py")
run_sql = load_module("run_sql", "scripts/run_sql.py")
run_daily = load_module("run_daily", "scripts/run_daily.py")
backfill = load_module("backfill", "scripts/backfill.py")
perf = load_module("perf_benchmark", "scripts/perf_benchmark.py")


def small_dataset(days=2, seed=42, dirty_rate=0.03):
    return gen_data.build_dataset(
        days=days,
        seed=seed,
        dirty_rate=dirty_rate,
        end_date=date(2026, 11, 23),
        user_count=200,
        order_count=800,
        product_count=50,
        channel_count=20,
    )


def test_parse_date_accepts_iso_date():
    assert common.parse_date("2026-11-23") == date(2026, 11, 23)


def test_parse_date_rejects_invalid_date():
    with pytest.raises(ValueError):
        common.parse_date("2026-02-30")


def test_validate_date_range_rejects_inverted_range():
    with pytest.raises(ValueError):
        common.validate_date_range(date(2026, 11, 23), date(2026, 11, 22))


def test_validate_date_range_rejects_more_than_90_days():
    with pytest.raises(ValueError):
        common.validate_date_range(date(2026, 8, 25), date(2026, 11, 23))


def test_validate_date_range_accepts_90_days():
    assert common.validate_date_range(date(2026, 8, 26), date(2026, 11, 23)) == 90


def test_split_sql_statements_splits_plain_sql():
    assert run_sql.split_sql_statements("CREATE DATABASE a; USE a;") == ["CREATE DATABASE a", "USE a"]


def test_split_sql_statements_preserves_procedure_body():
    sql = "DELIMITER $$\nCREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END$$\nDELIMITER ;"
    statements = run_sql.split_sql_statements(sql)
    assert len(statements) == 1
    assert "SELECT 1; SELECT 2;" in statements[0]


def test_split_sql_statements_ignores_empty_delimiter_segments():
    assert run_sql.split_sql_statements("SELECT 1;;") == ["SELECT 1"]


def test_build_dataset_has_requested_business_counts():
    data = small_dataset()
    assert len(data["users"]) == 200
    assert len(data["orders"]) == 800
    assert len(data["products"]) == 50
    assert len(data["channels"]) == 20


def test_build_dataset_is_reproducible_with_fixed_seed():
    assert small_dataset() == small_dataset()


def test_build_dataset_changes_with_seed():
    assert small_dataset(seed=42) != small_dataset(seed=43)


def test_build_dataset_has_exact_three_percent_dirty_orders():
    data = small_dataset()
    assert len(data["dirty_orders"]) == 24
    assert len({row["order_id"] for row in data["dirty_orders"]}) == 24


def test_build_dataset_dirty_rate_zero_has_no_dirty_orders():
    assert small_dataset(dirty_rate=0)["dirty_orders"] == []


def test_build_dataset_rejects_invalid_dirty_rate():
    with pytest.raises(ValueError):
        small_dataset(dirty_rate=1.1)


def test_build_dataset_rejects_more_than_90_days():
    with pytest.raises(ValueError):
        small_dataset(days=91)


def test_dirty_dataset_contains_all_four_source_anomaly_types():
    data = small_dataset()
    kinds = {row["dirty_type"] for row in data["dirty_orders"]}
    assert kinds == {"duplicate_order", "null_channel", "negative_amount", "orphan_user"}


def test_duplicate_order_anomaly_has_same_order_no_different_order_id():
    data = small_dataset()
    dirty = next(row for row in data["dirty_orders"] if row["dirty_type"] == "duplicate_order")
    matching = [row for row in data["orders"] if row["order_no"] == dirty["order_no"]]
    assert len(matching) == 2
    assert len({row["order_id"] for row in matching}) == 2


def test_negative_amount_is_source_data_not_silently_cleaned():
    data = small_dataset()
    dirty_ids = {row["order_id"] for row in data["dirty_orders"] if row["dirty_type"] == "negative_amount"}
    assert all(next(row for row in data["orders"] if row["order_id"] == oid)["amount"] < 0 for oid in dirty_ids)


def test_orphan_user_does_not_exist_in_user_source():
    data = small_dataset()
    user_ids = {row["user_id"] for row in data["users"]}
    orphan_ids = {row["user_id"] for row in data["dirty_orders"] if row["dirty_type"] == "orphan_user"}
    assert orphan_ids
    assert orphan_ids.isdisjoint(user_ids)


def test_generated_dates_stay_inside_requested_window():
    data = small_dataset(days=2)
    expected = {date(2026, 11, 22), date(2026, 11, 23)}
    assert {row["pay_time"].date() for row in data["orders"]} <= expected


def test_generated_text_supports_chinese_and_special_characters():
    data = small_dataset()
    assert any("测试" in row["user_name"] for row in data["users"])
    assert any("O'Neil" in row["user_name"] for row in data["users"])


def test_backfill_selects_all_dates_in_ascending_order():
    dates = backfill.select_dates(date(2026, 11, 21), date(2026, 11, 23), set(), False)
    assert dates == [date(2026, 11, 21) + timedelta(days=i) for i in range(3)]


def test_backfill_retry_only_selects_failed_dates():
    failed = {date(2026, 11, 22)}
    assert backfill.select_dates(date(2026, 11, 21), date(2026, 11, 23), failed, True) == [date(2026, 11, 22)]


def test_exit_code_for_blocked_status_is_two():
    assert run_daily.exit_code_for_status("blocked") == 2


def test_exit_code_for_failed_status_is_three():
    assert run_daily.exit_code_for_status("failed") == 3


def test_exit_code_for_success_is_zero():
    assert run_daily.exit_code_for_status("success") == 0
    assert run_daily.exit_code_for_status("skipped") == 0


def test_summary_json_contains_contract_fields(tmp_path):
    target = tmp_path / "run_summary.json"
    run_daily.write_summary(target, batch_id="B20261123-010203", dt=date(2026, 11, 23), status="success", layers={"ODS": 10}, exit_code=0)
    text = target.read_text(encoding="utf-8")
    for key in ("batch_id", "dt", "status", "layers", "exit_code"):
        assert f'"{key}"' in text


def test_median_and_speedup_use_real_measurements():
    assert perf.median_ms([30, 10, 20, 40, 50]) == 30.0
    assert perf.speedup(Decimal("30"), Decimal("10")) == Decimal("3.00")
    assert perf.speedup(Decimal("10"), Decimal("0")) is None


def test_collect_metrics_uses_requested_date(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchall(self):
            return [("GMV", Decimal("12.50"))]

        def fetchone(self):
            return (Decimal("1.25"),)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(run_daily, "connect", lambda database: Connection())
    metrics = run_daily.collect_metrics(date(2026, 11, 22))
    assert metrics == {"GMV": "12.50", "ROI": "1.25"}
    assert len(calls) == 2
    assert all(params == (date(2026, 11, 22),) for _, params in calls)
    assert all("MAX(dt)" not in sql for sql, _ in calls)


def test_backfill_invokes_daily_command_from_backend_directory(monkeypatch):
    observed = []

    class Result:
        returncode = 0

    monkeypatch.setattr(backfill.subprocess, "run", lambda *args, **kwargs: observed.append(kwargs) or Result())
    monkeypatch.setattr(sys, "argv", ["backfill.py", "--start", "2026-11-23", "--end", "2026-11-23"])
    assert backfill.main() == 0
    assert observed[0]["cwd"] == backfill.SCRIPT_DIR.parent


def test_incremental_window_preserves_full_dataset_ids_and_history():
    full = gen_data.build_dataset(days=90, seed=42, dirty_rate=0.04, end_date=date(2026, 11, 23), user_count=200, order_count=900, product_count=50, channel_count=20)
    subset = gen_data.limit_dataset_to_days(full, 2)
    expected_ids = [row["order_id"] for row in full["orders"] if row["pay_time"].date() >= date(2026, 11, 22)]
    assert [row["order_id"] for row in subset["orders"]] == expected_ids
    assert len(subset["orders"]) == 20
    assert len(subset["users"]) == 200
    assert {row["pay_time"].date() for row in subset["orders"]} == {date(2026, 11, 22), date(2026, 11, 23)}
    assert all(row["order_id"] in expected_ids for row in subset["dirty_orders"])
