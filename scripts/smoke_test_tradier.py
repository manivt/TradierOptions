"""Live smoke test against the real Tradier API.

Reads the real .env, performs a handful of read-only requests and prints a
sanitized summary.  Secrets are never printed: the token and account id are
only ever reported as present or absent.

    python scripts/smoke_test_tradier.py
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import ConfigError, load_settings  # noqa: E402
from tradier_collector.market_clock import build_market_clock  # noqa: E402
from tradier_collector.normalization import extract_spot  # noqa: E402
from tradier_collector.strike_selector import select_atm_window  # noqa: E402
from tradier_collector.tradier_client import TradierAPIError, TradierClient  # noqa: E402

SAFE_OPTION_FIELDS = (
    "symbol",
    "description",
    "option_type",
    "strike",
    "bid",
    "bidsize",
    "ask",
    "asksize",
    "last",
    "volume",
    "open_interest",
    "trade_date",
    "bid_date",
    "ask_date",
    "expiration_date",
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    print("Tradier smoke test")
    print("=" * 60)
    print(f"base_url          : {settings.base_url}")
    print(f"api token         : {'present' if settings.api_token else 'MISSING'}")
    print(f"account id        : {'configured' if settings.account_id else 'not set'}")

    clock = build_market_clock(settings)
    now = datetime.now(tz=UTC)
    today = clock.today(now)
    window = clock.session_window(today)
    print(f"current NY date   : {today.isoformat()}")
    print(f"trading day       : {clock.is_trading_day(today)}")
    print(f"early close       : {clock.is_early_close(today)}")
    if window:
        print(f"collection window : {window.start_utc.isoformat()} .. {window.end_utc.isoformat()}")
    print("")
    exit_code = 0
    with TradierClient(
        settings.api_token,
        base_url=settings.base_url,
        timeout=settings.http_timeout,
        max_attempts=settings.http_max_attempts,
    ) as client:
        spots: dict[str, float | None] = {}
        for ticker in settings.tickers:
            try:
                quote = client.get_quote(ticker, greeks=False)
            except TradierAPIError as exc:
                print(f"{ticker:<5} quote            : FAILED ({exc})")
                exit_code = 1
                continue
            spot = extract_spot(quote)
            spots[ticker] = spot
            print(
                f"{ticker:<5} quote            : last={quote.get('last')} "
                f"bid={quote.get('bid')} ask={quote.get('ask')} spot={spot}"
            )

        print("")
        zero_dte: dict[str, str | None] = {}
        for ticker in settings.tickers:
            try:
                expirations = client.get_expirations(ticker)
            except TradierAPIError as exc:
                print(f"{ticker:<5} expirations      : FAILED ({exc})")
                exit_code = 1
                continue
            same_day = today.isoformat() if today.isoformat() in expirations else None
            zero_dte[ticker] = same_day
            summary = (
                f"YES {same_day}" if same_day else f"no (next listed: {expirations[:1]})"
            )
            print(
                f"{ticker:<5} same-day exp     : "
                f"{summary}"
            )

        print("")
        primary = settings.tickers[0] if settings.tickers else "SPY"
        expiration = zero_dte.get(primary)
        if not expiration:
            print(f"{primary} has no same-day expiration; skipping the chain check.")
            return exit_code

        chain = client.get_chain(primary, expiration, greeks=True)
        strikes = sorted({float(c["strike"]) for c in chain if c.get("strike") is not None})
        print(f"{primary} chain contracts   : {len(chain)}")
        print(
            f"{primary} strike range      : {strikes[0]} .. {strikes[-1]} "
            f"({len(strikes)} strikes)"
        )

        spot = spots.get(primary)
        if spot is None:
            print("No usable spot price; skipping the ATM window sample.")
            return exit_code

        atm_window = select_atm_window(chain, spot, settings.discovery_strikes_each_side)
        print(
            f"{primary} ATM window        : atm={atm_window.atm_strike} "
            f"strikes={len(atm_window.strikes)} contracts={len(atm_window.contracts)}"
        )
        if atm_window.contracts:
            sample = atm_window.contracts[0]
            print("")
            print("Representative contract (sanitized):")
            for key in SAFE_OPTION_FIELDS:
                print(f"  {key:<16}: {sample.get(key)}")
            greeks = sample.get("greeks")
            if isinstance(greeks, dict):
                print("  greeks (vendor) :")
                for key in sorted(greeks):
                    print(f"    {key:<14}: {greeks[key]}")
            else:
                print("  greeks (vendor) : not returned for this contract")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
