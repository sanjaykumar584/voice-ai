from app.voice.collections import build_call_context

BODY = {
    "agent_name": "Meena",
    "company_name": "ABC Finance",
    "customer_name": "Kumar",
    "account_number_last4": "1234",
    "principal": 371987,
    "emi": 11183,
    "first_due_date": "2025-07-01",
    "tenor_months": 36,
    "emis_received": 6,
}


def test_all_placeholders_filled():
    sys_prompt, _ = build_call_context(BODY)
    assert "{customer_name}" not in sys_prompt
    assert "{company_name}" not in sys_prompt
    assert "{overdue_count}" not in sys_prompt
    assert "{principal}" not in sys_prompt
    assert "{emi}" not in sys_prompt


def test_greeting_contains_name():
    sys_prompt, _ = build_call_context(BODY)
    assert "Hello, Kumar pesreengala?" in sys_prompt


def test_developer_message_has_values():
    _, dev = build_call_context(BODY)
    assert "overdue_count" in dev
    assert "overdue_amount" in dev
    assert "Kumar" in dev
    assert "20" in dev  # year in today's date


def test_developer_message_matches_computed_derived():
    """The dev message must carry exactly what compute_derived returns."""
    from datetime import date

    from app.voice.collections import compute_derived

    _, dev = build_call_context(BODY)
    expected = compute_derived(BODY, date.today())
    for key in ("emis_due_till_today", "overdue_count", "overdue_amount", "remaining_tenor"):
        assert f'"{key}"' in dev
        assert str(expected[key]) in dev


def test_partial_body_no_crash():
    sys_prompt, dev = build_call_context({"emi": 1000})
    assert isinstance(sys_prompt, str) and len(sys_prompt) > 500
    assert isinstance(dev, str)


def test_empty_body_no_crash():
    sys_prompt, dev = build_call_context(None)
    assert isinstance(sys_prompt, str) and isinstance(dev, str)


def test_blank_customer_name_still_builds():
    # The script allows a blank customer name; the prompt still assembles.
    sys_prompt, _ = build_call_context({**BODY, "customer_name": ""})
    assert "pesreengala?" in sys_prompt


def test_rounding_guidance_present():
    sys_prompt, _ = build_call_context(BODY)
    assert "Round naturally" in sys_prompt
