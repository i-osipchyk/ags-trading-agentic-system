import json
import threading
from datetime import date, datetime, timezone

from ags.logging.run_log import RunLog
from ags.pipeline import run_pipeline

AS_OF = date(2026, 6, 5)
TRIGGER = datetime(2026, 6, 5, 18, 30, 0, tzinfo=timezone.utc)

_CANNED = {
    "Technical analyst": {
        "seasonality_alignment": "typical",
        "key_levels": {"recent_range": [1, 2], "breakout_points": []},
        "chart_description": "flat",
    },
    "News analyst": {"headlines": []},
    "Weather/season analyst": {"regions_covered": [], "current_conditions": {}, "risk_assessment": {}},
    "Supply/demand analyst": {"latest_report": None, "key_figures": [], "stocks_to_use": None},
    "Coordinator": {
        "call": "neutral",
        "conviction": 1,
        "supporting_analysts": [],
        "dissenting_analysts": [],
        "thesis": "No fresh evidence.",
        "key_drivers": [],
        "invalidation_conditions": [],
    },
}


class RoutingChatClient:
    """Stub model: answers each analyst with a canned JSON reply, routed by its system prompt.

    Thread-safe because the pipeline runs the four analysts concurrently.
    """

    def __init__(self, replies=None):
        self._replies = replies or _CANNED
        self._lock = threading.Lock()
        self.calls = []

    def complete(self, messages, tools, *, model):
        with self._lock:
            self.calls.append(messages)
        system = messages[0]["content"]
        for marker, reply in self._replies.items():
            if marker in system:
                if isinstance(reply, list):  # scripted multi-turn conversation
                    with self._lock:
                        return reply.pop(0)
                return {"role": "assistant", "content": json.dumps(reply), "tool_calls": None}
        raise AssertionError(f"unrecognised analyst prompt: {system[:80]}")


class EmptyNewsClient:
    def search(self, query, *, published_before):
        return []


class FailingPricesClient:
    def get_daily_bars(self, symbol_name, start, end):
        raise ConnectionError("cTrader connection refused")


def _run(tmp_path, chat_client=None):
    return run_pipeline(
        commodity="corn",
        as_of=AS_OF,
        trigger_timestamp=TRIGGER,
        chat_client=chat_client or RoutingChatClient(),
        model="stub-model",
        log_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
        prices_client=FailingPricesClient(),
        news_client=EmptyNewsClient(),
    )


def test_run_pipeline_returns_run_id_and_one_output_per_analyst(tmp_path):
    result = _run(tmp_path)

    assert result["run_id"] == "corn_2026-06-05T18-30-00Z"
    assert set(result["outputs"]) == {"technical", "news", "weather", "supply_demand"}
    assert result["outputs"]["news"]["headlines"] == []


def _events(tmp_path, run_id):
    return RunLog(tmp_path / "logs", run_id).read()


def test_run_pipeline_logs_prompt_and_output_for_every_analyst_with_contiguous_seq(tmp_path):
    result = _run(tmp_path)

    events = _events(tmp_path, result["run_id"])

    assert [e["seq"] for e in events] == list(range(len(events)))
    for agent in ("technical", "news", "weather", "supply_demand"):
        agent_events = [e for e in events if e["agent"] == agent]
        prompts = [e for e in agent_events if e["event_type"] == "prompt"]
        outputs = [e for e in agent_events if e["event_type"] == "output"]
        assert len(prompts) == 1
        assert prompts[0]["payload"]["messages"][0]["role"] == "system"
        assert len(outputs) == 1
        assert outputs[0]["payload"] == result["outputs"][agent]


def test_run_pipeline_logs_each_tool_call_and_its_result_in_order(tmp_path):
    replies = dict(_CANNED)
    replies["News analyst"] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_news", "arguments": json.dumps({"query": "corn futures news"})},
                }
            ],
        },
        {"role": "assistant", "content": json.dumps({"headlines": []}), "tool_calls": None},
    ]

    result = _run(tmp_path, RoutingChatClient(replies))

    news_events = [e for e in _events(tmp_path, result["run_id"]) if e["agent"] == "news"]
    assert [e["event_type"] for e in news_events] == ["prompt", "tool_call", "tool_result", "output"]
    assert news_events[1]["payload"] == {"id": "call_1", "name": "search_news", "arguments": {"query": "corn futures news"}}
    assert news_events[2]["payload"]["tool_call_id"] == "call_1"
    assert json.loads(news_events[2]["payload"]["content"]) == {"results": []}


def test_run_pipeline_gives_every_analyst_the_same_as_of(tmp_path):
    result = _run(tmp_path)

    prompts = [e for e in _events(tmp_path, result["run_id"]) if e["event_type"] == "prompt"]

    analyst_prompts = [e for e in prompts if e["agent"] != "coordinator"]
    assert len(analyst_prompts) == 4
    for event in analyst_prompts:
        assert "2026-06-05" in event["payload"]["messages"][0]["content"]


def test_run_pipeline_keeps_analysts_blind_to_each_others_output(tmp_path):
    replies = {
        **_CANNED,
        "Technical analyst": {**_CANNED["Technical analyst"], "chart_description": "MARKER-TECH"},
        "News analyst": {"headlines": [{"headline": "MARKER-NEWS"}]},
        "Weather/season analyst": {**_CANNED["Weather/season analyst"], "current_conditions": {"IA": "MARKER-WX"}},
        "Supply/demand analyst": {**_CANNED["Supply/demand analyst"], "latest_report": "MARKER-SD"},
    }
    markers = {"technical": "MARKER-TECH", "news": "MARKER-NEWS", "weather": "MARKER-WX", "supply_demand": "MARKER-SD"}

    result = _run(tmp_path, RoutingChatClient(replies))

    for event in _events(tmp_path, result["run_id"]):
        if event["agent"] != "coordinator" and event["event_type"] in ("prompt", "tool_call", "tool_result"):
            text = json.dumps(event["payload"])
            for owner, marker in markers.items():
                if owner != event["agent"]:
                    assert marker not in text


def test_run_pipeline_records_a_crashed_analyst_as_degraded_and_finishes_the_others(tmp_path):
    class GarbageTechnicalClient(RoutingChatClient):
        def complete(self, messages, tools, *, model):
            if "Technical analyst" in messages[0]["content"]:
                return {"role": "assistant", "content": "not json", "tool_calls": None}
            return super().complete(messages, tools, model=model)

    result = _run(tmp_path, GarbageTechnicalClient())

    assert result["outputs"]["technical"]["degraded"] is True
    assert "error" in result["outputs"]["technical"]
    assert result["outputs"]["news"] == {"headlines": [], "degraded": False}
    technical_outputs = [
        e
        for e in _events(tmp_path, result["run_id"])
        if e["agent"] == "technical" and e["event_type"] == "output"
    ]
    assert technical_outputs[0]["payload"] == result["outputs"]["technical"]


def test_run_pipeline_returns_the_coordinators_call_and_logs_it(tmp_path):
    result = _run(tmp_path)

    assert result["call"]["call"] == "neutral"
    assert result["call"]["horizon"] == "1 week"
    assert result["call"]["degraded"] is False
    coordinator_events = [e for e in _events(tmp_path, result["run_id"]) if e["agent"] == "coordinator"]
    assert [e["event_type"] for e in coordinator_events] == ["prompt", "output"]
    assert coordinator_events[1]["payload"] == result["call"]


def test_run_pipeline_feeds_the_coordinator_exactly_the_four_analyst_outputs(tmp_path):
    replies = {**_CANNED, "News analyst": {"headlines": [{"headline": "MARKER-NEWS"}]}}

    result = _run(tmp_path, RoutingChatClient(replies))

    events = _events(tmp_path, result["run_id"])
    coordinator_prompt = json.dumps(next(e for e in events if e["agent"] == "coordinator" and e["event_type"] == "prompt")["payload"])
    assert "MARKER-NEWS" in coordinator_prompt
    # The coordinator runs after the fan-out: no analyst prompt may contain its output.
    last_analyst_output = max(e["seq"] for e in events if e["event_type"] == "output" and e["agent"] != "coordinator")
    first_coordinator = min(e["seq"] for e in events if e["agent"] == "coordinator")
    assert first_coordinator > last_analyst_output


def test_run_pipeline_records_a_crashed_coordinator_as_a_degraded_call(tmp_path):
    class GarbageCoordinatorClient(RoutingChatClient):
        def complete(self, messages, tools, *, model):
            if "Coordinator" in messages[0]["content"]:
                raise RuntimeError("model unavailable")
            return super().complete(messages, tools, model=model)

    result = _run(tmp_path, GarbageCoordinatorClient())

    assert result["call"]["degraded"] is True
    assert result["call"]["call"] is None
    assert "model unavailable" in result["call"]["error"]
    assert result["outputs"]["news"] == {"headlines": [], "degraded": False}


class WeekdayPricesClient:
    """Serves a flat 4.00 close on every weekday, except 5.25 on the as-of day."""

    def get_daily_bars(self, symbol_name, start, end):
        from datetime import timedelta

        bars = []
        day = start
        while day <= end:
            if day.weekday() < 5:
                close = 5.25 if day == AS_OF else 4.0
                bars.append(
                    {"date": day.isoformat(), "open": close, "high": close, "low": close, "close": close, "volume": 1}
                )
            day += timedelta(days=1)
        return bars


def test_run_pipeline_stamps_the_call_with_the_as_of_close_from_code_not_the_model(tmp_path):
    result = run_pipeline(
        commodity="corn",
        as_of=AS_OF,
        trigger_timestamp=TRIGGER,
        chat_client=RoutingChatClient(),
        model="stub-model",
        log_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
        prices_client=WeekdayPricesClient(),
        news_client=EmptyNewsClient(),
    )

    assert result["call"]["price_at_call"] == {"price": 5.25, "date": "2026-06-05"}


def test_run_pipeline_leaves_price_at_call_empty_when_prices_are_unavailable(tmp_path):
    result = _run(tmp_path)  # FailingPricesClient

    assert result["call"]["price_at_call"] is None
    assert result["call"]["degraded"] is False


def test_run_pipeline_gives_the_coordinator_its_past_calls_but_not_the_current_run(tmp_path):
    past_id = "corn_2026-05-29T18-30-00Z"
    past_call = {**_CANNED["Coordinator"], "call": "bullish", "price_at_call": None}
    (tmp_path / "logs").mkdir()
    RunLog(tmp_path / "logs", past_id).append(agent="coordinator", event_type="output", payload=past_call)
    (tmp_path / "audits" / past_id).mkdir(parents=True)
    (tmp_path / "audits" / past_id / "1w.json").write_text(
        json.dumps(
            {
                "checkpoint_weeks": 1,
                "direction_correct": True,
                "driver_verdicts": [],
                "calibration_note": "fine",
                "thesis_vs_outcome": "matched",
                "graded_at": "2026-06-05T12:00:00+00:00",
            }
        )
    )
    tool_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "read_track_record", "arguments": '{"lookback_weeks": 8}'}}
        ],
    }
    final = {"role": "assistant", "content": json.dumps(_CANNED["Coordinator"]), "tool_calls": None}
    client = RoutingChatClient({**_CANNED, "Coordinator": [tool_call, final]})

    result = run_pipeline(
        commodity="corn",
        as_of=AS_OF,
        trigger_timestamp=TRIGGER,
        chat_client=client,
        model="stub-model",
        log_dir=tmp_path / "logs",
        audit_dir=tmp_path / "audits",
        data_dir=tmp_path / "data",
        prices_client=FailingPricesClient(),
        news_client=EmptyNewsClient(),
    )

    tool_results = [
        m for e in _events(tmp_path, result["run_id"]) if e["agent"] == "coordinator" and e["event_type"] == "tool_result"
        for m in [e["payload"]]
    ]
    [runs] = [json.loads(r["content"])["runs"] for r in tool_results]
    assert [r["run_id"] for r in runs] == [past_id]
    assert [a["checkpoint_weeks"] for a in runs[0]["audits"]] == [1]
