"""Opening Range Breakout Strategy."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from tsxbot.constants import SignalDirection
from tsxbot.strategies.base import BaseStrategy, TradeSignal

if TYPE_CHECKING:
    from tsxbot.data.bars import Bar
    from tsxbot.data.market_data import Tick


class ORBStrategy(BaseStrategy):
    """
    Opening Range Breakout (ORB) strategy.
    """

    def __init__(self, config, session_manager):
        super().__init__(config, session_manager)
        self.reset()
        self._last_log_time = None  # Throttle logging
        self._tick_count = 0

    def reset(self) -> None:
        self.range_high = Decimal("-Infinity")
        self.range_low = Decimal("Infinity")
        self.range_formed = False
        self.trades_taken = 0
        self.session_date = None
        self.tick_size: Decimal | None = None

        # Latches to prevent signal spam
        self.long_triggered = False
        self.short_triggered = False

    def _update_range(self, price: Decimal) -> None:
        if self.range_high == Decimal("-Infinity"):
            self.range_high = price
            self.range_low = price
        else:
            self.range_high = max(self.range_high, price)
            self.range_low = min(self.range_low, price)

    def _get_tick_size(self, symbol: str) -> Decimal:
        """Resolve tick size for symbol."""
        if symbol == self.config.symbols.es.contract_id_prefix or "ES" in symbol or "EP" in symbol:
            return self.config.symbols.es.tick_size
        if symbol == self.config.symbols.mes.contract_id_prefix or "MES" in symbol:
            return self.config.symbols.mes.tick_size
        return Decimal("0.25")

    def on_tick(self, tick: Tick) -> list[TradeSignal]:
        signals = []
        self._tick_count += 1

        if self.tick_size is None:
            self.tick_size = self._get_tick_size(tick.symbol)

        # Check if we're in RTH
        if not self.session.is_rth(tick.timestamp):
            # Log occasionally outside RTH
            if self._tick_count % 500 == 0:
                self.logger.debug(f"Outside RTH - {tick.timestamp.strftime('%H:%M:%S')}")
            return signals

        date = tick.timestamp.date()
        if self.session_date != date:
            self.reset()
            self.session_date = date
            self.tick_size = self._get_tick_size(tick.symbol)
            self.logger.info(f"========== NEW SESSION: {date} ==========")
            self.logger.info(f"Symbol: {tick.symbol}, Tick Size: {self.tick_size}")

        rth_start = tick.timestamp.replace(
            hour=self.session.rth_start_time.hour,
            minute=self.session.rth_start_time.minute,
            second=0,
            microsecond=0,
        )

        orb_cfg = self.config.strategy.orb
        range_end = rth_start + timedelta(minutes=orb_cfg.opening_range_minutes)
        buffer = Decimal(str(orb_cfg.breakout_buffer_ticks)) * self.tick_size

        # Before Range End: Build Range
        if tick.timestamp <= range_end:
            if tick.timestamp >= rth_start:
                old_high, old_low = self.range_high, self.range_low
                self._update_range(tick.price)
                self.range_formed = False

                # Log range updates (every 30 seconds or on new high/low)
                time_left = (range_end - tick.timestamp).total_seconds()
                if (
                    old_high != self.range_high
                    or old_low != self.range_low
                    or self._last_log_time is None
                    or (tick.timestamp - self._last_log_time).total_seconds() >= 30
                ):
                    self.logger.info(
                        f"[BUILDING RANGE] {tick.timestamp.strftime('%H:%M:%S')} | "
                        f"Price: {tick.price} | Range: {self.range_low} - {self.range_high} | "
                        f"Width: {self.range_high - self.range_low} pts | "
                        f"Time left: {int(time_left)}s"
                    )
                    self._last_log_time = tick.timestamp
            return signals

        # Range Complete
        if not self.range_formed:
            self.range_formed = True
            # Assuming range was valid (prices recorded)
            if self.range_high == Decimal("-Infinity"):
                self.logger.warning("ORB Range failed to form (no ticks in window)")
                return signals
            range_width = self.range_high - self.range_low
            self.logger.info("=" * 60)
            self.logger.info(f"[RANGE FORMED] {tick.timestamp.strftime('%H:%M:%S')}")
            self.logger.info(f"  Range High:    {self.range_high}")
            self.logger.info(f"  Range Low:     {self.range_low}")
            self.logger.info(f"  Range Width:   {range_width} pts")
            self.logger.info(
                f"  Buffer:        {buffer} pts ({orb_cfg.breakout_buffer_ticks} ticks)"
            )
            self.logger.info(f"  LONG trigger:  >= {self.range_high + buffer}")
            self.logger.info(f"  SHORT trigger: <= {self.range_low - buffer}")
            self.logger.info(f"  Direction:     {orb_cfg.direction}")
            self.logger.info(f"  Max trades:    {orb_cfg.max_trades}")
            self.logger.info("=" * 60)

        if self.trades_taken >= orb_cfg.max_trades:
            return signals

        # Safety check: If range never formed (late start), do not trade
        if self.range_high == Decimal("-Infinity"):
            if self._tick_count % 300 == 0:  # Log periodically
                self.logger.warning("Cannot trade: Opening Range not captured (started late?)")
            return signals

        # Log distance to breakout (every 100 ticks to avoid spam)
        if self._tick_count % 100 == 0:
            dist_to_long = tick.price - (self.range_high + buffer)
            dist_to_short = (self.range_low - buffer) - tick.price
            self.logger.info(
                f"[WATCHING] {tick.timestamp.strftime('%H:%M:%S')} | "
                f"Price: {tick.price} | "
                f"To LONG: {dist_to_long:+.2f} pts | "
                f"To SHORT: {dist_to_short:+.2f} pts"
            )

        # Signal Generation
        # Long
        if (orb_cfg.direction in ["long", "both"]) and (tick.price >= self.range_high + buffer):
            if not self.long_triggered:
                self.logger.info("*" * 60)
                self.logger.info("[SIGNAL] LONG BREAKOUT!")
                self.logger.info(f"  Price: {tick.price} >= {self.range_high} + {buffer}")
                self.logger.info("*" * 60)
                signals.append(
                    TradeSignal(
                        symbol=tick.symbol,
                        direction=SignalDirection.LONG,
                        timestamp=tick.timestamp,
                        quantity=1,
                        stop_ticks=orb_cfg.stop_ticks,
                        target_ticks=orb_cfg.target_ticks,
                        reason=f"ORB High Breakout {tick.price} >= {self.range_high} + {buffer}",
                    )
                )
                self.long_triggered = True
                self.trades_taken += 1

        # Short
        elif (orb_cfg.direction in ["short", "both"]) and (tick.price <= self.range_low - buffer):
            if not self.short_triggered:
                self.logger.info("*" * 60)
                self.logger.info("[SIGNAL] SHORT BREAKOUT!")
                self.logger.info(f"  Price: {tick.price} <= {self.range_low} - {buffer}")
                self.logger.info("*" * 60)
                signals.append(
                    TradeSignal(
                        symbol=tick.symbol,
                        direction=SignalDirection.SHORT,
                        timestamp=tick.timestamp,
                        quantity=1,
                        stop_ticks=orb_cfg.stop_ticks,
                        target_ticks=orb_cfg.target_ticks,
                        reason=f"ORB Low Breakout {tick.price} <= {self.range_low} - {buffer}",
                    )
                )
                self.short_triggered = True
                self.trades_taken += 1

        return signals

    def on_bar(self, bar: Bar) -> list[TradeSignal]:
        return []
