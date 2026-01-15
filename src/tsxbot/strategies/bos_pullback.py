"""Break of Structure Pullback Strategy."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Optional

from tsxbot.constants import OrderType, SignalDirection
from tsxbot.data.indicators import find_swings
from tsxbot.strategies.base import BaseStrategy, TradeSignal

if TYPE_CHECKING:
    from tsxbot.data.bars import Bar
    from tsxbot.data.market_data import Tick


class BOSState(Enum):
    LOOKING_FOR_STRUCTURE = "looking_for_structure"
    WAITING_FOR_BOS = "waiting_for_bos"
    WAITING_FOR_PULLBACK = "waiting_for_pullback"
    ENTRY_TRIGGERED = "entry_triggered"


class BOSPullbackStrategy(BaseStrategy):
    """
    BOS Pullback strategy implementation.

    Trades pullbacks to the broken structure level (breakout retest).
    """

    def __init__(self, config, session_manager):
        super().__init__(config, session_manager)
        self.reset()
        self.bar_minutes = 5  # Fixed 5-minute bars

    def reset(self) -> None:
        """Reset strategy state."""
        self.bars: list[Bar] = []
        self.current_bar: dict | None = None
        self.bar_start_time: datetime | None = None
        self.trades_taken = 0
        self.last_trade_time: datetime | None = None
        self.session_date = None

        # State machine
        self.state = BOSState.LOOKING_FOR_STRUCTURE
        self.active_swing: dict | None = None  # The swing we are watching or have broken
        self.breakout_level: Decimal | None = None
        self.pullback_zone_top: Decimal | None = None
        self.pullback_zone_bottom: Decimal | None = None
        self.bos_direction: SignalDirection | None = None

    def _start_new_bar(self, tick: Tick) -> None:
        """Initialize a new bar."""
        minute = tick.timestamp.minute
        aligned_minute = (minute // self.bar_minutes) * self.bar_minutes
        self.bar_start_time = tick.timestamp.replace(minute=aligned_minute, second=0, microsecond=0)

        # Use a dict for mutable aggregation
        self.current_bar = {
            "timestamp": self.bar_start_time,
            "open": tick.price,
            "high": tick.price,
            "low": tick.price,
            "close": tick.price,
            "volume": tick.volume,
            "symbol": tick.symbol,
        }

    def _update_current_bar(self, tick: Tick) -> None:
        """Update current bar with new tick."""
        if self.current_bar is None:
            return

        self.current_bar["high"] = max(self.current_bar["high"], tick.price)
        self.current_bar["low"] = min(self.current_bar["low"], tick.price)
        self.current_bar["close"] = tick.price
        self.current_bar["volume"] += tick.volume

    def _is_bar_complete(self, timestamp: datetime) -> bool:
        """Check if current bar is complete."""
        if self.bar_start_time is None:
            return False
        next_bar_time = self.bar_start_time + timedelta(minutes=self.bar_minutes)
        return timestamp >= next_bar_time

    def _complete_bar(self) -> None:
        """Finalize current bar."""
        if self.current_bar is None:
            return

        from tsxbot.data.bars import Bar  # Local import

        frozen_bar = Bar(
            timestamp=self.current_bar["timestamp"],
            open=self.current_bar["open"],
            high=self.current_bar["high"],
            low=self.current_bar["low"],
            close=self.current_bar["close"],
            volume=self.current_bar["volume"],
            symbol=self.current_bar["symbol"],
        )

        self.bars.append(frozen_bar)
        if len(self.bars) > 100:
            self.bars = self.bars[-100:]

        self.current_bar = None
        self.bar_start_time = None

    def _get_tick_size(self, symbol: str) -> Decimal:
        """Resolve tick size."""
        if "ES" in symbol or "EP" in symbol:
            return self.config.symbols.es.tick_size
        return self.config.symbols.mes.tick_size if "MES" in symbol else Decimal("0.25")

    def _process_bar(self, bar: Bar) -> TradeSignal | None:
        """Process bar close for BOS logic."""
        cfg = self.config.strategy.bos_pullback

        # 1. State: LOOKING_FOR_STRUCTURE
        # Goal: Find the most significant recent Swing High/Low
        if self.state == BOSState.LOOKING_FOR_STRUCTURE:
            swings = find_swings(self.bars, window_size=5)
            if not swings:
                return None

            # Simple logic: Take the most recent completed swing
            # In production, we'd want more complex structure mapping
            last_swing = swings[-1]

            # Allow some time to pass since swing creation
            if len(self.bars) - last_swing["index"] < 3:
                return None

            self.active_swing = last_swing
            self.state = BOSState.WAITING_FOR_BOS
            return None

        # 2. State: WAITING_FOR_BOS
        # Goal: Wait for a confirmed CLOSE beyond the swing level
        if self.state == BOSState.WAITING_FOR_BOS and self.active_swing:
            swing_price = self.active_swing["price"]
            swing_type = self.active_swing["type"]

            # Reset if structure gets too old (e.g. 20 bars without break)
            if len(self.bars) - self.active_swing["index"] > 20:
                self.state = BOSState.LOOKING_FOR_STRUCTURE
                self.active_swing = None
                return None

            # Check Bullish BOS (Break of High)
            if swing_type == "high":
                if bar.close > swing_price:
                    self.bos_direction = SignalDirection.LONG
                    self.breakout_level = swing_price
                    self.state = BOSState.WAITING_FOR_PULLBACK
                    self.logger.info(f"BOS Long Validated (Low: {self.active_swing['price']})")

            # Check Bearish BOS (Break of Low)
            elif swing_type == "low":
                if bar.close < swing_price:
                    self.bos_direction = SignalDirection.SHORT
                    self.breakout_level = swing_price
                    self.state = BOSState.WAITING_FOR_PULLBACK
                    self.logger.info(f"BOS Short Validated (High: {self.active_swing['price']})")

            return None

        # 3. State: WAITING_FOR_PULLBACK
        # Goal: Wait for price to return to the breakout level
        if self.state == BOSState.WAITING_FOR_PULLBACK and self.breakout_level:
            # Check if we moved too far away (momentum trade missed) or invalidated
            # For simplicity, just wait for touch

            tick_size = self._get_tick_size(bar.symbol)
            tolerance = Decimal("2.0") * tick_size  # 2 ticks tolerance

            # Long Pullback (Retest High from above)
            if self.bos_direction == SignalDirection.LONG:
                # If price drops below breakout level substantially, it's failed
                if bar.close < self.breakout_level - (Decimal("10") * tick_size):
                    self.state = BOSState.LOOKING_FOR_STRUCTURE
                    return None

                # If we entered the zone (Near breakout level)
                if abs(bar.low - self.breakout_level) <= tolerance or (
                    bar.low <= self.breakout_level and bar.close >= self.breakout_level
                ):  # Dip and reclaim
                    self.state = BOSState.ENTRY_TRIGGERED  # Or reset to look for new structure
                    return TradeSignal(
                        symbol=bar.symbol,
                        direction=SignalDirection.LONG,
                        timestamp=bar.timestamp,
                        stop_ticks=cfg.stop_ticks,
                        target_rr=cfg.target_rr_ratio,
                        reason=f"BOS Pullback Long at {self.breakout_level}",
                    )

            # Short Pullback (Retest Low from below)
            elif self.bos_direction == SignalDirection.SHORT:
                # Failed break back up
                if bar.close > self.breakout_level + (Decimal("10") * tick_size):
                    self.state = BOSState.LOOKING_FOR_STRUCTURE
                    return None

                # Entered zone
                if abs(bar.high - self.breakout_level) <= tolerance or (
                    bar.high >= self.breakout_level and bar.close <= self.breakout_level
                ):
                    self.state = BOSState.ENTRY_TRIGGERED
                    return TradeSignal(
                        symbol=bar.symbol,
                        direction=SignalDirection.SHORT,
                        timestamp=bar.timestamp,
                        stop_ticks=cfg.stop_ticks,
                        target_rr=cfg.target_rr_ratio,
                        reason=f"BOS Pullback Short at {self.breakout_level}",
                    )

        # Reset if entry triggered (one trade per BOS)
        if self.state == BOSState.ENTRY_TRIGGERED:
            self.state = BOSState.LOOKING_FOR_STRUCTURE
            self.active_swing = None
            self.breakout_level = None

        return None

    def on_tick(self, tick: Tick) -> list[TradeSignal]:
        """Process incoming tick."""
        signals = []

        if not self.session.is_rth(tick.timestamp):
            return signals

        # Session Reset
        date = tick.timestamp.date()
        if self.session_date != date:
            self.reset()
            self.session_date = date

        # Bar Aggregation
        if self.current_bar is None:
            self._start_new_bar(tick)
        elif self._is_bar_complete(tick.timestamp):
            self._complete_bar()

            if self.bars:
                signal = self._process_bar(self.bars[-1])
                if signal:
                    signals.append(signal)

            self._start_new_bar(tick)

        self._update_current_bar(tick)

        return signals

    def on_bar(self, bar: Bar) -> list[TradeSignal]:
        return []
