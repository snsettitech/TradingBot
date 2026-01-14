"""Simulation broker implementation."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from tsxbot.broker.base import Broker
from tsxbot.broker.models import Fill, Order, OrderRequest, Position, generate_id
from tsxbot.config_loader import ExecutionConfig, SymbolsConfig
from tsxbot.constants import OrderSide, OrderStatus, OrderType
from tsxbot.data.market_data import Tick

logger = logging.getLogger(__name__)


class SimBroker(Broker):
    """
    Simulation broker for testing and dry runs.
    Executes orders against internal market data feed.
    """

    def __init__(
        self,
        symbols_config: SymbolsConfig,
        initial_balance: Decimal = Decimal("100000.00"),
        execution_config: ExecutionConfig | None = None,
    ):
        super().__init__()
        self.symbols_config = symbols_config
        self.execution_config = execution_config or ExecutionConfig()
        self._balance = initial_balance
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = defaultdict(lambda: Position(symbol=""))
        self._last_ticks: dict[str, Tick] = {}

    async def connect(self) -> None:
        logger.info(f"SimBroker connected. Balance: {self._balance}")

    async def disconnect(self) -> None:
        logger.info("SimBroker disconnected")

    async def subscribe(self, symbol: str) -> None:
        logger.info(f"Subscribed to {symbol}")

    async def get_account_balance(self) -> Decimal:
        return self._balance

    async def get_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if not o.is_done]

    async def get_position(self, symbol: str) -> Position:
        pos = self._positions.get(symbol)
        if not pos:
            return Position(symbol=symbol)
        return pos

    async def place_order(self, request: OrderRequest) -> Order:
        order = Order(
            id=generate_id(), request=request, status=OrderStatus.PENDING, created_at=datetime.now()
        )
        self._orders[order.id] = order
        logger.info(
            f"Order placed: {order.id} {request.symbol} {request.side} {request.qty} {request.type}"
        )

        # Try instant fill if market data available
        if request.symbol in self._last_ticks:
            import asyncio

            # Schedule match to run after current task yields, allowing caller to receive order ID first
            asyncio.create_task(self._try_match(order, self._last_ticks[request.symbol]))

        return order

    async def cancel_order(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order and not order.is_done:
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now()
            logger.info(f"Order cancelled: {order_id}")

    async def process_tick(self, tick: Tick) -> None:
        """External driver feeds tick here."""
        self._last_ticks[tick.symbol] = tick

        # Update positions unrealized PnL (visual only/sim)
        # Check active orders
        for order in list(self._orders.values()):
            if not order.is_done:
                # Naive matching: match against *this* tick
                # In real sim, maybe check High/Low of bar, but we use Ticks.
                if order.request.symbol == tick.symbol:
                    await self._try_match(order, tick)

        # Emit callback
        for cb in self._tick_callbacks:
            await cb(tick)

    async def _try_match(self, order: Order, tick: Tick) -> None:
        if order.is_done:
            return

        req = order.request
        fill_price = None

        if req.type == OrderType.MARKET:
            # Apply slippage (unfavorable direction)
            slippage = Decimal(str(self.execution_config.slippage_ticks)) * Decimal("0.25")
            if req.side == OrderSide.BUY:
                fill_price = tick.price + slippage  # Pay more
            else:
                fill_price = tick.price - slippage  # Receive less
        elif req.type == OrderType.LIMIT:
            if req.side == OrderSide.BUY and tick.price <= req.limit_price:
                fill_price = (
                    req.limit_price
                )  # Assume filled at limit (pessimistic) or tick? Standard is Limit or Better.
                # Matching engine usually fills at limit if gaps, or price if touching.
                # Simplicity: fill at limit price if crossed.
                pass
            elif req.side == OrderSide.SELL and tick.price >= req.limit_price:
                fill_price = req.limit_price

        elif req.type == OrderType.STOP:
            # STOP orders trigger when price crosses the stop price
            if req.stop_price is None:
                return  # Invalid stop order without stop_price
            # SELL STOP (for long position protection): triggers when price <= stop_price
            if req.side == OrderSide.SELL and tick.price <= req.stop_price:
                fill_price = (
                    req.stop_price
                )  # Fill at stop price (could use tick.price for slippage)
            # BUY STOP (for short position protection): triggers when price >= stop_price
            elif req.side == OrderSide.BUY and tick.price >= req.stop_price:
                fill_price = req.stop_price

        if fill_price:
            # Create Fill
            fill = Fill(
                id=generate_id(),
                order_id=order.id,
                symbol=req.symbol,
                side=req.side,
                qty=req.qty,  # Full fill assumption
                price=fill_price,
                timestamp=tick.timestamp,
            )

            # Update Order
            order.status = OrderStatus.FILLED
            order.filled_qty = req.qty
            order.avg_fill_price = fill_price
            order.updated_at = tick.timestamp

            # Deduct commission
            commission = self._get_commission(req.symbol, req.qty)
            self._balance -= commission
            logger.debug(f"Commission deducted: ${commission} for {req.qty} {req.symbol}")

            # Update Position & Balance
            await self._update_position(fill)

            # Callback
            for cb in self._fill_callbacks:
                await cb(fill)

    def _get_commission(self, symbol: str, qty: int) -> Decimal:
        """Calculate commission for a trade (one side, half round-turn)."""
        if "MES" in symbol.upper():
            # MES: $0.74 per round-turn, so $0.37 per side
            return (self.execution_config.commissions.mes_round_turn / 2) * qty
        else:
            # ES: $2.80 per round-turn, so $1.40 per side
            return (self.execution_config.commissions.es_round_turn / 2) * qty

    async def _update_position(self, fill: Fill) -> None:
        symbol = fill.symbol
        pos = self._positions[symbol]
        pos.symbol = symbol  # Ensure set

        # Calculate Multiplier
        # Resolve config
        mult = Decimal("50.0")  # Default ES
        if "MES" in symbol:
            mult = Decimal("5.0")
        # Better: lookup strictly if possible
        # (Assuming standard logic for MVP)

        qty_signed = fill.qty if fill.side == OrderSide.BUY else -fill.qty

        # PnL Logic
        # If reducing position:
        if (pos.qty > 0 and qty_signed < 0) or (pos.qty < 0 and qty_signed > 0):
            # Closing
            qty_closing = min(abs(pos.qty), abs(qty_signed))
            # Profit per contract = (Sell Price - Buy Price)
            # If Long (pos.qty > 0), Sell Price is Fill. PnL = (Fill - Avg) * qty
            # If Short, Buy Price is Fill. PnL = (Avg - Fill) * qty

            if pos.qty > 0:
                pnl = (fill.price - pos.avg_price) * qty_closing * mult
            else:
                pnl = (pos.avg_price - fill.price) * qty_closing * mult

            pos.realized_pnl += pnl
            self._balance += pnl

        # Update Quantity and Avg Price
        new_qty = pos.qty + qty_signed

        if new_qty == 0:
            pos.avg_price = Decimal("0.0")
        elif (pos.qty == 0) or (pos.qty > 0 and qty_signed > 0) or (pos.qty < 0 and qty_signed < 0):
            # Opening/Increasing
            # Weighted average
            total_val = (abs(pos.qty) * pos.avg_price) + (abs(qty_signed) * fill.price)
            pos.avg_price = total_val / abs(new_qty)
        else:
            # Flip or Partial Close?
            # If flip (e.g. +1 to -1):
            # The closing part handled PnL.
            # The remaining part is new position.
            if (pos.qty > 0 and new_qty < 0) or (pos.qty < 0 and new_qty > 0):
                # This implies we closed fully and opened opposite.
                # The 'closing' logic above used `min(abs)`.
                # So if Old +2, fill -3. Closing 2. Remaining -1.
                # New Avg Price should be Fill Price for the remainder (-1).
                pos.avg_price = fill.price

        pos.qty = new_qty
