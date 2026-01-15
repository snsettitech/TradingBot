"""Backtest Engine.

Replays historical data through strategies and collects trade results.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from tsxbot.backtest.data_loader import Bar
from tsxbot.backtest.results import BacktestResult, TradeRecord
from tsxbot.constants import SignalDirection
from tsxbot.strategies.base import TradeSignal

if TYPE_CHECKING:
    from tsxbot.ai.advisor import AIAdvisor
    from tsxbot.config_loader import AppConfig
    from tsxbot.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Backtest engine that replays history through strategies.

    Features:
    - Bar-by-bar replay
    - Trade simulation with stops/targets
    - AI post-trade analysis
    - Performance metrics
    """

    def __init__(
        self,
        config: AppConfig,
        strategy: BaseStrategy,
        ai_advisor: AIAdvisor | None = None,
        tick_value: Decimal = Decimal("12.50"),  # ES tick value
        fee_per_trade: Decimal = Decimal("4.00"),  # Round-trip fee
    ):
        self.config = config
        self.strategy = strategy
        self.ai_advisor = ai_advisor
        self.tick_value = tick_value
        self.fee_per_trade = fee_per_trade

        # State
        self.bars: list[Bar] = []
        self.current_bar_idx = 0
        self.current_position: dict | None = None
        self.trades: list[TradeRecord] = []

        # Tracking
        self.session_high = Decimal("-Infinity")
        self.session_low = Decimal("Infinity")
        self.vwap = Decimal("0")
        self.cumulative_volume = 0
        self.cumulative_tp_vol = Decimal("0")
        self.current_session_date = None

    def load_data(self, bars: list[Bar]) -> None:
        """Load bar data for backtest."""
        self.bars = bars
        logger.info(f"Loaded {len(bars)} bars for backtest")

    def run(self) -> BacktestResult:
        """
        Run the backtest synchronously.

        Returns:
            BacktestResult with all trades and metrics
        """
        if not self.bars:
            raise ValueError("No data loaded. Call load_data() first.")

        logger.info(
            f"Starting backtest: {len(self.bars)} bars, strategy={self.strategy.__class__.__name__}"
        )

        for i, bar in enumerate(self.bars):
            self.current_bar_idx = i
            self._process_bar(bar)

        # Close any open position at end
        if self.current_position:
            self._close_position(self.bars[-1], "End of backtest")

        # Build result
        result = BacktestResult(
            strategy=self.strategy.__class__.__name__,
            symbol=self.bars[0].symbol if self.bars else "ES",
            start_date=self.bars[0].timestamp if self.bars else datetime.now(),
            end_date=self.bars[-1].timestamp if self.bars else datetime.now(),
            trades=self.trades,
            total_fees=self.fee_per_trade * len(self.trades),
        )
        result.calculate_metrics()

        logger.info(f"Backtest complete: {len(self.trades)} trades")
        return result

    async def run_with_ai(self) -> BacktestResult:
        """
        Run backtest with AI analysis AND learning.

        Steps:
        1. Run standard backtest
        2. Analyze individual trades (Advisor)
        3. Learn from aggregate results (BacktestLearner)
        4. Generate parameter recommendations (AIRecommender)
        """
        result = self.run()

        # 1. Per-trade analysis (existing)
        if self.ai_advisor and self.ai_advisor.is_available:
            logger.info("Running AI post-trade analysis...")
            await self._analyze_trades_with_ai(result)

        # 2. Aggregate Learning
        try:
            from tsxbot.learning.ai_recommender import AIRecommender
            from tsxbot.learning.backtest_learner import BacktestLearner

            # Learn from results
            learner = BacktestLearner(self.config)
            learned_params = await learner.analyze_and_learn(result)

            # Generate recommendations for trusted params
            if getattr(self.config, "openai", None) and self.config.openai.enabled:
                recommender = AIRecommender(self.config.openai)
                if recommender.is_available:
                    logger.info("Generating AI parameter recommendations...")
                    for params in learned_params:
                        if params.is_trusted():
                            await recommender.generate_recommendation(params)
                            # Update store with recommendation
                            learner.param_store.update_parameters(
                                params, reason="AI Recommendation Update"
                            )

        except Exception as e:
            logger.error(f"Learning pipeline failed: {e}")

        return result

    def _process_bar(self, bar: Bar) -> None:
        """Process a single bar."""
        # Check for new session
        if self.current_session_date != bar.timestamp.date():
            self._reset_session(bar)

        # Update session levels
        self._update_session_levels(bar)

        # Check stops/targets on open position
        if self.current_position:
            self._check_exit_conditions(bar)

        # Only generate new signals if no position
        if not self.current_position:
            signals = self._generate_signals(bar)
            if signals:
                self._open_position(signals[0], bar)

    def _reset_session(self, bar: Bar) -> None:
        """Reset for new trading session."""
        self.current_session_date = bar.timestamp.date()
        self.session_high = bar.high
        self.session_low = bar.low
        self.vwap = bar.close
        self.cumulative_volume = bar.volume
        self.cumulative_tp_vol = bar.typical_price * bar.volume

        # Strategy reset is handled by strategy itself if needed
        # if hasattr(self.strategy, "reset"):
        #     self.strategy.reset()

    def _update_session_levels(self, bar: Bar) -> None:
        """Update session high/low and VWAP."""
        if bar.high > self.session_high:
            self.session_high = bar.high
        if bar.low < self.session_low:
            self.session_low = bar.low

        # Update VWAP
        self.cumulative_volume += bar.volume
        self.cumulative_tp_vol += bar.typical_price * bar.volume
        if self.cumulative_volume > 0:
            self.vwap = self.cumulative_tp_vol / Decimal(str(self.cumulative_volume))

    def _generate_signals(self, bar: Bar) -> list[TradeSignal]:
        """Generate signals from strategy using bar data."""
        # Convert bar to tick-like format for strategy
        from tsxbot.data.market_data import Tick

        tick = Tick(symbol=bar.symbol, price=bar.close, volume=bar.volume, timestamp=bar.timestamp)

        return self.strategy.on_tick(tick)

    def _open_position(self, signal: TradeSignal, bar: Bar) -> None:
        """Open a new position from signal."""
        tick_size = Decimal("0.25")

        entry_price = bar.close

        if signal.direction == SignalDirection.LONG:
            stop_price = entry_price - (Decimal(str(signal.stop_ticks)) * tick_size)
            target_price = entry_price + (Decimal(str(signal.target_ticks)) * tick_size)
        else:
            stop_price = entry_price + (Decimal(str(signal.stop_ticks)) * tick_size)
            target_price = entry_price - (Decimal(str(signal.target_ticks)) * tick_size)

        self.current_position = {
            "direction": signal.direction.value.upper(),
            "entry_price": entry_price,
            "entry_time": bar.timestamp,
            "stop_price": stop_price,
            "target_price": target_price,
            "quantity": signal.quantity,
            "reason": signal.reason,
            "vwap_at_entry": float(self.vwap),
            "regime": self._classify_regime(),
        }

        if self.current_position:
            logger.info(f"Opened {self.current_position['direction']} @ {entry_price}")

    def _check_exit_conditions(self, bar: Bar) -> None:
        """Check if stop or target was hit."""
        pos = self.current_position
        if not pos:
            return

        if pos["direction"] == "LONG":
            # Check stop (use bar low)
            if bar.low <= pos["stop_price"]:
                self._close_position(bar, "Stop hit", exit_price=pos["stop_price"])
            # Check target (use bar high)
            elif bar.high >= pos["target_price"]:
                self._close_position(bar, "Target hit", exit_price=pos["target_price"])
        else:  # SHORT
            # Check stop (use bar high)
            if bar.high >= pos["stop_price"]:
                self._close_position(bar, "Stop hit", exit_price=pos["stop_price"])
            # Check target (use bar low)
            elif bar.low <= pos["target_price"]:
                self._close_position(bar, "Target hit", exit_price=pos["target_price"])

    def _close_position(self, bar: Bar, reason: str, exit_price: Decimal | None = None) -> None:
        """Close current position and record trade."""
        pos = self.current_position
        if not pos:
            return

        if exit_price is None:
            exit_price = bar.close

        # Calculate P&L
        tick_size = Decimal("0.25")
        if pos["direction"] == "LONG":
            pnl_ticks = int((exit_price - pos["entry_price"]) / tick_size)
        else:
            pnl_ticks = int((pos["entry_price"] - exit_price) / tick_size)

        pnl_dollars = Decimal(str(pnl_ticks)) * self.tick_value * pos["quantity"]

        # Create trade record
        trade = TradeRecord(
            entry_time=pos["entry_time"],
            exit_time=bar.timestamp,
            symbol=bar.symbol,
            direction=pos["direction"],
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            quantity=pos["quantity"],
            pnl_ticks=pnl_ticks,
            pnl_dollars=pnl_dollars,
            strategy=self.strategy.__class__.__name__,
            entry_reason=pos["reason"],
            regime=pos.get("regime", "unknown"),
            vwap_distance=float(pos["entry_price"]) - pos.get("vwap_at_entry", 0),
        )

        self.trades.append(trade)
        self.current_position = None

        logger.info(
            f"Closed {pos['direction']} @ {exit_price}, P&L: {pnl_ticks} ticks (${pnl_dollars})"
        )

    def _classify_regime(self) -> str:
        """Classify current market regime."""
        if self.session_high == Decimal("-Infinity"):
            return "unknown"

        session_range = float(self.session_high - self.session_low)
        vwap_dist = abs(float(self.bars[self.current_bar_idx].close - self.vwap))

        if vwap_dist > 5:
            return "trending"
        elif session_range < 10:
            return "choppy"
        elif session_range > 25:
            return "high_volatility"
        return "normal"

    async def _analyze_trades_with_ai(self, result: BacktestResult) -> None:
        """Add AI analysis to each trade."""
        if not self.ai_advisor:
            return

        for trade in result.trades[:10]:  # Limit to first 10 to save costs
            try:
                from tsxbot.ai.models import TradeResult

                trade_result = TradeResult(
                    symbol=trade.symbol,
                    direction=trade.direction,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    quantity=trade.quantity,
                    pnl_ticks=trade.pnl_ticks,
                    pnl_usd=trade.pnl_dollars,
                    duration_seconds=int(trade.hold_time_minutes * 60),
                    exit_reason="stop" if trade.pnl_ticks < 0 else "target",
                    signal_reason=trade.entry_reason,
                )

                # Set entry context on trade_result
                from tsxbot.ai.models import MarketContext

                trade_result.entry_context = MarketContext(
                    symbol=trade.symbol,
                    timestamp=trade.entry_time,
                    current_price=trade.entry_price,
                    session_high=trade.entry_price + Decimal("5"),
                    session_low=trade.entry_price - Decimal("5"),
                )

                # Get AI analysis (only pass trade_result)
                analysis = await self.ai_advisor.analyze_completed_trade(trade_result)

                if analysis:
                    trade.ai_grade = analysis.grade
                    trade.ai_analysis = (
                        analysis.what_worked[0]
                        if trade.is_winner and analysis.what_worked
                        else (analysis.what_didnt[0] if analysis.what_didnt else "")
                    )
                    trade.ai_lessons = analysis.lessons

            except Exception as e:
                logger.warning(f"AI analysis failed for trade: {e}")

        # Generate overall recommendation
        if result.regime_performance:
            best_regime = max(result.regime_performance.items(), key=lambda x: x[1]["win_rate"])
            worst_regime = min(result.regime_performance.items(), key=lambda x: x[1]["win_rate"])
            result.ai_recommendation = (
                f"Best performance in {best_regime[0]} markets ({best_regime[1]['win_rate']:.0%} win rate). "
                f"Consider reducing size in {worst_regime[0]} conditions."
            )

    async def run_with_feedback(self, confidence_threshold: float = 0.6) -> BacktestResult:
        """
        Run backtest with AI feedback loop (Online Learning).

        For each potential trade:
        1. Pre-Trade: AI validates signal with lessons from previous trades
        2. If approved: Execute trade and simulate outcome
        3. Post-Trade: AI analyzes result and extracts lessons
        4. Lessons are fed into the next pre-trade validation

        Args:
            confidence_threshold: Minimum AI confidence (0-1) to take trade.

        Returns:
            BacktestResult with all trades and AI insights.
        """
        if not self.bars:
            raise ValueError("No data loaded. Call load_data() first.")

        if not self.ai_advisor or not self.ai_advisor.is_available:
            logger.warning("AI not available, falling back to standard backtest")
            return self.run()

        from tsxbot.ai.models import MarketContext, TradeResult

        logger.info(
            f"Starting AI Feedback backtest: {len(self.bars)} bars, "
            f"strategy={self.strategy.__class__.__name__}, threshold={confidence_threshold:.0%}"
        )

        # Track lessons learned across trades
        accumulated_lessons: list[str] = []
        trades_validated = 0
        trades_rejected = 0

        for i, bar in enumerate(self.bars):
            self.current_bar_idx = i

            # Check for new session
            if self.current_session_date != bar.timestamp.date():
                self._reset_session(bar)

            # Update session levels
            self._update_session_levels(bar)

            # Check stops/targets on open position
            if self.current_position:
                # Check if position closed this bar
                prev_trade_count = len(self.trades)
                self._check_exit_conditions(bar)

                # If trade just closed, analyze it
                if len(self.trades) > prev_trade_count:
                    closed_trade = self.trades[-1]
                    lessons = await self._analyze_single_trade(closed_trade)
                    if lessons:
                        accumulated_lessons.extend(lessons)
                        logger.info(f"[AI] Learned: {lessons[0][:50]}...")

            # Only generate new signals if no position
            if not self.current_position:
                signals = self._generate_signals(bar)
                if signals:
                    signal = signals[0]

                    # Build market context for AI
                    context = MarketContext(
                        symbol=bar.symbol,
                        timestamp=bar.timestamp,
                        current_price=bar.close,
                        session_high=self.session_high,
                        session_low=self.session_low,
                        vwap=self.vwap,
                    )

                    # Pre-trade validation with lessons
                    validation = await self.ai_advisor.validate_trade(
                        signal=signal,
                        context=context,
                        recent_lessons=accumulated_lessons,
                    )

                    if validation and validation.confidence >= confidence_threshold * 10:
                        trades_validated += 1
                        logger.debug(
                            f"[AI APPROVED] {signal.direction.value} @ {bar.close} "
                            f"confidence={validation.confidence}/10"
                        )
                        self._open_position(signal, bar)
                    else:
                        trades_rejected += 1
                        conf = validation.confidence if validation else 0
                        logger.debug(
                            f"[AI REJECTED] {signal.direction.value} @ {bar.close} "
                            f"confidence={conf}/10 < threshold={confidence_threshold * 10}"
                        )

        # Close any open position at end
        if self.current_position:
            self._close_position(self.bars[-1], "End of backtest")

        # Build result
        result = BacktestResult(
            strategy=self.strategy.__class__.__name__,
            symbol=self.bars[0].symbol if self.bars else "ES",
            start_date=self.bars[0].timestamp if self.bars else datetime.now(),
            end_date=self.bars[-1].timestamp if self.bars else datetime.now(),
            trades=self.trades,
            total_fees=self.fee_per_trade * len(self.trades),
        )
        result.calculate_metrics()

        # Add AI stats to result
        result.ai_recommendation = (
            f"AI Feedback Loop: {trades_validated} trades approved, "
            f"{trades_rejected} rejected. {len(accumulated_lessons)} lessons learned."
        )

        logger.info(
            f"AI Feedback backtest complete: {len(self.trades)} trades "
            f"(approved={trades_validated}, rejected={trades_rejected})"
        )
        return result

    async def _analyze_single_trade(self, trade: TradeRecord) -> list[str]:
        """Analyze a single completed trade and return lessons."""
        if not self.ai_advisor:
            return []

        try:
            from tsxbot.ai.models import MarketContext, TradeResult

            trade_result = TradeResult(
                symbol=trade.symbol,
                direction=trade.direction,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                quantity=trade.quantity,
                pnl_ticks=trade.pnl_ticks,
                pnl_usd=trade.pnl_dollars,
                duration_seconds=int(trade.hold_time_minutes * 60),
                exit_reason="stop" if trade.pnl_ticks < 0 else "target",
                signal_reason=trade.entry_reason,
            )

            trade_result.entry_context = MarketContext(
                symbol=trade.symbol,
                timestamp=trade.entry_time,
                current_price=trade.entry_price,
                session_high=trade.entry_price + Decimal("5"),
                session_low=trade.entry_price - Decimal("5"),
            )

            analysis = await self.ai_advisor.analyze_completed_trade(trade_result)

            if analysis and analysis.lessons:
                trade.ai_grade = analysis.grade
                trade.ai_lessons = analysis.lessons
                return analysis.lessons

        except Exception as e:
            logger.warning(f"AI single-trade analysis failed: {e}")

        return []
