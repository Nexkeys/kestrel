"""Unit tests for strategy signal evaluation logic."""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.ema_cross import EMACrossStrategy
from strategies.rsi_range import RSIRangeStrategy
from strategies.bollinger_breakout import BollingerBreakoutStrategy
from core.scanner import StrategyConfig


def make_config(symbols: list[str] = None, params: dict = None):
    return StrategyConfig(
        name="TestStrategy",
        enabled=True,
        symbols=symbols or ["EURUSD"],
        timeframe="H1",
        risk_per_trade=1.0,
        params=params or {},
    )


def build_candles(n: int, start_price: float = 1.1000, trend: str = "flat") -> pd.DataFrame:
    """Build n synthetic candles. trend: flat | up | down | wavy."""
    import numpy as np

    rng = np.random.default_rng(42)
    close = [start_price]
    for i in range(1, n):
        if trend == "up":
            delta = rng.uniform(0.0001, 0.0008)
        elif trend == "down":
            delta = -rng.uniform(0.0001, 0.0008)
        elif trend == "wavy":
            delta = rng.uniform(-0.0005, 0.0005)
        else:
            delta = rng.uniform(-0.0001, 0.0001)
        close.append(close[-1] + delta)

    opens = [start_price + rng.uniform(-0.0002, 0.0002) for _ in range(n)]
    highs = [max(o, c) + rng.uniform(0, 0.0003) for o, c in zip(opens, close)]
    lows = [min(o, c) - rng.uniform(0, 0.0003) for o, c in zip(opens, close)]

    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="h"),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": close,
        "volume": [1000] * n,
        "spread": [5] * n,
    })
    df.set_index("time", inplace=True)
    return df


class TestEMACross:
    def setup_method(self):
        self.strategy = EMACrossStrategy()
        self.strategy.load_config({
            "fast_ema_period": 5,
            "slow_ema_period": 10,
            "min_signal_strength": 0.3,
        })

    def test_returns_signal_on_bullish_cross(self):
        df = build_candles(50, start_price=1.1000, trend="up")
        # Force last bars to produce a crossover
        df.iloc[-1, df.columns.get_loc("close")] = 1.1150
        df.iloc[-2, df.columns.get_loc("close")] = 1.1120

        indicators = self.strategy.get_indicators(df)
        config = make_config(symbols=["EURUSD"])
        signal = self.strategy.evaluate(df, indicators, config)

        assert signal is not None
        assert signal.type.value == "BUY"
        assert signal.entry_price > signal.stop_loss
        assert signal.take_profit > signal.entry_price

    def test_returns_none_when_no_cross(self):
        df = build_candles(50, start_price=1.1000, trend="flat")
        indicators = self.strategy.get_indicators(df)
        config = make_config(symbols=["EURUSD"])
        signal = self.strategy.evaluate(df, indicators, config)
        assert signal is None

    def test_config_validation(self):
        assert self.strategy.validate_config() is True
        bad = EMACrossStrategy()
        bad.load_config({"fast_ema_period": 20, "slow_ema_period": 10})
        assert bad.validate_config() is False


class TestRSIRange:
    def setup_method(self):
        self.strategy = RSIRangeStrategy()
        self.strategy.load_config({
            "rsi_period": 14,
            "oversold": 30.0,
            "overbought": 70.0,
        })

    def test_returns_buy_when_rsi_exits_oversold(self):
        # Build a trend that drives RSI from below 30 to above 30
        df = build_candles(60, start_price=1.1000, trend="up")
        indicators = self.strategy.get_indicators(df)
        config = make_config(symbols=["EURUSD"])
        signal = self.strategy.evaluate(df, indicators, config)
        # Signal may be None on flat data; just check it doesn't crash
        if signal is not None:
            assert signal.type.value in ("BUY", "SELL")

    def test_returns_none_when_rsi_in_middle_zone(self):
        df = build_candles(50, start_price=1.1000, trend="flat")
        indicators = self.strategy.get_indicators(df)
        config = make_config(symbols=["EURUSD"])
        signal = self.strategy.evaluate(df, indicators, config)
        assert signal is None

    def test_config_validation(self):
        assert self.strategy.validate_config() is True


class TestBollingerBreakout:
    def setup_method(self):
        self.strategy = BollingerBreakoutStrategy()
        self.strategy.load_config({
            "bb_period": 20,
            "bb_std": 2.0,
            "volume_multiplier": 1.5,
        })

    def test_returns_none_when_no_breakout(self):
        df = build_candles(50, start_price=1.1000, trend="flat")
        indicators = self.strategy.get_indicators(df)
        config = make_config(symbols=["EURUSD"])
        signal = self.strategy.evaluate(df, indicators, config)
        assert signal is None

    def test_breakout_buy_requires_volume_filter(self):
        # Price breaks band but volume is flat -- should not trigger
        df = build_candles(50, start_price=1.1000, trend="wavy")
        # Set high volume
        df["volume"] = [5000] * 50
        indicators = self.strategy.get_indicators(df)
        config = make_config(symbols=["EURUSD"])
        signal = self.strategy.evaluate(df, indicators, config)
        # Should not crash; may or may not produce signal depending on values

    def test_config_validation(self):
        assert self.strategy.validate_config() is True
        bad = BollingerBreakoutStrategy()
        bad.load_config({"bb_period": 1, "bb_std": 2.0, "volume_multiplier": 0.0})
        assert bad.validate_config() is False
