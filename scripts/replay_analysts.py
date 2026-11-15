"""CLI: rerun one official commodity run on exactly what it saw, as the live pipeline, the aligned one, or both.

Takes the news articles, the intraday quote and the positioning the cycle was given from its log, reads everything
else from the point-in-time store at the cycle's own `as_of`, and refuses every live fetch (`ags/frozen.py`). So two
variants, or two attempts of one, differ only in what the models do with the same inputs. Reads the local mirror
(`logs/`, `data/`, `audits/`) and writes only under --out; never the official record.

Usage: uv run python scripts/replay_analysts.py --run-id corn_2026-11-13T11-00-00Z [--variant live|aligned|both]
                                                [--out replays/analysts/<run-id>]
"""

import argparse
import json
from pathlib import Path

from ags.agents.auditor import _triggered_at
from ags.config import Config
from ags.frozen import LayeredStorage, frozen_from_events
from ags.llm.client import LLMClient
from ags.pipeline import run_pipeline
from ags.storage import LocalStorage

_ROOT = Path(__file__).resolve().parents[1]  # repo root
_VARIANTS = {"live": False, "aligned": True}


def _official_events(run_id: str) -> list[dict]:
    path = _ROOT / "logs" / f"{run_id}.jsonl"
    if not path.exists():
        raise SystemExit(f"no official log for {run_id} in {path.parent}; sync the mirror first")
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _summary(call: dict) -> str:
    support, dissent = ", ".join(call.get("supporting_analysts") or []) or "-", ", ".join(call.get("dissenting_analysts") or []) or "-"
    return f"{call.get('call')} (conviction {call.get('conviction')}); supports: {support}; dissents: {dissent}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Rerun an official run on its frozen inputs.")
    parser.add_argument("--run-id", required=True, help="an official run in the local mirror, e.g. corn_2026-11-13T11-00-00Z")
    parser.add_argument("--variant", choices=[*_VARIANTS, "both"], default="both")
    parser.add_argument("--out", type=Path, default=None, help="default: replays/analysts/<run-id>")
    args = parser.parse_args(argv)

    run_id = args.run_id
    commodity = run_id.rsplit("_", 1)[0]
    scheduled = _triggered_at(run_id)
    if scheduled is None:
        raise SystemExit(f"{run_id!r} is not a run id")
    out = args.out or _ROOT / "replays" / "analysts" / run_id
    events = _official_events(run_id)
    frozen = frozen_from_events(run_id, events)
    official = [e["payload"] for e in events if e["agent"] == "coordinator" and e["event_type"] == "output"][-1]
    print(f"frozen from {run_id}: {len(frozen.news_results)} articles, "
          f"quote {'yes' if frozen.reference_quote else 'no'}, positioning {'yes' if frozen.positioning else 'no'}")

    config = Config.from_env()
    results = {}
    for name in (_VARIANTS if args.variant == "both" else [args.variant]):
        client = LLMClient()
        # The earlier official logs feed the track record and news carry-forward; the official log of this very
        # run is hidden, and the rerun's own log lands under out/<variant>/logs.
        log_dir = LayeredStorage(
            LocalStorage(_ROOT / "logs"), LocalStorage(out / name / "logs"), hide={f"{run_id}.jsonl"}
        )
        results[name] = run_pipeline(
            commodity=commodity, as_of=scheduled, trigger_timestamp=scheduled, chat_client=client,
            model=config.deepseek_model, log_dir=log_dir, data_dir=_ROOT / "data", audit_dir=_ROOT / "audits",
            aligned=_VARIANTS[name], frozen=frozen,
        )
        client.flush()
        (out / name).mkdir(parents=True, exist_ok=True)
        (out / name / "result.json").write_text(json.dumps(results[name], indent=2, default=str))

    print(f"\nofficial : {_summary(official)}")
    for name, result in results.items():
        print(f"{name:9}: {_summary(result['call'])}")
        for analyst, output in result["outputs"].items():
            bias = f"bias {output['bias']:+d} (code {output['code_bias']:+d}), confidence {output['confidence']}, {output['horizon']}" if "bias" in output else "no envelope"
            print(f"           {analyst:14} degraded={output.get('degraded')}  {bias}")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
