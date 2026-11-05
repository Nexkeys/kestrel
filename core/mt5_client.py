"""MetaTrader 5 connection and data client."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


class MT5ConnectionStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    INITIALIZED = "initialized"


@dataclass
class MT5AccountInfo:
    login: int
    name: str
    server: str
    currency: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    leverage: int
    margin_so_mode: int


@dataclass
class MTCandle:
    symbol: str
    timeframe: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int

    @classmethod
    def from_mt5_tick(cls, symbol: str, tick: Any) -> "MTCandle":
        return cls(
            symbol=symbol,
            timeframe="M1",
            time=datetime.fromtimestamp(tick.time, tz=timezone.utc),
            open=tick.bid,
            high=tick.bid,
            low=tick.bid,
            close=tick.bid,
            tick_volume=tick.volume,
            spread=int((tick.ask - tick.bid) / tick.digits * 10**tick.digits) if tick.ask and tick.bid else 0,
            real_volume=0,
        )


MT5_TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
}


class MT5Client:
    """Thread-safe MT5 connection wrapper with auto-reconnect."""

    def __init__(
        self,
        terminal_path: Optional[str] = None,
        timeout: int = 60_000,
        retry_attempts: int = 5,
        retry_delay: float = 2.0,
    ) -> None:
        self.terminal_path = terminal_path
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self._status = MT5ConnectionStatus.DISCONNECTED
        self._lock = __import__("threading").Lock()

    @property
    def status(self) -> MT5ConnectionStatus:
        return self._status

    def connect(self) -> bool:
        """Establish connection to MT5 terminal."""
        if self._status == MT5ConnectionStatus.INITIALIZED:
            logger.debug("MT5 already connected and initialized")
            return True

        with self._lock:
            if not mt5.initialize(terminal=self.terminal_path or "", timeout=self.timeout):
                error = mt5.last_error()
                logger.error("MT5 initialize failed: %s [%d]", error[2], error[0])
                self._status = MT5ConnectionStatus.DISCONNECTED
                return False

            logger.info("MT5 terminal initialized successfully")
            self._status = MT5ConnectionStatus.CONNECTED

            if self._initialize_mt5():
                self._status = MT5ConnectionStatus.INITIALIZED
                return True

            return False

    def _initialize_mt5(self) -> bool:
        """Additional MT5 terminal setup and validation."""
        account = mt5.account_info()
        if account is None:
            logger.warning("Could not retrieve MT5 account info: %s", mt5.last_error())
            return False

        logger.info(
            "MT5 Account: %d | Server: %s | Balance: %.2f %s",
            account.login,
            account.server,
            account.balance,
            account.currency,
        )
        return True

    def disconnect(self) -> None:
        """Shutdown MT5 connection cleanly."""
        with self._lock:
            mt5.shutdown()
            self._status = MT5ConnectionStatus.DISCONNECTED
            logger.info("MT5 disconnected")

    def reconnect(self) -> bool:
        """Attempt to reconnect with retries."""
        logger.warning("MT5 connection lost, attempting reconnect...")
        self.disconnect()
        for attempt in range(1, self.retry_attempts + 1):
            logger.info("Reconnect attempt %d/%d", attempt, self.retry_attempts)
            if self.connect():
                return True
            time.sleep(self.retry_delay * attempt)
        logger.error("MT5 reconnect failed after %d attempts", self.retry_attempts)
        return False

    def is_connected(self) -> bool:
        """Check if MT5 is still responsive."""
        try:
            with self._lock:
                return mt5.symbol_info("EURUSD") is not None
        except Exception:
            return False

    def get_account_info(self) -> Optional[MT5AccountInfo]:
        """Retrieve current account information."""
        try:
            with self._lock:
                acc = mt5.account_info()
                if acc is None:
                    return None
                return MT5AccountInfo(
                    login=acc.login,
                    name=acc.name,
                    server=acc.server,
                    currency=acc.currency,
                    balance=acc.balance,
                    equity=acc.equity,
                    margin=acc.margin,
                    free_margin=acc.free_margin,
                    leverage=acc.leverage,
                    margin_so_mode=acc.margin_so_mode,
                )
        except Exception as e:
            logger.error("Failed to get account info: %s", e)
            return None

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 100,
        from_time: Optional[datetime] = None,
    ) -> list[MTCandle]:
        """Fetch historical OHLCV candles for a symbol."""
        tf = MT5_TIMEFRAMES.get(timeframe.upper())
        if tf is None:
            logger.error("Unknown timeframe: %s", timeframe)
            return []

        try:
            with self._lock:
                if from_time:
                    rates = mt5.copy_rates_from(symbol, tf, from_time)
                else:
                    rates = mt5.copy_rates(symbol, tf, count)
                if rates is None or len(rates) == 0:
                    logger.warning("No candles returned for %s %s", symbol, timeframe)
                    return []
        except Exception as e:
            logger.error("Failed to fetch candles for %s: %s", symbol, e)
            return []

        return [
            MTCandle(
                symbol=symbol,
                timeframe=timeframe,
                time=datetime.fromtimestamp(r.time, tz=timezone.utc),
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                tick_volume=r.tick_volume,
                spread=int(r.spread),
                real_volume=r.real_volume,
            )
            for r in rates
        ]

    def get_current_price(self, symbol: str) -> Optional[tuple[float, float]]:
        """Return (bid, ask) for a symbol."""
        try:
            with self._lock:
                info = mt5.symbol_info(symbol)
                if info is None:
                    return None
                return info.bid, info.ask
        except Exception as e:
            logger.error("Failed to get price for %s: %s", symbol, e)
            return None

    def get_symbol_info(self, symbol: str) -> Optional[dict[str, Any]]:
        """Return full symbol info dict."""
        try:
            with self._lock:
                info = mt5.symbol_info(symbol)
                if info is None:
                    return None
                return {
                    "symbol": info.name,
                    "bid": info.bid,
                    "ask": info.ask,
                    "spread": info.spread,
                    "digits": info.digits,
                    "tick_value": info.trade_tick_value,
                    "tick_size": info.trade_tick_value_profit,
                    "contract_size": info.trade_contract_size,
                    "volume_min": info.volume_min,
                    "volume_max": info.volume_max,
                    "volume_step": info.volume_step,
                }
        except Exception as e:
            logger.error("Failed to get symbol info for %s: %s", symbol, e)
            return None

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Return all open positions."""
        try:
            with self._lock:
                positions = mt5.positions_get()
                if positions is None:
                    return []
                return [self._position_to_dict(p) for p in positions]
        except Exception as e:
            logger.error("Failed to get positions: %s", e)
            return []

    def _position_to_dict(self, pos: Any) -> dict[str, Any]:
        return {
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "type": "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL",
            "volume": pos.volume,
            "price_open": pos.price_open,
            "price_current": pos.price_current,
            "sl": pos.sl,
            "tp": pos.tp,
            "profit": pos.profit,
            "time": datetime.fromtimestamp(pos.time, tz=timezone.utc),
        }

    def get_orders(self) -> list[dict[str, Any]]:
        """Return all pending orders."""
        try:
            with self._lock:
                orders = mt5.orders_get()
                if orders is None:
                    return []
                return [self._order_to_dict(o) for o in orders]
        except Exception as e:
            logger.error("Failed to get orders: %s", e)
            return []

    def _order_to_dict(self, order: Any) -> dict[str, Any]:
        return {
            "ticket": order.ticket,
            "symbol": order.symbol,
            "type": order.type,
            "volume": order.volume_current,
            "price_open": order.price_open,
            "sl": order.sl,
            "tp": order.tp,
            "state": order.state,
            "time_setup": datetime.fromtimestamp(order.time_setup, tz=timezone.utc),
        }
