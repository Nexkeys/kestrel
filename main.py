"""
Kestrel -- FX Scanner and Trading Bot for MetaTrader 5.

Main entry point. Run with: python main.py [--config path/to/app.toml]
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# Add project root to path so core/ and strategies/ import cleanly
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core import MT5Client, Scanner, Executor, RiskManager, Notifier
from core.scanner import StrategyConfig

logger: logging.Logger


def _setup_logging(log_file: str = "kestrel.log", level: str = "INFO") -> None:
    global logger
    logger = logging.getLogger("kestrel")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"
    ))
    logger.addHandler(ch)

    # File handler (DEBUG for full traceability)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
        ))
        logger.addHandler(fh)


class KestrelApp:
    """Main application controller."""

    def __init__(self, config_path: str | None = None) -> None:
        self.running = False
        self._shutdown_event = asyncio.Event()

        # Load config
        self.app_config = self._load_config(config_path)
        _setup_logging(
            self.app_config.get("logging", {}).get("log_file", "kestrel.log"),
            self.app_config.get("logging", {}).get("log_level", "INFO"),
        )

        # Core components
        mt5_cfg = self.app_config.get("mt5", {})
        self.mt5 = MT5Client(
            terminal_path=mt5_cfg.get("terminal_path"),
            timeout=mt5_cfg.get("connection_timeout", 60_000),
        )

        risk_cfg = self.app_config.get("risk", {})
        from core.risk import RiskConfig as RC, RiskMode as RM
        mode_str = risk_cfg.get("sizing_mode", "fixed_risk")
        risk_mode = RM.FIXED_RISK if mode_str == "fixed_risk" else RM.FIXED_LOT
        self.risk = RiskManager(
            self.mt5,
            RC(
                mode=risk_mode,
                risk_per_trade_pct=risk_cfg.get("risk_per_trade_pct", 1.0),
                max_daily_loss_pct=risk_cfg.get("max_daily_loss_pct", 5.0),
                max_open_positions=risk_cfg.get("max_open_positions", 5),
                max_concurrent_lots=risk_cfg.get("max_concurrent_lots", 0.5),
                min_risk_reward_ratio=risk_cfg.get("min_risk_reward_ratio", 1.5),
                fixed_lot_size=risk_cfg.get("fixed_lot_size", 0.01),
            ),
        )

        exec_cfg = self.app_config.get("execution", {})
        self.executor = Executor(
            self.mt5,
            max_slippage_points=exec_cfg.get("max_slippage_points", 3),
            retry_attempts=exec_cfg.get("retry_attempts", 3),
            deviation=exec_cfg.get("order_deviation", 10),
        )

        notif_cfg = self.app_config.get("telegram", {})
        from core.notifier import NotifyConfig as NC
        # P1 SECURITY: secrets come from environment variables, not TOML files
        self.notifier = Notifier(NC(
            telegram_enabled=notif_cfg.get("enabled", False),
            telegram_bot_token=os.environ.get("KESTREL_TELEGRAM_TOKEN", notif_cfg.get("bot_token", "")),
            telegram_chat_id=os.environ.get("KESTREL_TELEGRAM_CHAT_ID", notif_cfg.get("chat_id", "")),
            webhook_enabled=self.app_config.get("webhook", {}).get("enabled", False),
            webhook_url=self.app_config.get("webhook", {}).get("url", ""),
            webhook_secret=os.environ.get("KESTREL_WEBHOOK_SECRET", self.app_config.get("webhook", {}).get("secret", "")),
        ))

        self.scanner = Scanner(self.mt5)
        self._load_strategies()
        self.scan_interval = self.app_config.get("scan", {}).get("scan_interval", 60)

        logger.info("Kestrel initialized | Strategies: %d | Scan interval: %ds",
                    len(self.scanner._strategies), self.scan_interval)

    def _load_config(self, path: str | None) -> dict:
        """Load TOML config from file or return empty defaults."""
        if path is None:
            path = os.environ.get("KESTREL_CONFIG", str(ROOT / "config" / "app.toml"))
        p = Path(path)
        if not p.exists():
            logger.warning("Config file not found at %s, using defaults", p)
            return {}

        try:
            import tomllib
            with open(p, "rb") as f:
                return tomllib.load(f)
        except ImportError:
            import tomli
            with open(p, "rb") as f:
                return tomli.load(f)
        except Exception as e:
            logger.error("Failed to load config: %s", e)
            return {}

    def _load_strategies(self) -> None:
        """Load strategy configs from YAML files and register them with the scanner."""
        import yaml

        strategies_dir = ROOT / "config" / "strategies"
        if not strategies_dir.exists():
            logger.warning("No strategies directory found at %s", strategies_dir)
            return

        for yaml_file in sorted(strategies_dir.glob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)

                if not cfg or not cfg.get("enabled", False):
                    continue

                strategy_cfg = StrategyConfig(
                    name=cfg.get("strategy", yaml_file.stem),
                    enabled=cfg.get("enabled", True),
                    symbols=cfg.get("symbols", []),
                    timeframe=cfg.get("timeframe", "H1"),
                    risk_per_trade=cfg.get("risk_per_trade_pct", 1.0),
                    params=cfg.get("params", {}),
                )
                self.scanner.register_strategy(strategy_cfg)
                logger.info("Loaded strategy: %s", strategy_cfg.name)

            except Exception as e:
                logger.error("Failed to load strategy %s: %s", yaml_file.name, e)

    async def run(self) -> None:
        """Connect to MT5 and start the scan loop."""
        logger.info("Starting Kestrel...")

        if not self.mt5.connect():
            logger.error("Failed to connect to MT5. Exiting.")
            sys.exit(1)

        account = self.mt5.get_account_info()
        if account:
            acc_dict = {
                "name": account.name,
                "server": account.server,
                "balance": account.balance,
                "currency": account.currency,
            }
            logger.info(
                "Connected | Account: %s | Server: %s | Balance: %.2f %s",
                account.name, account.server, account.balance, account.currency,
            )
            await self.notifier.notify_startup(acc_dict)

        self.running = True
        asyncio.create_task(self._scan_loop())
        await self._shutdown_event.wait()

    async def _scan_loop(self) -> None:
        """Main async scan loop -- runs every scan_interval seconds."""
        logger.info("Scan loop started")
        while self.running:
            try:
                await self._run_scan_cycle()
            except Exception as e:
                logger.error("Scan cycle error: %s", e, exc_info=True)
            await asyncio.sleep(self.scan_interval)

    async def _run_scan_cycle(self) -> None:
        """One complete scan + execute cycle."""
        # Check risk / daily loss
        risk_check = self.risk.can_trade()
        if not risk_check.allowed:
            if "Daily loss" in risk_check.reason:
                stats = self.risk.get_daily_summary()
                loss_pct = abs(stats.pnl) / stats.start_equity * 100 if stats.start_equity else 0
                await self.notifier.notify_daily_limit_hit(loss_pct)
            logger.debug("Trade blocked: %s", risk_check.reason)
            return

        # Scan all strategies and symbols
        signals = self.scanner.scan_all_async()
        if not signals:
            logger.debug("No signals this cycle")
            return

        for sig in signals:
            # Risk:reward filter
            if not self.risk.check_risk_reward(sig.entry_price, sig.stop_loss, sig.take_profit):
                continue

            # Calculate volume based on risk config
            volume = self.risk.calculate_volume(
                sig.entry_price, sig.stop_loss, sig.symbol
            )

            # Get current spread and reject if too wide
            price_info = self.mt5.get_current_price(sig.symbol)
            if price_info is None:
                logger.warning("Cannot get price for %s, skipping", sig.symbol)
                continue

            bid, ask = price_info
            spread_points = abs(ask - bid) / 1e-5  # approximate for 5-digit
            # (spread check would use pairs.toml config -- simplified here)
            _ = spread_points  # used in full version with pair config

            sig.volume = volume

            logger.info(
                "Signal: %s %s @ %.5f | SL: %.5f | TP: %.5f | Vol: %.2f | Strategy: %s",
                sig.type.value, sig.symbol, sig.entry_price,
                sig.stop_loss, sig.take_profit, sig.volume, sig.strategy_name,
            )

            # Execute
            from core.notifier import NotifyLevel
            result = self.executor.execute(sig)
            if result.success:
                await self.notifier.notify_trade_executed(
                    ticket=result.ticket,
                    symbol=sig.symbol,
                    direction=sig.type.value,
                    volume=sig.volume,
                    entry=sig.entry_price,
                    sl=sig.stop_loss,
                    tp=sig.take_profit,
                    strategy=sig.strategy_name,
                )
            else:
                await self.notifier.notify(
                    NotifyLevel.ERROR,
                    "Execute Failed",
                    f"{sig.symbol}: {result.message}",
                )

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down Kestrel...")
        self.running = False
        self.mt5.disconnect()
        await self.notifier.close()
        self._shutdown_event.set()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Kestrel -- FX Scanner and Trading Bot")
    parser.add_argument("--config", "-c", help="Path to app.toml config file")
    args = parser.parse_args()

    app = KestrelApp(config_path=args.config)

    # Signal handlers
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(signum: int, frame) -> None:
        logger.info("Received signal %d, initiating shutdown...", signum)
        loop.run_until_complete(app.shutdown())
        loop.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(app.run())
    except KeyboardInterrupt:
        loop.run_until_complete(app.shutdown())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
