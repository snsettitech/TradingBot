"""VWAP Bounce Strategy.

Trades pullbacks to VWAP in established trends.
- Long: Price trending above VWAP, pulls back to VWAP, shows rejection
- Short: Price trending below VWAP, rallies to VWAP, shows rejection
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from tsxbot.constants import SignalDirection
from tsxbot.strategies.base import BaseStrategy, TradeSignal

if TYPE_CHECKING:
    from tsxbot.data.bars import Bar
    from tsxbot.data.market_data import Tick


class VWAPBounceStrategy(BaseStrategy):
    """
    VWAP Bounce/Rejection Strategy.

    Trades pullbacks to VWAP when price has established a trend direction.
    High win rate (60-70%) strategy that works well on trending days.
    """

    def __init__(self, config, session_manager):
        super().__init__(config, session_manager)
        self.logger = logging.getLogger(__name__)
        self.reset()

    def reset(self) -> None:
        """Reset strategy state for new session."""
        self.session_date = None
        self.tick_size: Decimal | None = None

        # VWAP calculation components
        self.cumulative_volume = Decimal("0")
        self.cumulative_tp_volume = Decimal("0")  # Typical Price * Volume
        self.vwap = Decimal("0")

        # Session tracking
        self.session_high = Decimal("-Infinity")
        self.session_low = Decimal("Infinity")

        # Trend tracking (last N prices to determine trend)
        self.price_history: deque[Decimal] = deque(maxlen=100)

        # State for bounce detection
        self.trend_direction: str | None = None  # "bullish", "bearish", None
        self.prices_above_vwap_count = 0
        self.prices_below_vwap_count = 0
        self.trend_window = 50  # ticks to establish trend

        # Rejection candle detection (simplified for tick-based)
        self.recent_prices: deque[Decimal] = deque(maxlen=5)

        # Trade management
        self.trades_taken = 0
        self.last_trade_time: datetime | None = None
        self.long_triggered = False
        self.short_triggered = False

        # Logging throttle
        self._tick_count = 0
        self._last_vwap_log: datetime | None = None

    def _get_tick_size(self, symbol: str) -> Decimal:
        """Resolve tick size for symbol."""
        if "ES" in symbol or "EP" in symbol:
            return self.config.symbols.es.tick_size
        if "MES" in symbol:
            return self.config.symbols.mes.tick_size
        return Decimal("0.25")

    def _update_vwap(self, price: Decimal, volume: int) -> None:
        """Update VWAP with new tick data."""
        if volume <= 0:
            return

        vol = Decimal(str(volume))
        typical_price = price  # For ticks, price IS the typical price

        self.cumulative_volume += vol
        self.cumulative_tp_volume += typical_price * vol

        if self.cumulative_volume > 0:
            self.vwap = self.cumulative_tp_volume / self.cumulative_volume

    def _update_session_levels(self, price: Decimal) -> None:
        """Track session high/low."""
        if price > self.session_high or self.session_high == Decimal("-Infinity"):
            self.session_high = price
        if price < self.session_low or self.session_low == Decimal("Infinity"):
            self.session_low = price

    def _update_trend(self, price: Decimal) -> None:
        """Determine trend based on price position relative to VWAP."""
        if self.vwap <= 0:
            return

        # Count recent prices above/below VWAP
        if price > self.vwap:
            self.prices_above_vwap_count += 1
            self.prices_below_vwap_count = max(0, self.prices_below_vwap_count - 1)
        else:
            self.prices_below_vwap_count += 1
            self.prices_above_vwap_count = max(0, self.prices_above_vwap_count - 1)

        # Establish trend when we have enough data
        if self.prices_above_vwap_count > self.trend_window:
            self.trend_direction = "bullish"
        elif self.prices_below_vwap_count > self.trend_window:
            self.trend_direction = "bearish"
        else:
            self.trend_direction = None  # No clear trend

    def _is_near_vwap(self, price: Decimal, threshold_ticks: int = 3) -> bool:
        """Check if price is within threshold of VWAP."""
        if self.vwap <= 0 or self.tick_size is None:
            return False
        threshold = Decimal(str(threshold_ticks)) * self.tick_size
        return abs(price - self.vwap) <= threshold

    def _is_rejection(self, price: Decimal) -> bool:
        """
        Detect rejection pattern from VWAP.
        For bullish: price touches VWAP then moves up (lower wick pattern)
        For bearish: price touches VWAP then moves down (upper wick pattern)
        """
        if len(self.recent_prices) < 3:
            return False

        prices = list(self.recent_prices)

        if self.trend_direction == "bullish":
            # Look for bounce pattern: went down to VWAP, now moving up
            low_price = min(prices)
            return (
                self._is_near_vwap(low_price, threshold_ticks=2)
                and price > low_price
                and price > self.vwap
            )
        elif self.trend_direction == "bearish":
            # Look for rejection pattern: went up to VWAP, now moving down
            high_price = max(prices)
            return (
                self._is_near_vwap(high_price, threshold_ticks=2)
                and price < high_price
                and price < self.vwap
            )
        return False

    def _can_trade(self, now: datetime) -> bool:
        """Check if we can take another trade."""
        cfg = self.config.strategy.vwap_bounce

        # Max trades check
        if self.trades_taken >= cfg.max_trades:
            return False

        # Cooldown between trades
        if self.last_trade_time:
            cooldown = timedelta(minutes=cfg.cooldown_minutes)
            if now - self.last_trade_time < cooldown:
                return False

        return True

    def on_tick(self, tick: Tick) -> list[TradeSignal]:
        signals = []
        self._tick_count += 1

        # Initialize tick size
        if self.tick_size is None:
            self.tick_size = self._get_tick_size(tick.symbol)

        # Only trade during RTH
        if not self.session.is_rth(tick.timestamp):
            return signals

        # New session check
        date = tick.timestamp.date()
        if self.session_date != date:
            self.reset()
            self.session_date = date
            self.tick_size = self._get_tick_size(tick.symbol)
            self.logger.info(f"[VWAP] New session: {date}")

        # Get config
        cfg = self.config.strategy.vwap_bounce

        # Skip first N minutes (let ORB run first if enabled)
        rth_start = tick.timestamp.replace(
            hour=self.session.rth_start_time.hour,
            minute=self.session.rth_start_time.minute,
            second=0,
            microsecond=0,
        )
        mins_since_open = (tick.timestamp - rth_start).total_seconds() / 60

        if mins_since_open < cfg.skip_first_minutes:
            # Still update VWAP though
            self._update_vwap(tick.price, tick.volume)
            self._update_session_levels(tick.price)
            return signals

        # Update all tracking
        self._update_vwap(tick.price, tick.volume)
        self._update_session_levels(tick.price)
        self._update_trend(tick.price)
        self.recent_prices.append(tick.price)

        # Log VWAP state periodically
        if (
            self._last_vwap_log is None
            or (tick.timestamp - self._last_vwap_log).total_seconds() >= 60
        ):
            self.logger.info(
                f"[VWAP] Price: {tick.price} | VWAP: {self.vwap:.2f} | "
                f"Trend: {self.trend_direction or 'None'} | "
                f"Distance: {float(tick.price - self.vwap):+.2f} pts"
            )
            self._last_vwap_log = tick.timestamp

        # No signals if no trend or can't trade
        if not self.trend_direction:
            return signals
        if not self._can_trade(tick.timestamp):
            return signals

        # === LONG SIGNAL: Bullish trend + VWAP bounce ===
        if (
            self.trend_direction == "bullish"
            and cfg.direction in ["long", "both"]
            and not self.long_triggered
        ):
            if self._is_near_vwap(tick.price, cfg.touch_threshold_ticks):
                # Wait for rejection
                if self._is_rejection(tick.price) and tick.price > self.vwap:
                    self.logger.info("=" * 60)
                    self.logger.info("[VWAP BOUNCE] LONG SIGNAL!")
                    self.logger.info(f"  Price: {tick.price} bounced from VWAP: {self.vwap:.2f}")
                    self.logger.info("  Trend: BULLISH")
                    self.logger.info("=" * 60)

                    signals.append(
                        TradeSignal(
                            symbol=tick.symbol,
                            direction=SignalDirection.LONG,
                            timestamp=tick.timestamp,
                            quantity=1,
                            stop_ticks=cfg.stop_ticks,
                            target_ticks=cfg.target_ticks,
                            reason=f"VWAP Bounce Long - price {tick.price} bounced from VWAP {self.vwap:.2f}",
                        )
                    )
                    self.long_triggered = True
                    self.trades_taken += 1
                    self.last_trade_time = tick.timestamp

        # === SHORT SIGNAL: Bearish trend + VWAP rejection ===
        elif (
            self.trend_direction == "bearish"
            and cfg.direction in ["short", "both"]
            and not self.short_triggered
        ):
            if self._is_near_vwap(tick.price, cfg.touch_threshold_ticks):
                if self._is_rejection(tick.price) and tick.price < self.vwap:
                    self.logger.info("=" * 60)
                    self.logger.info("[VWAP REJECTION] SHORT SIGNAL!")
                    self.logger.info(f"  Price: {tick.price} rejected from VWAP: {self.vwap:.2f}")
                    self.logger.info("  Trend: BEARISH")
                    self.logger.info("=" * 60)

                    signals.append(
                        TradeSignal(
                            symbol=tick.symbol,
                            direction=SignalDirection.SHORT,
                            timestamp=tick.timestamp,
                            quantity=1,
                            stop_ticks=cfg.stop_ticks,
                            target_ticks=cfg.target_ticks,
                            reason=f"VWAP Rejection Short - price {tick.price} rejected from VWAP {self.vwap:.2f}",
                        )
                    )
                    self.short_triggered = True
                    self.trades_taken += 1
                    self.last_trade_time = tick.timestamp

        return signals

    def on_bar(self, bar: Bar) -> list[TradeSignal]:
        """Process bar data (not used for this tick-based strategy)."""
        return []
