from datetime import date

from collections_logic import compute_derived


def body(**over):
    base = {
        "first_due_date": "2025-07-01",
        "due_day": 1,
        "emi": 11183,
        "tenor_months": 36,
        "emis_received": 6,
    }
    base.update(over)
    return base


def test_worked_example():
    d = compute_derived(body(), date(2026, 8, 8))
    assert d["emis_due_till_today"] == 14
    assert d["overdue_count"] == 8
    assert d["overdue_amount"] == 89464
    assert d["remaining_tenor"] == 30
    assert d["has_arrears"] is True


def test_before_first_due():
    d = compute_derived(body(), date(2025, 3, 1))
    assert d["overdue_count"] == 0
    assert d["overdue_amount"] == 0
    assert d["has_arrears"] is False


def test_all_paid():
    d = compute_derived(body(emis_received=14), date(2026, 8, 8))
    assert d["overdue_count"] == 0
    assert d["overdue_amount"] == 0
    assert d["has_arrears"] is False


def test_overpaid_floors_to_zero():
    d = compute_derived(body(emis_received=20), date(2026, 8, 8))
    assert d["overdue_count"] == 0
    assert d["overdue_amount"] == 0


def test_due_day_not_yet_reached_this_month():
    d = compute_derived(body(first_due_date="2025-07-10", due_day=10), date(2026, 8, 5))
    assert d["emis_due_till_today"] == 13
    assert d["overdue_count"] == 7


def test_missing_first_due_date_no_crash():
    d = compute_derived({}, date(2026, 8, 8))
    assert d["has_arrears"] is False
    assert d["overdue_count"] == 0


def test_invalid_first_due_date_no_crash():
    d = compute_derived(body(first_due_date="not-a-date"), date(2026, 8, 8))
    assert d["has_arrears"] is False


def test_varying_tenor():
    d = compute_derived(body(tenor_months=12), date(2026, 8, 8))
    assert d["remaining_tenor"] == 6


def test_zero_emi():
    d = compute_derived(body(emi=0), date(2026, 8, 8))
    assert d["overdue_amount"] == 0
    assert d["overdue_count"] == 8
