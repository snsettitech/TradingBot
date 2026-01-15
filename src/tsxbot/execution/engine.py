"""Execution Engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from tsxbot.broker.models import Fill, OrderRequest
from tsxbot.config_loader import SymbolsConfig
from tsxbot.constants import OrderSide, OrderType, SignalDirection
from tsxbot.risk.risk_governor import RiskGovernor

if TYPE_CHECKING:
    from tsxbot.ai.advisor import AIAdvisor
    from tsxbot.ai.models import MarketContext
    from tsxbot.broker.base import Broker
    from tsxbot.journal.journaler import Journaler
    from tsxbot.strategies.base import TradeSignal
    from tsxbot.time.session_manager import SessionManager

logger = logging.getLogger(__name__)


@dataclass
class TradeContext:
    """Tracking context for an active trade lifecycle."""

    symbol: str
    side: OrderSide
    signal_id: str
    entry_order_id: str | None = None
    stop_order_id: str | None = None
    target_order_id: str | None = None
    entry_filled_qty: int = 0

    # Configuration
    stop_ticks: int | None = None
    target_ticks: int | None = None
    entry_price: Decimal | None = None
    quantity: int = 0

    # AI Feedback Loop Context
    entry_time: datetime | None = None
    entry_context: MarketContext | None = None
    signal_reason: str = ""
    ai_confidence_at_entry: int | None = None


class ExecutionEngine:
    """
    Orchestrates order execution and trade management.
    """

    def __init__(
        self,
        broker: Broker,
        risk_governor: RiskGovernor,
        symbols_config: SymbolsConfig,
        journal: Journaler | None = None,
        session_manager: SessionManager | None = None,
        ai_advisor: AIAdvisor | None = None,
        dry_run: bool = False,
    ):
        self.broker = broker
        self.risk = risk_governor
        self.symbols_config = symbols_config
        self.journal = journal
        self.session = session_manager
        self.ai_advisor = ai_advisor
        self.dry_run = dry_run

        self.active_trades: dict[str, TradeContext] = {}  # Map entry_order_id -> Context
        self.order_map: dict[str, str] = {}  # Map bracket_order_id -> entry_order_id

        # AI Feedback Loop (Online Learning)
        self.accumulated_lessons: list[str] = []
        self._last_ai_confidence: int | None = None
        self._last_market_context: MarketContext | None = None

        self.broker.add_fill_callback(self.on_fill)

    def _get_tick_size(self, symbol: str) -> Decimal:
        if symbol == self.symbols_config.mes.contract_id_prefix or "MES" in symbol:
            return self.symbols_config.mes.tick_size
        return self.symbols_config.es.tick_size

    def _build_market_context(self, signal: TradeSignal) -> MarketContext:
        """
        Build market context snapshot for AI analysis.

        Computes interpreted metrics (RVOL, level distances, trend)
        since GPT-4o-mini struggles with raw number math.
        """
        from tsxbot.ai.models import MarketContext

        now = datetime.now()
        tick_size = self._get_tick_size(signal.symbol)

        # Get current price from broker if available
        current_price = Decimal("0")
        session_high = Decimal("0")
        session_low = Decimal("9999999")

        # Try to get market data from broker's last known state
        if hasattr(self.broker, "last_tick") and self.broker.last_tick:
            current_price = self.broker.last_tick.price
        if hasattr(self.broker, "session_high"):
            session_high = getattr(self.broker, "session_high", current_price)
        if hasattr(self.broker, "session_low"):
            session_low = getattr(self.broker, "session_low", current_price)

        # Calculate minutes since RTH open
        minutes_since_open = 0
        session_phase = "unknown"
        if self.session:
            rth_start = self.session.get_rth_start_today()
            if rth_start:
                delta = now - rth_start
                minutes_since_open = max(0, int(delta.total_seconds() / 60))

                # Determine session phase
                if minutes_since_open < 30:
                    session_phase = "opening"
                elif minutes_since_open < 120:
                    session_phase = "morning"
                elif minutes_since_open < 240:
                    session_phase = "midday"
                elif minutes_since_open < 360:
                    session_phase = "afternoon"
                else:
                    session_phase = "close"

        # Calculate volatility description
        if session_high > 0 and session_low < Decimal("9999999"):
            range_ticks = int((session_high - session_low) / tick_size)
            if range_ticks < 10:
                volatility_desc = f"Low ({range_ticks} tick range)"
            elif range_ticks < 25:
                volatility_desc = f"Normal ({range_ticks} tick range)"
            else:
                volatility_desc = f"High ({range_ticks} tick range)"
        else:
            volatility_desc = "Unknown"

        # Calculate distance to HOD/LOD in ticks
        dist_to_hod = None
        dist_to_lod = None
        if current_price > 0 and session_high > 0:
            dist_to_hod = int((session_high - current_price) / tick_size)
            dist_to_lod = int((current_price - session_low) / tick_size)

        # Get risk state
        daily_pnl = self.risk.state.daily_pnl if self.risk.state else Decimal("0")
        trade_count = self.risk.state.trade_count if self.risk.state else 0

        return MarketContext(
            symbol=signal.symbol,
            timestamp=now,
            current_price=current_price,
            session_high=session_high,
            session_low=session_low,
            minutes_since_open=minutes_since_open,
            session_phase=session_phase,
            daily_pnl=daily_pnl,
            trade_count_today=trade_count,
            volatility_description=volatility_desc,
            dist_to_hod_ticks=dist_to_hod,
            dist_to_lod_ticks=dist_to_lod,
            # Future: Add RVOL, VWAP, EMA20 when data sources available
            rvol_description="RVOL: N/A (future enhancement)",
            trend_description="Trend: N/A (future enhancement)",
        )

    async def process_signal(self, signal: TradeSignal) -> None:
        """Process a trading signal from strategy."""
        logger.info(f"Processing signal: {signal}")

        # 0. AI Pre-Trade Validation (commentary only - never rejects)
        if self.ai_advisor and self.ai_advisor.is_available:
            try:
                context = self._build_market_context(signal)
                # Pass recent lessons to validation
                validation = await self.ai_advisor.validate_trade(
                    signal, context, recent_lessons=self.accumulated_lessons
                )
                if validation:
                    self._last_ai_confidence = validation.confidence
                    self._last_market_context = context
                    logger.info(f"AI Confidence: {validation.confidence}/10")
                    for obs in validation.observations:
                        logger.debug(f"  AI: {obs}")
                    # Store in journal for review
                    if self.journal:
                        await self.journal.log_ai_validation(signal, validation)
            except Exception as e:
                logger.warning(f"AI validation failed (continuing): {e}")

        # 1. DRY_RUN Check - Do not place any orders
        if self.dry_run:
            logger.info(
                f"DRY RUN: Would process signal {signal.direction.value} {signal.symbol} x{signal.quantity}"
            )
            return

        # 2. RTH Check - Ensure we're within trading hours
        if self.session is not None and not self.session.is_trading_allowed():
            logger.warning("Signal rejected: outside trading hours")
            return

        # 3. Risk Checks
        allowed, reason = self.risk.can_trade()
        if not allowed:
            logger.warning(f"Risk check failed: {reason}")
            return

        is_safe, risk_msg = self.risk.check_trade_risk(signal.symbol, signal.quantity)
        if not is_safe:
            logger.warning(f"Trade risk check failed: {risk_msg}")
            return

        # 2. Submit Entry
        side = OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL
        req = OrderRequest(
            symbol=signal.symbol,
            side=side,
            qty=signal.quantity,
            type=signal.entry_type,
            limit_price=signal.limit_price,
        )

        try:
            order = await self.broker.place_order(req)
            self.risk.record_trade_execution()

            if self.journal:
                await self.journal.log_order(order)

            # 3. Track Context
            ctx = TradeContext(
                symbol=signal.symbol,
                side=side,
                signal_id=str(uuid4()),
                entry_order_id=order.id,
                stop_ticks=signal.stop_ticks,
                target_ticks=signal.target_ticks,
                quantity=signal.quantity,
                entry_time=datetime.now(),
                entry_context=self._last_market_context,
                signal_reason=signal.reason,
                ai_confidence_at_entry=self._last_ai_confidence,
            )

            self.active_trades[order.id] = ctx
            self.order_map[order.id] = order.id

        except Exception as e:
            logger.error(f"Failed to place entry order: {e}")

    async def on_fill(self, fill: Fill) -> None:
        """Handle fill events."""
        logger.info(f"Fill received: {fill}")

        if self.journal:
            await self.journal.log_fill(fill)

        entry_id = self.order_map.get(fill.order_id)
        if not entry_id:
            # Maybe orphaned fill?
            return

        ctx = self.active_trades.get(entry_id)
        if not ctx:
            return

        if fill.order_id == ctx.entry_order_id:
            await self._handle_entry_fill(ctx, fill)
        elif fill.order_id == ctx.stop_order_id:
            await self._handle_exit_fill(ctx, fill, is_stop=True)
        elif fill.order_id == ctx.target_order_id:
            await self._handle_exit_fill(ctx, fill, is_stop=False)

    async def _handle_entry_fill(self, ctx: TradeContext, fill: Fill) -> None:
        ctx.entry_filled_qty += fill.qty
        ctx.entry_price = fill.price

        # Simplified Bracket Logic: Place brackets once fully filled
        if ctx.entry_filled_qty >= ctx.quantity:
            await self._place_bracket_orders(ctx)

    async def _place_bracket_orders(self, ctx: TradeContext) -> None:
        if not ctx.stop_ticks and not ctx.target_ticks:
            return

        if ctx.entry_price is None:
            return

        exit_side = OrderSide.SELL if ctx.side == OrderSide.BUY else OrderSide.BUY
        tick_size = self._get_tick_size(ctx.symbol)
        qty = ctx.quantity

        # Stop Order
        if ctx.stop_ticks:
            stop_price = (
                (ctx.entry_price - (ctx.stop_ticks * tick_size))
                if ctx.side == OrderSide.BUY
                else (ctx.entry_price + (ctx.stop_ticks * tick_size))
            )
            stop_req = OrderRequest(
                symbol=ctx.symbol,
                side=exit_side,
                qty=qty,
                type=OrderType.STOP,
                stop_price=stop_price,
            )
            try:
                stop_order = await self.broker.place_order(stop_req)
                ctx.stop_order_id = stop_order.id
                self.order_map[stop_order.id] = ctx.entry_order_id
                if self.journal:
                    await self.journal.log_order(stop_order)
                logger.info(f"Placed Stop Loss: {stop_order.id} @ {stop_price}")
            except Exception as e:
                logger.critical(f"CRITICAL: Failed to place stop for {ctx.entry_order_id}: {e}")
                # Emergency: close position immediately since we can't protect it
                await self._emergency_flatten(ctx)
                self.risk.trip_kill_switch(f"Stop order failed: {e}")
                return  # Don't proceed to target order

        # Target Order
        if ctx.target_ticks:
            target_price = (
                (ctx.entry_price + (ctx.target_ticks * tick_size))
                if ctx.side == OrderSide.BUY
                else (ctx.entry_price - (ctx.target_ticks * tick_size))
            )
            target_req = OrderRequest(
                symbol=ctx.symbol,
                side=exit_side,
                qty=qty,
                type=OrderType.LIMIT,
                limit_price=target_price,
            )
            try:
                target_order = await self.broker.place_order(target_req)
                ctx.target_order_id = target_order.id
                self.order_map[target_order.id] = ctx.entry_order_id
                if self.journal:
                    await self.journal.log_order(target_order)
                logger.info(f"Placed Target: {target_order.id} @ {target_price}")
            except Exception as e:
                logger.error(f"Failed to place target: {e}")

    async def _handle_exit_fill(self, ctx: TradeContext, fill: Fill, is_stop: bool) -> None:
        # Cancel other leg
        other_id = ctx.target_order_id if is_stop else ctx.stop_order_id

        if other_id:
            try:
                await self.broker.cancel_order(other_id)
                logger.info(f"Cancelled OCO leg: {other_id}")
            except Exception as e:
                logger.error(f"Failed cancel leg: {e}")

        # Determine if closed?
        # Assuming full fill.
        if fill.qty >= ctx.quantity:
            # 1. Post-Trade Analysis for Learning Loop
            if self.ai_advisor and self.ai_advisor.is_available:
                try:
                    from tsxbot.ai.models import TradeResult

                    # Compute duration
                    duration = 0
                    if ctx.entry_time:
                        duration = int((datetime.now() - ctx.entry_time).total_seconds())

                    # Compute P&L
                    tick_size = self._get_tick_size(ctx.symbol)
                    pnl_ticks = 0
                    if ctx.entry_price:
                        pnl_ticks = int((fill.price - ctx.entry_price) / tick_size)
                        if ctx.side == OrderSide.SELL:
                            pnl_ticks = -pnl_ticks

                    # Simplified USD calculation (assuming ES for now or using config)
                    pnl_usd = Decimal(str(pnl_ticks)) * Decimal("12.50")  # ES default

                    result = TradeResult(
                        symbol=ctx.symbol,
                        direction="LONG" if ctx.side == OrderSide.BUY else "SHORT",
                        entry_price=ctx.entry_price or Decimal("0"),
                        exit_price=fill.price,
                        quantity=ctx.quantity,
                        pnl_ticks=pnl_ticks,
                        pnl_usd=pnl_usd,
                        duration_seconds=duration,
                        exit_reason="stop" if is_stop else "target",
                        entry_context=ctx.entry_context,
                        signal_reason=ctx.signal_reason,
                        ai_confidence_at_entry=ctx.ai_confidence_at_entry,
                    )

                    analysis = await self.ai_advisor.analyze_completed_trade(result)
                    if analysis and analysis.lessons:
                        logger.info(f"AI Learned {len(analysis.lessons)} new lessons from trade")
                        self.accumulated_lessons.extend(analysis.lessons)
                        # Keep only last 20 lessons
                        if len(self.accumulated_lessons) > 20:
                            self.accumulated_lessons = self.accumulated_lessons[-20:]
                except Exception as e:
                    logger.warning(f"Post-trade analysis failed: {e}")

            self.active_trades.pop(ctx.entry_order_id, None)

    async def _emergency_flatten(self, ctx: TradeContext) -> None:
        """
        Emergency close position when stop order placement fails.

        This is a critical safety feature - we must not leave naked positions.
        """
        exit_side = OrderSide.SELL if ctx.side == OrderSide.BUY else OrderSide.BUY
        req = OrderRequest(
            symbol=ctx.symbol, side=exit_side, qty=ctx.quantity, type=OrderType.MARKET
        )
        try:
            order = await self.broker.place_order(req)
            logger.warning(
                f"EMERGENCY FLATTEN: Closed position for {ctx.entry_order_id} with order {order.id}"
            )
            if self.journal:
                await self.journal.log_order(order)
            # Remove from active trades
            self.active_trades.pop(ctx.entry_order_id, None)
        except Exception as e:
            logger.critical(f"CRITICAL: FAILED TO EMERGENCY FLATTEN for {ctx.entry_order_id}: {e}")
            # At this point we have a naked position and can't close it
            # The kill switch will already be tripped by the caller
