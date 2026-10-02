"""CLI: trigger one weekly pipeline run for a commodity.

Fans out the four analysts, then the coordinator synthesizes them into one
call. Prints the final call and the analyst outputs as readable text.
"""

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from ags.config import Config
from ags.llm.deepseek_client import DeepSeekChatClient
from ags.pipeline import run_pipeline
from ags.ui.format import to_markdown

_ROOT = Path(__file__).parent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the analyst pipeline for one commodity.")
    parser.add_argument("--commodity", required=True, help="e.g. corn")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, help="ISO date; defaults to today (UTC)")
    args = parser.parse_args(argv)

    config = Config.from_env()
    now = datetime.now(timezone.utc)

    result = run_pipeline(
        commodity=args.commodity,
        as_of=args.as_of or now.date(),
        trigger_timestamp=now,
        chat_client=DeepSeekChatClient(config),
        model=config.deepseek_model,
        log_dir=_ROOT / "logs",
        audit_dir=_ROOT / "audits",
        data_dir=_ROOT / "data",
    )

    print(f"run_id: {result['run_id']}")
    print("\n== call ==")
    print(to_markdown(result["call"]))
    for agent, output in result["outputs"].items():
        print(f"\n== {agent} ==")
        print(to_markdown(output))


if __name__ == "__main__":
    main()
