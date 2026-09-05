from datetime import datetime

from app.batch.mapper import (
    DEFAULT_CSV,
    to_int,
    to_iso,
    default_csv,
    derive_result,
    ist_now,
    normalize_phone,
    row_to_body,
    within_calling_hours,
)


def test_default_csv_from_env(monkeypatch):
    monkeypatch.setenv("BATCH_INPUT_CSV", "/tmp/other.csv")
    assert default_csv() == "/tmp/other.csv"


def test_default_csv_env_empty_falls_back(monkeypatch):
    monkeypatch.setenv("BATCH_INPUT_CSV", "  ")
    assert default_csv() == DEFAULT_CSV


def test_default_csv_fallback(monkeypatch):
    monkeypatch.delenv("BATCH_INPUT_CSV", raising=False)
    assert default_csv() == DEFAULT_CSV


def testto_int():
    assert to_int("11183") == 11183
    assert to_int("81144.58") == 81144
    assert to_int("") == 0
    assert to_int(None) == 0
    assert to_int(" 42 ") == 42


def testto_iso():
    assert to_iso("01/07/2025") == "2025-07-01"
    assert to_iso("26/05/2025") == "2025-05-26"
    assert to_iso("2025-07-01") == "2025-07-01"
    assert to_iso("") == ""
    assert to_iso(None) == ""


def test_normalize_phone():
    assert normalize_phone("7299159380") == "+917299159380"
    assert normalize_phone("+917299159380") == "+917299159380"
    assert normalize_phone("007299159380") == "+7299159380"
    assert normalize_phone(" 7299 1593 80 ") == "+917299159380"
    assert normalize_phone("") == ""


def test_row_to_body():
    row = {
        "loanNo": "65797283",
        "customerName": "M  RAJASEKAR",
        "pos": "160509",
        "installmentAmount": "11183",
        "agentName": "Gayathri",
        "noOfEmisReceived": "6",
        "emiStartDate": "01/07/2025",
        "tenor": "24",
        "bank": "HDB Finance",
    }
    body = row_to_body(row)
    assert body["account_number_last4"] == "7283"
    assert body["principal"] == 160509
    assert body["emi"] == 11183
    assert body["first_due_date"] == "2025-07-01"
    assert body["tenor_months"] == 24
    assert body["emis_received"] == 6
    assert body["company_name"] == "HDB Finance"
    assert body["customer_name"] == "M  RAJASEKAR"


def test_row_to_body_decimal_pos():
    row = {"loanNo": "48261849", "pos": "81144.58", "installmentAmount": "6861",
           "emiStartDate": "07/05/2024", "tenor": "24", "noOfEmisReceived": "10"}
    body = row_to_body(row)
    assert body["principal"] == 81144


def test_row_to_body_missing_fields():
    body = row_to_body({})
    assert body["principal"] == 0
    assert body["first_due_date"] == ""
    assert body["account_number_last4"] == ""


def test_derive_result_outcome():
    rec = {"status": "ended", "connected": True, "outcome": "PTP",
           "outcome_note": "20000 on 2026-08-29", "recording_url": "http://x/recordings/a.mp3"}
    assert derive_result(rec) == ("ENDED", "PTP", "20000 on 2026-08-29", "http://x/recordings/a.mp3")


def test_derive_result_no_outcome_connected():
    rec = {"status": "ended", "connected": True, "outcome": None}
    assert derive_result(rec) == ("ENDED", "NO_OUTCOME", "", "")


def test_derive_result_no_answer():
    rec = {"status": "ended", "connected": False, "outcome": None}
    assert derive_result(rec) == ("NO_ANSWER", "", "", "")


def test_derive_result_failed_and_timeout():
    assert derive_result({"status": "failed"}) == ("FAILED", "", "", "")
    assert derive_result({"status": "timeout"}) == ("TIMEOUT", "", "", "")


def test_ist_now_is_ist_offset():
    now = ist_now()
    assert now.utcoffset().total_seconds() == 5 * 3600 + 30 * 60


def test_within_calling_hours():
    assert within_calling_hours(datetime(2026, 8, 29, 12, 0, tzinfo=now_tz())) is True
    assert within_calling_hours(datetime(2026, 8, 29, 20, 0, tzinfo=now_tz())) is False
    assert within_calling_hours(datetime(2026, 8, 29, 7, 0, tzinfo=now_tz())) is False


def now_tz():
    from datetime import timezone
    return timezone.utc
