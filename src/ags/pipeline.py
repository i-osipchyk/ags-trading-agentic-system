import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ags.agents.coordinator import run_coordinator
from ags.agents.news import carry_forward_news, fetches_news, run_news_analyst
from ags.agents.supply_demand import run_supply_demand_analyst
from ags.agents.technical import run_technical_analyst
from ags.agents.weather import run_weather_analyst
from ags.llm.client import bind, trace
from ags.llm.loop import ChatClient
from ags.logging.run_id import generate_run_id
from ags.logging.run_log import RunLog
from ags.storage import LocalStorage, as_storage
from ags.tools.pit_store import ET
from ags.tools.sources.prices import LIVE_CYCLE_MAX_AGE, get_prices, get_quote
from ags.tools.track_record import read_track_record


class _SharedRunLog:
    # RunLog.append assigns seq and writes non-atomically; the four analysts
    # run in parallel threads, so appends must be serialised to keep seq
    # unique and contiguous.
    def __init__(self, run_log: RunLog) -> None:
        self._run_log = run_log
        self._lock = threading.Lock()

    def append(self, *, agent: str, event_type: str, payload: dict) -> None:
        with self._lock:
            self._run_log.append(agent=agent, event_type=event_type, payload=payload)


class LoggingChatClient:
    """Wraps a ChatClient, recording one agent's prompts, raw replies, tool calls and tool results.

    The inner client is bound to the agent as the llmkit component, with `metadata` (commodity,
    run_id) on every call record. The loop sends the full message history on every call, so what is new
    since the previous call (tool results, the cap-hit nudge) is what gets logged.
    """

    def __init__(self, inner: ChatClient, run_log: _SharedRunLog, agent: str, **metadata) -> None:
        self._inner = bind(inner, agent, **metadata)
        self._run_log = run_log
        self._agent = agent
        self._seen = 0

    def complete(self, messages: list[dict], tools: list[dict] | None, *, model: str) -> dict:
        if self._seen == 0:
            self._log("prompt", {"messages": list(messages), "model": model})
        else:
            for message in messages[self._seen :]:
                if message["role"] == "tool":
                    self._log("tool_result", {"tool_call_id": message["tool_call_id"], "content": message["content"]})
                else:
                    self._log("prompt", {"messages": [message], "model": model})

        response = self._inner.complete(messages, tools, model=model)

        # The raw reply, before any parsing — a reply that fails JSON parsing
        # is otherwise unrecoverable from the log.
        self._log("response", {"content": response.get("content"), "usage": response.get("usage")})
        for tool_call in response.get("tool_calls") or []:
            raw_arguments = tool_call["function"]["arguments"]
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                # Logged verbatim; the loop answers the model with an error rather than crashing.
                arguments = raw_arguments
            self._log(
                "tool_call",
                {"id": tool_call["id"], "name": tool_call["function"]["name"], "arguments": arguments},
            )
        # +1 for the assistant response the loop appends after this call.
        self._seen = len(messages) + 1
        return response

    def _log(self, event_type: str, payload: dict) -> None:
        self._run_log.append(agent=self._agent, event_type=event_type, payload=payload)


def _reference_quote(data_dir, commodity: str, as_of: datetime, prices_client, now: datetime) -> dict | None:
    """The intraday bid/ask when the cycle starts, stamped by code. None if unavailable or not a live cycle."""
    if now - as_of > LIVE_CYCLE_MAX_AGE:
        return None
    try:
        quote = get_quote(data_dir, commodity=commodity, as_of=now, client=prices_client, now=now)
    except Exception:
        return None
    if not quote or not (quote.get("bid") and quote.get("ask")):
        return None
    return {
        "kind": "intraday_quote", "bid": quote["bid"], "ask": quote["ask"], "price": (quote["bid"] + quote["ask"]) / 2,
        "quote_timestamp": quote["timestamp"], "captured_at": now.isoformat(),
    }


def _price_at_call(data_dir: Path, commodity: str, as_of: datetime, prices_client, reference: dict | None = None) -> dict | None:
    """The price the call is graded from: the intraday reference quote if there is one, else the last close.

    Deterministic fact, stamped by code — never asked of the model.
    """
    close = _last_close(data_dir, commodity, as_of, prices_client)
    if reference is None:
        return None if close is None else {**close, "kind": "daily_close"}
    return {**reference, "date": as_of.astimezone(ET).date().isoformat(), "last_close": close}


def _last_close(data_dir: Path, commodity: str, as_of: datetime, prices_client) -> dict | None:
    # A week lookback spans weekends/holidays so the latest trading close is found.
    try:
        bars = get_prices(
            data_dir, commodity=commodity, start=as_of.astimezone(ET).date() - timedelta(days=7),
            end=as_of.astimezone(ET).date(),
            as_of=as_of,
            client=prices_client,
        )
    except Exception:
        return None
    if not bars:
        return None
    return {"price": bars[-1]["close"], "date": bars[-1]["date"]}


def run_pipeline(
    *,
    commodity: str,
    as_of: datetime,
    trigger_timestamp: datetime,
    chat_client: ChatClient,
    model: str,
    log_dir: Path,
    data_dir: Path,
    audit_dir: Path | None = None,
    prices_client=None,
    news_client=None,
    weather_client=None,
    usda_client=None,
    cot_client=None,
    replace_log: bool = False,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    run_id = generate_run_id(commodity, trigger_timestamp)
    log_store = as_storage(log_dir)
    # Non-local backends buffer the log and upload once; the finally below
    # guarantees a crashed run still leaves its partial log. `replace_log`: a retry, whose
    # earlier attempt the caller has archived, starts the log over instead of appending to it.
    raw_log = RunLog(log_store, run_id, buffered=not isinstance(log_store, LocalStorage), fresh=replace_log)
    try:
        with trace(chat_client, run_id):
            return _run(raw_log, run_id, commodity, as_of, trigger_timestamp, chat_client, model,
                        log_store, data_dir, audit_dir, prices_client, news_client, weather_client, usda_client, cot_client, now)
    finally:
        raw_log.close()


def _run(raw_log, run_id, commodity, as_of, trigger_timestamp, chat_client, model,
         log_dir, data_dir, audit_dir, prices_client, news_client, weather_client, usda_client, cot_client, now) -> dict:
    reference = _reference_quote(data_dir, commodity, as_of, prices_client, now)
    run_log = _SharedRunLog(raw_log)
    common = {"commodity": commodity, "as_of": as_of, "model": model}

    def _client(agent: str) -> LoggingChatClient:
        return LoggingChatClient(chat_client, run_log, agent, commodity=commodity, run_id=run_id)

    analysts = {
        "technical": lambda: run_technical_analyst(
            data_dir, _client("technical"), prices_client=prices_client, cot_client=cot_client,
            reference_quote=reference, now=now, **common
        ),
        "news": (
            (lambda: run_news_analyst(data_dir, _client("news"), news_client=news_client, **common))
            if fetches_news(as_of)
            # Bounded by both: a local run's trigger is wall-clock time, which can be after as_of.
            else lambda: carry_forward_news(log_dir, commodity=commodity, before=min(as_of, trigger_timestamp))
        ),
        "weather": lambda: run_weather_analyst(
            data_dir, _client("weather"), weather_client=weather_client, **common
        ),
        "supply_demand": lambda: run_supply_demand_analyst(
            data_dir, _client("supply_demand"), usda_client=usda_client, **common
        ),
    }

    def _guarded(fn) -> dict:
        # One analyst crashing (e.g. malformed model output) must not abort
        # the run — the others still complete and the failure is surfaced
        # as degraded, same as a source failure.
        try:
            return fn()
        except Exception as exc:
            return {"degraded": True, "error": f"{type(exc).__name__}: {exc}"}

    async def _fan_out() -> list[dict]:
        return await asyncio.gather(*(asyncio.to_thread(_guarded, fn) for fn in analysts.values()))

    outputs = dict(zip(analysts, asyncio.run(_fan_out())))
    for agent, output in outputs.items():
        run_log.append(agent=agent, event_type="output", payload=output)

    track_record = None
    if audit_dir is not None:
        # Coordinator-only, and bounded by this run's own trigger so the
        # current run (and anything later) never appears in its own record.
        track_record = lambda lookback_weeks: read_track_record(  # noqa: E731
            log_dir, audit_dir, commodity=commodity, lookback_weeks=lookback_weeks, as_of=trigger_timestamp
        )

    try:
        call = run_coordinator(
            _client("coordinator"),
            commodity=commodity,
            as_of=as_of,
            model=model,
            analyst_outputs=outputs,
            price_at_call=_price_at_call(data_dir, commodity, as_of, prices_client, reference),
            track_record=track_record,
        )
    except Exception as exc:
        call = {"call": None, "degraded": True, "error": f"{type(exc).__name__}: {exc}"}
    run_log.append(agent="coordinator", event_type="output", payload=call)
    return {"run_id": run_id, "outputs": outputs, "call": call}
