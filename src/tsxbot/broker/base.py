"""Base broker interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from decimal import Decimal

from tsxbot.broker.models import Fill, Order, OrderRequest, Position
from tsxbot.data.market_data import Tick


class Broker(ABC):
    """Abstract broker interface."""

    def __init__(self):
        self._fill_callbacks: list[Callable[[Fill], Awaitable[None]]] = []
        self._tick_callbacks: list[Callable[[Tick], Awaitable[None]]] = []

    def add_fill_callback(self, callback: Callable[[Fill], Awaitable[None]]):
        """Register callback for fill events."""
        self._fill_callbacks.append(callback)

    def add_tick_callback(self, callback: Callable[[Tick], Awaitable[None]]):
        """Register callback for market data ticks."""
        self._tick_callbacks.append(callback)

    @abstractmethod
    async def connect(self) -> None:
        """Connect to broker and data feed."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect."""
        pass

    @abstractmethod
    async def subscribe(self, symbol: str) -> None:
        """Subscribe to market data for symbol."""
        pass

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> Order:
        """Submit an order."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None:
        """Cancel an existing order."""
        pass

    @abstractmethod
    async def get_position(self, symbol: str) -> Position:
        """Get current position for symbol."""
        pass

    @abstractmethod
    async def get_account_balance(self) -> Decimal:
        """Get current account balance."""
        pass

    @abstractmethod
    async def get_orders(self) -> list[Order]:
        """Get all active/working orders."""
        pass
