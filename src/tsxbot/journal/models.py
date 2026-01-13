"""Journal Models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tsxbot.strategies.base import TradeSignal


@dataclass
class Decision:
    """Represents a strategy decision point."""

    timestamp: datetime
    symbol: str
    strategy_name: str
    signal: TradeSignal | None = None
    features: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
