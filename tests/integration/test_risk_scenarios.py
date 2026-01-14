"""Integration test: Risk scenario tests."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tsxbot.broker.models import OrderRequest
from tsxbot.broker.sim import SimBroker
from tsxbot.config_loader import (
    AppConfig,
    CommissionConfig,
    ExecutionConfig,
    RiskConfig,
    SymbolsConfig,
    SymbolSpecConfig,
)
from tsxbot.constants import OrderSide, OrderType
from tsxbot.data.market_data import Tick
from tsxbot.persistence.state_store import StateStore
from tsxbot.risk.risk_governor import RiskGovernor


@pytest.fixture
def symbols_config():
    return SymbolsConfig(
        primary="ES",
        micros="MES",
        es=SymbolSpecConfig(tick_size=Decimal("0.25"), tick_value=Decimal("12.50")),
        mes=SymbolSpecConfig(tick_size=Decimal("0.25"), tick_value=Decimal("1.25")),
    )


@pytest.fixture
def risk_config():
    return RiskConfig(
        daily_loss_limit_usd=Decimal("500.0"),
        max_loss_limit_usd=Decimal("1000.0"),
        max_risk_per_trade_usd=Decimal("100.0"),
        max_contracts_es=2,
        max_contracts_mes=10,
        max_trades_per_day=5,
        kill_switch=False,
    )


@pytest.fixture
def risk_governor(risk_config, symbols_config):
    return RiskGovernor(risk_config, symbols_config)


@pytest.fixture
def broker(symbols_config):
    exec_config = ExecutionConfig(
        slippage_ticks=0,
        commissions=CommissionConfig(
            es_round_turn=Decimal("0.00"),
            mes_round_turn=Decimal("0.00"),
        ),
    )
    return SimBroker(
        symbols_config,
        initial_balance=Decimal("50000.00"),
        execution_config=exec_config,
    )


class TestDailyLossLimit:
    """Test daily loss limit triggers kill switch."""

    @pytest.mark.asyncio
    async def test_daily_loss_triggers_kill_switch(self, risk_governor, broker):
        """When daily P&L exceeds limit, trading should be blocked."""
        # Simulate loss that exceeds daily limit
        risk_governor.update_account_status(
            balance=Decimal("49400.00"),
            daily_pnl=Decimal("-600.00"),  # Exceeds $500 limit
        )

        can_trade, reason = risk_governor.can_trade()
        assert can_trade is False
        assert "Daily loss limit" in reason or "Kill switch" in reason

    @pytest.mark.asyncio
    async def test_near_limit_still_allows_trading(self, risk_governor):
        """Trading allowed when near but not over limit."""
        risk_governor.update_account_status(
            balance=Decimal("49600.00"),
            daily_pnl=Decimal("-400.00"),  # Under $500 limit
        )

        can_trade, reason = risk_governor.can_trade()
        assert can_trade is True


class TestMaxTrades:
    """Test max trades per day limit."""

    @pytest.mark.asyncio
    async def test_max_trades_blocks_new_entries(self, risk_governor):
        """After max trades, new entries should be blocked."""
        # Simulate 5 trades (at limit)
        for _ in range(5):
            risk_governor.record_trade_execution()

        can_trade, reason = risk_governor.can_trade()
        assert can_trade is False
        assert "trades" in reason.lower()

    @pytest.mark.asyncio
    async def test_under_max_trades_allows_trading(self, risk_governor):
        """Trading allowed when under max trades."""
        for _ in range(4):
            risk_governor.record_trade_execution()

        can_trade, reason = risk_governor.can_trade()
        assert can_trade is True


class TestPositionPersistence:
    """Test state persistence across restarts."""

    def test_state_persists_same_day(self, tmp_path):
        """State should be restored when loading on same day."""
        store = StateStore(data_dir=tmp_path)

        # First session
        state1 = store.load(current_balance=Decimal("50000.00"))
        store.update_pnl(Decimal("250.00"), Decimal("50250.00"))
        store.increment_trade_count()
        store.increment_trade_count()

        # "Restart" - new StateStore instance
        store2 = StateStore(data_dir=tmp_path)
        state2 = store2.load(current_balance=Decimal("50000.00"))

        # Should restore previous state
        assert state2.daily_pnl == Decimal("250.00")
        assert state2.trade_count == 2
        assert state2.current_balance == Decimal("50250.00")

    def test_kill_switch_persists(self, tmp_path):
        """Kill switch status should persist."""
        store = StateStore(data_dir=tmp_path)
        store.load(current_balance=Decimal("50000.00"))
        store.set_kill_switch(True, "Daily loss limit hit")

        # Restart
        store2 = StateStore(data_dir=tmp_path)
        state2 = store2.load(current_balance=Decimal("50000.00"))

        assert state2.kill_switch_active is True
        assert "Daily loss" in state2.kill_switch_reason


class TestDrawdownTracking:
    """Test trailing drawdown calculation."""

    def test_drawdown_tracks_peak(self, tmp_path):
        """Drawdown should track from peak balance."""
        store = StateStore(data_dir=tmp_path)
        store.load(current_balance=Decimal("50000.00"))

        # Make profit - peak goes to 50500
        store.update_pnl(Decimal("500.00"), Decimal("50500.00"))
        assert store.state.peak_balance == Decimal("50500.00")

        # Lose some - drawdown increases
        store.update_pnl(Decimal("-200.00"), Decimal("50300.00"))
        assert store.get_current_drawdown() == Decimal("200.00")

        # Lose more
        store.update_pnl(Decimal("-100.00"), Decimal("50200.00"))
        assert store.get_current_drawdown() == Decimal("300.00")

        # Peak should not decrease
        assert store.state.peak_balance == Decimal("50500.00")
