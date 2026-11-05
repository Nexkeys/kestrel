"""EMA Crossover strategy -- BUY when fast EMA crosses above slow EMA, SELL on reverse."""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from core.scanner import Signal, SignalConfidence, SignalType
from strategies.base import BaseStrategy


class EMACrossStrategy(BaseStrategy):
    """EMA fast/slow crossover strategy with ATR-based SL/TP."""

    name = "EMA_Cross"

    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self._defaults = {
            "fast_ema_period": 8,
            "slow_ema_period": 21,
            "min_signal_strength": 0.6,
        }

    def load_config(self, config: dict[str, Any]) -> None:
        self.config = {**self._defaults, **config}

    def validate_config(self) -> bool:
        p = self.config
        if p.get("fast_ema_period", 0) >= p.get("slow_ema_period", 1):
            return False
        if p.get("min_signal_strength", 0) <= 0:
            return False
        return True

    def get_indicators(self, candles: pd.DataFrame) -> dict[str, Any]:
        fast = candles["close"].ewm(span=self.config["fast_ema_period"], adjust=False).mean()
        slow = candles["close"].ewm(span=self.config["slow_ema_period"], adjust=False).mean()

        high = candles["high"]
        low = candles["low"]
        close = candles["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()

        return {
            "ema_fast": fast,
            "ema_slow": slow,
            "atr": atr,
            "close": close,
        }

    def evaluate(
        self,
        candles: pd.DataFrame,
        indicators: dict[str, Any],
        config: Any,
    ) -> Optional[Signal]:
        fast = indicators["ema_fast"]
        slow = indicators["ema_slow"]
        atr = indicators["atr"]
        close = indicators["close"]

        if len(fast) < 3:
            return None

        idx = -1  # most recent bar
        prev_fast = float(fast.iloc[idx - 1])
        curr_fast = float(fast.iloc[idx])
        prev_slow = float(slow.iloc[idx - 1])
        curr_slow = float(slow.iloc[idx])

        # Detect crossover
        cross_up = prev_fast <= prev_slow and curr_fast > curr_slow
        cross_down = prev_fast >= prev_slow and curr_fast < curr_slow

        if not (cross_up or cross_down):
            return None

        # Gap between fast and slow as a percentage of slow price
        gap_pct = abs(curr_fast - curr_slow) / curr_slow * 100
        strength = min(1.0, gap_pct * 0.5)

        if strength < self.config["min_signal_strength"] / 100:
            return None

        # Determine direction and SL/TP
        if cross_up:
            direction = SignalType.BUY
            entry = float(close.iloc[idx])
            sl = entry - float(atr.iloc[idx]) * 1.5
            tp = entry + float(atr.iloc[idx]) * 3.0
        else:
            direction = SignalType.SELL
            entry = float(close.iloc[idx])
            sl = entry + float(atr.iloc[idx]) * 1.5
            tp = entry - float(atr.iloc[idx]) * 3.0

        confidence = (
            SignalConfidence.HIGH if strength > 0.8
            else SignalConfidence.MEDIUM if strength > 0.5
            else SignalConfidence.LOW
        )

        return Signal(
            type=direction,
            symbol=config.symbols[0] if config.symbols else "UNKNOWN",
            timeframe=config.timeframe,
            strategy_name=self.name,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            volume=0.01,  # RiskManager will override
            confidence=confidence,
            strength=strength,
            metadata={
                "fast_ema": curr_fast,
                "slow_ema": curr_slow,
                "atr": float(atr.iloc[idx]),
                "gap_pct": gap_pct,
            },
        )
