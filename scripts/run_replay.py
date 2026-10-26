"""CLI: run (or resume) a counterfactual replay on this machine.

Reads the official record (analyst outputs, books, market data) from the data bucket, re-runs
the coordinator and manager variants named in the spec — from this working tree, so a new
prompt needs no deploy — and writes the replay to replays/{replay_id}/ in the bucket. A local
copy goes to replays/{replay_id}/ here, with report.csv and differences.md (how this replay
differs from the deployed coordinator and manager). Re-running the same spec resumes.

Spec (JSON): {"replay_id": "coord-v2", "fork_at": "2026-10-20T08:00:00+00:00",
              "coordinator": {"variant": "default", "model": "deepseek-chat"},
              "manager": {"variant": "default", "model": "deepseek-chat"}}

Usage: uv run python scripts/run_replay.py --spec replay.json [--env live]
"""

import argparse
import json
import subprocess
from pathlib import Path

from ags.config import load_env
from ags.portfolio.stage import PortfolioConfig
from ags.replay import ReplaySpec, run_replay
from ags.replay.report import LINES
from ags.storage import S3Storage

ROOT = Path(__file__).parent.parent
PROJECT = "ags"


def _code_version() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()

    commit = git("rev-parse", "--short", "HEAD")
    return f"{commit} (uncommitted changes)" if git("status", "--porcelain") else commit


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run or resume a counterfactual replay locally.")
    parser.add_argument("--spec", required=True, help="path to the replay spec JSON")
    parser.add_argument("--env", default="live")
    args = parser.parse_args(argv)
    load_env(ROOT / ".env")  # DEEPSEEK_API_KEY; also defaults AWS_REGION to eu-west-1

    import boto3

    from ags.llm.client import LLMClient

    with open(args.spec) as f:
        spec = ReplaySpec.from_mapping(json.load(f))
    account = boto3.client("sts").get_caller_identity()["Account"]
    store = S3Storage(f"{PROJECT}-{args.env}-data-{account}")
    # The deployed portfolio Lambda's limits, so the replayed books run under the live guardrails.
    live_env = boto3.client("lambda").get_function_configuration(FunctionName=f"{PROJECT}-{args.env}-portfolio")
    config = PortfolioConfig.from_mapping(live_env["Environment"]["Variables"])

    chat_client = LLMClient()
    try:
        rows = run_replay(
            store, spec, chat_client, config, local_dir=ROOT / "replays" / spec.replay_id, code_version=_code_version(),
        )
    finally:
        chat_client.flush()

    print(f"{'cycle':<28}" + "".join(f"{line:>18}" for line in LINES) + f"{'calls changed':>15}")
    for row in rows:
        print(f"{row['cycle_id']:<28}" + "".join(f"{row['pnl'][line]:>18.2f}" for line in LINES) + f"{row['calls_changed']:>15}")
    print(f"\nlocal copy: {ROOT / 'replays' / spec.replay_id}")


if __name__ == "__main__":
    main()
