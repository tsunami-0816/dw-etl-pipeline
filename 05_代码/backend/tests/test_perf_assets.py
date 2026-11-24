from pathlib import Path

PERF = Path(__file__).resolve().parents[1] / "sql" / "perf"


def query(name: str) -> str:
    return (PERF / name).read_text(encoding="utf-8").lower()


def test_q1_after_has_bounded_date_window_and_pay_filter():
    sql = query("q1_after.sql")
    assert "%(start_date)s" in sql
    assert "%(end_date)s" in sql
    assert "is_refund" in sql


def test_q1_before_is_unbounded_full_window_baseline():
    sql = query("q1_before.sql")
    assert "group by channel_id" in sql
    assert "%(start_date)s" not in sql


def test_q2_after_filters_current_scd2_version():
    assert "is_current = 1" in query("q2_after.sql")


def test_q2_before_preserves_unfiltered_version_baseline():
    assert "is_current = 1" not in query("q2_before.sql")


def test_q3_after_uses_lag_window():
    assert "lag(kpi_value)" in query("q3_after.sql")


def test_q3_before_uses_self_join_baseline():
    sql = query("q3_before.sql")
    assert "join ads_kpi_daily" in sql
    assert "lag(" not in sql


def test_q3_after_returns_null_when_previous_calendar_day_is_missing():
    sql = query("q3_after.sql")
    assert "lag(dt)" in sql
    assert "date_sub(dt, interval 1 day)" in sql
    assert "case when" in sql
