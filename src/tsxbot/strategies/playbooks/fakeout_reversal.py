"""Fakeout Reversal Playbook.

Strategy: Fade false breakouts that fail and reclaim the level.

Entry Rules:
1. Price breaks a key level (PDH/PDL, ORH/ORL)
2. Break fails within 5 bars (price reclaims level)
3. Enter in reversal direction on reclaim

Exit Rules:
- Stop: 6 ticks from entry
- Target: 12 ticks from entry
- Time Stop: 90 minutes max hold
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from tsxbot.constants import SignalDirection
from tsxbot.intelligence.interaction_detector import InteractionType, LevelInteraction
from tsxbot.strategies.base import BaseStrategy, TradeSignal

if TYPE_CHECKING:
    from tsxbot.config_loader import AppConfig
    from tsxbot.data.market_data import Tick
    from tsxbot.intelligence.level_store import SessionLevels
    from tsxbot.time.session_manager import SessionManager

logger = logging.getLogger(__name__)


@dataclass
class FakeoutReversalConfig:
    """Configuration for Fakeout Reversal playbook."""
    
    stop_ticks: int = 6
    target_ticks: int = 12
    time_stop_minutes: int = 90
    valid_levels: tuple = ("pdh", "pdl", "orh", "orl")


class FakeoutReversalPlaybook(BaseStrategy):
    """
    Fakeout Reversal Strategy.
    
    Fades failed breakouts when price reclaims the level.
    """
    
    name = "FakeoutReversal"
    
    def __init__(
        self,
        config: AppConfig,
        session_manager: SessionManager | None = None,
        playbook_config: FakeoutReversalConfig | None = None,
    ):
        super().__init__(config, session_manager)
        self.pb_config = playbook_config or FakeoutReversalConfig()
        self.tick_size = Decimal("0.25")
    
    def on_tick(self, tick: Tick) -> list[TradeSignal]:
        """Process tick - not used for this bar-based strategy."""
        return []
    
    def on_interaction(
        self,
        interaction: LevelInteraction,
        current_price: Decimal,
        timestamp: datetime,
    ) -> TradeSignal | None:
        """
        Generate signal from a fakeout-reclaim interaction.
        """
        # Only act on Fakeout-Reclaim events
        if interaction.interaction_type != InteractionType.FAKEOUT_RECLAIM:
            return None
        
        # Only trade valid levels
        if interaction.level_name not in self.pb_config.valid_levels:
            return None
        
        # Direction is OPPOSITE of the failed break
        # If price tried to break above and failed, we go SHORT
        # If price tried to break below and failed, we go LONG
        if interaction.direction == "above":
            # Reclaimed from above = failed upside break = SHORT
            direction = SignalDirection.SHORT
            stop_price = current_price + (self.pb_config.stop_ticks * self.tick_size)
            target_price = current_price - (self.pb_config.target_ticks * self.tick_size)
        else:
            # Reclaimed from below = failed downside break = LONG
            direction = SignalDirection.LONG
            stop_price = current_price - (self.pb_config.stop_ticks * self.tick_size)
            target_price = current_price + (self.pb_config.target_ticks * self.tick_size)
        
        time_stop = timestamp + timedelta(minutes=self.pb_config.time_stop_minutes)
        
        signal = TradeSignal(
            timestamp=timestamp,
            symbol=self.config.symbols.primary,
            direction=direction,
            quantity=1,
            entry_price=current_price,
            stop_price=stop_price,
            target_price=target_price,
            reason=f"Fakeout Reversal: {interaction.level_name.upper()} false break faded",
            metadata={
                "playbook": self.name,
                "level": interaction.level_name,
                "level_price": str(interaction.level_price),
                "time_stop": time_stop.isoformat(),
            },
        )
        
        logger.info(f"Fakeout signal: {direction.value} at {current_price}, level={interaction.level_name}")
        
        return signal
    
    def get_skip_conditions(self, levels: SessionLevels | None = None) -> list[str]:
        """Return conditions under which this playbook should be skipped."""
        conditions = []
        
        if levels:
            if not levels.or_formed:
                conditions.append("Opening Range not yet formed")
        
        return conditions
    
    def reset(self) -> None:
        """Reset strategy state."""
        pass
