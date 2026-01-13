"""Mean Reversion Strategy.

Trades extreme RSI conditions expecting price to revert to mean.
- Long: RSI oversold (<30), price near session low
- Short: RSI overbought (>70), price near session high
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


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy using RSI extremes.

    Trades when price reaches extreme levels, expecting a snap-back
    to the mean. Works best during choppy/range-bound days (60% of days).
    """

    def __init__(self, config, session_manager):
        super().__init__(config, session_manager)
        self.logger = logging.getLogger(__name__)
        self.reset()

    def reset(self) -> None:
        """Reset strategy state for new session."""
        self.session_date = None
        self.tick_size: Decimal | None = None

        # Session tracking
        self.session_high = Decimal("-Infinity")
        self.session_low = Decimal("Infinity")
        self.session_open: Decimal | None = None

        # RSI calculation components
        self.price_changes: deque[Decimal] = deque(maxlen=14)  # RSI period
        self.last_price: Decimal | None = None
        self.rsi: float = 50.0  # Start neutral

        # Price history for mean calculation
        self.prices: deque[Decimal] = deque(maxlen=200)
        self.mean_price: Decimal = Decimal("0")

        # Trade management
        self.trades_taken = 0
        self.last_trade_time: datetime | None = None
        self.long_triggered = False
        self.short_triggered = False

        # Cooldown reset
        self.trigger_cooldown: datetime | None = None

        # Logging throttle
        self._tick_count = 0
        self._last_log_time: datetime | None = None

    def _get_tick_size(self, symbol: str) -> Decimal:
        """Resolve tick size for symbol."""
        if "ES" in symbol or "EP" in symbol:
            return self.config.symbols.es.tick_size
        if "MES" in symbol:
            return self.config.symbols.mes.tick_size
        return Decimal("0.25")

    def _update_session_levels(self, price: Decimal) -> None:
        """Track session high/low/open."""
        if self.session_open is None:
            self.session_open = price
        if price > self.session_high or self.session_high == Decimal("-Infinity"):
            self.session_high = price
        if price < self.session_low or self.session_low == Decimal("Infinity"):
            self.session_low = price

    def _update_rsi(self, price: Decimal) -> None:
        """Calculate RSI(14) from price changes."""
        if self.last_price is None:
            self.last_price = price
            return

        change = price - self.last_price
        self.price_changes.append(change)
        self.last_price = price

        if len(self.price_changes) < 14:
            return

        # Calculate average gain and loss
        gains = [float(c) for c in self.price_changes if c > 0]
        losses = [abs(float(c)) for c in self.price_changes if c < 0]

        avg_gain = sum(gains) / 14 if gains else 0.001
        avg_loss = sum(losses) / 14 if losses else 0.001

        if avg_loss == 0:
            self.rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            self.rsi = 100 - (100 / (1 + rs))

    def _update_mean(self, price: Decimal) -> None:
        """Update rolling mean price."""
        self.prices.append(price)
        if len(self.prices) > 0:
            self.mean_price = sum(self.prices) / len(self.prices)

    def _is_near_session_low(self, price: Decimal, threshold_ticks: int = 8) -> bool:
        """Check if price is near session low."""
        if self.session_low == Decimal("Infinity") or self.tick_size is None:
            return False
        threshold = Decimal(str(threshold_ticks)) * self.tick_size
        return price - self.session_low <= threshold

    def _is_near_session_high(self, price: Decimal, threshold_ticks: int = 8) -> bool:
        """Check if price is near session high."""
        if self.session_high == Decimal("-Infinity") or self.tick_size is None:
            return False
        threshold = Decimal(str(threshold_ticks)) * self.tick_size
        return self.session_high - price <= threshold

    def _can_trade(self, now: datetime) -> bool:
        """Check if we can take another trade."""
        cfg = self.config.strategy.mean_reversion

        if self.trades_taken >= cfg.max_trades:
            return False

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
            self.logger.info(f"[MEAN_REV] New session: {date}")

        # Get config
        cfg = self.config.strategy.mean_reversion

        # Skip first N minutes (avoid opening volatility)
        rth_start = tick.timestamp.replace(
            hour=self.session.rth_start_time.hour,
            minute=self.session.rth_start_time.minute,
            second=0,
            microsecond=0,
        )
        mins_since_open = (tick.timestamp - rth_start).total_seconds() / 60

        if mins_since_open < cfg.skip_first_minutes:
            self._update_session_levels(tick.price)
            self._update_rsi(tick.price)
            self._update_mean(tick.price)
            return signals

        # Update all tracking
        self._update_session_levels(tick.price)
        self._update_rsi(tick.price)
        self._update_mean(tick.price)

        # Log state periodically
        if (
            self._last_log_time is None
            or (tick.timestamp - self._last_log_time).total_seconds() >= 60
        ):
            session_range = self.session_high - self.session_low
            self.logger.info(
                f"[MEAN_REV] Price: {tick.price} | RSI: {self.rsi:.1f} | "
                f"Range: {self.session_low}-{self.session_high} ({session_range:.2f} pts) | "
                f"Mean: {self.mean_price:.2f}"
            )
            self._last_log_time = tick.timestamp

        # Not enough data yet
        if len(self.price_changes) < 14:
            return signals

        if not self._can_trade(tick.timestamp):
            return signals

        # === LONG SIGNAL: RSI oversold + near session low ===
        if (
            self.rsi < cfg.rsi_oversold
            and cfg.direction in ["long", "both"]
            and not self.long_triggered
        ):
            if self._is_near_session_low(tick.price, cfg.level_threshold_ticks):
                self.logger.info("=" * 60)
                self.logger.info("[MEAN REVERSION] LONG SIGNAL!")
                self.logger.info(f"  Price: {tick.price} near session low: {self.session_low}")
                self.logger.info(f"  RSI: {self.rsi:.1f} (oversold < {cfg.rsi_oversold})")
                self.logger.info(f"  Mean: {self.mean_price:.2f} (target area)")
                self.logger.info("=" * 60)

                signals.append(
                    TradeSignal(
                        symbol=tick.symbol,
                        direction=SignalDirection.LONG,
                        timestamp=tick.timestamp,
                        quantity=1,
                        stop_ticks=cfg.stop_ticks,
                        target_ticks=cfg.target_ticks,
                        reason=f"Mean Reversion Long - RSI {self.rsi:.1f} near low {self.session_low}",
                    )
                )
                self.long_triggered = True
                self.trades_taken += 1
                self.last_trade_time = tick.timestamp

        # === SHORT SIGNAL: RSI overbought + near session high ===
        elif (
            self.rsi > cfg.rsi_overbought
            and cfg.direction in ["short", "both"]
            and not self.short_triggered
        ):
            if self._is_near_session_high(tick.price, cfg.level_threshold_ticks):
                self.logger.info("=" * 60)
                self.logger.info("[MEAN REVERSION] SHORT SIGNAL!")
                self.logger.info(f"  Price: {tick.price} near session high: {self.session_high}")
                self.logger.info(f"  RSI: {self.rsi:.1f} (overbought > {cfg.rsi_overbought})")
                self.logger.info(f"  Mean: {self.mean_price:.2f} (target area)")
                self.logger.info("=" * 60)

                signals.append(
                    TradeSignal(
                        symbol=tick.symbol,
                        direction=SignalDirection.SHORT,
                        timestamp=tick.timestamp,
                        quantity=1,
                        stop_ticks=cfg.stop_ticks,
                        target_ticks=cfg.target_ticks,
                        reason=f"Mean Reversion Short - RSI {self.rsi:.1f} near high {self.session_high}",
                    )
                )
                self.short_triggered = True
                self.trades_taken += 1
                self.last_trade_time = tick.timestamp

        # Reset triggers when RSI returns to neutral zone
        if 40 < self.rsi < 60:
            self.long_triggered = False
            self.short_triggered = False

        return signals

    def on_bar(self, bar: Bar) -> list[TradeSignal]:
        """Process bar data (not used for this tick-based strategy)."""
        return []
