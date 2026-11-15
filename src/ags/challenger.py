"""Run the aligned pipeline over past official runs, into its own folder, so it can be scored against them.

The challenger (ROADMAP.md Step 1) never touches the official record. Each official run is rerun on its frozen
inputs (frozen.py) with `aligned=True`, and the rerun's log is written under `<out>/logs/<run_id>.jsonl`: the same
shape as an official log, so the scoring code reads it unchanged. A commodity's runs go in time order, so a 12:30 run
carries forward the news of that day's challenger 06:00 run, as the live cycle does; commodities go in parallel.
Reruns can be stopped and resumed: a run that already has its log with a call is skipped.
"""

import hashlib
import json
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ags.agents.auditor import _triggered_at
from ags import alignment
from ags.frozen import FrozenInputs, LayeredStorage, frozen_from_events
from ags.storage import LocalStorage
from ags.tools.pit_store import ET

SCHEDULED_SLOTS = ("06:00", "12:30")
# The cycle times since the review window opened (2026-10-13): 04:00 and 16:00 ET until the schedule moved to
# 06:00 and 12:30 on 2026-10-28. Runs at other times were ad hoc.
REVIEW_SLOTS = ("04:00", "06:00", "12:30", "16:00")
# Measured on a pilot day (7 runs): the aligned reruns cost 0.95x the official runs. A longer coordinator prompt is
# offset by frozen news searches returning about what a live one does.
ALIGNED_COST_FACTOR = 1.0
SECONDS_PER_RUN = 40          # measured on the same day: 29 to 52 s a run, mostly waiting on the model


class BatchChanged(Exception):
    """A batch is being resumed with prompts or bias rules other than the ones its earlier runs used."""


def fingerprint() -> dict:
    """What decides a challenger run's output besides the inputs: the aligned prompts and the bias rules."""
    from ags.versioning import prompt_hashes

    rules = hashlib.sha256(Path(alignment.__file__).read_bytes()).hexdigest()[:12]
    return {"prompt_hashes": prompt_hashes(aligned=True), "alignment_rules": rules}


def check_batch(out: Path, current: dict | None = None) -> None:
    """Pin a batch to its prompts and rules on first use, and refuse to resume it under different ones.

    Resuming skips every run that already has its log, so after a prompt or threshold change a batch of the same
    name would quietly mix old and new results. Use a new batch name instead.
    """
    current = fingerprint() if current is None else current
    pinned = Path(out) / "batch.json"
    if not pinned.exists():
        pinned.parent.mkdir(parents=True, exist_ok=True)
        pinned.write_text(json.dumps(current, indent=2))
        return
    earlier = json.loads(pinned.read_text())
    if earlier != current:
        changed = sorted(
            [k for k in earlier["prompt_hashes"] if earlier["prompt_hashes"][k] != current["prompt_hashes"].get(k)]
            + (["alignment_rules"] if earlier["alignment_rules"] != current["alignment_rules"] else [])
        )
        raise BatchChanged(
            f"{Path(out).name} was made with other prompts or bias rules (changed: {', '.join(changed)}); "
            "use a new --batch name"
        )


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _has_call(path: Path) -> bool:
    if not path.exists():
        return False
    outputs = [e["payload"] for e in _events(path) if e["agent"] == "coordinator" and e["event_type"] == "output"]
    return bool(outputs and outputs[-1].get("call"))


def select_runs(
    log_dir: Path, *, since: date, until: date | None = None, slots: tuple[str, ...] = SCHEDULED_SLOTS,
    commodities: tuple[str, ...] | None = None,
) -> list[str]:
    """The official run ids to rerun: from `since` (and up to `until`), at the given ET cycle times, in time order."""
    chosen = []
    for path in Path(log_dir).glob("*.jsonl"):
        triggered = _triggered_at(path.stem)
        if triggered is None:
            continue
        et = triggered.astimezone(ET)
        if et.date() < since or (until is not None and et.date() > until) or et.strftime("%H:%M") not in slots:
            continue
        if commodities is not None and path.stem.rsplit("_", 1)[0] not in commodities:
            continue
        chosen.append((triggered, path.stem))
    return [run_id for _, run_id in sorted(chosen)]


def official_cost(path: Path) -> float:
    """What the official run's model calls cost, from the usage llmkit priced into its log."""
    return sum(
        (e["payload"]["usage"] or {}).get("cost_usd") or 0
        for e in _events(path) if e["event_type"] == "response" and e["payload"].get("usage")
    )


def estimate(log_dir: Path, run_ids: list[str], *, workers: int) -> dict:
    """A rough cost and time for rerunning `run_ids` as the challenger, from what the official runs cost."""
    cost = sum(official_cost(Path(log_dir) / f"{r}.jsonl") for r in run_ids) * ALIGNED_COST_FACTOR
    return {"runs": len(run_ids), "cost_usd": cost, "hours": len(run_ids) * SECONDS_PER_RUN / max(1, workers) / 3600}


RunOne = Callable[[str, FrozenInputs, LayeredStorage], None]


@dataclass
class Outcome:
    done: list[str]
    skipped: list[str]
    failed: dict[str, str]
    stopped: str | None = None


def run_challenger(
    run_ids: list[str], *, official_logs: Path, out: Path, run_one: RunOne, workers: int = 4,
    blocked: Callable[[], dict] = dict, progress: Callable[[str], None] = print,
) -> Outcome:
    """Rerun each official run through `run_one`, one worker per commodity chain, resuming what is already done.

    `run_one(run_id, frozen, log_store)` makes the run and writes its log through `log_store`. A run that raises is
    recorded in `<out>/failures.jsonl` and the batch goes on; a provider that is blocking every call (no credit, a
    rejected key: `blocked()` is non-empty) stops it.
    """
    official_logs, out = Path(official_logs), Path(out)
    upper = LocalStorage(out / "logs")
    chains: dict[str, list[str]] = defaultdict(list)
    for run_id in run_ids:
        chains[run_id.rsplit("_", 1)[0]].append(run_id)
    outcome, lock, stop = Outcome([], [], {}), threading.Lock(), threading.Event()

    def record(kind: str, run_id: str, detail: str = "") -> None:
        with lock:
            if kind == "done":
                outcome.done.append(run_id)
            elif kind == "skipped":
                outcome.skipped.append(run_id)
            else:
                outcome.failed[run_id] = detail
                out.mkdir(parents=True, exist_ok=True)
                with (out / "failures.jsonl").open("a") as f:
                    f.write(json.dumps({"run_id": run_id, "error": detail}) + "\n")
            progress(f"{len(outcome.done) + len(outcome.skipped) + len(outcome.failed)}/{len(run_ids)} {run_id} {kind}"
                     + (f": {detail}" if detail else ""))

    def chain(commodity: str) -> None:
        for run_id in chains[commodity]:
            if stop.is_set():
                return
            if _has_call(out / "logs" / f"{run_id}.jsonl"):
                record("skipped", run_id)
                continue
            try:
                frozen = frozen_from_events(run_id, _events(official_logs / f"{run_id}.jsonl"))
                store = LayeredStorage(LocalStorage(official_logs), upper, hide={f"{run_id}.jsonl"})
                run_one(run_id, frozen, store)
                record("done", run_id)
            except Exception as exc:
                record("failed", run_id, f"{type(exc).__name__}: {exc}")
            if blockers := blocked():
                outcome.stopped = f"a provider is blocking every call: {blockers}"
                stop.set()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(chain, chains))
    return outcome
