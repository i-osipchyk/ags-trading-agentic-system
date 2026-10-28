"""Check the Pepperstone demo-account connection before a scheduled cycle relies on it.

    uv run python scripts/demo_smoke.py --list-accounts      # which ids the token has, demo or live
    uv run python scripts/demo_smoke.py                      # read-only: connect, account, sessions, quote
    uv run python scripts/demo_smoke.py --trade              # also open and close the minimum volume once
    uv run python scripts/demo_smoke.py --symbol corn --trade

Read-only mode checks the token's trading scope and that the account is a demo one (the gateway
refuses otherwise), then prints the balance, open positions, the symbol's session state and a
quote. `--trade` sends one minimum-volume market order and closes it straight away, only while the
symbol's market is open, and prints both fills with the broker's commission, so you can see the
round-trip cost. It needs CTRADER_DEMO=true when the account is a demo account (market data
connects to the demo host too).
"""

import argparse
import sys
from datetime import datetime, timezone

from ags.config import Config
from ags.portfolio.book import is_market_open
from ags.portfolio.demo_gateway import CTraderDemoGateway
from ags.tools.sources.prices import CTRADER_SYMBOL_NAMES, CTraderPricesClient


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the Pepperstone demo-account connection.")
    parser.add_argument("--symbol", default="corn", choices=sorted(CTRADER_SYMBOL_NAMES))
    parser.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    parser.add_argument("--trade", action="store_true", help="open and close the minimum volume once")
    parser.add_argument(
        "--list-accounts", action="store_true",
        help="only list the token's accounts (ctid for CTRADER_ACCOUNT_ID, login number, demo or live)",
    )
    args = parser.parse_args(argv)

    config = Config.from_env()
    if args.list_accounts:
        gateway = CTraderDemoGateway(config, verify=False)
        try:
            for a in gateway.accounts():
                print(f"ctid {a['ctid']}  login {a['login']}  {'LIVE' if a['live'] else 'demo'}")
            print(f"CTRADER_ACCOUNT_ID is {config.ctrader_trading_account_id}")
        finally:
            gateway.close()
        return
    now = datetime.now(timezone.utc)
    market = CTraderPricesClient(config)
    try:
        name = CTRADER_SYMBOL_NAMES[args.symbol]
        spec = market.get_symbol_specs(name)
        quote = market.get_quote(name)
    finally:
        market.close()
    is_open = is_market_open(spec, now)
    print(f"{args.symbol}: symbol_id {spec['symbol_id']}, min volume {spec['min_volume']}, lot {spec['lot_size']}")
    print(f"  quote bid {quote['bid']} ask {quote['ask']} at {quote['timestamp']}")
    print(f"  market {'OPEN' if is_open else 'CLOSED'} now ({now:%Y-%m-%d %H:%M} UTC), schedule tz {spec['schedule_timezone']!r}")

    gateway = CTraderDemoGateway(config)  # refuses a live account or a token without trading scope
    try:
        print(f"demo account {config.ctrader_trading_account_id}: connected, trading scope OK, not live")
        account = gateway.account()
        print(f"  account balance {account['balance']:.2f} (other strategies share it), {len(account['positions'])} open position(s)")
        for p in account["positions"]:
            print(f"    #{p['position_id']} {p['trade_side']} {p['volume']} symbol {p['symbol_id']} @ {p['price']}  label {p['label']!r}")
        if not args.trade:
            print("read-only: no order sent (use --trade to open and close the minimum volume)")
            return
        if not is_open:
            sys.exit("market is closed: not trading")

        volume = spec["min_volume"]
        opened = gateway.place_market(
            symbol_id=spec["symbol_id"], side=args.side, volume=volume, client_order_id=f"smoke_{now:%Y%m%dT%H%M%S}"
        )
        print(f"opened  {args.side} {volume} @ {opened['price']}  commission {opened['commission']}")
        closed = gateway.close_position(position_id=opened["position_id"], volume=volume)
        print(
            f"closed          @ {closed['price']}  commission {closed['commission']}  "
            f"gross {closed['gross_profit']}  swap {closed['swap']}"
        )
        total = opened["commission"] + closed["commission"] + closed["gross_profit"] + closed["swap"]
        print(f"round trip: {total:+.2f} (commission {opened['commission'] + closed['commission']:+.2f})")
        print(f"orders on the account since the script started: {len(gateway.orders_since(now))} (expected 2)")
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
