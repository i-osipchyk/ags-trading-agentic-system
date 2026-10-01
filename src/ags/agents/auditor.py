import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ags.llm.loop import ChatClient, run_loop
from ags.tools.sources.prices import PricesClient, get_prices
from ags.tools.trend import get_trend_state

CHECKPOINT_WEEKS = (1, 2, 4, 8)

_RUN_ID = re.compile(r"^[a-z]+_(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)$")


def _triggered_at(run_id: str) -> datetime | None:
    match = _RUN_ID.match(run_id)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)


_TREND_HISTORY_DAYS = 200
_PLAYED_OUT = {"yes", "no", "partially"}
_VERDICT_FIELDS = ("driver_verdicts", "calibration_note", "thesis_vs_outcome")

SYSTEM_PROMPT_TEMPLATE = """You are the Auditor for {commodity} futures, grading one past call at its
{weeks}-week checkpoint ({checkpoint_date}). You see only the frozen record of
that run and the price path up to the checkpoint — nothing newer.

The price outcome below is computed by code; treat it as fact. Your job is
judgment: for each named driver, did it play out? Did price move for the stated
reason or for something else (right call / wrong reason is a distinct case)? Was
the conviction level borne out or followed by chop? If the call was neutral it is
graded only via thesis_vs_outcome. Report disagreement with the original analysts
as you see it; do not defer to them.

Respond with a JSON object containing exactly these fields:
  "driver_verdicts": list of {{"driver": ..., "played_out": "yes" | "no" | "partially", "evidence": ...}}, one per key driver
  "calibration_note": did the conviction level resolve cleanly or was it followed by chop
  "thesis_vs_outcome": did price move for the stated reason, or for something else
No other text — JSON only."""


def _outputs(log_path: Path) -> dict[str, dict]:
    outputs = {}
    for line in log_path.read_text().splitlines():
        event = json.loads(line)
        if event["event_type"] == "output":
            outputs[event["agent"]] = event["payload"]
    return outputs


def find_due_checkpoints(log_dir: Path, audit_dir: Path, *, now: datetime) -> list[tuple[str, int]]:
    due = []
    for log_path in sorted(Path(log_dir).glob("*.jsonl")):
        triggered = _triggered_at(log_path.stem)
        if triggered is None:
            continue
        output = _outputs(log_path).get("coordinator")
        if output is None or output.get("call") is None:
            continue
        for weeks in CHECKPOINT_WEEKS:
            graded = Path(audit_dir, log_path.stem, f"{weeks}w.json").exists()
            if triggered + timedelta(weeks=weeks) <= now and not graded:
                due.append((log_path.stem, weeks))
    return due


def _closes(bars: list[dict]) -> pd.Series:
    return pd.Series({pd.Timestamp(b["date"]): b["close"] for b in bars}).sort_index()


def _price_outcome(
    data_dir: Path, *, commodity: str, call_day: date, checkpoint_day: date, start_price: float, prices_client
) -> dict:
    # as_of is the checkpoint date, so nothing newer than the checkpoint can be read.
    bars = get_prices(
        data_dir,
        commodity=commodity,
        start=call_day - timedelta(days=_TREND_HISTORY_DAYS),
        end=checkpoint_day,
        as_of=checkpoint_day,
        client=prices_client,
    )
    closes = _closes(bars)
    at_call = closes[closes.index <= pd.Timestamp(call_day)]
    path = closes[closes.index > pd.Timestamp(call_day)]
    if path.empty:
        raise ValueError(f"no price data after {call_day} up to {checkpoint_day}")

    end_price = float(path.iloc[-1])
    ret = end_price / start_price - 1
    # Trend-only benchmark: follow the medium trend as it stood at the call.
    state = get_trend_state(at_call)["medium"]["state"]
    position = {"up": "long", "down": "short", "sideways": "flat"}[state]
    benchmark = {"long": ret, "short": -ret, "flat": 0.0}[position]
    return {
        "start_price": start_price,
        "end_price": end_price,
        "end_date": path.index[-1].date().isoformat(),
        "return": ret,
        "trend_benchmark": {"position": position, "return": benchmark},
        "path": [{"date": ts.date().isoformat(), "close": float(c)} for ts, c in path.items()],
    }


def _direction_correct(call: str, ret: float) -> bool | None:
    # Neutral calls are excluded from direction scoring entirely.
    if call == "neutral":
        return None
    return ret > 0 if call == "bullish" else ret < 0


def _validate_verdicts(parsed: dict) -> None:
    for field in _VERDICT_FIELDS:
        if field not in parsed:
            raise ValueError(f"missing field: {field}")
    for verdict in parsed["driver_verdicts"]:
        if verdict.get("played_out") not in _PLAYED_OUT:
            raise ValueError(f"unknown played_out: {verdict.get('played_out')!r}")


def grade_checkpoint(
    chat_client: ChatClient,
    *,
    log_dir: Path,
    data_dir: Path,
    run_id: str,
    weeks: int,
    model: str,
    now: datetime,
    prices_client: PricesClient | None = None,
) -> dict:
    outputs = _outputs(Path(log_dir) / f"{run_id}.jsonl")
    call = outputs["coordinator"]
    triggered = _triggered_at(run_id)
    commodity = run_id.split("_")[0]
    checkpoint_day = (triggered + timedelta(weeks=weeks)).date()

    price_outcome = _price_outcome(
        data_dir,
        commodity=commodity,
        call_day=triggered.date(),
        checkpoint_day=checkpoint_day,
        start_price=call["price_at_call"]["price"],
        prices_client=prices_client,
    )

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        commodity=commodity, weeks=weeks, checkpoint_date=checkpoint_day.isoformat()
    )
    user_prompt = (
        "Frozen run record (analyst outputs and the coordinator's call):\n"
        + json.dumps(outputs, indent=2, default=str)
        + "\n\nPrice outcome (code-computed, including the daily closes since the call):\n"
        + json.dumps(price_outcome, indent=2)
    )
    loop_result = run_loop(chat_client, model=model, system_prompt=system_prompt, user_prompt=user_prompt, tools=[])
    parsed = json.loads(loop_result.content)
    _validate_verdicts(parsed)

    return {
        "run_id": run_id,
        "checkpoint_weeks": weeks,
        "price_outcome": price_outcome,
        "direction_correct": _direction_correct(call["call"], price_outcome["return"]),
        **{field: parsed[field] for field in _VERDICT_FIELDS},
        "graded_at": now.isoformat(),
    }


def run_auditor(
    chat_client: ChatClient,
    *,
    log_dir: Path,
    audit_dir: Path,
    data_dir: Path,
    model: str,
    now: datetime,
    prices_client: PricesClient | None = None,
) -> dict:
    graded, failed = [], []
    for run_id, weeks in find_due_checkpoints(log_dir, audit_dir, now=now):
        try:
            audit = grade_checkpoint(
                chat_client,
                log_dir=log_dir,
                data_dir=data_dir,
                run_id=run_id,
                weeks=weeks,
                model=model,
                now=now,
                prices_client=prices_client,
            )
        except Exception as exc:
            # Nothing is written, so the pair stays due and the next scan retries it.
            failed.append(((run_id, weeks), f"{type(exc).__name__}: {exc}"))
            continue
        path = Path(audit_dir) / run_id / f"{weeks}w.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(audit, indent=2))
        graded.append((run_id, weeks))
    return {"graded": graded, "failed": failed}
