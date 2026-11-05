"""Trade execution engine -- places, modifies, and closes orders via MT5."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import MetaTrader5 as mt5

from .scanner import Signal

logger = logging.getLogger(__name__)


class OrderType(Enum):
    MARKET_BUY = mt5.ORDER_TYPE_BUY
    MARKET_SELL = mt5.ORDER_TYPE_SELL
    LIMIT_BUY = mt5.ORDER_TYPE_BUY_LIMIT
    LIMIT_SELL = mt5.ORDER_TYPE_SELL_LIMIT
    STOP_BUY = mt5.ORDER_TYPE_BUY_STOP
    STOP_SELL = mt5.ORDER_TYPE_SELL_STOP


@dataclass
class ExecutionResult:
    success: bool
    ticket: Optional[int] = None
    message: str = ""
    order_info: Optional[dict[str, Any]] = None
    error_code: Optional[int] = None
    timestamp: datetime = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class Executor:
    """Places and manages trades on MT5 with SL/TP attached at order level."""

    def __init__(
        self,
        mt5_client: Any,  # MT5Client instance
        max_slippage_points: int = 3,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        deviation: int = 10,
    ) -> None:
        self.mt5 = mt5_client
        self.max_slippage = max_slippage_points
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.deviation = deviation
        self._lock = __import__("threading").Lock()

    def execute(self, signal: Signal) -> ExecutionResult:
        """Execute a BUY or SELL market order with SL and TP attached."""
        if not signal.is_valid():
            return ExecutionResult(False, message="Invalid signal: validation failed")

        logger.info(
            "Executing %s %s @ %.5f | SL: %.5f | TP: %.5f | Vol: %.2f",
            signal.type.value, signal.symbol, signal.entry_price,
            signal.stop_loss, signal.take_profit, signal.volume,
        )

        order_type = (
            OrderType.MARKET_BUY
            if signal.type.value == "BUY"
            else OrderType.MARKET_SELL
        )

        for attempt in range(1, self.retry_attempts + 1):
            result = self._place_order(
                symbol=signal.symbol,
                order_type=order_type,
                volume=signal.volume,
                price=0,  # market order
                sl=signal.stop_loss,
                tp=signal.take_profit,
                comment=f"kestrel:{signal.strategy_name}",
            )
            if result.success:
                logger.info(
                    "Order placed successfully | Ticket: %d | %s %s",
                    result.ticket, signal.type.value, signal.symbol,
                )
                return result

            logger.warning(
                "Execute attempt %d/%d failed for %s: %s",
                attempt, self.retry_attempts, signal.symbol, result.message,
            )
            if attempt < self.retry_attempts:
                time.sleep(self.retry_delay * attempt)

        logger.error("All execute attempts exhausted for %s", signal.symbol)
        return ExecutionResult(False, message=f"All {self.retry_attempts} attempts failed")

    def _place_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        sl: float,
        tp: float,
        comment: str = "",
    ) -> ExecutionResult:
        """Internal order placement via MT5."""
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type.value,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.deviation,
            "comment": comment,
            "type_filling": mt5.ORDER_FILLING_IOC,
            "type_time": mt5.ORDER_TIME_GTC,
        }

        with self._lock:
            result = mt5.order_send(request)
            if result is None:
                err = mt5.last_error()
                return ExecutionResult(
                    False,
                    message=f"order_send returned None: {err[2]}",
                    error_code=err[0],
                )
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return ExecutionResult(
                    False,
                    message=f"MT5 returned error {result.retcode}: {result.comment}",
                    error_code=result.retcode,
                )

            return ExecutionResult(
                success=True,
                ticket=result.order,
                message=f"Order {result.order} placed",
                order_info={
                    "ticket": result.order,
                    "retcode": result.retcode,
                    "deal": result.deal,
                    "volume": result.volume,
                    "price": result.price,
                },
            )

    def close_position(self, ticket: int, volume: Optional[float] = None) -> ExecutionResult:
        """Close an open position by ticket (full or partial)."""
        positions = self.mt5.get_open_positions()
        pos = next((p for p in positions if p["ticket"] == ticket), None)
        if pos is None:
            return ExecutionResult(False, message=f"Position {ticket} not found")

        order_type = (
            OrderType.MARKET_SELL
            if pos["type"] == "BUY"
            else OrderType.MARKET_BUY
        )
        close_volume = volume or pos["volume"]

        logger.info("Closing position %d | Type: %s | Vol: %.2f", ticket, order_type, close_volume)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos["symbol"],
            "volume": close_volume,
            "type": order_type.value,
            "price": 0,
            "deviation": self.deviation,
            "comment": f"kestrel_close:{ticket}",
            "position": ticket,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        with self._lock:
            result = mt5.order_send(request)
            if result is None:
                err = mt5.last_error()
                return ExecutionResult(False, message=err[2], error_code=err[0])
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return ExecutionResult(False, message=result.comment, error_code=result.retcode)

            logger.info("Position %d closed | Deal: %d", ticket, result.deal)
            return ExecutionResult(
                success=True,
                ticket=ticket,
                message=f"Position closed, deal {result.deal}",
            )

    def modify_position(self, ticket: int, new_sl: float, new_tp: float) -> ExecutionResult:
        """Update SL and TP on an open position."""
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
            "type_time": mt5.ORDER_TIME_GTC,
        }

        with self._lock:
            result = mt5.order_send(request)
            if result is None:
                err = mt5.last_error()
                return ExecutionResult(False, message=err[2])
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return ExecutionResult(False, message=result.comment, error_code=result.retcode)

            logger.info("Position %d modified | New SL: %.5f | New TP: %.5f", ticket, new_sl, new_tp)
            return ExecutionResult(True, ticket=ticket, message="SL/TP updated")

    def close_all_profitable(self, min_profit: float = 10.0) -> list[ExecutionResult]:
        """Close all positions with profit above min_profit."""
        results = []
        positions = self.mt5.get_open_positions()
        for pos in positions:
            if pos["profit"] >= min_profit:
                res = self.close_position(pos["ticket"])
                results.append(res)
        return results
