"""Market data structures and types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Tick:
    """Individual trade tick."""

    symbol: str
    timestamp: datetime
    price: Decimal
    volume: int


@dataclass(frozen=True)
class DOMUpdate:
    """Depth of Market update (simplified)."""

    symbol: str
    timestamp: datetime
    bid_price: Decimal
    ask_price: Decimal
    bid_size: int
    ask_size: int
