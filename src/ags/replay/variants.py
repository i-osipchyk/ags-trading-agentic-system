"""Named coordinator and manager variants a replay can swap in.

A variant is a prompt and output format, kept in code so a replay's spec names it and git
versions it. Each one takes the same arguments as the live agent it replaces and must return
what the next stage reads: a coordinator call with `call`/`conviction`/`thesis`/`key_drivers`,
and a manager result whose `proposal` has the guardrails' `legs` shape. "default" is the live one.
"""

from ags.agents.coordinator import run_coordinator
from ags.portfolio.manager import run_portfolio_manager

COORDINATORS = {"default": run_coordinator}
MANAGERS = {"default": run_portfolio_manager}
