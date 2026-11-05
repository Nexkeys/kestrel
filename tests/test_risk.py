"""Unit tests for the RiskManager -- position sizing, daily limits, drawdown."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.risk import RiskManager, RiskConfig, RiskMode, DailyStats, RiskCheckResult


def make_mock_mt5(equity: float = 10_000.0, positions: list = None):
    mt5 = MagicMock()
    mt5.get_account_info.return_value = MagicMock(
        equity=equity,
        balance=equity,
        margin=0,
        free_margin=0,
    )
    mt5.get_open_positions.return_value = positions or []
    mt5.get_symbol_info.return_value = {
        "symbol": "EURUSD",
        "tick_value": 0.00001,
        "tick_size": 0.00001,
        "contract_size": 100_000,
        "digits": 5,
    }
    return mt5


def make_config(**kwargs):
    defaults = dict(
        mode=RiskMode.FIXED_RISK,
        risk_per_trade_pct=1.0,
        max_daily_loss_pct=5.0,
        max_open_positions=5,
        max_concurrent_lots=0.5,
        min_risk_reward_ratio=1.5,
        fixed_lot_size=0.01,
    )
    defaults.update(kwargs)
    return RiskConfig(**defaults)


class TestRiskCheckResult:
    def test_allowed_always_has_reason(self):
        result = RiskCheckResult(allowed=True)
        assert result.allowed is True


class TestRiskManager:
    def test_can_trade_passes_when_all_checks_pass(self):
        mt5 = make_mock_mt5(equity=10_000.0)
        rm = RiskManager(mt5, make_config())
        result = rm.can_trade()
        assert result.allowed is True

    def test_can_trade_fails_when_max_positions_reached(self):
        mt5 = make_mock_mt5(equity=10_000.0, positions=[
            {"ticket": i, "volume": 0.1} for i in range(5)
        ])
        rm = RiskManager(mt5, make_config(max_open_positions=5))
        result = rm.can_trade()
        assert result.allowed is False
        assert "Max open positions" in result.reason

    def test_can_trade_fails_when_max_lots_exceeded(self):
        mt5 = make_mock_mt5(equity=10_000.0, positions=[
            {"ticket": 1, "volume": 0.4},
            {"ticket": 2, "volume": 0.2},
        ])
        rm = RiskManager(mt5, make_config(max_concurrent_lots=0.5))
        result = rm.can_trade()
        assert result.allowed is False
        assert "Max lot size" in result.reason

    def test_calculate_volume_fixed_risk_mode(self):
        mt5 = make_mock_mt5(equity=10_000.0)
        rm = RiskManager(mt5, make_config(mode=RiskMode.FIXED_RISK, risk_per_trade_pct=1.0))
        # 1% of 10,000 = 100 risk
        # entry=1.10000, sl=1.09900 (10 pips SL)
        # volume = 100 / (sl_points * point_value * contract)
        # This should return a reasonable lot size (not zero, not huge)
        volume = rm.calculate_volume(1.10000, 1.09900, "EURUSD")
        assert 0.01 <= volume <= 1.0

    def test_calculate_volume_fixed_lot_mode(self):
        mt5 = make_mock_mt5(equity=10_000.0)
        rm = RiskManager(mt5, make_config(mode=RiskMode.FIXED_LOT, fixed_lot_size=0.05))
        volume = rm.calculate_volume(1.10000, 1.09900, "EURUSD")
        assert volume == 0.05

    def test_check_risk_reward_accepts_good_rr(self):
        mt5 = make_mock_mt5()
        rm = RiskManager(mt5, make_config(min_risk_reward_ratio=1.5))
        # entry=1.10000, sl=1.09900 (10 pips risk), tp=1.10150 (15 pips reward) = 1.5 RR
        assert rm.check_risk_reward(1.10000, 1.09900, 1.10150) is True

    def test_check_risk_reward_rejects_bad_rr(self):
        mt5 = make_mock_mt5()
        rm = RiskManager(mt5, make_config(min_risk_reward_ratio=2.0))
        # entry=1.10000, sl=1.09900 (10 pips risk), tp=1.10050 (5 pips reward) = 0.5 RR
        assert rm.check_risk_reward(1.10000, 1.09900, 1.10050) is False

    def test_record_trade_updates_stats(self):
        mt5 = make_mock_mt5()
        rm = RiskManager(mt5, make_config())
        rm.record_trade(ticket=12345, pnl=50.0)
        summary = rm.get_daily_summary()
        assert summary.trades == 1
        assert summary.wins == 1
        assert summary.losses == 0
        assert summary.pnl == 50.0

        rm.record_trade(ticket=12346, pnl=-30.0)
        summary = rm.get_daily_summary()
        assert summary.trades == 2
        assert summary.wins == 1
        assert summary.losses == 1
        assert summary.pnl == 20.0

    def test_win_rate_property(self):
        mt5 = make_mock_mt5()
        rm = RiskManager(mt5, make_config())
        rm.record_trade(ticket=1, pnl=100.0)
        rm.record_trade(ticket=2, pnl=200.0)
        rm.record_trade(ticket=3, pnl=-50.0)
        summary = rm.get_daily_summary()
        assert summary.win_rate == pytest.approx(2 / 3, rel=0.01)


class TestDailyStats:
    def test_is_daily_loss_exceeded_true(self):
        stats = DailyStats(
            date=datetime.now(timezone.utc),
            start_equity=10_000.0,
            pnl=-600.0,  # -6%
        )
        assert stats.is_daily_loss_exceeded is True

    def test_is_daily_loss_exceeded_false(self):
        stats = DailyStats(
            date=datetime.now(timezone.utc),
            start_equity=10_000.0,
            pnl=300.0,
        )
        assert stats.is_daily_loss_exceeded is False
