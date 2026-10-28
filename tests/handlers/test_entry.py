import pytest

from ags.handlers.entry import invoke


class FakeMarket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeRuntime:
    def __init__(self):
        self.market_client = FakeMarket()


def test_invoke_hands_the_event_and_a_fresh_runtime_to_the_handler_and_closes_the_market_client():
    runtime = FakeRuntime()

    result = invoke(lambda event, rt: {"echo": event, "rt": rt}, {"a": 1}, build=lambda: runtime)

    assert result == {"echo": {"a": 1}, "rt": runtime}
    assert runtime.market_client.closed


def test_invoke_closes_the_market_client_when_the_handler_raises():
    runtime = FakeRuntime()

    def boom(event, rt):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        invoke(boom, {}, build=lambda: runtime)

    assert runtime.market_client.closed


class AuthError(Exception):
    pass


def test_an_auth_error_rebuilds_the_runtime_once_to_pick_up_a_refreshed_secret():
    builds = []

    def build():
        builds.append(1)
        if len(builds) == 1:
            raise AuthError("CH_ACCESS_TOKEN_INVALID: stale token")
        return FakeRuntime()

    result = invoke(lambda event, rt: "ok", {}, build=build)

    assert result == "ok"
    assert len(builds) == 2


def test_a_second_auth_error_is_not_retried_again():
    builds = []

    def build():
        builds.append(1)
        raise AuthError("CH_ACCESS_TOKEN_INVALID")

    with pytest.raises(AuthError):
        invoke(lambda event, rt: "ok", {}, build=build)

    assert len(builds) == 2


def test_other_errors_are_not_retried():
    builds = []

    def build():
        builds.append(1)
        raise ValueError("unrelated")

    with pytest.raises(ValueError):
        invoke(lambda event, rt: "ok", {}, build=build)

    assert len(builds) == 1


def test_a_failing_handler_logs_one_ags_error_line_with_the_event_ids(caplog):
    from ags import blockers

    def handle(event, rt):
        blockers.record("deepseek", "HTTP 402: insufficient balance")
        raise ValueError("boom")

    handle.__module__ = "ags.handlers.commodity_pipeline"
    event = {"commodity": "coffee", "scheduled_time": "2026-10-26T20:00:00+00:00"}

    with pytest.raises(ValueError), caplog.at_level("ERROR"):
        invoke(handle, event, build=FakeRuntime)

    [record] = [r for r in caplog.records if r.getMessage().startswith("ags-error")]
    assert record.getMessage() == (
        "ags-error handler=commodity_pipeline commodity=coffee scheduled_time=2026-10-26T20:00:00+00:00 "
        "run_id=coffee_2026-10-26T20-00-00Z blocked=deepseek: HTTP 402: insufficient balance "
        "error=ValueError: boom"
    )


def test_each_invocation_starts_with_no_blockers():
    from ags import blockers

    blockers.record("tavily", "stale from the last invocation")

    assert invoke(lambda event, rt: blockers.seen(), {}, build=FakeRuntime) == {}
