import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from ags.portfolio.stage import COMMODITIES, PortfolioConfig
from ags.replay.coordinator import replay_coordinator
from ags.replay.differences import describe_differences
from ags.replay.plan import pending_cycles
from ags.replay.portfolio import replay_portfolio
from ags.replay.report import LINES, report
from ags.replay.spec import ReplaySpec, save_spec
from ags.storage import Storage


def run_replay(
    store: Storage,
    spec: ReplaySpec,
    chat_client,
    config: PortfolioConfig,
    *,
    local_dir: Path,
    now: datetime | None = None,
    code_version: str | None = None,
) -> list[dict]:
    """Run (or resume) a replay on this machine against `store`, then keep a local copy of it.

    Cycles run oldest first, each one's seven coordinators in parallel and then its portfolio
    step. A failed model call stops the run; running it again resumes where it stopped. The
    local copy (`replays/{id}/` from the store, plus report.csv and differences.md, which names
    `code_version`) is written either way.
    """
    save_spec(store, spec)
    try:
        for cycle_id in pending_cycles(store, spec):
            stamp = cycle_id.removeprefix("cycle_")
            with ThreadPoolExecutor(max_workers=len(COMMODITIES)) as pool:
                futures = [
                    pool.submit(replay_coordinator, store, spec, f"{c}_{stamp}", chat_client) for c in COMMODITIES
                ]
            for future in futures:
                future.result()  # re-raises the first failure, after every coordinator has finished
            replay_portfolio(store, spec, cycle_id, chat_client, config, now=now)
    finally:
        _keep_locally(store, spec.replay_id, Path(local_dir), code_version)
    return report(store, spec.replay_id)


def _keep_locally(store: Storage, replay_id: str, local_dir: Path, code_version: str | None) -> None:
    prefix = f"replays/{replay_id}/"
    for key in store.list(prefix):
        path = local_dir / key.removeprefix(prefix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(store.get(key))
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "differences.md").write_text(describe_differences(store, replay_id, code_version=code_version))
    try:
        rows = report(store, replay_id)
    except ValueError:  # nothing replayed yet: no fork cycle to measure from
        return
    with open(local_dir / "report.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["cycle_id", *LINES, "calls_changed"])
        for row in rows:
            writer.writerow([row["cycle_id"], *(row["pnl"][line] for line in LINES), row["calls_changed"]])
