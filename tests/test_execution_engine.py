"""Tests for Execution Engine."""

from datetime import datetime
from decimal import Decimal

import pytest

from tsxbot.broker.sim import SimBroker
from tsxbot.config_loader import (
    RiskConfig,
    SymbolsConfig,
    SymbolSpecConfig,
)
from tsxbot.constants import OrderSide, OrderStatus, OrderType, SignalDirection
from tsxbot.data.market_data import Tick
from tsxbot.execution.engine import ExecutionEngine
from tsxbot.risk.risk_governor import RiskGovernor
from tsxbot.strategies.base import TradeSignal


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
def broker(symbols_config):
    return SimBroker(symbols_config)


@pytest.fixture
def risk_governor(risk_config, symbols_config):
    return RiskGovernor(risk_config, symbols_config)


@pytest.fixture
def engine(broker, risk_governor, symbols_config):
    return ExecutionEngine(broker, risk_governor, symbols_config)


@pytest.mark.asyncio
async def test_signal_flow_buyside(engine, broker):
    # 1. Start Signal (Long ES)
    now = datetime.now()
    signal = TradeSignal(
        symbol="ES",
        direction=SignalDirection.LONG,
        timestamp=now,
        quantity=1,
        entry_type=OrderType.MARKET,
        stop_ticks=8,  # 2.00
        target_ticks=16,  # 4.00
    )

    # 2. Process Signal
    await engine.process_signal(signal)

    # Verify Entry Order Placed
    orders = await broker.get_orders()
    assert len(orders) == 1
    entry_order = orders[0]
    assert entry_order.request.symbol == "ES"
    assert entry_order.request.side == OrderSide.BUY
    assert entry_order.status == OrderStatus.PENDING

    # 3. Simulate Entry Fill
    # Market -> Fill at current tick
    # Need to feed tick to broker
    tick = Tick("ES", now, Decimal("5000.00"), 1)
    await broker.process_tick(tick)

    # Verify Filled
    assert entry_order.status == OrderStatus.FILLED

    # Verify Brackets Placed (Stop and Target)
    orders = await broker.get_orders()
    # entry_order is filled, so get_orders returns newly placed brackets
    assert len(orders) == 2

    stop_order = next(o for o in orders if o.request.type == OrderType.STOP)
    target_order = next(o for o in orders if o.request.type == OrderType.LIMIT)

    # Verify Bracket Prices
    # Entry 5000. Stop 8 ticks (2.00) -> 4998.00
    assert stop_order.request.stop_price == Decimal("4998.00")
    assert stop_order.request.side == OrderSide.SELL

    # Target 16 ticks (4.00) -> 5004.00
    assert target_order.request.limit_price == Decimal("5004.00")
    assert target_order.request.side == OrderSide.SELL

    # 4. Simulate Target Fill (TP Hit)
    # Price moves to 5004.00 (or higher)
    tp_tick = Tick("ES", now, Decimal("5004.00"), 1)
    await broker.process_tick(tp_tick)

    # Verify Target Filled
    assert target_order.status == OrderStatus.FILLED

    # Verify Stop Cancelled (OCO)
    assert stop_order.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_risk_rejection(engine, broker, risk_governor):
    # Trip kill switch manually
    risk_governor.trip_kill_switch("Test")

    signal = TradeSignal(
        symbol="ES", direction=SignalDirection.LONG, timestamp=datetime.now(), quantity=1
    )

    await engine.process_signal(signal)

    # No orders placed
    orders = await broker.get_orders()
    assert len(orders) == 0
