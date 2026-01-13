"""Broker models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from tsxbot.constants import OrderSide, OrderStatus, OrderType


def generate_id() -> str:
    """Generate unique ID."""
    return str(uuid4())


@dataclass
class OrderRequest:
    """Request to place an order."""

    symbol: str
    side: OrderSide
    qty: int
    type: OrderType
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None  # For stop orders if supported

    # Bracket info (optional, handled by executor usually but broker might natively support)
    # Keeping clean: Broker usually takes atomic orders. Bracket logic in Executor.


@dataclass
class Order:
    """Order state."""

    id: str
    request: OrderRequest
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_fill_price: Decimal = Decimal("0.0")
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def timestamp(self) -> datetime:
        """Return order creation time (for compatibility with journal/API)."""
        return self.created_at

    @property
    def remaining_qty(self) -> int:
        return self.request.qty - self.filled_qty

    @property
    def is_done(self) -> bool:
        return self.status in [
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        ]


@dataclass
class Fill:
    """Trade fill."""

    id: str
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: Decimal
    timestamp: datetime


@dataclass
class Position:
    """Current position."""

    symbol: str
    qty: int = 0  # + Long, - Short
    avg_price: Decimal = Decimal("0.0")

    # PnL tracking
    realized_pnl: Decimal = Decimal("0.0")
    unrealized_pnl: Decimal = Decimal("0.0")

    @property
    def side(self) -> OrderSide | None:
        if self.qty > 0:
            return OrderSide.BUY  # Long
        if self.qty < 0:
            return OrderSide.SELL  # Short
        return None
