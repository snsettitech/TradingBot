"""ORB Pullback Playbook.

Strategy: Enter on pullback after ORB breakout confirmation.

Entry Rules:
1. Price breaks ORH or ORL (Opening Range Breakout)
2. Wait for pullback toward the OR level
3. Enter when pullback holds and resumes breakout direction

Exit Rules:
- Stop: 10 ticks from entry
- Target: 20 ticks from entry
- Time Stop: 2 hours max hold
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from tsxbot.constants import SignalDirection
from tsxbot.strategies.base import BaseStrategy, TradeSignal

if TYPE_CHECKING:
    from tsxbot.config_loader import AppConfig
    from tsxbot.data.market_data import Tick
    from tsxbot.intelligence.feature_snapshot import FeatureSnapshot
    from tsxbot.intelligence.level_store import SessionLevels
    from tsxbot.time.session_manager import SessionManager

logger = logging.getLogger(__name__)


class ORBState(Enum):
    """State machine for ORB Pullback."""
    WAITING_FOR_OR = "waiting_for_or"
    WAITING_FOR_BREAKOUT = "waiting_for_breakout"
    WAITING_FOR_PULLBACK = "waiting_for_pullback"
    WAITING_FOR_ENTRY = "waiting_for_entry"
    DONE = "done"


@dataclass
class ORBPullbackConfig:
    """Configuration for ORB Pullback playbook."""
    
    stop_ticks: int = 10
    target_ticks: int = 20
    time_stop_minutes: int = 120
    pullback_threshold_pct: float = 0.5  # 50% retracement of breakout
    breakout_buffer_ticks: int = 2
    max_trades_per_session: int = 2


class ORBPullbackPlaybook(BaseStrategy):
    """
    ORB Pullback Strategy.
    
    Two-stage entry:
    1. Wait for ORB (breakout above ORH or below ORL)
    2. Wait for pullback, enter on hold
    """
    
    name = "ORBPullback"
    
    def __init__(
        self,
        config: AppConfig,
        session_manager: SessionManager | None = None,
        playbook_config: ORBPullbackConfig | None = None,
    ):
        super().__init__(config, session_manager)
        self.pb_config = playbook_config or ORBPullbackConfig()
        self.tick_size = Decimal("0.25")
        
        # State machine
        self._state = ORBState.WAITING_FOR_OR
        self._breakout_direction: SignalDirection | None = None
        self._breakout_price: Decimal | None = None
        self._pullback_low: Decimal | None = None
        self._pullback_high: Decimal | None = None
        self._trades_today = 0
    
    def on_tick(self, tick: Tick) -> list[TradeSignal]:
        """Process tick for ORB Pullback strategy."""
        # This strategy is designed for bar-based processing
        # Use on_bar for actual signal generation
        return []
    
    def on_bar(
        self,
        bar_close: Decimal,
        timestamp: datetime,
        levels: SessionLevels | None = None,
    ) -> TradeSignal | None:
        """
        Process bar close for ORB Pullback.
        
        Args:
            bar_close: Close price of the bar
            timestamp: Bar timestamp
            levels: Current session levels
        
        Returns:
            TradeSignal if entry conditions met
        """
        if levels is None:
            return None
        
        # Check max trades
        if self._trades_today >= self.pb_config.max_trades_per_session:
            return None
        
        # State machine
        if self._state == ORBState.WAITING_FOR_OR:
            if levels.or_formed:
                self._state = ORBState.WAITING_FOR_BREAKOUT
                logger.debug("ORB Pullback: OR formed, waiting for breakout")
        
        elif self._state == ORBState.WAITING_FOR_BREAKOUT:
            signal = self._check_breakout(bar_close, levels)
            if signal:
                return signal
        
        elif self._state == ORBState.WAITING_FOR_PULLBACK:
            self._track_pullback(bar_close)
            if self._is_pullback_complete(bar_close, levels):
                self._state = ORBState.WAITING_FOR_ENTRY
        
        elif self._state == ORBState.WAITING_FOR_ENTRY:
            signal = self._check_entry(bar_close, timestamp, levels)
            if signal:
                return signal
        
        return None
    
    def _check_breakout(self, price: Decimal, levels: SessionLevels) -> TradeSignal | None:
        """Check for ORB breakout."""
        if levels.orh is None or levels.orl is None:
            return None
        
        buffer = self.pb_config.breakout_buffer_ticks * self.tick_size
        
        # Long breakout
        if price > levels.orh + buffer:
            self._breakout_direction = SignalDirection.LONG
            self._breakout_price = price
            self._pullback_high = price
            self._pullback_low = price
            self._state = ORBState.WAITING_FOR_PULLBACK
            logger.debug(f"ORB Pullback: Long breakout at {price}")
        
        # Short breakout
        elif price < levels.orl - buffer:
            self._breakout_direction = SignalDirection.SHORT
            self._breakout_price = price
            self._pullback_high = price
            self._pullback_low = price
            self._state = ORBState.WAITING_FOR_PULLBACK
            logger.debug(f"ORB Pullback: Short breakout at {price}")
        
        return None
    
    def _track_pullback(self, price: Decimal) -> None:
        """Track pullback extremes."""
        if self._pullback_high is None or price > self._pullback_high:
            self._pullback_high = price
        if self._pullback_low is None or price < self._pullback_low:
            self._pullback_low = price
    
    def _is_pullback_complete(self, price: Decimal, levels: SessionLevels) -> bool:
        """Check if pullback is sufficient."""
        if self._breakout_price is None or self._breakout_direction is None:
            return False
        
        if levels.orh is None or levels.orl is None:
            return False
        
        # For long: price should pull back toward ORH
        if self._breakout_direction == SignalDirection.LONG:
            breakout_distance = self._breakout_price - levels.orh
            pullback_distance = self._breakout_price - price
            if breakout_distance > 0:
                retracement = pullback_distance / breakout_distance
                return retracement >= self.pb_config.pullback_threshold_pct
        
        # For short: price should pull back toward ORL
        else:
            breakout_distance = levels.orl - self._breakout_price
            pullback_distance = price - self._breakout_price
            if breakout_distance > 0:
                retracement = pullback_distance / breakout_distance
                return retracement >= self.pb_config.pullback_threshold_pct
        
        return False
    
    def _check_entry(
        self,
        price: Decimal,
        timestamp: datetime,
        levels: SessionLevels,
    ) -> TradeSignal | None:
        """Check for entry after pullback."""
        if self._breakout_direction is None:
            return None
        
        if levels.orh is None or levels.orl is None:
            return None
        
        # For long: enter when price moves back above pullback high
        if self._breakout_direction == SignalDirection.LONG:
            if self._pullback_low and price > self._pullback_low + (2 * self.tick_size):
                return self._create_signal(price, timestamp, SignalDirection.LONG)
        
        # For short: enter when price moves back below pullback low
        else:
            if self._pullback_high and price < self._pullback_high - (2 * self.tick_size):
                return self._create_signal(price, timestamp, SignalDirection.SHORT)
        
        return None
    
    def _create_signal(
        self,
        price: Decimal,
        timestamp: datetime,
        direction: SignalDirection,
    ) -> TradeSignal:
        """Create trade signal."""
        if direction == SignalDirection.LONG:
            stop_price = price - (self.pb_config.stop_ticks * self.tick_size)
            target_price = price + (self.pb_config.target_ticks * self.tick_size)
        else:
            stop_price = price + (self.pb_config.stop_ticks * self.tick_size)
            target_price = price - (self.pb_config.target_ticks * self.tick_size)
        
        time_stop = timestamp + timedelta(minutes=self.pb_config.time_stop_minutes)
        
        signal = TradeSignal(
            timestamp=timestamp,
            symbol=self.config.symbols.primary,
            direction=direction,
            quantity=1,
            entry_price=price,
            stop_price=stop_price,
            target_price=target_price,
            reason=f"ORB Pullback: {direction.value} entry after pullback",
            metadata={
                "playbook": self.name,
                "breakout_price": str(self._breakout_price),
                "time_stop": time_stop.isoformat(),
            },
        )
        
        self._state = ORBState.DONE
        self._trades_today += 1
        
        logger.info(f"ORB Pullback signal: {direction.value} at {price}")
        return signal
    
    def get_skip_conditions(self, levels: SessionLevels | None = None) -> list[str]:
        """Return conditions under which this playbook should be skipped."""
        conditions = []
        
        if self._trades_today >= self.pb_config.max_trades_per_session:
            conditions.append(f"Max trades ({self.pb_config.max_trades_per_session}) reached for session")
        
        if self._state == ORBState.DONE:
            conditions.append("Already traded ORB today")
        
        return conditions
    
    def reset(self) -> None:
        """Reset strategy state for new session."""
        self._state = ORBState.WAITING_FOR_OR
        self._breakout_direction = None
        self._breakout_price = None
        self._pullback_low = None
        self._pullback_high = None
        self._trades_today = 0
