"""
Pin the backtest universe to the current most-liquid OKX USDT spot pairs.

The list is today's liquidity, then used for the whole history. Coins that
were large in the past and later died are missing, and coins that just became
liquid are included before they were liquid. Treat the backtest as a practical
check, not as a point-in-time research replication.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt


ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT / "okx_universe.json"
BACKTEST_CONFIG_PATH = ROOT / "config.backtest.json"
DRY_RUN_CONFIG_PATH = ROOT / "config.json"

TOP_N = 25
MIN_QUOTE_VOLUME = 2_000_000
# OKX public instruments: 1 crypto, 3 tokenized stocks (including leveraged
# equity tokens such as XSOXL), 4 precious-metal tokens such as XAUT.
CRYPTO_INST_CATEGORY = "1"
MIN_LISTED_DAYS = 60
STABLE_BASES = {
    "USDT",
    "USDC",
    "DAI",
    "TUSD",
    "FDUSD",
    "USDE",
    "USDD",
    "USD1",
    "PYUSD",
    "USDG",
    "USDP",
    "GUSD",
    "USTC",
    "USDS",
    "USD0",
    "RLUSD",
    "USDT0",
    "AUSD",
    "USDB",
    "XUSD",
    "EUR",
    "EURS",
    "EURT",
    "USD",
}
LEVERAGED_BASE = re.compile(r"(UP|DOWN|BULL|BEAR|\d+[LS])$")


def quote_volume(ticker: dict) -> float:
    value = ticker.get("quoteVolume")
    if value:
        return float(value)
    info = ticker.get("info") or {}
    for key in ("volCcy24h", "volCcyQuote24h"):
        raw = info.get(key)
        if raw not in (None, ""):
            return float(raw)
    return 0.0


def select_pairs(exchange: ccxt.okx) -> list[dict]:
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()
    rows: list[dict] = []
    for symbol, market in markets.items():
        if not market.get("spot") or not market.get("active"):
            continue
        if market.get("quote") != "USDT":
            continue
        base = str(market.get("base") or "")
        if base in STABLE_BASES or LEVERAGED_BASE.search(base):
            continue
        info = market.get("info") or {}
        if str(info.get("instCategory")) != CRYPTO_INST_CATEGORY:
            continue
        list_ms = int(info.get("listTime") or 0)
        if list_ms <= 0:
            continue
        listed_at = datetime.fromtimestamp(list_ms / 1000, timezone.utc)
        if datetime.now(timezone.utc) - listed_at < timedelta(days=MIN_LISTED_DAYS):
            continue
        volume = quote_volume(tickers.get(symbol, {}))
        if volume < MIN_QUOTE_VOLUME:
            continue
        rows.append(
            {
                "pair": symbol,
                "base": base,
                "quote_volume_usdt": round(volume, 2),
                "listed_at": listed_at.date().isoformat(),
            }
        )
    rows.sort(key=lambda item: item["quote_volume_usdt"], reverse=True)
    selected = rows[:TOP_N]
    selected_pairs = {item["pair"] for item in selected}
    for required in ("BTC/USDT", "ETH/USDT"):
        if required in selected_pairs:
            continue
        match = next((item for item in rows if item["pair"] == required), None)
        if match is not None:
            selected.append(match)
    return selected


def write_whitelist(path: Path, pairs: list[str]) -> None:
    config = json.loads(path.read_text())
    config["exchange"]["pair_whitelist"] = pairs
    path.write_text(json.dumps(config, indent=2) + "\n")


def main() -> None:
    exchange = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    selected = select_pairs(exchange)
    if len(selected) < 5:
        raise SystemExit(f"Only found {len(selected)} OKX USDT pairs. Refusing to overwrite.")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exchange": "okx",
        "quote": "USDT",
        "min_quote_volume_usdt": MIN_QUOTE_VOLUME,
        "min_listed_days": MIN_LISTED_DAYS,
        "inst_category": CRYPTO_INST_CATEGORY,
        "selection": (
            "top quote volume among OKX crypto spot (instCategory 1); "
            "tokenized stocks, metal tokens, stables, and leveraged tokens removed; "
            "listed at least 60 days"
        ),
        "bias": (
            "Snapshot of current liquidity applied to the whole backtest. "
            "Not a point-in-time historical universe."
        ),
        "pairs": selected,
    }
    UNIVERSE_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    pairs = [item["pair"] for item in selected]
    write_whitelist(BACKTEST_CONFIG_PATH, pairs)
    write_whitelist(DRY_RUN_CONFIG_PATH, pairs)
    print(f"Wrote {len(pairs)} pairs to {UNIVERSE_PATH.name}")
    for item in selected:
        print(f"  {item['pair']:<16} {item['quote_volume_usdt']:,.0f} USDT")


if __name__ == "__main__":
    main()
