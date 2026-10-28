"""Push secrets from .env to SSM SecureStrings under /ags/<env>/<kebab-name>.

Terraform never holds secret values (they'd land in state). Prints names only, never values.

    uv run python scripts/put_secrets.py --env live [--dry-run]

Add a key: list it in SECRETS (or OPTIONAL) and in .env.example. The Lambda sees it as the same
UPPER_SNAKE env var (config.load_env).

The cTrader client id/secret and tokens are not pushed from here: they're the account's shared
pair under /platform/shared/ctrader-*, seeded once and rotated by the platform (platform
docs/conventions.md § Secrets). Only CTRADER_ACCOUNT_ID is per project.
"""

import argparse
import os
import sys
from pathlib import Path

import boto3

from ags.config import load_env, parameter_name

SECRETS: tuple[str, ...] = (
    "DEEPSEEK_API_KEY",
    "CTRADER_ACCOUNT_ID",
    "TAVILY_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)
# Non-secret overrides; pushed only when set in .env (otherwise the code's default applies).
OPTIONAL: tuple[str, ...] = ("CTRADER_DEMO", "CTRADER_TRADING_ACCOUNT_ID", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "MAX_TOOL_CALLS", "CHART_LOOKBACK_CANDLES")


def main() -> None:
    load_env(Path(__file__).parents[1] / ".env")  # also defaults AWS_REGION to eu-west-1
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["live"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    keys = [*SECRETS, *(k for k in OPTIONAL if os.getenv(k))]
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")

    region = os.environ["AWS_REGION"]
    ssm = boto3.client("ssm", region_name=region)
    for key in keys:
        name = f"/ags/{args.env}/{parameter_name(key)}"
        print(f"{'would put' if args.dry_run else 'put'} {name} ({region})")
        if not args.dry_run:
            ssm.put_parameter(Name=name, Value=os.environ[key], Type="SecureString", Overwrite=True)


if __name__ == "__main__":
    main()
