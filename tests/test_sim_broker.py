"""Tests for SimBroker."""

from datetime import datetime
from decimal import Decimal

import pytest

from tsxbot.broker.models import OrderRequest
from tsxbot.broker.sim import SimBroker
from tsxbot.config_loader import ExecutionConfig, SymbolsConfig, SymbolSpecConfig, CommissionConfig
from tsxbot.constants import OrderSide, OrderStatus, OrderType
from tsxbot.data.market_data import Tick


@pytest.fixture
def symbols_config():
    return SymbolsConfig(
        primary="ES",
        micros="MES",
        es=SymbolSpecConfig(tick_size=Decimal("0.25"), tick_value=Decimal("12.50")),
        mes=SymbolSpecConfig(tick_size=Decimal("0.25"), tick_value=Decimal("1.25")),
    )


@pytest.fixture
def broker(symbols_config):
    # Zero slippage and zero commission for predictable test results
    exec_config = ExecutionConfig(
        slippage_ticks=0,
        commissions=CommissionConfig(
            es_round_turn=Decimal("0.00"),
            mes_round_turn=Decimal("0.00")
        )
    )
    return SimBroker(symbols_config, initial_balance=Decimal("100000.00"), execution_config=exec_config)


@pytest.mark.asyncio
async def test_market_order_nofill_without_data(broker):
    req = OrderRequest("ES", OrderSide.BUY, 1, OrderType.MARKET)
    order = await broker.place_order(req)
    assert order.status == OrderStatus.PENDING


@pytest.mark.asyncio
async def test_market_order_instant_fill(broker):
    tick = Tick("ES", datetime.now(), Decimal("5000.00"), 1)
    await broker.process_tick(tick)

    req = OrderRequest("ES", OrderSide.BUY, 1, OrderType.MARKET)
    order = await broker.place_order(req)
    import asyncio
    await asyncio.sleep(0.01)

    assert order.status == OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("5000.00")

    pos = await broker.get_position("ES")
    assert pos.qty == 1
    assert pos.avg_price == Decimal("5000.00")


@pytest.mark.asyncio
async def test_market_order_delayed_fill(broker):
    req = OrderRequest("ES", OrderSide.BUY, 1, OrderType.MARKET)
    order = await broker.place_order(req)
    assert order.status == OrderStatus.PENDING

    tick = Tick("ES", datetime.now(), Decimal("5002.50"), 1)
    await broker.process_tick(tick)

    assert order.status == OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("5002.50")

    pos = await broker.get_position("ES")
    assert pos.qty == 1
    assert pos.avg_price == Decimal("5002.50")


@pytest.mark.asyncio
async def test_limit_order(broker):
    # Buy Limit @ 5000
    req = OrderRequest("ES", OrderSide.BUY, 1, OrderType.LIMIT, limit_price=Decimal("5000.00"))
    order = await broker.place_order(req)

    # Tick @ 5001 (Above) -> No Fill
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5001.00"), 1))
    assert order.status == OrderStatus.PENDING

    # Tick @ 5000 (Touch) -> Fill
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))
    assert order.status == OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("5000.00")


@pytest.mark.asyncio
async def test_pnl_calculation_long_profit(broker):
    # ES Multiplier: 50.0

    # 1. Buy 1 @ 5000
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))
    await broker.place_order(OrderRequest("ES", OrderSide.BUY, 1, OrderType.MARKET))
    import asyncio
    await asyncio.sleep(0.01)

    # Val: 1 * 5000. Cost = 5000.

    # 2. Sell 1 @ 5010 (+10 pts)
    # Profit = 10 * 50 = $500
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5010.00"), 1))
    await broker.place_order(OrderRequest("ES", OrderSide.SELL, 1, OrderType.MARKET))
    import asyncio
    await asyncio.sleep(0.01)

    pos = await broker.get_position("ES")
    assert pos.qty == 0
    assert pos.realized_pnl == Decimal("500.00")

    bal = await broker.get_account_balance()
    assert bal == Decimal("100500.00")


@pytest.mark.asyncio
async def test_pnl_calculation_short_loss(broker):
    # 1. Sell 1 @ 5000
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))
    await broker.place_order(OrderRequest("ES", OrderSide.SELL, 1, OrderType.MARKET))
    import asyncio
    await asyncio.sleep(0.01)

    # 2. Buy 1 @ 5010 (-10 pts)
    # Loss = 10 * 50 = $500
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5010.00"), 1))
    await broker.place_order(OrderRequest("ES", OrderSide.BUY, 1, OrderType.MARKET))
    import asyncio
    await asyncio.sleep(0.01)

    pos = await broker.get_position("ES")
    assert pos.qty == 0
    assert pos.realized_pnl == Decimal("-500.00")

    bal = await broker.get_account_balance()
    assert bal == Decimal("99500.00")


@pytest.mark.asyncio
async def test_position_flip(broker):
    # 1. Buy 1 @ 5000
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5000.00"), 1))
    await broker.place_order(OrderRequest("ES", OrderSide.BUY, 1, OrderType.MARKET))
    import asyncio
    await asyncio.sleep(0.01)

    # 2. Sell 2 @ 5010 (Flip to Short 1)
    # Close 1 @ 5010: Profit $500.
    # Open Short 1 @ 5010.
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5010.00"), 1))
    await broker.place_order(OrderRequest("ES", OrderSide.SELL, 2, OrderType.MARKET))
    import asyncio
    await asyncio.sleep(0.01)

    pos = await broker.get_position("ES")
    assert pos.qty == -1
    assert pos.avg_price == Decimal("5010.00")
    assert pos.realized_pnl == Decimal("500.00")

    # 3. Buy 1 @ 5005 (Close Short)
    # Profit: (5010 - 5005) * 50 = 5 * 50 = $250
    # Total PnL: 500 + 250 = 750
    await broker.process_tick(Tick("ES", datetime.now(), Decimal("5005.00"), 1))
    await broker.place_order(OrderRequest("ES", OrderSide.BUY, 1, OrderType.MARKET))
    import asyncio
    await asyncio.sleep(0.01)

    pos = await broker.get_position("ES")
    assert pos.qty == 0
    assert pos.realized_pnl == Decimal("750.00")
