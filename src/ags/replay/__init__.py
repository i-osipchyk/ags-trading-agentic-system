"""Counterfactual replay: re-run the coordinator and manager with another variant over logged analyst outputs.

See docs/ARCHITECTURE.md § Counterfactual replay.
"""

from ags.replay.coordinator import replay_coordinator
from ags.replay.differences import describe_differences
from ags.replay.plan import pending_cycles
from ags.replay.portfolio import replay_portfolio
from ags.replay.report import report
from ags.replay.runner import run_replay
from ags.replay.spec import Agent, ReplaySpec, load_spec, save_spec

__all__ = ["Agent", "ReplaySpec", "describe_differences", "load_spec", "pending_cycles", "replay_coordinator", "replay_portfolio", "report", "run_replay", "save_spec"]
