import asyncio

import bot
from call_state import active_calls


class FakeParams:
    def __init__(self, app_resources):
        self.app_resources = app_resources
        self.result = None

    async def result_callback(self, result):
        self.result = result


def _run(coro):
    return asyncio.run(coro)


def test_log_outcome_writes_to_call_state():
    active_calls.clear()
    active_calls["call-1"] = {"status": "active"}
    params = FakeParams({"call_id": "call-1"})
    _run(bot.log_outcome(params, "PTP", "20000 on 2026-08-29"))
    assert active_calls["call-1"]["outcome"] == "PTP"
    assert active_calls["call-1"]["outcome_note"] == "20000 on 2026-08-29"
    assert params.result == {"recorded": True, "status": "PTP"}


def test_log_outcome_without_call_id_is_safe():
    active_calls.clear()
    params = FakeParams(None)
    _run(bot.log_outcome(params, "HARDSHIP"))
    assert params.result == {"recorded": True, "status": "HARDSHIP"}


def test_log_outcome_unknown_call_id_is_safe():
    active_calls.clear()
    active_calls["call-1"] = {"status": "active"}
    params = FakeParams({"call_id": "nope"})
    _run(bot.log_outcome(params, "DISPUTE"))
    assert "nope" not in active_calls
