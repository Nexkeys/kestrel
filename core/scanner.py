"""Market scanner -- generates trade signals from strategy evaluation."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

import pandas as pd

if TYPE_CHECKING:
    from core.mt5_client import MT5Client

logger = logging.getLogger(__name__)


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalConfidence(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Signal:
    """A trade signal produced by a strategy scanner."""

    type: SignalType
    symbol: str
    timeframe: str
    strategy_name: str
    entry_price: float
    stop_loss: float
    take_profit: float
    volume: float = 0.01
    confidence: SignalConfidence = SignalConfidence.MEDIUM
    strength: float = 0.5  # 0.0 - 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_valid(self) -> bool:
        if self.type == SignalType.HOLD:
            return False
        if self.entry_price <= 0 or self.volume <= 0:
            return False
        if self.stop_loss <= 0 or self.take_profit <= 0:
            return False
        # BUY: SL must be below entry, TP must be above entry
        if self.type == SignalType.BUY:
            if not (self.stop_loss < self.entry_price < self.take_profit):
                logger.warning(
                    "Invalid BUY signal for %s: entry=%.5f sl=%.5f tp=%.5f",
                    self.symbol, self.entry_price, self.stop_loss, self.take_profit,
                )
                return False
        # SELL: SL must be above entry, TP must be below entry
        elif self.type == SignalType.SELL:
            if not (self.take_profit < self.entry_price < self.stop_loss):
                logger.warning(
                    "Invalid SELL signal for %s: entry=%.5f sl=%.5f tp=%.5f",
                    self.symbol, self.entry_price, self.stop_loss, self.take_profit,
                )
                return False
        return True

    def risk_reward_ratio(self) -> float:
        """Calculate the configured risk:reward ratio."""
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit - self.entry_price)
        if risk == 0:
            return 0.0
        return reward / risk


@dataclass
class StrategyConfig:
    name: str
    enabled: bool = True
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "H1"
    risk_per_trade: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)


class Scanner:
    """Scans multiple symbols using registered strategies and produces signals."""

    def __init__(self, mt5_client: "MT5Client") -> None:
        self.mt5 = mt5_client
        self._strategies: dict[str, StrategyConfig] = {}
        self._candle_cache: dict[str, list[pd.DataFrame]] = {}

    def register_strategy(self, config: StrategyConfig) -> None:
        self._strategies[config.name] = config
        logger.info("Registered strategy: %s (timeframe=%s, symbols=%s)",
                    config.name, config.timeframe, config.symbols)

    def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame:
        """Fetch candles and return as a pandas DataFrame."""
        candles = self.mt5.get_candles(symbol, timeframe, count=count)
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame([{
            "time": c.time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.tick_volume,
            "spread": c.spread,
        } for c in candles])

        df.set_index("time", inplace=True)
        return df

    def compute_ema(df: pd.DataFrame, period: int, column: str = "close") -> pd.Series:
        return df[column].ewm(span=period, adjust=False).mean()

    def compute_sma(df: pd.DataFrame, period: int, column: str = "close") -> pd.Series:
        return df[column].rolling(window=period).mean()

    def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    def compute_rsi(df: pd.DataFrame, period: int = 14, column: str = "close") -> pd.Series:
        delta = df[column].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        return 100 - (100 / (1 + rs))

    def scan_all_async(self) -> list[Signal]:
        """Synchronous wrapper -- runs scan across all registered strategies."""
        signals: list[Signal] = []

        for name, config in self._strategies.items():
            if not config.enabled:
                continue

            for symbol in config.symbols:
                try:
                    sig = self._scan_symbol_strategy(symbol, config)
                    if sig is not None and sig.is_valid():
                        signals.append(sig)
                except Exception as e:
                    logger.error("Error scanning %s with %s: %s", symbol, name, e)

        logger.debug("Scan complete: %d signals generated", len(signals))
        return signals

    def _scan_symbol_strategy(self, symbol: str, config: StrategyConfig) -> Optional[Signal]:
        """Run a single strategy against a single symbol."""
        df = self.get_candles(symbol, config.timeframe, count=200)
        if df.empty or len(df) < 30:
            return None

        strategy = self._load_strategy_instance(config)
        if strategy is None:
            return None

        indicators = strategy.get_indicators(df)
        signal = strategy.evaluate(df, indicators, config)
        return signal

    def _load_strategy_instance(self, config: StrategyConfig) -> Optional[Any]:
        """Lazily import and instantiate a strategy class by name."""
        from strategies.ema_cross import EMACrossStrategy
        from strategies.rsi_range import RSIRangeStrategy
        from strategies.bollinger_breakout import BollingerBreakoutStrategy

        registry = {
            "EMA_Cross": EMACrossStrategy,
            "RSI_Range": RSIRangeStrategy,
            "BB_Breakout": BollingerBreakoutStrategy,
        }

        cls = registry.get(config.name)
        if cls is None:
            logger.warning("Unknown strategy: %s", config.name)
            return None

        instance = cls()
        instance.load_config(config.params)
        return instance
