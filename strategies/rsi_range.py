"""RSI Range strategy -- BUY at oversold crossover, SELL at overbought crossover."""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from core.scanner import Signal, SignalConfidence, SignalType
from strategies.base import BaseStrategy


class RSIRangeStrategy(BaseStrategy):
    """RSI oversold/overbought mean-reversion strategy."""

    name = "RSI_Range"

    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self._defaults = {
            "rsi_period": 14,
            "oversold": 30.0,
            "overbought": 70.0,
        }

    def load_config(self, config: dict[str, Any]) -> None:
        self.config = {**self._defaults, **config}

    def validate_config(self) -> bool:
        p = self.config
        return (
            p.get("rsi_period", 0) > 1
            and 0 <= p.get("oversold", 50) < p.get("overbought", 50) <= 100
        )

    def get_indicators(self, candles: pd.DataFrame) -> dict[str, Any]:
        close = candles["close"]
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        period = self.config["rsi_period"]
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # ATR
        high = candles["high"]
        low = candles["low"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()

        return {"rsi": rsi, "atr": atr, "close": close}

    def evaluate(
        self,
        candles: pd.DataFrame,
        indicators: dict[str, Any],
        config: Any,
    ) -> Optional[Signal]:
        rsi = indicators["rsi"]
        atr = indicators["atr"]
        close = indicators["close"]

        if len(rsi) < 2:
            return None

        oversold = self.config["oversold"]
        overbought = self.config["overbought"]

        prev_rsi = float(rsi.iloc[-2])
        curr_rsi = float(rsi.iloc[-1])

        cross_up = prev_rsi < oversold <= curr_rsi  # RSI exits oversold
        cross_down = prev_rsi > overbought >= curr_rsi  # RSI exits overbought

        if not (cross_up or cross_down):
            return None

        idx = -1
        entry = float(close.iloc[idx])
        current_atr = float(atr.iloc[idx])

        if cross_up:
            direction = SignalType.BUY
            sl = entry - current_atr * 2.0
            tp = entry + current_atr * 4.0
        else:
            direction = SignalType.SELL
            sl = entry + current_atr * 2.0
            tp = entry - current_atr * 4.0

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
            strength=0.6,
            metadata={"rsi": curr_rsi, "oversold": oversold, "overbought": overbought},
        )
