"""Notification system -- Telegram alerts, console logging, and webhooks."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import aiohttp
import re

logger = logging.getLogger(__name__)


class NotifyLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


def _escape_markdown(text: str) -> str:
    """Escape Telegram MarkdownV1 special characters to prevent injection."""
    return re.sub(r"[*_`[]()>", r"\\\g<0>", str(text))


TELEGRAM_EMOJI = {
    NotifyLevel.DEBUG: "[DEBUG]",
    NotifyLevel.INFO: "[INFO]",
    NotifyLevel.SUCCESS: "[OK]",
    NotifyLevel.WARNING: "[WARN]",
    NotifyLevel.ERROR: "[ERROR]",
}


@dataclass
class NotifyConfig:
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_secret: str = ""
    min_level: NotifyLevel = NotifyLevel.INFO


class Notifier:
    """Sends formatted notifications via Telegram, webhook, or standard logging."""

    def __init__(self, config: Optional[NotifyConfig] = None) -> None:
        self.config = config or NotifyConfig()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def send_telegram(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send a message via Telegram Bot API."""
        if not self.config.telegram_enabled or not self.config.telegram_bot_token:
            return False

        url = (
            f"https://api.telegram.org/bot{self.config.telegram_bot_token}"
            f"/sendMessage"
        )
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }

        try:
            session = await self._get_session()
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.debug("Telegram notification sent")
                    return True
                body = await resp.text()
                logger.warning("Telegram send failed (%d): %s", resp.status, body)
                return False
        except Exception as e:
            logger.error("Telegram notification error: %s", e)
            return False

    async def send_webhook(self, payload: dict[str, Any], signed: bool = True) -> bool:
        """POST JSON to a webhook URL, optionally with HMAC-SHA256 signature."""
        import hmac
        import hashlib

        if not self.config.webhook_enabled or not self.config.webhook_url:
            return False

        body = __import__("json").dumps(payload)
        headers = {"Content-Type": "application/json"}

        if signed and self.config.webhook_secret:
            sig = hmac.new(
                self.config.webhook_secret.encode(),
                body.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Kestrel-Signature"] = sig

        try:
            session = await self._get_session()
            async with session.post(
                self.config.webhook_url,
                data=body.encode(),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status < 300:
                    logger.debug("Webhook delivered: %s", self.config.webhook_url)
                    return True
                resp_body = await resp.text()
                logger.warning("Webhook failed (%d): %s", resp.status, resp_body)
                return False
        except Exception as e:
            logger.error("Webhook error: %s", e)
            return False

    async def notify(
        self,
        level: NotifyLevel,
        title: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Send a notification through all configured channels."""
        emoji = TELEGRAM_EMOJI.get(level, "")
        safe_title = _escape_markdown(title)
        safe_message = _escape_markdown(message)
        formatted = f"{emoji} *{safe_title}*\n{safe_message}"
        if details:
            formatted += "\n\n" + "\n".join(f"`{k}`: {v}" for k, v in details.items())

        # Always log
        log_fn = {
            NotifyLevel.DEBUG: logger.debug,
            NotifyLevel.INFO: logger.info,
            NotifyLevel.SUCCESS: logger.info,
            NotifyLevel.WARNING: logger.warning,
            NotifyLevel.ERROR: logger.error,
        }.get(level, logger.info)
        log_fn("%s %s", title, message)

        # Telegram (async)
        if self.config.telegram_enabled:
            await self.send_telegram(formatted)

        # Webhook (async)
        if self.config.webhook_enabled:
            await self.send_webhook({
                "level": level.value,
                "title": title,
                "message": message,
                "details": details,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    async def notify_trade_executed(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        volume: float,
        entry: float,
        sl: float,
        tp: float,
        strategy: str,
    ) -> None:
        await self.notify(
            NotifyLevel.SUCCESS,
            f"Trade Opened: {direction} {symbol}",
            f"Entry: {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Vol: {volume:.2f}",
            {"ticket": ticket, "strategy": strategy},
        )

    async def notify_trade_closed(
        self,
        ticket: int,
        symbol: str,
        pnl: float,
        reason: str,
    ) -> None:
        level = NotifyLevel.SUCCESS if pnl > 0 else NotifyLevel.ERROR
        await self.notify(
            level,
            f"Trade Closed: {symbol}",
            f"PnL: {pnl:+.2f} | Reason: {reason}",
            {"ticket": ticket},
        )

    async def notify_signal_found(
        self,
        symbol: str,
        strategy: str,
        direction: str,
        confidence: float,
        entry: float,
    ) -> None:
        await self.notify(
            NotifyLevel.INFO,
            f"Signal: {direction} {symbol}",
            f"Strategy: {strategy} | Entry: {entry:.5f} | Confidence: {confidence:.0%}",
        )

    async def notify_risk_blocked(self, reason: str) -> None:
        await self.notify(
            NotifyLevel.WARNING,
            "Trade Blocked (Risk)",
            reason,
        )

    async def notify_daily_limit_hit(self, loss_pct: float) -> None:
        await self.notify(
            NotifyLevel.ERROR,
            "Daily Loss Limit Hit",
            f"Drawdown: {loss_pct:.2f}% | All trading halted.",
        )

    async def notify_startup(self, account_info: dict[str, Any]) -> None:
        await self.notify(
            NotifyLevel.INFO,
            "Kestrel Started",
            f"Account: {account_info.get('name','?')} ({account_info.get('server','?')}) | "
            f"Balance: {account_info.get('balance',0):.2f} {account_info.get('currency','')}",
        )
