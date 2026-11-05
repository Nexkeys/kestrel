# Kestrel
### FX Scanner and Trading Bot for MetaTrader 5

> A production-grade, automated trading system that scans a configurable pool of forex pairs, finds setups matching your strategies within a configurable interval, and executes trades with automatic stop loss and take profit -- all from a single Python codebase.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Usage](#usage)
7. [Strategies](#strategies)
8. [Risk Management](#risk-management)
9. [Notifications](#notifications)
10. [Docker Deployment](#docker-deployment)
11. [Project Structure](#project-structure)
12. [Adding a New Strategy](#adding-a-new-strategy)
13. [Troubleshooting](#troubleshooting)
14. [Disclaimer](#disclaimer)

---

## Overview

Kestrel is an automated FX trading bot that:

- Connects directly to your MetaTrader 5 (MT5) terminal via the official Python API
- Scans multiple forex pairs across multiple timeframes using plug-in strategies
- Generates trade signals and executes them market-side with SL/TP attached at order level
- Enforces daily loss limits, position sizing rules, and risk:reward filters
- Sends real-time alerts via Telegram or webhook
- Runs 24/7 on a Windows VPS or inside Docker

**Key characteristics:**
- Fast: async scanner loop with thread-safe MT5 execution
- Smart: plug-in strategy system, configurable pair pools, risk rules
- Solid: no TODOs in code, full error handling, graceful shutdown, retry logic
- Audit-ready: structured logging to file and console

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Kestrel                                     │
│                                                                       │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────────┐  │
│  │  main.py    │───>│   Scanner    │───>│   Strategy Plugins      │  │
│  │  (entry pt) │    │  (async loop) │    │  - EMA_Cross            │  │
│  └──────┬──────┘    └──────┬───────┘    │  - RSI_Range            │  │
│         │                  │            │  - BB_Breakout          │  │
│         │                  v            └────────────────────────┘  │
│         │         ┌─────────────────┐                               │
│         │         │  RiskManager    │                               │
│         │         │ (position sizing│                               │
│         │         │  daily limits)  │                               │
│         │         └────────┬────────┘                               │
│         │                  │                                        │
│         v                  v                                        │
│  ┌──────────────┐    ┌──────────────────┐    ┌─────────────────┐   │
│  │ MT5Client   │<───│    Executor       │───>│  MT5 Terminal   │   │
│  │ (IPC connect│    │  (market orders   │    │  (your broker)  │   │
│  │  thread-safe)│    │   SL/TP attach)   │    └─────────────────┘   │
│  └──────────────┘    └──────────────────┘                          │
│                              │                                        │
│                              v                                        │
│                     ┌──────────────────┐                              │
│                     │    Notifier      │                              │
│                     │ (Telegram,       │                              │
│                     │  webhook, log)   │                              │
│                     └──────────────────┘                              │
└──────────────────────────────────────────────────────────────────────┘
```

**Data flow:**

1. `Scanner` loads strategy configs and pair pools at startup
2. Every `scan_interval` seconds, `Scanner` fetches OHLCV candles for each (symbol, timeframe) pair
3. Each strategy computes indicators (EMA, RSI, Bollinger, ATR, etc.) on the candles
4. Each strategy evaluates indicators and returns a `Signal` if entry conditions are met
5. `RiskManager` validates the signal against daily loss limits, open position counts, and min risk:reward ratio
6. `Executor` calculates the correct lot size and places a market order with SL/TP attached
7. `Notifier` sends a Telegram/webhook alert and logs the execution result

---

## Requirements

| Requirement | Details |
|-------------|---------|
| Python | 3.10 or higher |
| MT5 Terminal | MT5 must be installed and running on the host (Windows) or a remote Windows VPS |
| Broker | Any broker supporting MT5 (IC Markets, Exness, etc.) |
| OS | Windows VPS recommended for 24/7 operation. Linux via Docker (with MT5 on host) |
| Network | MT5 terminal must have internet access to your broker's servers |

---

## Installation

### Option A -- Direct (Windows)

```bash
# Clone the project
git clone https://github.com/your-org/kestrel.git
cd kestrel

# Create a virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and edit the config
copy config\app.toml.example config\app.toml
# Edit config\app.toml with your MT5 path, broker details, and strategy settings
```

### Option B -- Docker

```bash
# Build
docker build -t kestrel:latest .

# Run (MT5 terminal must be accessible)
docker run -d \
  --name kestrel \
  -v /path/to/mt5:/app/mt5:ro \
  -e KESTREL_CONFIG=/app/config/app.toml \
  kestrel:latest
```

---

## Configuration

All configuration is done through TOML files in `config/`.

### `config/app.toml` (copy from `app.toml.example`)

```toml
[scan]
scan_interval = 60        # seconds between scan cycles

[mt5]
terminal_path = ""        # leave empty for default MT5 install path
connection_timeout = 60000

[risk]
risk_per_trade_pct = 1.0  # % of equity risked per trade
max_daily_loss_pct = 5.0  # halt trading if daily loss reaches this %
max_open_positions = 5    # max concurrent positions
min_risk_reward_ratio = 1.5

[telegram]
enabled = false
bot_token = ""            # from @BotFather
chat_id = ""              # from @userinfobot
```

### `config/pairs.toml`

Defines which forex pairs to scan. Each entry can specify spread limits.

### `config/strategies/*.yaml`

One YAML file per strategy. Example:

```yaml
strategy: "EMA_Cross"
enabled: true
timeframe: "H1"
symbols:
  - "EURUSD"
  - "GBPUSD"
params:
  fast_ema_period: 8
  slow_ema_period: 21
  min_signal_strength: 0.6
```

---

## Usage

```bash
# Run with default config path (config/app.toml)
python main.py

# Run with a custom config
python main.py --config /path/to/custom.toml
```

Expected output on startup:

```
14:32:01 | INFO     | Kestrel initialized | Strategies: 3 | Scan interval: 60s
14:32:01 | INFO     | MT5 terminal initialized successfully
14:32:01 | INFO     | MT5 Account: 12345678 | Server: ICMarkets-Demo | Balance: 10000.00 USD
14:32:01 | INFO     | Scan loop started
14:32:01 | INFO     | Signal: BUY EURUSD @ 1.08521 | SL: 1.08350 | TP: 1.09121 | Vol: 0.10 | Strategy: EMA_Cross
14:32:01 | SUCCESS  | Trade Opened: BUY EURUSD | Entry: 1.08521 | SL: 1.08350 | TP: 1.09121 | Vol: 0.10
```

---

## Strategies

Kestrel ships with three built-in strategies. Each can be enabled/disabled and configured via YAML.

### EMA Cross (`EMA_Cross`)

**Logic:** BUY when the fast EMA (default 8) crosses above the slow EMA (default 21). SELL on the reverse crossover.

**Parameters:**
| Param | Default | Description |
|-------|---------|-------------|
| `fast_ema_period` | 8 | Fast EMA lookback period |
| `slow_ema_period` | 21 | Slow EMA lookback period |
| `min_signal_strength` | 0.6 | Minimum gap between EMAs as a percentage of slow price |

**Stop/Take:** SL = entry - ATR*1.5 | TP = entry + ATR*3.0

---

### RSI Range (`RSI_Range`)

**Logic:** BUY when RSI crosses above the oversold level (default 30). SELL when RSI crosses below overbought (default 70).

**Parameters:**
| Param | Default | Description |
|-------|---------|-------------|
| `rsi_period` | 14 | RSI lookback period |
| `oversold` | 30.0 | BUY trigger threshold |
| `overbought` | 70.0 | SELL trigger threshold |

**Stop/Take:** SL = entry - ATR*2 | TP = entry + ATR*4

---

### Bollinger Breakout (`BB_Breakout`)

**Logic:** BUY when price closes above the upper Bollinger Band on above-average volume. SELL on the reverse.

**Parameters:**
| Param | Default | Description |
|-------|---------|-------------|
| `bb_period` | 20 | SMA period for middle band |
| `bb_std` | 2.0 | Standard deviations for outer bands |
| `volume_multiplier` | 1.5 | Volume must exceed this multiple of the 20-bar average |

**Stop/Take:** SL = entry - ATR*2 | TP = entry + ATR*4

---

## Risk Management

Kestrel's `RiskManager` enforces the following rules on every signal:

| Rule | Default | Config key |
|------|---------|-----------|
| Max daily loss | 5% of starting equity | `max_daily_loss_pct` |
| Max open positions | 5 | `max_open_positions` |
| Max concurrent lot size | 0.5 lots | `max_concurrent_lots` |
| Min risk:reward ratio | 1.5 | `min_risk_reward_ratio` |
| Position sizing | % of equity | `sizing_mode = "fixed_risk"` |

If the daily loss limit is hit, the bot halts all trading and sends a critical alert.

---

## Notifications

### Telegram

1. Create a bot via `@BotFather` on Telegram
2. Get your chat ID via `@userinfobot`
3. Set in `app.toml`:

```toml
[telegram]
enabled = true
bot_token = "123456:ABC-DEF..."
chat_id = "987654321"
```

### Webhook

```toml
[webhook]
enabled = true
url = "https://your-webhook-server.com/kestrel"
secret = "your-hmac-secret"
```

All webhook payloads include an `X-Kestrel-Signature` header with HMAC-SHA256.

---

## Docker Deployment

### Recommended: Docker Compose

```yaml
version: "3.8"
services:
  kestrel:
    build: .
    container_name: kestrel
    restart: unless-stopped
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs
      # Mount MT5 terminal folder from host into container
      - /path/to/MT5:/app/mt5:ro
    environment:
      - KESTREL_CONFIG=/app/config/app.toml
      - PYTHONUNBUFFERED=1
    env_file:
      - .env  # optional: contains bot_token, etc.
```

### Important: MT5 Must Be Running

The Docker container communicates with the MT5 terminal via IPC. MT5 itself is a Windows application and must be running on the host machine or a remote Windows VPS. The container just needs to be able to reach the MT5 IPC pipe or share the MT5 install directory.

For pure Linux 24/7 operation, consider a broker that exposes a REST API (e.g., IC Markets cTrader, OANDA) and swap `mt5_client.py` for an HTTP-based client.

---

## Project Structure

```
kestrel/
  config/
    app.toml.example         # main config template
    pairs.toml               # forex pair pool
    strategies/
      ema_cross.yaml         # EMA Cross strategy config
      rsi_range.yaml          # RSI Range strategy config
      bollinger_breakout.yaml # BB Breakout strategy config
  core/
    __init__.py
    mt5_client.py             # MT5 connection wrapper (thread-safe)
    scanner.py                # signal generation engine
    executor.py               # order execution, SL/TP attachment
    risk.py                   # position sizing, daily loss limits
    notifier.py               # Telegram + webhook notifications
  strategies/
    __init__.py
    base.py                   # BaseStrategy abstract class
    ema_cross.py              # EMA Crossover implementation
    rsi_range.py              # RSI Range implementation
    bollinger_breakout.py     # Bollinger Breakout implementation
  tests/
    test_scanner.py           # unit tests for signal generation
    test_risk.py              # unit tests for risk calculations
    test_strategies.py        # unit tests for strategy logic
  main.py                     # entry point, app lifecycle
  Dockerfile                  # multi-stage Docker build
  requirements.txt            # Python dependencies
  README.md                   # this file
```

---

## Adding a New Strategy

1. **Create the strategy file** in `strategies/`:

```python
# strategies/my_strategy.py
from typing import Any, Optional
import pandas as pd
from core.scanner import Signal, SignalType
from strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    name = "My_Strategy"

    def load_config(self, config: dict[str, Any]) -> None:
        self.config = config

    def get_indicators(self, candles: pd.DataFrame) -> dict[str, Any]:
        # compute your indicators here
        return {}

    def evaluate(self, candles, indicators, config) -> Optional[Signal]:
        # return Signal(...) if entry found, else None
        return None
```

2. **Register it in `core/scanner.py`** inside `_load_strategy_instance()`:

```python
from strategies.my_strategy import MyStrategy
registry = {
    "EMA_Cross": EMACrossStrategy,
    "RSI_Range": RSIRangeStrategy,
    "BB_Breakout": BollingerBreakoutStrategy,
    "My_Strategy": MyStrategy,  # add here
}
```

3. **Create a config file** in `config/strategies/my_strategy.yaml`:

```yaml
strategy: "My_Strategy"
enabled: true
timeframe: "H1"
symbols: ["EURUSD", "GBPUSD"]
params:
  my_param: 42
```

4. Restart Kestrel. The strategy loads automatically from the YAML file.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `MT5 initialize failed` | Make sure MT5 terminal is running and reachable |
| `No candles returned` | Check symbol name is correct (e.g., "EURUSD" not "EURUSDm") |
| `Order rejected: requote` | Spread is too wide or market is gapping. Retry logic handles this. |
| `Daily loss limit already hit` | Reset the bot after the trading day or increase `max_daily_loss_pct` |
| `Telegram messages not arriving` | Verify `bot_token` and `chat_id` are correct |
| Docker: MT5 IPC not working | Mount the MT5 folder from host: `-v /path/to/mt5:/app/mt5:ro` |

---

## Disclaimer

**Trading forex and CFDs carries a high level of risk and may not be suitable for all investors.** Past performance is not indicative of future results. Kestrel is a tool for experienced traders and developers. Use at your own risk. The authors and contributors of this project accept no liability for any financial losses incurred while using this software. Always test on a demo account before going live.

---

*Kestrel v1.0.0 -- Built for speed, precision, and reliability.*
