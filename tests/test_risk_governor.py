"""Tests for Risk Governor."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tsxbot.config_loader import RiskConfig, SymbolsConfig, SymbolSpecConfig
from tsxbot.risk.risk_governor import RiskGovernor


@pytest.fixture
def risk_config():
    return RiskConfig(
        daily_loss_limit_usd=500.0,
        max_loss_limit_usd=1000.0,  # Drawdown limit
        max_risk_per_trade_usd=200.0,
        max_contracts_es=2,
        max_contracts_mes=10,
        max_trades_per_day=5,
        kill_switch=False,
    )


@pytest.fixture
def symbols_config():
    return SymbolsConfig(
        primary="ES",
        micros="MES",
        es=SymbolSpecConfig(tick_size=Decimal("0.25"), tick_value=Decimal("12.50")),
        mes=SymbolSpecConfig(tick_size=Decimal("0.25"), tick_value=Decimal("1.25")),
    )


class TestRiskGovernor:
    def test_initial_state(self, risk_config, symbols_config):
        governor = RiskGovernor(risk_config, symbols_config)
        assert governor.state.trade_count == 0
        assert governor.state.kill_switch_active is False
        allowed, _ = governor.can_trade()
        assert allowed is True

    def test_daily_loss_limit(self, risk_config, symbols_config):
        governor = RiskGovernor(risk_config, symbols_config)

        # Loss of 400 (Limit 500)
        governor.update_account_status(Decimal("49600"), Decimal("-400"))
        allowed, _ = governor.can_trade()
        assert allowed is True

        # Loss of 501 (Limit 500)
        governor.update_account_status(Decimal("49499"), Decimal("-501"))
        assert governor.state.kill_switch_active is True
        allowed, reason = governor.can_trade()
        assert allowed is False
        assert "Kill switch active" in reason

    def test_drawdown_limit(self, risk_config, symbols_config):
        governor = RiskGovernor(risk_config, symbols_config)

        # Initial balance 50000 -> HWM
        governor.update_account_status(Decimal("50000"), Decimal("0"))

        # Profit -> HWM 51000
        governor.update_account_status(Decimal("51000"), Decimal("1000"))
        assert governor.state.high_water_mark == Decimal("51000")

        # Drawdown 900 (Limit 1000) -> Balance 50100
        governor.update_account_status(Decimal("50100"), Decimal("100"))
        assert governor.state.current_drawdown == Decimal("900")
        allowed, _ = governor.can_trade()
        assert allowed is True

        # Drawdown 1100 -> Balance 49900
        governor.update_account_status(Decimal("49900"), Decimal("-100"))
        assert governor.state.current_drawdown == Decimal("1100")
        assert governor.state.kill_switch_active is True
        allowed, _ = governor.can_trade()
        assert allowed is False

    def test_max_trades_per_day(self, risk_config, symbols_config):
        governor = RiskGovernor(risk_config, symbols_config)

        # Record 4 trades (Limit 5)
        for _ in range(4):
            governor.record_trade_execution()
            allowed, _ = governor.can_trade()
            assert allowed is True

        # Record 5th trade
        governor.record_trade_execution()
        assert governor.state.trade_count == 5

        # Next check should fail
        allowed, reason = governor.can_trade()
        assert allowed is False
        assert "Max trades" in reason
        # But kill switch NOT active (soft stop)
        assert governor.state.kill_switch_active is False

    def test_contract_limits(self, risk_config, symbols_config):
        governor = RiskGovernor(risk_config, symbols_config)

        # ES Limit 2
        allowed, _ = governor.check_trade_risk("ES", 1)
        assert allowed is True
        allowed, _ = governor.check_trade_risk("ES", 2)
        assert allowed is True
        allowed, reason = governor.check_trade_risk("ES", 3)
        assert allowed is False
        assert "exceeds max 2" in reason

        # MES Limit 10
        allowed, _ = governor.check_trade_risk("MES", 10)
        assert allowed is True
        allowed, reason = governor.check_trade_risk("MES", 11)
        assert allowed is False
        assert "exceeds max 10" in reason

    def test_risk_per_trade_usd(self, risk_config, symbols_config):
        governor = RiskGovernor(risk_config, symbols_config)

        # Config: Max $200
        # ES: $12.50 per tick, $50 per point.
        # Limit $200 = 4 points = 16 ticks.

        entry = Decimal("5000.00")

        # Safe: 3 points stop (12 ticks * 12.50 = $150)
        stop_safe = Decimal("4997.00")
        allowed, _ = governor.check_trade_risk("ES", 1, entry, stop_safe)
        assert allowed is True

        # Unsafe: 5 points stop (20 ticks * 12.50 = $250)
        stop_unsafe = Decimal("4995.00")
        allowed, reason = governor.check_trade_risk("ES", 1, entry, stop_unsafe)
        assert allowed is False
        # Expected risk: 250.00
        assert "exceeds limit $200" in reason

        # Unsafe: 2 contracts * 3 points ($300)
        allowed, reason = governor.check_trade_risk("ES", 2, entry, stop_safe)
        assert allowed is False
        assert "exceeds limit $200" in reason

    def test_kill_switch_config(self, risk_config, symbols_config):
        risk_config.kill_switch = True
        governor = RiskGovernor(risk_config, symbols_config)
        assert governor.state.kill_switch_active is True
        allowed, _ = governor.can_trade()
        assert allowed is False
