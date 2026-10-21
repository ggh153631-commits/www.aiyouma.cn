"""Point-in-time checks for the momentum rank. No exchange and no price history."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "user_data" / "strategies"))

import WeeklyMomentumVolStrategy as strategy


def test_rank_keeps_only_positive_winners() -> None:
    dates = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
    # Winner doubles over the last 21 days. Loser is cut in half. Flat is unchanged.
    winner = pd.Series(100.0, index=dates)
    winner.iloc[-1] = 200.0
    loser = pd.Series(100.0, index=dates)
    loser.iloc[-1] = 50.0
    flat = pd.Series(100.0, index=dates)
    closes = pd.DataFrame({"WIN/USDT": winner, "LOSE/USDT": loser, "FLAT/USDT": flat})

    momentum, volatility, rank = strategy.momentum_vol_rank(closes, momentum_days=21, vol_days=5)
    last = -1
    assert momentum.iloc[last]["WIN/USDT"] == 1.0
    assert momentum.iloc[last]["LOSE/USDT"] == -0.5
    assert rank.iloc[last]["WIN/USDT"] == 1
    assert pd.isna(rank.iloc[last]["LOSE/USDT"])
    assert pd.isna(rank.iloc[last]["FLAT/USDT"])
    assert pd.isna(rank.iloc[0]["WIN/USDT"])
    assert volatility.iloc[last]["WIN/USDT"] > 0


def test_rebalance_is_sunday_only() -> None:
    dates = pd.Series(pd.date_range("2024-01-05", periods=8, freq="D", tz="UTC"))
    mask = strategy.weekly_rebalance_mask(dates)
    # 2024-01-07 is a Sunday.
    assert dates[mask].dt.day_name().tolist() == ["Sunday"]


def test_zero_price_does_not_produce_infinity() -> None:
    dates = pd.date_range("2024-01-01", periods=25, freq="D", tz="UTC")
    closes = pd.DataFrame({"BAD/USDT": 0.0, "OK/USDT": 10.0}, index=dates)
    closes.loc[dates[-1], "OK/USDT"] = 12.0
    momentum, _, _ = strategy.momentum_vol_rank(closes, momentum_days=21, vol_days=5)
    assert not momentum.isin([float("inf"), float("-inf")]).any().any()
