"""Provider credit, checked after every scheduled cycle so a top-up happens before a cycle fails.

DeepSeek: GET /user/balance (api-docs.deepseek.com/api/get-user-balance). Tavily: GET /usage
(docs.tavily.com, "Usage"). Neither is a model call (no completion, no tokens), so neither goes
through llmkit. A check that fails is reported as {"error": ...}, never raised: it must not
take the portfolio stage down.

The thresholds are in cycles, not dollars: what matters is how many more cycles the balance
covers at the rate this cycle spent it (`credit_alerts`).
"""

import logging
import math

import requests

logger = logging.getLogger(__name__)

TAVILY_USAGE_URL = "https://api.tavily.com/usage"
_TIMEOUT = 10


def deepseek_balance(api_key: str, base_url: str, *, get=requests.get) -> dict:
    """{"available", "currency", "balance"}: the USD balance if there is one, else the first."""
    # /user/balance sits at the API root, not under the OpenAI-compatible /v1.
    root = base_url.rstrip("/").removesuffix("/v1")
    response = get(f"{root}/user/balance", headers={"Authorization": f"Bearer {api_key}"}, timeout=_TIMEOUT)
    response.raise_for_status()
    body = response.json()
    infos = body.get("balance_infos") or []
    info = next((i for i in infos if i.get("currency") == "USD"), infos[0] if infos else None)
    return {
        "available": bool(body.get("is_available")),
        "currency": info.get("currency") if info else None,
        "balance": float(info["total_balance"]) if info else None,
    }


def tavily_remaining(api_key: str, *, get=requests.get) -> dict:
    """{"remaining"}: credits left this billing cycle, the tighter of the key's and the account's
    limits (plan plus pay-as-you-go); None when neither is limited."""
    response = get(TAVILY_USAGE_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=_TIMEOUT)
    response.raise_for_status()
    body = response.json()
    key, account = body.get("key") or {}, body.get("account") or {}
    left = []
    if key.get("limit") is not None:
        left.append(key["limit"] - (key.get("usage") or 0))
    if account.get("plan_limit") is not None:
        plan = max(0, account["plan_limit"] - (account.get("plan_usage") or 0))
        paygo = max(0, (account.get("paygo_limit") or 0) - (account.get("paygo_usage") or 0))
        left.append(plan + paygo)
    return {"remaining": min(left) if left else None}


def provider_balances(config, *, get=requests.get) -> dict:
    if config is None:
        return {}
    checks = {
        "deepseek": lambda: deepseek_balance(config.deepseek_api_key, config.deepseek_base_url, get=get),
        "tavily": lambda: tavily_remaining(config.tavily_api_key, get=get),
    }
    out = {}
    for name, check in checks.items():
        try:
            out[name] = check()
        except Exception as exc:
            logger.warning("%s balance check failed: %s", name, exc)
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def credit_alerts(
    balances: dict, *, cycle_cost_usd: float, tavily_credits_per_evening: int, min_cycles: int
) -> list[str]:
    """Alerts for a provider that's out, or covers fewer than `min_cycles` more cycles.

    The rate is this cycle's spend; for Tavily, only an evening (searching) cycle has one, so a
    morning cycle alerts only when the credits are gone.
    """
    alerts = []
    deepseek = balances.get("deepseek") or {}
    if "error" not in deepseek and deepseek:
        balance, currency = deepseek.get("balance"), deepseek.get("currency")
        shown = f"{balance:.2f} {currency}" if balance is not None else "unknown"
        if not deepseek.get("available"):
            alerts.append(
                f"⛔ DeepSeek says the balance ({shown}) is not enough for API calls: the next cycle "
                "will fail. Top up now."
            )
        elif currency == "USD" and balance is not None and cycle_cost_usd > 0:
            cycles = math.floor(balance / cycle_cost_usd)
            if cycles < min_cycles:
                alerts.append(
                    f"⚠️ DeepSeek balance ${balance:.2f} covers about {cycles} more cycle(s) "
                    f"(this one cost ${cycle_cost_usd:.2f}). Top up before it runs out."
                )
    tavily = balances.get("tavily") or {}
    remaining = tavily.get("remaining")
    if "error" not in tavily and remaining is not None:
        if remaining <= 0:
            alerts.append(
                "⛔ Tavily has no credits left this billing cycle: the next News-searching cycle (06:00 ET) will be held. "
                "Raise the limit or top up."
            )
        elif tavily_credits_per_evening > 0:
            evenings = remaining // tavily_credits_per_evening
            if evenings < min_cycles:
                alerts.append(
                    f"⚠️ Tavily has {remaining} credits left, about {evenings} more searching cycle(s) "
                    f"(this one used up to {tavily_credits_per_evening})."
                )
    return alerts
