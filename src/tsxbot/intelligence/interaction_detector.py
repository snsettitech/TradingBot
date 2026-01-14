"""InteractionDetector - Classifies price interactions with key levels.

Interaction Types:
- TOUCH: Price comes within 2 ticks of a level
- REJECT: Touch + reversal within 3 bars
- BREAK_AND_HOLD: Price crosses level and holds for 3+ bars
- FAKEOUT_RECLAIM: Break + fail + reverse within 5 bars
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.data.market_data import Tick
    from tsxbot.intelligence.level_store import SessionLevels

logger = logging.getLogger(__name__)


class InteractionType(Enum):
    """Types of price-level interactions."""

    TOUCH = "touch"
    REJECT = "reject"
    BREAK_AND_HOLD = "break_and_hold"
    FAKEOUT_RECLAIM = "fakeout_reclaim"


@dataclass
class LevelInteraction:
    """A detected interaction with a price level."""

    timestamp: datetime
    level_name: str
    level_price: Decimal
    interaction_type: InteractionType
    price_at_interaction: Decimal
    direction: str  # "above" or "below" - where price came from

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level_name": self.level_name,
            "level_price": str(self.level_price),
            "interaction_type": self.interaction_type.value,
            "price_at_interaction": str(self.price_at_interaction),
            "direction": self.direction,
        }


@dataclass
class _LevelState:
    """Internal state for tracking a level's interactions."""

    name: str
    price: Decimal
    last_side: str | None = None  # "above", "below", or None
    touch_bar: int | None = None
    break_bar: int | None = None
    break_direction: str | None = None  # Direction of the break


class InteractionDetector:
    """
    Detects and classifies interactions between price and key levels.

    Configuration:
    - touch_threshold_ticks: How close is a "touch" (default: 2)
    - reject_bars: Bars to confirm rejection (default: 3)
    - hold_bars: Bars to confirm break-and-hold (default: 3)
    - fakeout_bars: Max bars for fakeout-reclaim (default: 5)
    """

    def __init__(
        self,
        tick_size: Decimal = Decimal("0.25"),
        touch_threshold_ticks: int = 2,
        reject_bars: int = 3,
        hold_bars: int = 3,
        fakeout_bars: int = 5,
    ):
        self.tick_size = tick_size
        self.touch_threshold = tick_size * touch_threshold_ticks
        self.reject_bars = reject_bars
        self.hold_bars = hold_bars
        self.fakeout_bars = fakeout_bars

        # Tracking state per level
        self._level_states: dict[str, _LevelState] = {}

        # Bar counter (incremented per aggregated bar, not per tick)
        self._bar_count = 0

        # Recent prices for reversal detection
        self._recent_prices: deque[Decimal] = deque(maxlen=10)

        # Pending interactions to confirm
        self._pending: list[tuple[LevelInteraction, int]] = []

    def update_levels(self, levels: SessionLevels) -> None:
        """Update tracked levels from LevelStore."""
        level_map = {
            "pdh": levels.pdh,
            "pdl": levels.pdl,
            "pdc": levels.pdc,
            "orh": levels.orh,
            "orl": levels.orl,
            "vwap": levels.vwap,
        }

        for name, price in level_map.items():
            if price is not None:
                if name not in self._level_states:
                    self._level_states[name] = _LevelState(name=name, price=price)
                else:
                    self._level_states[name].price = price

    def on_bar_close(self, bar_close_price: Decimal, timestamp: datetime) -> list[LevelInteraction]:
        """
        Process a bar close and detect interactions.

        Returns list of confirmed interactions.
        """
        self._bar_count += 1
        self._recent_prices.append(bar_close_price)

        confirmed: list[LevelInteraction] = []

        for state in self._level_states.values():
            interaction = self._check_level(state, bar_close_price, timestamp)
            if interaction:
                confirmed.append(interaction)

        # Check pending interactions for confirmation
        confirmed.extend(self._process_pending(bar_close_price, timestamp))

        return confirmed

    def _check_level(
        self,
        state: _LevelState,
        price: Decimal,
        timestamp: datetime,
    ) -> LevelInteraction | None:
        """Check for interaction with a single level."""
        level_price = state.price
        distance = abs(price - level_price)

        # Determine current side
        if price > level_price:
            current_side = "above"
        elif price < level_price:
            current_side = "below"
        else:
            current_side = state.last_side or "above"

        interaction: LevelInteraction | None = None

        # Check for TOUCH
        if distance <= self.touch_threshold and state.touch_bar is None:
            state.touch_bar = self._bar_count
            # Create pending TOUCH (may upgrade to REJECT)
            interaction = LevelInteraction(
                timestamp=timestamp,
                level_name=state.name,
                level_price=level_price,
                interaction_type=InteractionType.TOUCH,
                price_at_interaction=price,
                direction=state.last_side or "unknown",
            )
            self._pending.append((interaction, self._bar_count))

        # Check for BREAK (crossed to other side)
        if state.last_side and current_side != state.last_side and state.break_bar is None:
            state.break_bar = self._bar_count
            state.break_direction = current_side
            logger.debug(f"Level {state.name} broken to {current_side} at bar {self._bar_count}")

        # Check for BREAK_AND_HOLD confirmation
        if state.break_bar is not None:
            bars_since_break = self._bar_count - state.break_bar
            if bars_since_break >= self.hold_bars:
                if current_side == state.break_direction:
                    # Confirmed break-and-hold
                    interaction = LevelInteraction(
                        timestamp=timestamp,
                        level_name=state.name,
                        level_price=level_price,
                        interaction_type=InteractionType.BREAK_AND_HOLD,
                        price_at_interaction=price,
                        direction=state.break_direction,
                    )
                    state.break_bar = None
                    state.break_direction = None
                else:
                    # Reclaimed - potential fakeout
                    if bars_since_break <= self.fakeout_bars:
                        interaction = LevelInteraction(
                            timestamp=timestamp,
                            level_name=state.name,
                            level_price=level_price,
                            interaction_type=InteractionType.FAKEOUT_RECLAIM,
                            price_at_interaction=price,
                            direction=current_side,
                        )
                    state.break_bar = None
                    state.break_direction = None

        state.last_side = current_side
        return interaction

    def _process_pending(
        self,
        current_price: Decimal,
        timestamp: datetime,
    ) -> list[LevelInteraction]:
        """Process pending touches for potential upgrade to REJECT."""
        confirmed: list[LevelInteraction] = []
        still_pending: list[tuple[LevelInteraction, int]] = []

        for interaction, touch_bar in self._pending:
            bars_since_touch = self._bar_count - touch_bar

            if bars_since_touch >= self.reject_bars:
                # Check if price has moved away (rejection)
                level_price = interaction.level_price
                distance = abs(current_price - level_price)

                # If price moved significantly away, upgrade to REJECT
                if distance > self.touch_threshold * 2:
                    reject_interaction = LevelInteraction(
                        timestamp=timestamp,
                        level_name=interaction.level_name,
                        level_price=level_price,
                        interaction_type=InteractionType.REJECT,
                        price_at_interaction=current_price,
                        direction=interaction.direction,
                    )
                    confirmed.append(reject_interaction)
                else:
                    # Just a touch, confirm as-is
                    confirmed.append(interaction)
            else:
                still_pending.append((interaction, touch_bar))

        self._pending = still_pending
        return confirmed

    def reset(self) -> None:
        """Reset all tracking state."""
        self._level_states.clear()
        self._bar_count = 0
        self._recent_prices.clear()
        self._pending.clear()
