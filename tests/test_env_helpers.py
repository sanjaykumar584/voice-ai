from bot import _dev_reminder_body, _env_bool, _env_float, _env_int, _is_collections_body


def test_env_bool_unset_uses_default(monkeypatch):
    monkeypatch.delenv("TB_X", raising=False)
    assert _env_bool("TB_X", True) is True
    assert _env_bool("TB_X", False) is False


def test_env_bool_parsing(monkeypatch):
    for value, expected in [("true", True), ("false", False), ("1", True), ("0", False), ("yes", True), ("no", False), ("on", True)]:
        monkeypatch.setenv("TB_X", value)
        assert _env_bool("TB_X", False) is expected


def test_env_int(monkeypatch):
    monkeypatch.delenv("TB_X", raising=False)
    assert _env_int("TB_X", 5) == 5
    monkeypatch.setenv("TB_X", "42")
    assert _env_int("TB_X", 5) == 42
    monkeypatch.setenv("TB_X", "")
    assert _env_int("TB_X", 5) == 5


def test_env_float(monkeypatch):
    monkeypatch.delenv("TB_X", raising=False)
    assert _env_float("TB_X", 0.5) == 0.5
    monkeypatch.setenv("TB_X", "1.25")
    assert _env_float("TB_X", 0.5) == 1.25
    monkeypatch.setenv("TB_X", "  ")
    assert _env_float("TB_X", 0.5) == 0.5


def test_is_collections_body():
    assert _is_collections_body({"first_due_date": "2025-07-01", "emi": 100}) is True
    assert _is_collections_body({"sdp": "v=0", "type": "offer", "pc_id": "x"}) is False
    assert _is_collections_body(None) is False
    assert _is_collections_body({}) is False
    assert _is_collections_body("string") is False


def test_dev_reminder_body_valid_json(monkeypatch):
    monkeypatch.setenv("DEV_REMINDER_BODY", '{"emi": 100}')
    assert _dev_reminder_body() == {"emi": 100}


def test_dev_reminder_body_invalid_json(monkeypatch):
    monkeypatch.setenv("DEV_REMINDER_BODY", "not json")
    assert _dev_reminder_body() is None


def test_dev_reminder_body_unset(monkeypatch):
    monkeypatch.delenv("DEV_REMINDER_BODY", raising=False)
    assert _dev_reminder_body() is None
