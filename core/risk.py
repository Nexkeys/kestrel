"""Risk and money management -- position sizing, daily loss limits, drawdown checks."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .mt5_client import MT5Client

logger = logging.getLogger(__name__)


class RiskMode(Enum):
    FIXED_LOT = "fixed_lot"
    FIXED_RISK = "fixed_risk"  # % of account risk per trade
    KELLY = "kelly"  # Kelly criterion (informational only)


@dataclass
class RiskConfig:
    mode: RiskMode = RiskMode.FIXED_RISK
    risk_per_trade_pct: float = 1.0  # % of account equity
    max_daily_loss_pct: float = 5.0  # max daily drawdown
    max_open_positions: int = 5
    max_concurrent_lots: float = 0.5  # max total lot size across all positions
    min_risk_reward_ratio: float = 1.5  # minimum RR to accept a signal
    fixed_lot_size: float = 0.01  # used when mode == FIXED_LOT


@dataclass
class DailyStats:
    date: datetime
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    peak_equity: float = 0.0
    start_equity: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def is_daily_loss_exceeded(self) -> bool:
        if self.start_equity <= 0:
            return False
        loss_pct = abs(self.pnl) / self.start_equity * 100
        return self.pnl < 0 and loss_pct >= 5.0


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str = ""
    adjusted_volume: float = 0.0


class RiskManager:
    """Enforces risk rules: position sizing, daily loss limits, drawdown."""

    def __init__(
        self,
        mt5_client: "MT5Client",
        config: Optional[RiskConfig] = None,
    ) -> None:
        self.mt5 = mt5_client
        self.config = config or RiskConfig()
        self._today_stats: Optional[DailyStats] = None
        self._check_date()  # reset if new day

    def _check_date(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self._today_stats is None or self._today_stats.date.date() != today:
            self._reset_daily_stats(today)

    def _reset_daily_stats(self, date: Any) -> None:
        acc = self.mt5.get_account_info()
        start_equity = acc.equity if acc else 0.0
        self._today_stats = DailyStats(
            date=datetime.combine(date, datetime.min.time(), tzinfo=timezone.utc),
            start_equity=start_equity,
            peak_equity=start_equity,
        )
        logger.info(
            "Daily stats reset | Date: %s | Start equity: %.2f",
            date, start_equity,
        )

    def can_trade(self) -> RiskCheckResult:
        """Master check -- returns whether a new trade is allowed and why."""
        self._check_date()

        acc = self.mt5.get_account_info()
        if acc is None:
            return RiskCheckResult(False, "Cannot fetch account info")

        # Update peak equity
        if acc.equity > self._today_stats.peak_equity:
            self._today_stats.peak_equity = acc.equity

        # Check daily loss limit
        if self._today_stats.pnl < 0:
            loss_pct = abs(self._today_stats.pnl) / self._today_stats.start_equity * 100
            if loss_pct >= self.config.max_daily_loss_pct:
                logger.warning(
                    "Daily loss limit hit: %.2f%% >= %.2f%% limit",
                    loss_pct, self.config.max_daily_loss_pct,
                )
                return RiskCheckResult(False, f"Daily loss limit hit: {loss_pct:.2f}%")

        # Check open position count
        open_pos = self.mt5.get_open_positions()
        if len(open_pos) >= self.config.max_open_positions:
            return RiskCheckResult(
                False,
                f"Max open positions reached: {len(open_pos)}/{self.config.max_open_positions}",
            )

        # Check total lot size
        total_lots = sum(p["volume"] for p in open_pos)
        if total_lots >= self.config.max_concurrent_lots:
            return RiskCheckResult(
                False,
                f"Max lot size reached: {total_lots:.2f}/{self.config.max_concurrent_lots}",
            )

        return RiskCheckResult(True, "All checks passed")

    def calculate_volume(
        self,
        entry_price: float,
        stop_loss: float,
        symbol: str = "EURUSD",
    ) -> float:
        """Calculate position volume in lots based on risk config and SL distance."""
        acc = self.mt5.get_account_info()
        if acc is None:
            return self.config.fixed_lot_size

        equity = acc.equity
        risk_amount = equity * (self.config.risk_per_trade_pct / 100)

        symbol_info = self.mt5.get_symbol_info(symbol)
        if symbol_info is None:
            logger.warning("Symbol info unavailable for %s, using fixed lot", symbol)
            return self.config.fixed_lot_size

        tick_value = symbol_info.get("tick_value", 0.00001)
        tick_size = symbol_info.get("tick_size", 0.00001)
        contract_size = symbol_info.get("contract_size", 100_000)
        digits = symbol_info.get("digits", 5)

        sl_distance_points = abs(entry_price - stop_loss) / tick_size
        if sl_distance_points == 0:
            return self.config.fixed_lot_size

        # Risk in account currency = SL_points * tick_value * volume * contract_size
        # Solve for volume: volume = risk_amount / (sl_distance_points * tick_value * contract_size)
        # Actually: risk_amount = sl_pips * point_value * lots
        # where point_value = tick_value * contract_size for 1 lot
        point_value_per_lot = tick_value * contract_size
        sl_points = sl_distance_points * tick_size

        if self.config.mode == RiskMode.FIXED_LOT:
            return self.config.fixed_lot_size

        # Fixed risk mode
        volume = risk_amount / (sl_points * contract_size * tick_value + 1e-10)
        volume = max(0.01, min(volume, self.config.max_concurrent_lots))

        # Round down to nearest lot step (usually 0.01)
        volume = round(volume, 2)
        logger.debug(
            "Volume calculated: %.2f lots | Risk: %.2f | SL points: %.0f",
            volume, risk_amount, sl_distance_points,
        )
        return volume

    def check_risk_reward(self, entry: float, sl: float, tp: float) -> bool:
        """Verify a trade meets minimum risk:reward threshold."""
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk == 0:
            return False
        rr = reward / risk
        if rr < self.config.min_risk_reward_ratio:
            logger.debug(
                "Signal rejected: RR=%.2f < minimum %.2f",
                rr, self.config.min_risk_reward_ratio,
            )
            return False
        return True

    def record_trade(self, ticket: int, pnl: float, closed: bool = True) -> None:
        """Update daily stats after a trade closes."""
        self._check_date()
        self._today_stats.trades += 1
        self._today_stats.pnl += pnl
        if pnl > 0:
            self._today_stats.wins += 1
        else:
            self._today_stats.losses += 1

        logger.info(
            "Trade recorded | Ticket: %d | PnL: %.2f | Today: %dW/%dL | Daily PnL: %.2f",
            ticket, pnl,
            self._today_stats.wins, self._today_stats.losses, self._today_stats.pnl,
        )

    def get_daily_summary(self) -> Optional[DailyStats]:
        self._check_date()
        return self._today_stats
