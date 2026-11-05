"""Base strategy interface -- all strategies inherit from here."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import pandas as pd


class BaseStrategy(ABC):
    """Abstract base class for all Kestrel strategies."""

    name: str = "BaseStrategy"
    timeframe: str = "H1"
    symbols: list[str] = []
    risk_per_trade: float = 1.0

    @abstractmethod
    def get_indicators(self, candles: pd.DataFrame) -> dict[str, Any]:
        """Compute all indicators needed by this strategy."""
        ...

    @abstractmethod
    def evaluate(
        self,
        candles: pd.DataFrame,
        indicators: dict[str, Any],
        config: Any,
    ) -> Optional[Any]:
        """Evaluate indicators and return a Signal or None."""
        ...

    def load_config(self, config: dict[str, Any]) -> None:
        """Load strategy-specific configuration."""
        self.config = config

    def validate_config(self) -> bool:
        """Validate that required config keys are present."""
        return True
