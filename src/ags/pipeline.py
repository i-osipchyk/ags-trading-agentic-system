import asyncio
import json
import threading
from datetime import date, datetime
from pathlib import Path

from ags.agents.news import run_news_analyst
from ags.agents.supply_demand import run_supply_demand_analyst
from ags.agents.technical import run_technical_analyst
from ags.agents.weather import run_weather_analyst
from ags.llm.loop import ChatClient
from ags.logging.run_id import generate_run_id
from ags.logging.run_log import RunLog


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
    """Wraps a ChatClient, recording one agent's prompts, tool calls and tool results.

    The loop sends the full message history on every call, so what is new
    since the previous call (tool results, the cap-hit nudge) is what gets logged.
    """

    def __init__(self, inner: ChatClient, run_log: _SharedRunLog, agent: str) -> None:
        self._inner = inner
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

        for tool_call in response.get("tool_calls") or []:
            self._log(
                "tool_call",
                {
                    "id": tool_call["id"],
                    "name": tool_call["function"]["name"],
                    "arguments": json.loads(tool_call["function"]["arguments"]),
                },
            )
        # +1 for the assistant response the loop appends after this call.
        self._seen = len(messages) + 1
        return response

    def _log(self, event_type: str, payload: dict) -> None:
        self._run_log.append(agent=self._agent, event_type=event_type, payload=payload)


def run_pipeline(
    *,
    commodity: str,
    as_of: date,
    trigger_timestamp: datetime,
    chat_client: ChatClient,
    model: str,
    log_dir: Path,
    data_dir: Path,
    prices_client=None,
    news_client=None,
    weather_client=None,
    usda_client=None,
) -> dict:
    run_id = generate_run_id(commodity, trigger_timestamp)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    run_log = _SharedRunLog(RunLog(log_dir, run_id))
    common = {"commodity": commodity, "as_of": as_of, "model": model}

    def _client(agent: str) -> LoggingChatClient:
        return LoggingChatClient(chat_client, run_log, agent)

    analysts = {
        "technical": lambda: run_technical_analyst(
            data_dir, _client("technical"), prices_client=prices_client, **common
        ),
        "news": lambda: run_news_analyst(data_dir, _client("news"), news_client=news_client, **common),
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
    return {"run_id": run_id, "outputs": outputs}
