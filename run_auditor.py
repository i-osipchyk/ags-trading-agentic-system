"""CLI: grade every due, not-yet-graded audit checkpoint (1/2/4/8 weeks).

Scans all logged runs, grades each outstanding (run_id, checkpoint) pair, and
writes audits/{run_id}/{weeks}w.json. Safe to re-run: a missed week self-heals.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from ags.agents.auditor import run_auditor
from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient

_ROOT = Path(__file__).parent


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description="Grade all due audit checkpoints.").parse_args(argv)

    config = Config.from_env()
    result = run_auditor(
        DeepSeekChatClient(config),
        log_dir=_ROOT / "logs",
        audit_dir=_ROOT / "audits",
        data_dir=_ROOT / "data",
        model=config.deepseek_model,
        now=datetime.now(timezone.utc),
    )

    for run_id, weeks in result["graded"]:
        print(f"graded {run_id} {weeks}w")
    for (run_id, weeks), error in result["failed"]:
        print(f"FAILED {run_id} {weeks}w: {error}")
    print(f"{len(result['graded'])} graded, {len(result['failed'])} failed")


if __name__ == "__main__":
    main()
