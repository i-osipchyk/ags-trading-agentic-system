"""CLI: score the logged coordinator calls against the baselines (read-only, from the local mirror).

Run `scripts/sync_local.sh logs data` first. Prints the 1-week and 1-day tables, raw and market-neutral.
"""

import argparse
from datetime import date
from pathlib import Path

from ags import scoring

_ROOT = Path(__file__).resolve().parents[1]  # repo root


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score logged calls against mechanical baselines.")
    parser.add_argument(
        "--since", type=date.fromisoformat, default=scoring.REVIEW_START,
        help=f"only calls from this date (the clean start date; default {scoring.REVIEW_START})",
    )
    args = parser.parse_args(argv)

    through = scoring.price_data_through(_ROOT / "data")
    print(f"Daily closes in the mirror run through {through}; calls need an exit close on or before it.")
    for label, days in scoring.HORIZONS.items():
        frame = scoring.build_rows(_ROOT / "logs", _ROOT / "data", horizon_days=days, since=args.since)
        print(f"\n=== {label} horizon: {len(frame)} scored calls ===")
        if frame.empty:
            continue
        for neutral in (False, True):
            print(f"\n{'market-neutral' if neutral else 'raw'}:")
            print(scoring.summarize(frame, neutral=neutral).round(4).to_string(index=False))
        if days == scoring.VERDICT_HORIZON:
            verdict = scoring.evaluate_pass_bar(frame)
            print(f"\nverdict vs pass bar: {verdict['verdict']} ({'; '.join(verdict['reasons']) or 'bar met'})")


if __name__ == "__main__":
    main()
