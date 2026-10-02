"""CLI: grade every due, not-yet-graded audit checkpoint (1/2/4/8 weeks).

Scans all logged runs, grades each outstanding (run_id, checkpoint) pair, and
writes audits/{run_id}/{weeks}w.json. Safe to re-run: a missed week self-heals.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from ags.agents.auditor import CHECKPOINT_WEEKS, run_auditor
from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient

_ROOT = Path(__file__).parent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Grade all due audit checkpoints, or just one.")
    parser.add_argument("--run-id", help="grade only this run's checkpoint (requires --weeks)")
    parser.add_argument("--weeks", type=int, choices=CHECKPOINT_WEEKS, help="the checkpoint horizon (requires --run-id)")
    args = parser.parse_args(argv)
    if (args.run_id is None) != (args.weeks is None):
        parser.error("--run-id and --weeks must be given together")

    config = Config.from_env()
    result = run_auditor(
        DeepSeekChatClient(config),
        log_dir=_ROOT / "logs",
        audit_dir=_ROOT / "audits",
        data_dir=_ROOT / "data",
        model=config.deepseek_model,
        now=datetime.now(timezone.utc),
        only=(args.run_id, args.weeks) if args.run_id else None,
    )

    for run_id, weeks in result["graded"]:
        print(f"graded {run_id} {weeks}w")
    for (run_id, weeks), error in result["failed"]:
        print(f"FAILED {run_id} {weeks}w: {error}")
    print(f"{len(result['graded'])} graded, {len(result['failed'])} failed")


if __name__ == "__main__":
    main()
