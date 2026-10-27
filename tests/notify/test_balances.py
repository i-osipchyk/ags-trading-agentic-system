import pytest

from ags.notify.balances import credit_alerts, deepseek_balance, provider_balances, tavily_remaining


class Response:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.body


def fake_get(body, status=200, seen=None):
    def get(url, *, headers, timeout):
        if seen is not None:
            seen.append((url, headers))
        return Response(body, status)

    return get


def test_deepseek_balance_prefers_usd_and_hits_the_api_root():
    seen = []
    body = {"is_available": True, "balance_infos": [
        {"currency": "CNY", "total_balance": "70.00"}, {"currency": "USD", "total_balance": "9.50"}]}

    balance = deepseek_balance("k", "https://api.deepseek.com/v1/", get=fake_get(body, seen=seen))

    assert balance == {"available": True, "currency": "USD", "balance": 9.5}
    assert seen == [("https://api.deepseek.com/user/balance", {"Authorization": "Bearer k"})]


def test_tavily_remaining_is_the_tighter_of_key_and_account_limits():
    body = {"key": {"usage": 900, "limit": 1000},
            "account": {"plan_usage": 1000, "plan_limit": 1000, "paygo_usage": 10, "paygo_limit": 50}}
    assert tavily_remaining("k", get=fake_get(body)) == {"remaining": 40}
    assert tavily_remaining("k", get=fake_get({"key": {"usage": 5, "limit": None}, "account": {}})) == {"remaining": None}


def test_a_failed_check_is_reported_not_raised():
    config = type("C", (), {"deepseek_api_key": "d", "deepseek_base_url": "https://x", "tavily_api_key": "t"})()
    balances = provider_balances(config, get=fake_get({}, status=500))
    assert set(balances) == {"deepseek", "tavily"} and all("error" in v for v in balances.values())
    assert provider_balances(None) == {}


def alerts(deepseek=None, tavily=None, cost=1.0, credits=10, min_cycles=4):
    balances = {"deepseek": deepseek or {}, "tavily": tavily or {}}
    return credit_alerts(balances, cycle_cost_usd=cost, tavily_credits_per_evening=credits, min_cycles=min_cycles)


@pytest.mark.parametrize("balance, expected", [(3.9, 1), (4.0, 0)])
def test_deepseek_warns_below_the_cycles_threshold(balance, expected):
    assert len(alerts(deepseek={"available": True, "currency": "USD", "balance": balance})) == expected


def test_deepseek_unavailable_is_an_outright_alert_and_cny_never_guesses_a_rate():
    assert "next cycle will fail" in alerts(deepseek={"available": False, "currency": "USD", "balance": 0.0})[0]
    assert alerts(deepseek={"available": True, "currency": "CNY", "balance": 0.5}) == []


def test_tavily_warns_on_evenings_left_and_when_empty():
    assert "about 3 more evening" in alerts(tavily={"remaining": 30})[0]
    assert alerts(tavily={"remaining": 40}) == []
    # A morning cycle uses no credits: only an empty account alerts.
    assert alerts(tavily={"remaining": 5}, credits=0) == []
    assert alerts(tavily={"remaining": 0}, credits=0)[0].startswith("⛔ Tavily")


def test_errors_and_unknowns_stay_quiet():
    assert alerts(deepseek={"error": "x"}, tavily={"error": "y"}) == []
    assert alerts(deepseek={"available": True, "currency": "USD", "balance": 0.1}, cost=0.0) == []
