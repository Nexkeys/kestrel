"""Bollinger Bands breakout strategy -- BUY when price breaks above upper band on high volume."""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from core.scanner import Signal, SignalConfidence, SignalType
from strategies.base import BaseStrategy


class BollingerBreakoutStrategy(BaseStrategy):
    """Bollinger Bands volatility breakout with volume confirmation."""

    name = "BB_Breakout"

    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self._defaults = {
            "bb_period": 20,
            "bb_std": 2.0,
            "volume_multiplier": 1.5,
            "atr_mult_sl": 2.0,
            "atr_mult_tp": 4.0,
        }

    def load_config(self, config: dict[str, Any]) -> None:
        self.config = {**self._defaults, **config}

    def validate_config(self) -> bool:
        p = self.config
        return (
            p.get("bb_period", 0) > 1
            and p.get("bb_std", 0) > 0
            and p.get("volume_multiplier", 0) > 0
        )

    def get_indicators(self, candles: pd.DataFrame) -> dict[str, Any]:
        close = candles["close"]
        period = self.config["bb_period"]
        std = self.config["bb_std"]

        sma = close.rolling(window=period).mean()
        std_dev = close.rolling(window=period).std()
        upper = sma + std_dev * std
        lower = sma - std_dev * std

        # ATR
        high = candles["high"]
        low = candles["low"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()

        # Volume SMA
        volume = candles["volume"]
        avg_volume = volume.rolling(window=20).mean()

        return {
            "bb_middle": sma,
            "bb_upper": upper,
            "bb_lower": lower,
            "atr": atr,
            "volume": volume,
            "avg_volume": avg_volume,
            "close": close,
        }

    def evaluate(
        self,
        candles: pd.DataFrame,
        indicators: dict[str, Any],
        config: Any,
    ) -> Optional[Signal]:
        upper = indicators["bb_upper"]
        lower = indicators["bb_lower"]
        atr = indicators["atr"]
        volume = indicators["volume"]
        avg_volume = indicators["avg_volume"]
        close = indicators["close"]

        if len(close) < max(self.config["bb_period"], 20) + 2:
            return None

        idx = -1
        prev_close = float(close.iloc[idx - 1])
        curr_close = float(close.iloc[idx])
        curr_upper = float(upper.iloc[idx])
        curr_lower = float(lower.iloc[idx])
        curr_vol = float(volume.iloc[idx])
        curr_avg_vol = float(avg_volume.iloc[idx])
        current_atr = float(atr.iloc[idx])

        vol_mult = self.config["volume_multiplier"]
        breakout_buy = prev_close <= curr_upper and curr_close > curr_upper
        breakout_sell = prev_close >= curr_lower and curr_close < curr_lower

        if not (breakout_buy or breakout_sell):
            return None

        # Volume filter
        if curr_avg_vol > 0 and curr_vol < vol_mult * curr_avg_vol:
            return None

        entry = curr_close
        if breakout_buy:
            direction = SignalType.BUY
            sl = entry - current_atr * self.config["atr_mult_sl"]
            tp = entry + current_atr * self.config["atr_mult_tp"]
        else:
            direction = SignalType.SELL
            sl = entry + current_atr * self.config["atr_mult_sl"]
            tp = entry - current_atr * self.config["atr_mult_tp"]

        confidence = SignalConfidence.MEDIUM

        return Signal(
            type=direction,
            symbol=config.symbols[0] if config.symbols else "UNKNOWN",
            timeframe=config.timeframe,
            strategy_name=self.name,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            volume=0.01,
            confidence=confidence,
            strength=0.65,
            metadata={
                "bb_upper": curr_upper,
                "bb_lower": curr_lower,
                "atr": current_atr,
                "volume_ratio": curr_vol / curr_avg_vol if curr_avg_vol > 0 else 0,
            },
        )
