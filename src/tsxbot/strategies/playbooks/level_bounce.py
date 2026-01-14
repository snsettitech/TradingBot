"""Level Bounce Playbook.

Strategy: Enter on rejection from a key level.

Entry Rules:
1. Price touches a key level (within 2 ticks)
2. Price rejects (moves away 3+ bars)
3. Enter in direction of rejection

Exit Rules:
- Stop: 6 ticks from entry
- Target: 10 ticks from entry
- Time Stop: 60 minutes max hold
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
class LevelBounceConfig:
    """Configuration for Level Bounce playbook."""
    
    stop_ticks: int = 6
    target_ticks: int = 10
    time_stop_minutes: int = 60
    valid_levels: tuple = ("pdh", "pdl", "pdc", "orh", "orl", "vwap")


class LevelBouncePlaybook(BaseStrategy):
    """
    Level Bounce Strategy.
    
    Enters after a Reject interaction is confirmed.
    """
    
    name = "LevelBounce"
    
    def __init__(
        self,
        config: AppConfig,
        session_manager: SessionManager | None = None,
        playbook_config: LevelBounceConfig | None = None,
    ):
        super().__init__(config, session_manager)
        self.pb_config = playbook_config or LevelBounceConfig()
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
        Generate signal from a rejection interaction.
        """
        # Only act on Reject events
        if interaction.interaction_type != InteractionType.REJECT:
            return None
        
        # Only trade valid levels
        if interaction.level_name not in self.pb_config.valid_levels:
            return None
        
        # Direction is based on where price came from
        # If approached from below and rejected, we go SHORT (bounce down)
        # If approached from above and rejected, we go LONG (bounce up)
        level_price = interaction.level_price
        
        if current_price > level_price:
            # Price is above level after rejection = bounced up = LONG
            direction = SignalDirection.LONG
            stop_price = current_price - (self.pb_config.stop_ticks * self.tick_size)
            target_price = current_price + (self.pb_config.target_ticks * self.tick_size)
        else:
            # Price is below level after rejection = bounced down = SHORT
            direction = SignalDirection.SHORT
            stop_price = current_price + (self.pb_config.stop_ticks * self.tick_size)
            target_price = current_price - (self.pb_config.target_ticks * self.tick_size)
        
        time_stop = timestamp + timedelta(minutes=self.pb_config.time_stop_minutes)
        
        signal = TradeSignal(
            timestamp=timestamp,
            symbol=self.config.symbols.primary,
            direction=direction,
            quantity=1,
            entry_price=current_price,
            stop_price=stop_price,
            target_price=target_price,
            reason=f"Level Bounce: Rejected at {interaction.level_name.upper()}",
            metadata={
                "playbook": self.name,
                "level": interaction.level_name,
                "level_price": str(interaction.level_price),
                "time_stop": time_stop.isoformat(),
            },
        )
        
        logger.info(f"Bounce signal: {direction.value} at {current_price}, level={interaction.level_name}")
        
        return signal
    
    def get_skip_conditions(self, levels: SessionLevels | None = None) -> list[str]:
        """Return conditions under which this playbook should be skipped."""
        conditions = []
        
        # Skip in first 5 minutes (too volatile)
        if levels and not levels.or_formed:
            conditions.append("Wait for Opening Range to form")
        
        return conditions
    
    def reset(self) -> None:
        """Reset strategy state."""
        pass
