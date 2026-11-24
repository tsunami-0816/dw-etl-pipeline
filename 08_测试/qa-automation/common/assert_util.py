"""统一断言工具：金额近似、分层行数单调性、状态合法性。"""
from __future__ import annotations

from decimal import Decimal

TOL = Decimal("0.01")


def assert_amount_close(actual, expected, tol: Decimal = TOL, msg: str = "") -> None:
    """金额对账：actual 与 expected 差值不超过 tol（浮点/Decimal 兼容）。"""
    a = Decimal(str(actual))
    e = Decimal(str(expected))
    assert abs(a - e) <= tol, f"{msg} 金额对账失败: actual={a} expected={e} diff={abs(a-e)} > {tol}"


def assert_layer_monotonic(layers: dict) -> None:
    """分层行数满足 ODS >= DWD >= DWS（清洗/聚合只减不增）。"""
    ods = int(layers.get("ODS", 0))
    dwd = int(layers.get("DWD", 0))
    dws = int(layers.get("DWS", 0))
    assert ods >= dwd, f"ODS({ods}) 应 >= DWD({dwd})"
    assert dwd >= dws, f"DWD({dwd}) 应 >= DWS({dws})"


def assert_status_in(status: str, allowed: set[str]) -> None:
    assert status in allowed, f"状态 {status!r} 不在合法集合 {allowed}"
