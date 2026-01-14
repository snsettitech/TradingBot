"""Breakout Acceptance Playbook.

Strategy: Enter when price breaks a key level and holds for confirmation.

Entry Rules:
1. Price breaks PDH/PDL or ORH/ORL
2. Price holds above/below level for 3 bars (Break-and-Hold confirmed)
3. Entry on close of confirmation bar

Exit Rules:
- Stop: 8 ticks from entry
- Target: 16 ticks from entry
- Time Stop: 2 hours max hold
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
class BreakoutAcceptanceConfig:
    """Configuration for Breakout Acceptance playbook."""
    
    stop_ticks: int = 8
    target_ticks: int = 16
    time_stop_minutes: int = 120
    confirmation_bars: int = 3
    valid_levels: tuple = ("pdh", "pdl", "orh", "orl")


class BreakoutAcceptancePlaybook(BaseStrategy):
    """
    Breakout Acceptance Strategy.
    
    Enters after a Break-and-Hold interaction is confirmed.
    """
    
    name = "BreakoutAcceptance"
    
    def __init__(
        self,
        config: AppConfig,
        session_manager: SessionManager | None = None,
        playbook_config: BreakoutAcceptanceConfig | None = None,
    ):
        super().__init__(config, session_manager)
        self.pb_config = playbook_config or BreakoutAcceptanceConfig()
        self.tick_size = Decimal("0.25")
        
        # State
        self._pending_signal: TradeSignal | None = None
        self._last_interaction: LevelInteraction | None = None
    
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
        Generate signal from a level interaction.
        
        Args:
            interaction: Detected level interaction
            current_price: Current market price
            timestamp: Current time
        
        Returns:
            TradeSignal if entry conditions met, else None
        """
        # Only act on Break-and-Hold events
        if interaction.interaction_type != InteractionType.BREAK_AND_HOLD:
            return None
        
        # Only trade valid levels
        if interaction.level_name not in self.pb_config.valid_levels:
            return None
        
        # Determine direction based on break direction
        if interaction.direction == "above":
            direction = SignalDirection.LONG
            stop_price = current_price - (self.pb_config.stop_ticks * self.tick_size)
            target_price = current_price + (self.pb_config.target_ticks * self.tick_size)
        else:
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
            reason=f"Breakout Acceptance: {interaction.level_name.upper()} break confirmed",
            metadata={
                "playbook": self.name,
                "level": interaction.level_name,
                "level_price": str(interaction.level_price),
                "time_stop": time_stop.isoformat(),
            },
        )
        
        self._last_interaction = interaction
        logger.info(f"Breakout signal: {direction.value} at {current_price}, level={interaction.level_name}")
        
        return signal
    
    def get_skip_conditions(self, levels: SessionLevels | None = None) -> list[str]:
        """Return conditions under which this playbook should be skipped."""
        conditions = []
        
        if levels:
            # Skip if OR not formed yet
            if not levels.or_formed:
                conditions.append("Opening Range not yet formed")
            
            # Skip if no prior day levels
            if levels.pdh is None or levels.pdl is None:
                conditions.append("Prior day levels not available")
        
        return conditions
    
    def reset(self) -> None:
        """Reset strategy state."""
        self._pending_signal = None
        self._last_interaction = None
