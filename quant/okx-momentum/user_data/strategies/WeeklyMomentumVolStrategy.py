"""
Weekly cross-sectional momentum with volatility sizing for OKX spot.

Research mapping (fixed before any backtest of this file):
- Liu, Tsyvinski, and Wu, Journal of Finance 2022, "Common Risk Factors in
  Cryptocurrency": past 1-4 week winners keep winning; 3-week momentum was the
  strongest, and portfolios are re-sorted every week. With about 25 liquid
  coins, the top quintile is 5 names.
- Volatility-managed momentum (Barroso and Santa-Clara; the 2025 Financial
  Markets and Portfolio Management crypto momentum paper): scale risk down when
  recent volatility is high. This file does that per coin, as a spot position
  cap. It does not replicate the paper's leveraged portfolio scaler.

This is the long-only half of that research. The published factors are
long winners and short losers. OKX spot cannot short, so losers are simply
not held. A negative Bitcoin 3-week return cuts new position size in half, and
a Bitcoin 3-week return below -8% flattens the book on the next weekly
rebalance. Those two overlays are risk controls, not part of the papers.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from pandas import DataFrame, Series

from freqtrade.strategy import IStrategy


logger = logging.getLogger(__name__)


def momentum_vol_rank(
    closes: DataFrame, momentum_days: int, vol_days: int
) -> tuple[DataFrame, DataFrame, DataFrame]:
    """
    Build point-in-time momentum, volatility, and cross-sectional rank.

    ``closes`` is indexed by candle open time, one column per pair.
    Momentum on row t uses only closes through t. Rank is computed only among
    pairs with positive momentum, so a down week does not force a full book.
    Rank 1 is the strongest positive momentum. Non-positive momentum is NaN.
    """
    price = closes.astype(float).replace(0, np.nan)
    momentum = price / price.shift(momentum_days) - 1.0
    volatility = price.pct_change().rolling(vol_days).std()
    rank = momentum.where(momentum > 0).rank(axis=1, ascending=False, method="min")
    return momentum, volatility, rank


def weekly_rebalance_mask(dates: Series) -> Series:
    """True on the Sunday candle. The fill happens at the next candle's open."""
    return dates.dt.dayofweek.eq(6)


def _column_values(frame: DataFrame, column: str) -> np.ndarray:
    """Positional values. Never assign the Series itself: its date index will not match."""
    if column not in frame.columns:
        return np.full(len(frame), np.nan)
    return frame[column].to_numpy()


def attach_cross_section(
    dataframe: DataFrame,
    pair: str,
    momentum: DataFrame,
    volatility: DataFrame,
    rank: DataFrame,
    btc_pair: str,
) -> DataFrame:
    """Left-join cross-sectional columns onto this pair's candles, matched on candle time."""
    dataframe = dataframe.drop(columns=["mom", "vol", "rank", "btc_mom"], errors="ignore").copy()
    extra = pd.DataFrame(
        {
            "_k": pd.to_datetime(momentum.index, utc=True),
            "mom": _column_values(momentum, pair),
            "vol": _column_values(volatility, pair),
            "rank": _column_values(rank, pair),
            "btc_mom": _column_values(momentum, btc_pair),
        }
    )
    dataframe["_k"] = pd.to_datetime(dataframe["date"], utc=True)
    merged = dataframe.merge(extra, on="_k", how="left")
    return merged.drop(columns=["_k"])


class WeeklyMomentumVolStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short: bool = False
    process_only_new_candles = True
    startup_candle_count = 40

    # Hold the week. Exit on the weekly rebalance, or on the crash stop below.
    minimal_roi = {"0": 100}
    stoploss = -0.20
    trailing_stop = False
    use_custom_stoploss = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # --- knobs -------------------------------------------------------------
    # 3 weeks of daily candles. Liu, Tsyvinski, and Wu's strongest window.
    momentum_days = 21
    # 4-week realized volatility, the shorter window in the 2025 vol paper.
    vol_days = 28
    # Top quintile of a ~25 coin liquid universe.
    top_n = 5
    # Sell when rank falls past this. Equal to top_n means no hold buffer.
    # A buffer (for example 8) cuts turnover, and max_open_trades must be
    # raised to at least that number or new entries get skipped.
    exit_rank = 5
    # Per-coin annualized volatility target. Spot sizing never levers up.
    target_ann_vol = 0.50
    min_vol_scale = 0.25
    max_vol_scale = 1.0
    annualization_days = 365
    # Bitcoin 3-week return overlays. Not from the momentum papers.
    btc_reduce_below = 0.0
    btc_block_below = -0.08
    reduced_regime_scale = 0.5
    btc_pair = "BTC/USDT"
    # Leave a little cash so five equal slots cannot overshoot the wallet.
    stake_haircut = 0.98

    max_open_trades = 5

    _panel_key: tuple | None = None
    _panel: tuple[DataFrame, DataFrame, DataFrame] | None = None

    def informative_pairs(self) -> list[tuple[str, str]]:
        # Bitcoin candles are required for the regime overlay even if a
        # volume pairlist ever drops BTC/USDT.
        return [(self.btc_pair, self.timeframe)]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = dataframe.copy()
        if dataframe.empty:
            return dataframe

        pair = metadata["pair"]
        momentum, volatility, rank = self._panel_frames()
        dataframe = attach_cross_section(
            dataframe, pair, momentum, volatility, rank, self.btc_pair
        )
        dataframe["rebalance"] = weekly_rebalance_mask(dataframe["date"])
        dataframe.replace([np.inf, -np.inf], np.nan, inplace=True)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = None
        enter = (
            dataframe["rebalance"].fillna(False)
            & dataframe["rank"].notna()
            & (dataframe["rank"] <= self.top_n)
            & (dataframe["mom"] > 0)
            & (dataframe["vol"] > 0)
            & dataframe["btc_mom"].notna()
            & (dataframe["btc_mom"] >= self.btc_block_below)
        )
        dataframe.loc[enter, "enter_long"] = 1
        dataframe.loc[enter, "enter_tag"] = "weekly_momentum"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_tag"] = None
        rebalance = dataframe["rebalance"].fillna(False)
        regime_off = dataframe["btc_mom"].isna() | (dataframe["btc_mom"] < self.btc_block_below)
        left_book = (
            dataframe["rank"].isna()
            | (dataframe["rank"] > self.exit_rank)
            | dataframe["mom"].isna()
            | (dataframe["mom"] <= 0)
            | dataframe["vol"].isna()
            | (dataframe["vol"] <= 0)
        )
        dataframe.loc[rebalance & left_book, "exit_long"] = 1
        dataframe.loc[rebalance & left_book, "exit_tag"] = "left_momentum_book"
        # Regime off overwrites the tag when both are true.
        dataframe.loc[rebalance & regime_off, "exit_long"] = 1
        dataframe.loc[rebalance & regime_off, "exit_tag"] = "btc_regime_off"
        return dataframe

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """
        Size one slot as equity / top_n, then scale by inverse volatility and
        by the Bitcoin regime. High-volatility coins get less, never more than
        a full slot.
        """
        row = self._latest_closed_row(pair)
        if row is None:
            return 0.0

        vol = float(row.get("vol", np.nan))
        btc_mom = float(row.get("btc_mom", np.nan))
        regime = self._regime_scale(btc_mom)
        vol_scale = self._volatility_scale(vol)
        if regime <= 0 or vol_scale <= 0:
            return 0.0

        equity = self._tradable_equity()
        base = (equity / self.top_n) if equity is not None else float(proposed_stake)
        stake = base * vol_scale * regime * self.stake_haircut
        if max_stake is not None and np.isfinite(max_stake):
            stake = min(stake, float(max_stake))
        if not np.isfinite(stake) or stake <= 0:
            return 0.0
        if min_stake is not None and stake < float(min_stake):
            return 0.0
        return float(stake)

    def _panel_frames(self) -> tuple[DataFrame, DataFrame, DataFrame]:
        pairs = self._universe_pairs()
        stamps: list[tuple[str, int, str]] = []
        closes: dict[str, Series] = {}
        for pair in pairs:
            raw = self.dp.get_pair_dataframe(pair=pair, timeframe=self.timeframe)
            if raw is None or raw.empty or "close" not in raw.columns:
                stamps.append((pair, 0, ""))
                continue
            stamps.append((pair, int(len(raw)), str(raw["date"].iloc[-1])))
            close = (
                raw[["date", "close"]]
                .drop_duplicates(subset="date", keep="last")
                .set_index("date")["close"]
                .astype(float)
            )
            closes[pair] = close

        cache_key = (tuple(stamps), self.momentum_days, self.vol_days)
        if self._panel_key == cache_key and self._panel is not None:
            return self._panel

        close_frame = pd.DataFrame(closes).sort_index() if closes else pd.DataFrame()
        panel = momentum_vol_rank(close_frame, self.momentum_days, self.vol_days)
        self._panel_key = cache_key
        self._panel = panel
        logger.info(
            "Built momentum panel for %s pairs, momentum_days=%s, vol_days=%s",
            len(closes),
            self.momentum_days,
            self.vol_days,
        )
        return panel

    def _universe_pairs(self) -> list[str]:
        pairs: list[str] = []
        if self.dp is not None:
            try:
                pairs = list(self.dp.current_whitelist())
            except Exception:
                logger.warning("Whitelist unavailable, falling back to config pairs.")
                pairs = []
        if not pairs:
            pairs = list(self.config.get("exchange", {}).get("pair_whitelist", []))
        if self.btc_pair not in pairs:
            pairs.append(self.btc_pair)
        return pairs

    def _latest_closed_row(self, pair: str) -> pd.Series | None:
        if self.dp is None:
            return None
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        return dataframe.iloc[-1]

    def _tradable_equity(self) -> float | None:
        wallets = getattr(self, "wallets", None)
        if wallets is None:
            return None
        try:
            total = float(wallets.get_total_stake_amount())
        except Exception:
            return None
        if not np.isfinite(total) or total <= 0:
            return None
        return total

    def _regime_scale(self, btc_mom: float) -> float:
        if not np.isfinite(btc_mom) or btc_mom < self.btc_block_below:
            return 0.0
        if btc_mom < self.btc_reduce_below:
            return self.reduced_regime_scale
        return 1.0

    def _volatility_scale(self, vol: float) -> float:
        if not np.isfinite(vol) or vol <= 0:
            return 0.0
        annualized = vol * np.sqrt(self.annualization_days)
        if not np.isfinite(annualized) or annualized <= 0:
            return 0.0
        scale = self.target_ann_vol / annualized
        return float(np.clip(scale, self.min_vol_scale, self.max_vol_scale))
