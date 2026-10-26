import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime

from ags.replay.variants import COORDINATORS, MANAGERS
from ags.storage import Storage

_REPLAY_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Agent:
    variant: str
    model: str


@dataclass(frozen=True)
class ReplaySpec:
    """What a replay swaps in, and from which official cycle on."""

    replay_id: str
    fork_at: datetime
    coordinator: Agent
    manager: Agent

    @classmethod
    def from_mapping(cls, m: dict) -> "ReplaySpec":
        if not _REPLAY_ID.match(m["replay_id"]):
            raise ValueError(f"replay_id must be lowercase letters, digits and dashes: {m['replay_id']!r}")
        fork_at = datetime.fromisoformat(m["fork_at"])
        if fork_at.tzinfo is None:
            raise ValueError("fork_at must carry a timezone")
        agents = {}
        for role, registry in (("coordinator", COORDINATORS), ("manager", MANAGERS)):
            agent = Agent(variant=m[role]["variant"], model=m[role]["model"])
            if agent.variant not in registry:
                raise ValueError(f"unknown {role} variant {agent.variant!r}; known: {sorted(registry)}")
            if not agent.model:
                raise ValueError(f"{role} model is required")
            agents[role] = agent
        return cls(replay_id=m["replay_id"], fork_at=fork_at, **agents)

    def to_mapping(self) -> dict:
        return {**asdict(self), "fork_at": self.fork_at.isoformat()}


def _key(replay_id: str) -> str:
    return f"replays/{replay_id}/spec.json"


def save_spec(store: Storage, spec: ReplaySpec) -> None:
    """Record the spec once. Re-saving the same one is a no-op (a resume); a different one is refused."""
    data = json.dumps(spec.to_mapping(), indent=2).encode()
    if store.put_if_absent(_key(spec.replay_id), data):
        return
    if load_spec(store, spec.replay_id) != spec:
        raise ValueError(f"replay {spec.replay_id} already exists with a different spec; pick a new replay_id")


def load_spec(store: Storage, replay_id: str) -> ReplaySpec:
    data = store.get(_key(replay_id))
    if data is None:
        raise KeyError(f"no replay {replay_id}")
    return ReplaySpec.from_mapping(json.loads(data))
