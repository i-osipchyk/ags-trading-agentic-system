import json
from datetime import datetime, timezone

from ags.notify.digest import compose_digest
from ags.storage import LocalStorage

NOW = datetime(2026, 6, 10, 21, 0, tzinfo=timezone.utc)  # 17:00 ET


def test_heading_is_the_digest_time_in_et_in_bold(tmp_path):
    text = compose_digest(LocalStorage(tmp_path), now=NOW)

    assert text.splitlines()[0] == "<b>AGS Digest · 2026-06-10 17:00 ET</b>"


def test_aws_cost_is_not_reported(tmp_path):
    text = compose_digest(LocalStorage(tmp_path), now=NOW)

    assert "AWS" not in text.split("\n", 1)[1]


COMMODITIES = ("corn", "soybeans", "wheat", "sugar", "coffee", "cotton", "cocoa")
CYCLE = "cycle_2026-06-10T20-00-00Z"
PREVIOUS = "cycle_2026-06-10T08-00-00Z"


def _cycle(storage, book, cycle_id, *, equity=10_000.0, used_margin=0.0, positions=(), marks=None, calls=None):
    """A portfolio cycle record with the fields the digest reads; `positions` are (commodity, lots)."""
    as_of = datetime.strptime(cycle_id.removeprefix("cycle_"), "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
    lots = {c: 0.0 for c in COMMODITIES} | dict(positions)
    # 1 lot = 100 units here = cTrader volume 10_000 (volume is 1/100 of a unit).
    held = [{"symbol_name": c, "trade_side": "BUY" if n > 0 else "SELL", "volume": int(abs(n) * 10_000)}
            for c, n in positions if n]
    storage.put(f"portfolio/{book}/cycles/{cycle_id}.json", json.dumps({
        "cycle_id": cycle_id, "as_of": as_of.isoformat(), "equity": equity, "opening_equity": 10_000.0, "used_margin": used_margin,
        "free_margin": equity - used_margin, "closing_lots": lots,
        "closing_state": {"positions": held, "marks": marks or {}},
        "calls": calls or {c: {"call": "neutral", "conviction": 1} for c in COMMODITIES},
    }).encode())


def _response(agent, timestamp, cost):
    return json.dumps({"agent": agent, "event_type": "response", "timestamp": timestamp,
                       "payload": {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost_usd": cost}}})


def _log(storage, key, *lines):
    storage.put(key, ("\n".join(lines) + "\n").encode())


def _section(text, heading):
    """The lines of one H2 section, heading included, up to the next heading; blank lines kept."""
    lines = text.splitlines()
    start = lines.index(f"<b><u>{heading}</u></b>")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("<b>")), len(lines))
    while lines[end - 1] == "":
        end -= 1
    return lines[start:end]


def _block(text, heading, first):
    """The lines of the block starting with `first` inside an H2 section, up to the blank line after it."""
    lines = _section(text, heading)
    start = lines.index(first)
    end = lines.index("", start) if "" in lines[start:] else len(lines)
    return lines[start:end]


def test_spend_is_this_cycle_by_agent_and_commodity_plus_the_month_to_date_total(tmp_path):
    storage = LocalStorage(tmp_path)
    _cycle(storage, "manager", PREVIOUS)
    _cycle(storage, "manager", CYCLE)
    _log(storage, "logs/corn_2026-06-10T20-00-00Z.jsonl",
         _response("technical", "2026-06-10T20:00:05+00:00", 0.5),
         _response("coordinator", "2026-06-10T20:01:00+00:00", 1.25))
    _log(storage, "logs/cocoa_2026-06-10T20-00-00Z.jsonl", _response("coordinator", "2026-06-10T20:01:00+00:00", 0.75))
    _log(storage, "portfolio/logs/cycle_2026-06-10T20-00-00Z.jsonl", _response("manager", "2026-06-10T20:05:00+00:00", 0.25))
    # A retried attempt of this cycle's corn run spent money too.
    _log(storage, "attempts/logs/corn_2026-06-10T20-00-00Z.attempt1.jsonl",
         _response("technical", "2026-06-10T19:55:00+00:00", 0.5))
    # The previous cycle counts toward the month only.
    _log(storage, "logs/corn_2026-06-10T08-00-00Z.jsonl", _response("technical", "2026-06-10T08:00:05+00:00", 2.0))

    assert _section(compose_digest(storage, now=NOW), "Spend") == [
        "<b><u>Spend</u></b>",
        "This cycle: $3.25",
        "Month to date: $5.25",
        "By agent:",
        "- coordinator $2.00",
        "- technical $1.00",
        "- manager $0.25",
        "By commodity:",
        "- corn $2.25",
        "- cocoa $0.75",
        "- portfolio $0.25",
    ]


def _books(storage, calls=None):
    """Manager and shadow at the previous and the latest cycle (1 lot = 100 units)."""
    _cycle(storage, "manager", PREVIOUS, equity=10_164.30, positions=[("corn", 1.0), ("cocoa", -1.0)],
           marks={"corn": 43.00, "cocoa": 61.20})
    _cycle(storage, "shadow", PREVIOUS, equity=10_064.80, positions=[("corn", 1.0), ("sugar", 1.0)],
           marks={"corn": 42.70, "sugar": 52.80})
    _cycle(storage, "manager", CYCLE, equity=10_212.40, used_margin=1_845.0,
           positions=[("corn", 2.0), ("coffee", -0.5)], marks={"corn": 43.10, "coffee": 196.60}, calls=calls)
    _cycle(storage, "shadow", CYCLE, equity=10_077.20, used_margin=1_230.0,
           positions=[("corn", 1.0), ("sugar", 1.5)], marks={"corn": 43.10, "sugar": 53.27})


def test_each_book_shows_equity_and_its_change_positions_value_and_margin(tmp_path):
    storage = LocalStorage(tmp_path)
    _books(storage)

    text = compose_digest(storage, now=NOW)

    assert _block(text, "Book", "Manager:")[:4] == [
        "Manager:",
        "- Equity $10,212.40 (+$48.10)",
        "- Positions value $18,450.00",  # 2 × 100 × 43.10 + 0.5 × 100 × 196.60: a short counts gross
        "- Margin $1,845.00 (18.1%)",
    ]
    assert _block(text, "Book", "Shadow:")[:4] == [
        "Shadow:",
        "- Equity $10,077.20 (+$12.40)",
        "- Positions value $12,300.50",
        "- Margin $1,230.00 (12.2%)",
    ]
    assert "cycle_" not in "\n".join(_section(text, "Book"))


def test_each_book_lists_every_commodity_with_lots_and_value_and_their_change(tmp_path):
    storage = LocalStorage(tmp_path)
    _books(storage)

    text = compose_digest(storage, now=NOW)

    assert _block(text, "Book", "Manager:")[4:] == [
        "By commodity:",
        "- Corn +2.0 (+1.0) | $8,620.00 (+$4,320.00)",
        "- Soybeans 0 (0) | $0.00 ($0.00)",
        "- Wheat 0 (0) | $0.00 ($0.00)",
        "- Sugar 0 (0) | $0.00 ($0.00)",
        "- Coffee −0.5 (−0.5) | $9,830.00 (+$9,830.00)",
        "- Cotton 0 (0) | $0.00 ($0.00)",
        "- Cocoa 0 (+1.0) | $0.00 (−$6,120.00)",  # a short of 1.0 closed
    ]
    assert _block(text, "Book", "Shadow:")[4:] == [
        "By commodity:",
        "- Corn +1.0 (0) | $4,310.00 (+$40.00)",  # same lots: the change is the price move
        "- Soybeans 0 (0) | $0.00 ($0.00)",
        "- Wheat 0 (0) | $0.00 ($0.00)",
        "- Sugar +1.5 (+0.5) | $7,990.50 (+$2,710.50)",
        "- Coffee 0 (0) | $0.00 ($0.00)",
        "- Cotton 0 (0) | $0.00 ($0.00)",
        "- Cocoa 0 (0) | $0.00 ($0.00)",
    ]


def test_book_ends_with_the_manager_minus_shadow_gap_and_its_change(tmp_path):
    storage = LocalStorage(tmp_path)
    _books(storage)

    # Now 10,212.40 − 10,077.20; previously 10,164.30 − 10,064.80 = 99.50.
    assert _section(compose_digest(storage, now=NOW), "Book")[-1] == "Manager − shadow since 2026-06-10: +$135.20 (+$35.70)"


def _calls(**overrides):
    return {c: {"call": "neutral", "conviction": 1} for c in COMMODITIES} | overrides


def test_latest_calls_show_each_commodity_next_to_its_previous_call(tmp_path):
    storage = LocalStorage(tmp_path)
    _cycle(storage, "manager", PREVIOUS, calls=_calls(
        corn={"call": "neutral", "conviction": 2}, wheat={"call": "bearish", "conviction": 2}, cocoa=None))
    _cycle(storage, "manager", CYCLE, calls=_calls(
        corn={"call": "bullish", "conviction": 4}, wheat=None, cocoa={"call": "bearish", "conviction": 3}))

    assert _section(compose_digest(storage, now=NOW), "Latest calls") == [
        "<b><u>Latest calls</u></b>",
        "- Corn: bullish 4 (was neutral 2)",
        "- Soybeans: neutral 1 (was neutral 1)",
        "- Wheat: failed (was bearish 2)",
        "- Sugar: neutral 1 (was neutral 1)",
        "- Coffee: neutral 1 (was neutral 1)",
        "- Cotton: neutral 1 (was neutral 1)",
        "- Cocoa: bearish 3 (was failed)",
    ]


def test_the_first_cycle_shows_no_changes_since_there_is_nothing_to_compare_with(tmp_path):
    storage = LocalStorage(tmp_path)
    _cycle(storage, "manager", CYCLE, equity=10_000.0, positions=[("corn", 1.0)], marks={"corn": 43.10})
    _cycle(storage, "shadow", CYCLE, equity=10_000.0)

    text = compose_digest(storage, now=NOW)

    assert _block(text, "Book", "Manager:")[1] == "- Equity $10,000.00"
    assert _block(text, "Book", "Manager:")[5] == "- Corn +1.0 | $4,310.00"
    assert _section(text, "Book")[-1] == "Manager − shadow since 2026-06-10: $0.00"
    assert _section(text, "Latest calls")[1] == "- Corn: neutral 1"


def test_with_no_cycles_yet_only_month_to_date_spend_is_shown(tmp_path):
    text = compose_digest(LocalStorage(tmp_path), now=NOW)

    assert _section(text, "Spend") == ["<b><u>Spend</u></b>", "Month to date: $0.00"]
    assert _section(text, "Book") == ["<b><u>Book</u></b>", "No scheduled cycle yet"]
    assert "Latest calls" not in text


GOLDEN = """<b>AGS Digest · 2026-06-10 17:00 ET</b>

<b><u>Spend</u></b>
This cycle: $2.00
Month to date: $2.00
By agent:
- coordinator $1.25
- technical $0.50
- manager $0.25
By commodity:
- corn $1.75
- portfolio $0.25

<b><u>Book</u></b>
Manager:
- Equity $10,212.40 (+$48.10)
- Positions value $18,450.00
- Margin $1,845.00 (18.1%)
By commodity:
- Corn +2.0 (+1.0) | $8,620.00 (+$4,320.00)
- Soybeans 0 (0) | $0.00 ($0.00)
- Wheat 0 (0) | $0.00 ($0.00)
- Sugar 0 (0) | $0.00 ($0.00)
- Coffee −0.5 (−0.5) | $9,830.00 (+$9,830.00)
- Cotton 0 (0) | $0.00 ($0.00)
- Cocoa 0 (+1.0) | $0.00 (−$6,120.00)

Shadow:
- Equity $10,077.20 (+$12.40)
- Positions value $12,300.50
- Margin $1,230.00 (12.2%)
By commodity:
- Corn +1.0 (0) | $4,310.00 (+$40.00)
- Soybeans 0 (0) | $0.00 ($0.00)
- Wheat 0 (0) | $0.00 ($0.00)
- Sugar +1.5 (+0.5) | $7,990.50 (+$2,710.50)
- Coffee 0 (0) | $0.00 ($0.00)
- Cotton 0 (0) | $0.00 ($0.00)
- Cocoa 0 (0) | $0.00 ($0.00)

Manager − shadow since 2026-06-10: +$135.20 (+$35.70)

<b><u>Latest calls</u></b>
- Corn: bullish 4 (was neutral 1)
- Soybeans: bearish 3 (was neutral 1)
- Wheat: neutral 1 (was neutral 1)
- Sugar: neutral 1 (was neutral 1)
- Coffee: bearish 4 (was neutral 1)
- Cotton: neutral 1 (was neutral 1)
- Cocoa: failed (was neutral 1)"""


def test_digest_matches_the_golden_text(tmp_path):
    storage = LocalStorage(tmp_path)
    _books(storage, calls=_calls(corn={"call": "bullish", "conviction": 4}, soybeans={"call": "bearish", "conviction": 3},
                                 coffee={"call": "bearish", "conviction": 4}, cocoa=None))
    _log(storage, "logs/corn_2026-06-10T20-00-00Z.jsonl",
         _response("technical", "2026-06-10T20:00:05+00:00", 0.5),
         _response("coordinator", "2026-06-10T20:01:00+00:00", 1.25))
    _log(storage, "portfolio/logs/cycle_2026-06-10T20-00-00Z.jsonl", _response("manager", "2026-06-10T20:05:00+00:00", 0.25))

    assert compose_digest(storage, now=NOW) == GOLDEN


def test_the_digest_reads_the_demo_managers_store_and_measures_the_gap_since_its_first_cycle(tmp_path):
    storage = LocalStorage(tmp_path)
    # The shadow has been running since 10k and is at 10_500 when the demo manager starts at its own 10k.
    _cycle(storage, "shadow", PREVIOUS, equity=10_500.0)
    _cycle(storage, "demo/manager", PREVIOUS, equity=10_020.0)
    _cycle(storage, "shadow", CYCLE, equity=10_560.0)
    _cycle(storage, "demo/manager", CYCLE, equity=10_030.0)
    # shadow's opening equity at the manager's first cycle is 10_500, not 10_000
    for key in storage.list("portfolio/shadow/cycles/"):
        record = json.loads(storage.get(key))
        record["opening_equity"] = 10_500.0
        storage.put(key, json.dumps(record).encode())

    text = compose_digest(storage, now=NOW, manager_store="demo/manager")

    assert _section(text, "Book")[-1] == "Manager − shadow since 2026-06-10: −$30.00 (−$50.00)"
