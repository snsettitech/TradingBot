"""Liquidity Sweep Reclaim Strategy."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from tsxbot.constants import OrderType, SignalDirection
from tsxbot.data.indicators import find_swings
from tsxbot.strategies.base import BaseStrategy, TradeSignal

if TYPE_CHECKING:
    from tsxbot.data.bars import Bar
    from tsxbot.data.market_data import Tick


class SweepReclaimStrategy(BaseStrategy):
    """
    Liquidity Sweep Reclaim strategy implementation.

    Trades distinct liquidity sweeps of key swing highs/lows.
    """

    def __init__(self, config, session_manager):
        super().__init__(config, session_manager)
        self.reset()
        self.bar_minutes = 5  # Fixed 5-minute bars for structure (can be made configurable)

    def reset(self) -> None:
        """Reset strategy state."""
        self.bars: list[Bar] = []
        self.current_bar: dict | None = None
        self.bar_start_time: datetime | None = None
        self.trades_taken = 0
        self.last_trade_time: datetime | None = None
        self.session_date = None

    def _start_new_bar(self, tick: Tick) -> None:
        """Initialize a new bar."""
        # Align to minute boundary
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
        """Check if current bar is complete based on new timestamp."""
        if self.bar_start_time is None:
            return False

        # Calculate expected end time
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
        # Keep enough history for swing detection (lookback * 2 is safe buffer)
        cfg = self.config.strategy.sweep_reclaim
        max_len = cfg.lookback_bars * 3
        if len(self.bars) > max_len:
            self.bars = self.bars[-max_len:]

        self.current_bar = None
        self.bar_start_time = None

    def _get_tick_size(self, symbol: str) -> Decimal:
        """Resolve tick size."""
        if "ES" in symbol or "EP" in symbol:
            return self.config.symbols.es.tick_size
        if "MES" in symbol:
            return self.config.symbols.mes.tick_size
        return Decimal("0.25")

    def _process_bar(self, bar: Bar) -> TradeSignal | None:
        """Check for signals on bar close."""
        cfg = self.config.strategy.sweep_reclaim

        # Need enough bars
        if len(self.bars) < cfg.lookback_bars + 2:
            return None

        # Find Swings
        swings = find_swings(self.bars, window_size=5)  # Fixed window for definition of "Swing"
        if not swings:
            return None

        current_close = bar.close
        prev_low = self.bars[-2].low  # The bar before the one that just closed

        tick_size = self._get_tick_size(bar.symbol)

        # --- LOGIC: Iterate recent swings to find a "Swept" level ---
        # We look for a swing that was formed RECENTLY (within lookback)
        # And was breached by the PREVIOUS bar (the sweep candle)
        # And is now RECLAIMED by the CURRENT bar (the reclaim candle)

        # Actually, simpler logic:
        # 1. Identify valid Swing Highs/Lows that occurred within 'lookback_bars'
        # 2. Check if specific recent bars violated them and then closed back inside

        # Filter relevant swings (active within last N bars, but not TOO recent to be the current bar)
        relevant_swings = [
            s
            for s in swings
            if len(self.bars) - s["index"] <= cfg.lookback_bars
            and len(self.bars) - s["index"] > 2  # Swing must be formed "before" the sweep action
        ]

        for swing in relevant_swings:
            swing_price = swing["price"]
            swing_type = swing["type"]

            # --- LONG SETUP: Sweep of Swing Low ---
            if swing_type == "low":
                if cfg.direction not in ["long", "both"]:
                    continue

                # Check if we swept this low recently
                # Verify the low was actually broken by a recent bar (e.g. this bar or previous)
                low_broken = bar.low < swing_price - (Decimal(cfg.min_sweep_ticks) * tick_size)

                # Check Reclaim: Close > Swing Low
                reclaimed = bar.close > swing_price

                if low_broken and reclaimed:
                    # Valid Sweep Reclaim
                    self.logger.info(
                        f"Sweep Reclaim LONG: Swept low {swing_price} and closed at {bar.close}"
                    )

                    return TradeSignal(
                        symbol=bar.symbol,
                        direction=SignalDirection.LONG,
                        timestamp=bar.timestamp,
                        stop_ticks=cfg.stop_ticks,
                        target_ticks=cfg.target_ticks,
                        reason=f"Sweep Reclaim of {swing_price}",
                    )

            # --- SHORT SETUP: Sweep of Swing High ---
            elif swing_type == "high":
                if cfg.direction not in ["short", "both"]:
                    continue

                # Check breakdown
                high_broken = bar.high > swing_price + (Decimal(cfg.min_sweep_ticks) * tick_size)

                # Check Reclaim: Close < Swing High
                reclaimed = bar.close < swing_price

                if high_broken and reclaimed:
                    self.logger.info(
                        f"Sweep Reclaim SHORT: Swept high {swing_price} and closed at {bar.close}"
                    )

                    return TradeSignal(
                        symbol=bar.symbol,
                        direction=SignalDirection.SHORT,
                        timestamp=bar.timestamp,
                        stop_ticks=cfg.stop_ticks,
                        target_ticks=cfg.target_ticks,
                        reason=f"Sweep Reclaim of {swing_price}",
                    )

        return None

    def on_tick(self, tick: Tick) -> list[TradeSignal]:
        """Process incoming tick."""
        signals = []

        # RTH Check
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

            # Process completed bar
            if self.bars:
                signal = self._process_bar(self.bars[-1])
                if signal:
                    signals.append(signal)

            self._start_new_bar(tick)

        self._update_current_bar(tick)

        return signals

    def on_bar(self, bar: Bar) -> list[TradeSignal]:
        """External bar feed handler (not used for tick-driven)."""
        return []
