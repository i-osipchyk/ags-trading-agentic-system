import json
from datetime import datetime, timezone

from ags.logging.run_log import RunLog
from ags.pipeline import LoggingChatClient, _price_at_call
from ags.replay.guards import NoMarket, ReadOnly
from ags.replay.spec import ReplaySpec
from ags.replay.variants import COORDINATORS
from ags.storage import LocalStorage, Prefixed, Storage
from ags.tools.track_record import read_track_record

ANALYSTS = ("technical", "news", "weather", "supply_demand")


def run_time(run_id: str) -> datetime:
    return datetime.strptime(run_id.rsplit("_", 1)[1], "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)


def _outputs(log_store: Storage, run_id: str) -> dict:
    data = log_store.get(f"{run_id}.jsonl")
    if data is None:
        return {}
    events = (json.loads(line) for line in data.decode().splitlines())
    return {e["agent"]: e["payload"] for e in events if e["event_type"] == "output"}


class _ForkedLogs:
    """The coordinator's own history as the replay sees it: official runs before the fork, replayed ones after."""

    def __init__(self, official: Storage, replayed: Storage, fork_at: datetime) -> None:
        self._official, self._replayed, self._fork_at = official, replayed, fork_at

    def _before_fork(self, key: str) -> bool:
        return "/" not in key and key.endswith(".jsonl") and run_time(key.removesuffix(".jsonl")) < self._fork_at

    def list(self, prefix: str) -> list[str]:
        official = [k for k in self._official.list(prefix) if self._before_fork(k)]
        return sorted({*official, *self._replayed.list(prefix)})

    def get(self, key: str) -> bytes | None:
        replayed = self._replayed.get(key)
        if replayed is not None or not self._before_fork(key):
            return replayed
        return self._official.get(key)


class _ForkedAudits:
    """Official audits of pre-fork runs only: a post-fork audit grades the official call, not the replayed one."""

    def __init__(self, official: Storage, fork_at: datetime) -> None:
        self._official, self._fork_at = official, fork_at

    def list(self, prefix: str) -> list[str]:
        return self._official.list(prefix) if run_time(prefix.rstrip("/")) < self._fork_at else []

    def get(self, key: str) -> bytes | None:
        return self._official.get(key)


def replay_coordinator(store: Storage, spec: ReplaySpec, run_id: str, chat_client) -> dict:
    """Re-run one commodity's coordinator on the official run's analyst outputs; returns the replayed call.

    Writes only `replays/{id}/logs/{run_id}.jsonl`: the analyst outputs it was given, then the
    coordinator's prompts, replies and output, in the official log format.
    """
    commodity, as_of = run_id.rsplit("_", 1)[0], run_time(run_id)
    official_logs = ReadOnly(Prefixed(store, "logs"))
    replay_logs = Prefixed(store, f"replays/{spec.replay_id}/logs")
    done = _outputs(replay_logs, run_id).get("coordinator")
    if done is not None:
        # A retried or resumed execution: the call is already made and logged, so don't pay for it twice.
        return done
    original = _outputs(official_logs, run_id)
    analyst_outputs = {a: original.get(a) or {"degraded": True, "error": "missing from the official log"} for a in ANALYSTS}
    price_at_call = (original.get("coordinator") or {}).get("price_at_call") or _price_at_call(
        ReadOnly(Prefixed(store, "data")), commodity, as_of, NoMarket()
    )

    history = _ForkedLogs(official_logs, replay_logs, spec.fork_at)
    audits = _ForkedAudits(ReadOnly(Prefixed(store, "audits")), spec.fork_at)

    def track_record(lookback_weeks: int) -> list[dict]:
        return read_track_record(history, audits, commodity=commodity, lookback_weeks=lookback_weeks, as_of=as_of)

    with RunLog(replay_logs, run_id, buffered=not isinstance(store, LocalStorage)) as log:
        for agent in ANALYSTS:
            log.append(agent=agent, event_type="output", payload=analyst_outputs[agent])
        client = LoggingChatClient(chat_client, log, "coordinator", commodity=commodity, run_id=run_id, replay_id=spec.replay_id)
        try:
            call = COORDINATORS[spec.coordinator.variant](
                client, commodity=commodity, as_of=as_of, model=spec.coordinator.model,
                analyst_outputs=analyst_outputs, price_at_call=price_at_call, track_record=track_record,
            )
        except Exception as exc:
            # Unlike a live run, a failed model call (outage, billing) is not recorded as a degraded
            # call: no output is logged, the run stops, and a resumed replay asks again. A reply
            # that is merely malformed still comes back from the variant as a degraded call.
            log.append(agent="coordinator", event_type="error", payload={"error": f"{type(exc).__name__}: {exc}"})
            raise
        log.append(agent="coordinator", event_type="output", payload=call)
    return call
