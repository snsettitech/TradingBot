"""Bar data structure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Bar:
    """OHLCV Bar."""

    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @property
    def hl2(self) -> Decimal:
        return (self.high + self.low) / Decimal("2.0")

    @property
    def hlc3(self) -> Decimal:
        return (self.high + self.low + self.close) / Decimal("3.0")
