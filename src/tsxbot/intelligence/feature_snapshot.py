"""FeatureSnapshot - Captures market state at interaction points.

Features captured:
- Regime: Trend vs Range, Volatility state
- Context: Distance to VWAP, OR width, Relative Volume
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.intelligence.level_store import SessionLevels

logger = logging.getLogger(__name__)


class RegimeType(Enum):
    """Market regime classification."""

    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    UNKNOWN = "unknown"


class VolatilityState(Enum):
    """Volatility state classification."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


@dataclass
class FeatureSnapshot:
    """
    Snapshot of market features at a point in time.

    Used for:
    - Regime classification input
    - Strategy selection context
    - Backtest tagging
    """

    timestamp: datetime

    # Regime
    regime: RegimeType = RegimeType.UNKNOWN
    volatility: VolatilityState = VolatilityState.NORMAL

    # Price Context
    price: Decimal = Decimal("0")
    vwap: Decimal | None = None
    distance_to_vwap_ticks: float = 0.0
    side_of_vwap: str = "unknown"  # "above", "below", "at"

    # Opening Range Context
    or_high: Decimal | None = None
    or_low: Decimal | None = None
    or_width_ticks: float = 0.0
    position_in_or: str = "unknown"  # "above", "below", "within"

    # Volume Context
    relative_volume: float = 1.0  # Current volume / average

    # Trend indicators
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    trend_direction: str = "unknown"  # "up", "down", "flat"

    # Volatility metrics
    atr_1min: Decimal | None = None
    atr_percentile: float = 50.0  # Where current ATR sits in 20-day distribution

    # Session context
    minutes_into_session: int = 0
    time_of_day: str = "unknown"  # "open", "midday", "close"

    def to_dict(self) -> dict:
        """Convert to dictionary for storage/logging."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "regime": self.regime.value,
            "volatility": self.volatility.value,
            "price": str(self.price),
            "vwap": str(self.vwap) if self.vwap else None,
            "distance_to_vwap_ticks": self.distance_to_vwap_ticks,
            "side_of_vwap": self.side_of_vwap,
            "or_width_ticks": self.or_width_ticks,
            "position_in_or": self.position_in_or,
            "relative_volume": self.relative_volume,
            "trend_direction": self.trend_direction,
            "minutes_into_session": self.minutes_into_session,
            "time_of_day": self.time_of_day,
        }


class FeatureEngine:
    """
    Computes feature snapshots from market data.

    This engine aggregates tick data and computes derived features
    for regime classification and strategy selection.
    """

    def __init__(
        self,
        tick_size: Decimal = Decimal("0.25"),
        rth_start: time = time(9, 30),
        ema_fast_period: int = 9,
        ema_slow_period: int = 21,
    ):
        self.tick_size = tick_size
        self.rth_start = rth_start
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period

        # EMA state
        self._ema_fast: Decimal | None = None
        self._ema_slow: Decimal | None = None
        self._price_count = 0

        # ATR tracking
        self._atr_values: list[Decimal] = []
        self._high_low_ranges: list[Decimal] = []

        # Volume tracking
        self._volume_ma: float = 0.0
        self._volume_count = 0

    def compute_snapshot(
        self,
        price: Decimal,
        timestamp: datetime,
        levels: SessionLevels | None = None,
        volume: int = 0,
        bar_range: Decimal | None = None,
    ) -> FeatureSnapshot:
        """
        Compute a feature snapshot at the current moment.

        Args:
            price: Current price
            timestamp: Current timestamp
            levels: Session levels from LevelStore
            volume: Current bar volume
            bar_range: High-Low of current bar (for ATR)
        """
        snapshot = FeatureSnapshot(timestamp=timestamp, price=price)

        # Update EMAs
        self._update_ema(price)
        snapshot.ema_fast = self._ema_fast
        snapshot.ema_slow = self._ema_slow

        # Compute trend direction
        if self._ema_fast and self._ema_slow:
            if self._ema_fast > self._ema_slow * Decimal("1.001"):
                snapshot.trend_direction = "up"
            elif self._ema_fast < self._ema_slow * Decimal("0.999"):
                snapshot.trend_direction = "down"
            else:
                snapshot.trend_direction = "flat"

        # Compute VWAP context
        if levels and levels.vwap:
            snapshot.vwap = levels.vwap
            dist = price - levels.vwap
            snapshot.distance_to_vwap_ticks = float(dist / self.tick_size)
            if dist > self.tick_size:
                snapshot.side_of_vwap = "above"
            elif dist < -self.tick_size:
                snapshot.side_of_vwap = "below"
            else:
                snapshot.side_of_vwap = "at"

        # Compute OR context
        if levels and levels.orh and levels.orl:
            snapshot.or_high = levels.orh
            snapshot.or_low = levels.orl
            or_width = levels.orh - levels.orl
            snapshot.or_width_ticks = float(or_width / self.tick_size)

            if price > levels.orh:
                snapshot.position_in_or = "above"
            elif price < levels.orl:
                snapshot.position_in_or = "below"
            else:
                snapshot.position_in_or = "within"

        # Compute session time context
        session_start = datetime.combine(timestamp.date(), self.rth_start)
        if timestamp.tzinfo:
            session_start = session_start.replace(tzinfo=timestamp.tzinfo)
        minutes = int((timestamp - session_start).total_seconds() / 60)
        snapshot.minutes_into_session = max(0, minutes)

        if minutes < 30:
            snapshot.time_of_day = "open"
        elif minutes < 300:  # 5 hours
            snapshot.time_of_day = "midday"
        else:
            snapshot.time_of_day = "close"

        # Update volume tracking
        if volume > 0:
            self._update_volume(volume)
            if self._volume_ma > 0:
                snapshot.relative_volume = volume / self._volume_ma

        # Update ATR
        if bar_range is not None:
            self._update_atr(bar_range)
            if self._atr_values:
                snapshot.atr_1min = self._atr_values[-1] if self._atr_values else None
                snapshot.atr_percentile = self._compute_atr_percentile()

        # Classify regime
        snapshot.regime = self._classify_regime(snapshot)
        snapshot.volatility = self._classify_volatility(snapshot)

        return snapshot

    def _update_ema(self, price: Decimal) -> None:
        """Update EMA values."""
        self._price_count += 1

        if self._ema_fast is None:
            self._ema_fast = price
            self._ema_slow = price
        else:
            # EMA multiplier
            mult_fast = Decimal(2) / (self.ema_fast_period + 1)
            mult_slow = Decimal(2) / (self.ema_slow_period + 1)

            self._ema_fast = price * mult_fast + self._ema_fast * (1 - mult_fast)
            self._ema_slow = price * mult_slow + self._ema_slow * (1 - mult_slow)

    def _update_volume(self, volume: int) -> None:
        """Update volume moving average."""
        self._volume_count += 1
        alpha = 2 / (20 + 1)  # 20-period EMA
        self._volume_ma = volume * alpha + self._volume_ma * (1 - alpha)

    def _update_atr(self, bar_range: Decimal) -> None:
        """Update ATR tracking."""
        self._high_low_ranges.append(bar_range)
        if len(self._high_low_ranges) > 100:
            self._high_low_ranges.pop(0)

        # Compute 14-period ATR
        if len(self._high_low_ranges) >= 14:
            atr = sum(self._high_low_ranges[-14:]) / 14
            self._atr_values.append(atr)
            if len(self._atr_values) > 100:
                self._atr_values.pop(0)

    def _compute_atr_percentile(self) -> float:
        """Compute where current ATR sits in recent distribution."""
        if not self._atr_values or len(self._atr_values) < 5:
            return 50.0

        current = self._atr_values[-1]
        below = sum(1 for v in self._atr_values if v < current)
        return (below / len(self._atr_values)) * 100

    def _classify_regime(self, snapshot: FeatureSnapshot) -> RegimeType:
        """Classify market regime based on features."""
        # Simple classification logic
        if snapshot.trend_direction == "up" and snapshot.position_in_or == "above":
            return RegimeType.TREND_UP
        elif snapshot.trend_direction == "down" and snapshot.position_in_or == "below":
            return RegimeType.TREND_DOWN
        elif snapshot.position_in_or == "within":
            return RegimeType.RANGE
        elif snapshot.trend_direction in ("up", "down"):
            return (
                RegimeType.TREND_UP if snapshot.trend_direction == "up" else RegimeType.TREND_DOWN
            )
        else:
            return RegimeType.RANGE

    def _classify_volatility(self, snapshot: FeatureSnapshot) -> VolatilityState:
        """Classify volatility state based on ATR percentile."""
        pct = snapshot.atr_percentile
        if pct < 25:
            return VolatilityState.LOW
        elif pct < 75:
            return VolatilityState.NORMAL
        elif pct < 95:
            return VolatilityState.HIGH
        else:
            return VolatilityState.EXTREME

    def reset(self) -> None:
        """Reset all state."""
        self._ema_fast = None
        self._ema_slow = None
        self._price_count = 0
        self._atr_values.clear()
        self._high_low_ranges.clear()
        self._volume_ma = 0.0
        self._volume_count = 0
