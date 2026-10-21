#!/usr/bin/env bash
# Download OKX daily candles for the pinned universe and backtest.
set -euo pipefail
cd "$(dirname "$0")/.."
FREQ="./.venv/bin/freqtrade"
if [[ ! -x "$FREQ" ]]; then
  echo "Missing $FREQ. Create the venv and pip install -r requirements.txt first." >&2
  exit 1
fi
"$FREQ" download-data \
  --userdir user_data \
  --config config.backtest.json \
  --timeframe 1d \
  --timerange 20230101-
"$FREQ" backtesting \
  --userdir user_data \
  --config config.backtest.json \
  --strategy WeeklyMomentumVolStrategy \
  --timeframe 1d \
  --timerange 20230101- \
  --fee 0.001 \
  --export trades \
  --breakdown month
