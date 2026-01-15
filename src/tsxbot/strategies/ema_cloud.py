"""Ripster EMA Cloud Strategy.

10-minute trend continuation strategy using EMA clouds.
- Fast cloud: 5-12 EMA
- Trend cloud: 34-50 EMA

Rules:
- Bullish bias ONLY when price above 34-50 cloud
- Bearish bias ONLY when price below 34-50 cloud
- Entry on pullback to 5-12 cloud with candle close confirmation
- Exit on 10-min candle close against 5-12 cloud
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from tsxbot.constants import SignalDirection
from tsxbot.data.indicators import Bar, calculate_ema_series
from tsxbot.strategies.base import BaseStrategy, TradeSignal

if TYPE_CHECKING:
    from tsxbot.data.market_data import Tick

logger = logging.getLogger(__name__)


class MarketBias(str, Enum):
    """Market bias based on trend cloud."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"  # Inside cloud or flat


class StrategyState(str, Enum):
    """Strategy state machine."""

    WAITING_PULLBACK = "waiting_pullback"
    PULLBACK_DETECTED = "pullback_detected"
    IN_TRADE = "in_trade"


@dataclass
class EMAValues:
    """Current EMA values for all periods."""

    ema_5: Decimal
    ema_12: Decimal
    ema_34: Decimal
    ema_50: Decimal

    @property
    def fast_cloud_top(self) -> Decimal:
        """Top of fast cloud (5-12)."""
        return max(self.ema_5, self.ema_12)

    @property
    def fast_cloud_bottom(self) -> Decimal:
        """Bottom of fast cloud (5-12)."""
        return min(self.ema_5, self.ema_12)

    @property
    def trend_cloud_top(self) -> Decimal:
        """Top of trend cloud (34-50)."""
        return max(self.ema_34, self.ema_50)

    @property
    def trend_cloud_bottom(self) -> Decimal:
        """Bottom of trend cloud (34-50)."""
        return min(self.ema_34, self.ema_50)

    @property
    def trend_cloud_separation(self) -> Decimal:
        """Separation between 34 and 50 EMA."""
        return abs(self.ema_34 - self.ema_50)


class EMACloudStrategy(BaseStrategy):
    """
    Ripster EMA Cloud Strategy.

    10-minute trend continuation with EMA clouds.
    High win rate when trading with established trends.
    """

    def __init__(self, config, session_manager):
        super().__init__(config, session_manager)
        self.logger = logging.getLogger(__name__)
        self.reset()

    def reset(self, keep_history: bool = False) -> None:
        """Reset strategy state for new session."""
        self.session_date = None
        self.tick_size = Decimal("0.25")

        # Bar aggregation
        if not keep_history:
            self.bars: list[Bar] = []
            self.bar_count = 0
            self.session_volume = 0
            self.emas = None

        self.current_bar: Bar | None = None
        self.bar_start_time: datetime | None = None

        # State machine
        self.state = StrategyState.WAITING_PULLBACK
        self.bias = MarketBias.NEUTRAL

        # Pullback tracking
        self.pullback_bar_count = 0

        # Trade management
        self.trades_taken = 0
        self.last_trade_time: datetime | None = None
        self.entry_price: Decimal | None = None
        self.entry_direction: SignalDirection | None = None

        # Logging throttle
        self._last_log_time: datetime | None = None

    def _get_cfg(self):
        """Get strategy config."""
        return self.config.strategy.ema_cloud

    def _is_bar_complete(self, tick_time: datetime) -> bool:
        """Check if current bar is complete based on time."""
        if self.bar_start_time is None:
            return False

        cfg = self._get_cfg()
        bar_duration = timedelta(minutes=cfg.bar_minutes)
        return tick_time >= self.bar_start_time + bar_duration

    def _start_new_bar(self, tick: Tick) -> None:
        """Start a new bar from tick."""
        cfg = self._get_cfg()

        # Align bar start to bar_minutes boundary
        minute = tick.timestamp.minute
        aligned_minute = (minute // cfg.bar_minutes) * cfg.bar_minutes
        self.bar_start_time = tick.timestamp.replace(
            minute=aligned_minute, second=0, microsecond=0
        )

        self.current_bar = Bar(
            timestamp=self.bar_start_time,
            open=tick.price,
            high=tick.price,
            low=tick.price,
            close=tick.price,
            volume=tick.volume,
        )

    def _update_current_bar(self, tick: Tick) -> None:
        """Update current bar with tick data."""
        if self.current_bar is None:
            return

        if tick.price > self.current_bar.high:
            self.current_bar.high = tick.price
        if tick.price < self.current_bar.low:
            self.current_bar.low = tick.price
        self.current_bar.close = tick.price
        self.current_bar.volume += tick.volume

    def _complete_bar(self) -> None:
        """Complete current bar and add to history."""
        if self.current_bar is None:
            return

        self.bars.append(self.current_bar)
        self.bar_count += 1
        self.session_volume += self.current_bar.volume

        # Limit bar history
        if len(self.bars) > 100:
            self.bars = self.bars[-100:]

        # Recalculate EMAs
        self._recalculate_emas()

        self.logger.debug(
            f"[EMA_CLOUD] Bar complete: O={self.current_bar.open} H={self.current_bar.high} "
            f"L={self.current_bar.low} C={self.current_bar.close} V={self.current_bar.volume}"
        )

    def _recalculate_emas(self) -> None:
        """Recalculate all EMA values."""
        cfg = self._get_cfg()

        if len(self.bars) < cfg.trend_ema_long:
            self.emas = None
            return

        ema_5_series = calculate_ema_series(self.bars, cfg.fast_ema_short)
        ema_12_series = calculate_ema_series(self.bars, cfg.fast_ema_long)
        ema_34_series = calculate_ema_series(self.bars, cfg.trend_ema_short)
        ema_50_series = calculate_ema_series(self.bars, cfg.trend_ema_long)

        self.emas = EMAValues(
            ema_5=ema_5_series[-1],
            ema_12=ema_12_series[-1],
            ema_34=ema_34_series[-1],
            ema_50=ema_50_series[-1],
        )

    def _determine_bias(self, close: Decimal) -> MarketBias:
        """Determine market bias based on price vs trend cloud."""
        if self.emas is None:
            return MarketBias.NEUTRAL

        cfg = self._get_cfg()

        # Check if EMAs are flat (compressed)
        separation_pts = self.emas.trend_cloud_separation
        min_separation = Decimal(str(cfg.min_cloud_separation_ticks)) * self.tick_size
        if separation_pts < min_separation:
            return MarketBias.NEUTRAL

        # Check price position
        if close > self.emas.trend_cloud_top and self.emas.ema_34 > self.emas.ema_50:
            return MarketBias.BULLISH
        elif close < self.emas.trend_cloud_bottom and self.emas.ema_34 < self.emas.ema_50:
            return MarketBias.BEARISH

        return MarketBias.NEUTRAL

    def _check_fast_cloud_alignment(self) -> bool:
        """Check if fast cloud aligns with bias."""
        if self.emas is None:
            return False

        if self.bias == MarketBias.BULLISH:
            return self.emas.ema_5 > self.emas.ema_12
        elif self.bias == MarketBias.BEARISH:
            return self.emas.ema_5 < self.emas.ema_12

        return False

    def _is_pullback_into_fast_cloud(self, bar: Bar) -> bool:
        """Check if bar pulled back into 5-12 cloud."""
        if self.emas is None:
            return False

        if self.bias == MarketBias.BULLISH:
            # Low touched or penetrated fast cloud
            return bar.low <= self.emas.fast_cloud_top
        elif self.bias == MarketBias.BEARISH:
            # High touched or penetrated fast cloud
            return bar.high >= self.emas.fast_cloud_bottom

        return False

    def _did_not_violate_trend_cloud(self, bar: Bar) -> bool:
        """Check if bar did NOT close beyond trend cloud."""
        if self.emas is None:
            return False

        if self.bias == MarketBias.BULLISH:
            # Must not close below trend cloud
            return bar.close > self.emas.trend_cloud_bottom
        elif self.bias == MarketBias.BEARISH:
            # Must not close above trend cloud
            return bar.close < self.emas.trend_cloud_top

        return False

    def _is_entry_candle(self, bar: Bar) -> bool:
        """Check if bar is a valid entry candle (close back outside fast cloud)."""
        if self.emas is None:
            return False

        if self.bias == MarketBias.BULLISH:
            # Bullish candle closes above fast cloud
            is_bullish = bar.close > bar.open
            closes_above = bar.close > self.emas.fast_cloud_top
            return is_bullish and closes_above
        elif self.bias == MarketBias.BEARISH:
            # Bearish candle closes below fast cloud
            is_bearish = bar.close < bar.open
            closes_below = bar.close < self.emas.fast_cloud_bottom
            return is_bearish and closes_below

        return False

    def _is_exit_condition(self, bar: Bar) -> bool:
        """Check if bar closes against 5-12 cloud (exit signal)."""
        if self.emas is None or self.entry_direction is None:
            return False

        if self.entry_direction == SignalDirection.LONG:
            # Exit if closes below fast cloud
            return bar.close < self.emas.fast_cloud_bottom
        elif self.entry_direction == SignalDirection.SHORT:
            # Exit if closes above fast cloud
            return bar.close > self.emas.fast_cloud_top

        return False

    def _can_trade(self, now: datetime) -> bool:
        """Check if we can take another trade."""
        cfg = self._get_cfg()

        # Max trades check
        if self.trades_taken >= cfg.max_trades:
            return False

        # Cooldown check
        if self.last_trade_time:
            cooldown = timedelta(minutes=cfg.cooldown_minutes)
            if now - self.last_trade_time < cooldown:
                return False

        return True

    def _check_volume_filter(self) -> bool:
        """Check if current bar volume meets threshold."""
        cfg = self._get_cfg()

        if self.bar_count == 0 or self.current_bar is None:
            return True  # Allow first bars

        avg_volume = self.session_volume / self.bar_count
        return self.current_bar.volume >= avg_volume * cfg.min_volume_ratio

    def _calculate_stop_price(self) -> Decimal:
        """Calculate stop loss price based on trend cloud."""
        if self.emas is None:
            return Decimal("0")

        cfg = self._get_cfg()
        buffer = Decimal(str(cfg.stop_buffer_ticks)) * self.tick_size

        if self.bias == MarketBias.BULLISH:
            return self.emas.trend_cloud_bottom - buffer
        elif self.bias == MarketBias.BEARISH:
            return self.emas.trend_cloud_top + buffer

        return Decimal("0")

    def _log_state(self, bar: Bar) -> None:
        """Log current state periodically."""
        now = datetime.now()
        if self._last_log_time and (now - self._last_log_time).total_seconds() < 60:
            return

        if self.emas is None:
            return

        self.logger.info(
            f"[EMA_CLOUD] Bar #{len(self.bars)} | Close: {bar.close} | "
            f"Bias: {self.bias.value} | State: {self.state.value} | "
            f"EMA5: {self.emas.ema_5:.2f} | EMA12: {self.emas.ema_12:.2f} | "
            f"EMA34: {self.emas.ema_34:.2f} | EMA50: {self.emas.ema_50:.2f}"
        )
        self._last_log_time = now

    def on_tick(self, tick: Tick) -> list[TradeSignal]:
        """Process incoming tick data."""
        signals: list[TradeSignal] = []

        # Only trade during RTH
        if not self.session.is_rth(tick.timestamp):
            return signals

        # New session check
        date = tick.timestamp.date()
        if self.session_date != date:
            # Keep history to maintain EMA clouds
            self.reset(keep_history=True)
            self.session_date = date
            self.logger.info(f"[EMA_CLOUD] New session: {date} (preserving indicator history)")

        cfg = self._get_cfg()

        # Bar aggregation
        if self.current_bar is None:
            self._start_new_bar(tick)
        elif self._is_bar_complete(tick.timestamp):
            # Complete current bar
            self._complete_bar()

            # Process completed bar for signals
            completed_bar = self.bars[-1]
            signal = self._process_bar(completed_bar)
            if signal:
                signals.append(signal)

            # Start new bar
            self._start_new_bar(tick)

        # Update current bar
        self._update_current_bar(tick)

        return signals

    def _process_bar(self, bar: Bar) -> TradeSignal | None:
        """Process completed bar for entry/exit signals."""
        cfg = self._get_cfg()

        # Need enough bars for EMA calculation
        if self.emas is None:
            return None

        # Update bias
        self.bias = self._determine_bias(bar.close)

        # Log state
        self._log_state(bar)

        # === EXIT LOGIC (checked first) ===
        if self.state == StrategyState.IN_TRADE and self._is_exit_condition(bar):
            self.logger.info("=" * 60)
            self.logger.info("[EMA_CLOUD] EXIT SIGNAL - 5-12 cloud violation!")
            self.logger.info(f"  Bar close: {bar.close}")
            self.logger.info(f"  Fast cloud: {self.emas.fast_cloud_bottom:.2f} - {self.emas.fast_cloud_top:.2f}")
            self.logger.info("=" * 60)

            self.state = StrategyState.WAITING_PULLBACK
            self.entry_direction = None
            self.entry_price = None
            # Note: Exit signal not returned here - position management handled elsewhere
            return None
        elif self.state == StrategyState.IN_TRADE:
            return None

        # === FILTER CHECKS ===
        # No trade if neutral bias
        if self.bias == MarketBias.NEUTRAL:
            self.state = StrategyState.WAITING_PULLBACK
            self.pullback_bar_count = 0
            return None

        # No trade if fast cloud not aligned
        if not self._check_fast_cloud_alignment():
            return None

        # Check direction filter
        if cfg.direction == "long" and self.bias != MarketBias.BULLISH:
            return None
        if cfg.direction == "short" and self.bias != MarketBias.BEARISH:
            return None

        # Volume filter
        if not self._check_volume_filter():
            return None

        # === PULLBACK DETECTION ===
        if (
            self.state == StrategyState.WAITING_PULLBACK
            and self._is_pullback_into_fast_cloud(bar)
            and self._did_not_violate_trend_cloud(bar)
        ):
            self.state = StrategyState.PULLBACK_DETECTED
            self.pullback_bar_count = 1
            self.logger.info(
                f"[EMA_CLOUD] Pullback detected - {self.bias.value} bias | "
                f"Bar touched fast cloud"
            )

        # === ENTRY LOGIC ===
        elif self.state == StrategyState.PULLBACK_DETECTED:
            self.pullback_bar_count += 1

            # Check if pullback failed (closed beyond trend cloud)
            if not self._did_not_violate_trend_cloud(bar):
                self.logger.info("[EMA_CLOUD] Pullback failed - trend cloud violated")
                self.state = StrategyState.WAITING_PULLBACK
                return None

            # Check for entry candle
            if self._is_entry_candle(bar) and self._can_trade(bar.timestamp):

                direction = (
                    SignalDirection.LONG
                    if self.bias == MarketBias.BULLISH
                    else SignalDirection.SHORT
                )
                stop_price = self._calculate_stop_price()
                stop_ticks = int(abs(bar.close - stop_price) / self.tick_size)

                self.logger.info("=" * 60)
                self.logger.info(f"[EMA_CLOUD] ENTRY SIGNAL - {direction.value.upper()}")
                self.logger.info(f"  Bias: {self.bias.value}")
                self.logger.info(f"  Entry price: {bar.close}")
                self.logger.info(f"  Stop price: {stop_price}")
                self.logger.info(f"  Pullback bars: {self.pullback_bar_count}")
                self.logger.info(f"  Trend cloud: {self.emas.trend_cloud_bottom:.2f} - {self.emas.trend_cloud_top:.2f}")
                self.logger.info(f"  Fast cloud: {self.emas.fast_cloud_bottom:.2f} - {self.emas.fast_cloud_top:.2f}")
                self.logger.info("=" * 60)

                self.state = StrategyState.IN_TRADE
                self.trades_taken += 1
                self.last_trade_time = bar.timestamp
                self.entry_price = bar.close
                self.entry_direction = direction

                return TradeSignal(
                    symbol=self.config.symbols.primary,
                    direction=direction,
                    timestamp=bar.timestamp,
                    quantity=1,
                    stop_ticks=stop_ticks,
                    target_ticks=stop_ticks * 2,  # 2:1 R:R default
                    reason=(
                        f"EMA Cloud {direction.value} - {self.bias.value} trend, "
                        f"pullback to 5-12 cloud, entry candle confirmed"
                    ),
                )

        return None

    def on_bar(self, bar: Bar) -> list[TradeSignal]:
        """Process bar data (alternative to tick aggregation)."""
        # This strategy aggregates its own bars from ticks
        # But can also accept pre-aggregated bars
        signals: list[TradeSignal] = []

        self.bars.append(bar)
        if len(self.bars) > 100:
            self.bars = self.bars[-100:]

        self._recalculate_emas()
        self.bar_count += 1
        self.session_volume += bar.volume

        signal = self._process_bar(bar)
        if signal:
            signals.append(signal)

        return signals

    def prime_history(self, bars: list[Bar]) -> None:
        """
        Prime the strategy with historical bars.
        Builds EMA history without triggering signals.
        """
        if not bars:
            return

        self.logger.info(f"[EMA_CLOUD] Priming strategy with {len(bars)} historical bars")

        # Sort bars by timestamp just in case
        sorted_bars = sorted(bars, key=lambda b: b.timestamp)

        for bar in sorted_bars:
            self.bars.append(bar)
            if len(self.bars) > 100:
                self.bars = self.bars[-100:]

            # Update session stats for volume filter
            self.bar_count += 1
            self.session_volume += bar.volume

            # Recalculate indicators
            self._recalculate_emas()

            # Update bias based on last bar
            if self.emas is not None:
                self.bias = self._determine_bias(bar.close)

        # Set session date to prevent immediate reset on first tick
        if self.bars:
            self.session_date = self.bars[-1].timestamp.date()

        self.logger.info(f"[EMA_CLOUD] Priming complete. Current bias: {self.bias.value}")
