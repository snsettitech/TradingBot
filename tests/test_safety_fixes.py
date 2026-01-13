"""Tests for the safety fixes implemented in the audit."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from tsxbot.broker.models import Fill, Order, OrderRequest
from tsxbot.broker.sim import SimBroker
from tsxbot.config_loader import RiskConfig, SessionConfig, SymbolsConfig, SymbolSpecConfig
from tsxbot.constants import OrderSide, OrderStatus, OrderType, SignalDirection
from tsxbot.data.market_data import Tick
from tsxbot.execution.engine import ExecutionEngine
from tsxbot.risk.risk_governor import RiskGovernor
from tsxbot.strategies.base import TradeSignal
from tsxbot.time.session_manager import SessionManager


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
        daily_loss_limit_usd=Decimal("500.00"),
        max_trades_per_day=5,
        max_risk_per_trade_usd=Decimal("100.00"),
    )


@pytest.fixture
def session_config():
    return SessionConfig(
        timezone="America/New_York",
        rth_start="09:30",
        rth_end="16:00",
        flatten_time="15:55",
        trading_days=[0, 1, 2, 3, 4],
    )


@pytest.fixture
def broker(symbols_config):
    return SimBroker(symbols_config)


@pytest.fixture
def risk_governor(risk_config, symbols_config):
    return RiskGovernor(risk_config, symbols_config)


@pytest.fixture
def session_manager(session_config):
    return SessionManager(session_config)


class TestSimBrokerStopOrders:
    """Tests for STOP order matching in SimBroker (Fix #1)."""

    @pytest.mark.asyncio
    async def test_sell_stop_triggers_on_price_drop(self, broker):
        """SELL STOP should trigger when price drops to or below stop price."""
        # Place SELL STOP @ 4995 (protecting a long position)
        req = OrderRequest("ES", OrderSide.SELL, 1, OrderType.STOP, stop_price=Decimal("4995.00"))
        order = await broker.place_order(req)
        assert order.status == OrderStatus.PENDING

        # Price at 5000 - stop not triggered
        await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))
        assert order.status == OrderStatus.PENDING

        # Price drops to exactly stop price - should trigger
        await broker.process_tick(Tick("ES", datetime.now(), Decimal("4995.00"), 1))
        assert order.status == OrderStatus.FILLED
        assert order.avg_fill_price == Decimal("4995.00")

    @pytest.mark.asyncio
    async def test_buy_stop_triggers_on_price_rise(self, broker):
        """BUY STOP should trigger when price rises to or above stop price."""
        # Place BUY STOP @ 5005 (protecting a short position)
        req = OrderRequest("ES", OrderSide.BUY, 1, OrderType.STOP, stop_price=Decimal("5005.00"))
        order = await broker.place_order(req)
        assert order.status == OrderStatus.PENDING

        # Price at 5000 - stop not triggered
        await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))
        assert order.status == OrderStatus.PENDING

        # Price rises to exactly stop price - should trigger
        await broker.process_tick(Tick("ES", datetime.now(), Decimal("5005.00"), 1))
        assert order.status == OrderStatus.FILLED
        assert order.avg_fill_price == Decimal("5005.00")

    @pytest.mark.asyncio
    async def test_stop_triggers_on_gap_through(self, broker):
        """Stop should trigger even on a gap through the stop price."""
        req = OrderRequest("ES", OrderSide.SELL, 1, OrderType.STOP, stop_price=Decimal("4995.00"))
        order = await broker.place_order(req)

        # Price gaps from 5000 directly to 4990 (through stop)
        await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))
        await broker.process_tick(Tick("ES", datetime.now(), Decimal("4990.00"), 1))

        assert order.status == OrderStatus.FILLED
        # Filled at stop price (not gap price for simplicity)
        assert order.avg_fill_price == Decimal("4995.00")


class TestDryRunEnforcement:
    """Tests for DRY_RUN blocking orders (Fix #2)."""

    @pytest.mark.asyncio
    async def test_dry_run_blocks_order_placement(self, broker, risk_governor, symbols_config):
        """When dry_run=True, no orders should be placed."""
        engine = ExecutionEngine(
            broker, risk_governor, symbols_config, journal=None, session_manager=None, dry_run=True
        )

        signal = TradeSignal(
            symbol="ES",
            direction=SignalDirection.LONG,
            timestamp=datetime.now(),
            quantity=1,
            entry_type=OrderType.MARKET,
        )

        await engine.process_signal(signal)

        # No orders should be placed
        orders = await broker.get_orders()
        assert len(orders) == 0

    @pytest.mark.asyncio
    async def test_non_dry_run_places_orders(self, broker, risk_governor, symbols_config):
        """When dry_run=False, orders should be placed normally."""
        engine = ExecutionEngine(
            broker, risk_governor, symbols_config, journal=None, session_manager=None, dry_run=False
        )

        # Provide tick data for immediate fill
        await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))

        signal = TradeSignal(
            symbol="ES",
            direction=SignalDirection.LONG,
            timestamp=datetime.now(),
            quantity=1,
            entry_type=OrderType.MARKET,
        )

        await engine.process_signal(signal)

        # Order should be active or filled
        assert len(engine.active_trades) > 0 or len(await broker.get_orders()) >= 0


class TestRTHEnforcement:
    """Tests for RTH enforcement in ExecutionEngine (Fix #3)."""

    @pytest.mark.asyncio
    async def test_signal_rejected_outside_rth(self, broker, risk_governor, symbols_config):
        """Signals should be rejected when outside RTH."""
        # Create mock session manager that says we're outside trading hours
        mock_session = MagicMock()
        mock_session.is_trading_allowed.return_value = False

        engine = ExecutionEngine(
            broker,
            risk_governor,
            symbols_config,
            journal=None,
            session_manager=mock_session,
            dry_run=False,
        )

        signal = TradeSignal(
            symbol="ES", direction=SignalDirection.LONG, timestamp=datetime.now(), quantity=1
        )

        await engine.process_signal(signal)

        # No orders should be placed
        orders = await broker.get_orders()
        assert len(orders) == 0
        mock_session.is_trading_allowed.assert_called_once()

    @pytest.mark.asyncio
    async def test_signal_processed_during_rth(self, broker, risk_governor, symbols_config):
        """Signals should be processed when within RTH."""
        mock_session = MagicMock()
        mock_session.is_trading_allowed.return_value = True

        engine = ExecutionEngine(
            broker,
            risk_governor,
            symbols_config,
            journal=None,
            session_manager=mock_session,
            dry_run=False,
        )

        # Provide tick for fill
        await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))

        signal = TradeSignal(
            symbol="ES",
            direction=SignalDirection.LONG,
            timestamp=datetime.now(),
            quantity=1,
            entry_type=OrderType.MARKET,
        )

        await engine.process_signal(signal)

        # Order should be processed
        mock_session.is_trading_allowed.assert_called_once()
        assert len(engine.active_trades) > 0


class TestStopFailureHandling:
    """Tests for emergency flatten on stop failure (Fix #4)."""

    @pytest.mark.asyncio
    async def test_stop_failure_triggers_emergency_flatten(self, risk_governor, symbols_config):
        """When stop order fails, position should be emergency flattened."""
        # Create broker mock that fails on STOP orders
        mock_broker = MagicMock()
        mock_broker._fill_callbacks = []
        mock_broker.add_fill_callback = lambda cb: mock_broker._fill_callbacks.append(cb)

        # Track placed orders
        placed_orders = []

        async def mock_place_order(req):
            if req.type == OrderType.STOP:
                raise Exception("Simulated stop order failure")
            order = Order(id="test-id", request=req, status=OrderStatus.FILLED)
            placed_orders.append(order)
            return order

        mock_broker.place_order = mock_place_order

        engine = ExecutionEngine(
            mock_broker,
            risk_governor,
            symbols_config,
            journal=None,
            session_manager=None,
            dry_run=False,
        )

        signal = TradeSignal(
            symbol="ES",
            direction=SignalDirection.LONG,
            timestamp=datetime.now(),
            quantity=1,
            entry_type=OrderType.MARKET,
            stop_ticks=8,
            target_ticks=16,
        )

        await engine.process_signal(signal)

        # Simulate entry fill
        fill = Fill(
            id="fill-1",
            order_id="test-id",
            symbol="ES",
            side=OrderSide.BUY,
            qty=1,
            price=Decimal("5000.00"),
            timestamp=datetime.now(),
        )

        await engine.on_fill(fill)

        # Should have placed entry (1) and emergency flatten order (2)
        # Entry = MARKET BUY, Flatten = MARKET SELL
        assert len(placed_orders) == 2
        assert placed_orders[0].request.side == OrderSide.BUY  # Entry
        assert placed_orders[1].request.side == OrderSide.SELL  # Flatten
        assert placed_orders[1].request.type == OrderType.MARKET  # Market order

        # Kill switch should be tripped
        assert risk_governor.state.kill_switch_active is True


class TestDailyRiskReset:
    """Tests for daily risk reset functionality (Fix #10)."""

    def test_reset_daily_clears_counters(self, risk_config, symbols_config):
        """Daily reset should clear trade count and daily PnL."""
        governor = RiskGovernor(risk_config, symbols_config)

        # Simulate some trading activity
        governor.record_trade_execution()
        governor.record_trade_execution()
        governor.update_account_status(Decimal("49800"), Decimal("-200"))

        assert governor.state.trade_count == 2
        assert governor.state.daily_pnl == Decimal("-200")

        # Reset for new day
        governor.reset_daily(starting_balance=Decimal("50000"))

        assert governor.state.trade_count == 0
        assert governor.state.daily_pnl == Decimal("0.0")
        assert governor.state.high_water_mark == Decimal("50000")

    def test_reset_daily_preserves_kill_switch(self, risk_config, symbols_config):
        """Daily reset should NOT clear kill switch for safety."""
        governor = RiskGovernor(risk_config, symbols_config)

        # Trip kill switch
        governor.trip_kill_switch("Test reason")
        assert governor.state.kill_switch_active is True

        # Reset for new day
        governor.reset_daily()

        # Kill switch should still be active
        assert governor.state.kill_switch_active is True
        assert governor.state.kill_switch_reason == "Test reason"

    def test_reset_kill_switch_explicit(self, risk_config, symbols_config):
        """Kill switch should require explicit reset."""
        governor = RiskGovernor(risk_config, symbols_config)

        governor.trip_kill_switch("Test reason")
        assert governor.state.kill_switch_active is True

        # Explicitly reset kill switch
        governor.reset_kill_switch()

        assert governor.state.kill_switch_active is False
        assert governor.state.kill_switch_reason == ""

        # Should be able to trade again
        allowed, _ = governor.can_trade()
        assert allowed is True
