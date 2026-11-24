from __future__ import annotations

import importlib.util
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# 先注入 dw_common，run_rules 导入时依赖它
load_module("dw_common", "scripts/dw_common.py")
run_rules = load_module("quality_run_rules", "quality/run_rules.py")
reconcile = load_module("spark_reconcile", "spark/reconcile.py")


def test_metrics_yml_has_12_metrics():
    data = yaml.safe_load((BACKEND / "governance" / "metrics.yml").read_text(encoding="utf-8"))
    metrics = data["metrics"]
    assert len(metrics) == 12, f"指标数应为 12，实际 {len(metrics)}"
    ids = {m["id"] for m in metrics}
    assert ids == {f"M{i:02d}" for i in range(1, 13)}
    for m in metrics:
        assert m["formula"], f"{m['id']} 缺公式"
        assert m["source_fields"], f"{m['id']} 缺源字段"
        assert m["ads_target"], f"{m['id']} 缺 ADS 落点"


def test_lineage_covers_all_12_metrics():
    text = (BACKEND / "governance" / "lineage.md").read_text(encoding="utf-8")
    for i in range(1, 13):
        assert f"M{i:02d}" in text, f"血缘文档缺少 M{i:02d}"


def test_dq_rules_has_5_rules():
    rules = run_rules.load_rules()
    assert set(rules) == {"DQ01", "DQ02", "DQ03", "DQ04", "DQ05"}


def test_load_rules_rejects_bad_threshold(tmp_path, monkeypatch):
    bad = tmp_path / "bad.yml"
    bad.write_text("rules:\n  DQ01:\n    name: x\n    p0_threshold: oops\n", encoding="utf-8")
    monkeypatch.setattr(run_rules, "RULES_FILE", bad)
    with pytest.raises(ValueError):
        run_rules.load_rules()


def test_load_rules_rejects_missing_rule(tmp_path, monkeypatch):
    bad = tmp_path / "bad.yml"
    bad.write_text("rules:\n  DQ01:\n    name: x\n", encoding="utf-8")
    monkeypatch.setattr(run_rules, "RULES_FILE", bad)
    with pytest.raises(ValueError):
        run_rules.load_rules()


def test_level_for_rate_3pct_not_p0():
    rule = {"base_level": "P2", "p0_level": "P0", "p0_threshold": 0.05}
    assert run_rules.level_for_rate(0.03, rule) == "P2"
    assert run_rules.level_for_rate(0.05, rule) == "P2"
    assert run_rules.level_for_rate(0.06, rule) == "P0"


def _make_fixture():
    orders = pd.DataFrame([
        {"dt": date(2026, 6, 1), "channel_id": 1, "order_id": 1, "order_status": "paid",
         "amount": Decimal("100.00"), "is_refund": 0},
        {"dt": date(2026, 6, 1), "channel_id": 1, "order_id": 2, "order_status": "refunded",
         "amount": Decimal("50.00"), "is_refund": 1},
        {"dt": date(2026, 6, 1), "channel_id": 2, "order_id": 3, "order_status": "cancelled",
         "amount": Decimal("999.00"), "is_refund": 0},
    ])
    profile = pd.DataFrame([
        {"user_id": 1, "channel_id": 1, "register_date": date(2026, 6, 1), "is_orphan": 0},
        {"user_id": 2, "channel_id": 1, "register_date": date(2026, 6, 1), "is_orphan": 0},
    ])
    channels = pd.DataFrame([
        {"channel_id": 1, "daily_cost": Decimal("200.00")},
        {"channel_id": 2, "daily_cost": Decimal("0.00")},
    ])
    return orders, profile, channels


def test_aggregate_channel_values():
    orders, profile, channels = _make_fixture()
    out = reconcile.aggregate_channel(orders, profile, channels)
    row1 = out[out["channel_id"] == 1].iloc[0]
    assert int(row1["orders"]) == 2
    assert int(row1["paid_orders"]) == 2  # paid + refunded
    assert float(row1["gmv"]) == 150.0
    assert int(row1["refund_orders"]) == 1
    assert float(row1["refund_amount"]) == 50.0
    assert float(row1["refund_rate"]) == 0.5
    assert int(row1["new_users"]) == 2
    assert float(row1["roi"]) == 0.75  # 150 / 200
    row2 = out[out["channel_id"] == 2].iloc[0]
    assert int(row2["paid_orders"]) == 0
    assert float(row2["gmv"]) == 0.0  # cancelled 不计
    assert float(row2["roi"]) == 0.0  # cost 0 保护


def test_reconcile_identical_zero_diff():
    orders, profile, channels = _make_fixture()
    built = reconcile.aggregate_channel(orders, profile, channels)
    _, cnt = reconcile.reconcile(built, built)
    assert cnt == 0


def test_reconcile_detects_gmv_diff():
    orders, profile, channels = _make_fixture()
    built = reconcile.aggregate_channel(orders, profile, channels)
    dws = built.copy()
    dws.loc[dws["channel_id"] == 1, "gmv"] = 9999.0
    _, cnt = reconcile.reconcile(built, dws)
    assert cnt == 1


def test_reconcile_detects_missing_side():
    orders, profile, channels = _make_fixture()
    built = reconcile.aggregate_channel(orders, profile, channels)
    dws = built[built["channel_id"] == 1].copy()  # 缺 channel 2
    _, cnt = reconcile.reconcile(built, dws)
    assert cnt == 1
