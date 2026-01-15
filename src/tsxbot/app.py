"""TSXBot Main Application."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime
from pathlib import Path

from tsxbot.ai.advisor import AIAdvisor
from tsxbot.ai.performance_tracker import PerformanceTracker, TradeOutcome
from tsxbot.ai.strategy_selector import AIStrategySelector
from tsxbot.broker.base import Broker
from tsxbot.broker.sim import SimBroker
from tsxbot.config_loader import AppConfig, load_config
from tsxbot.constants import StrategyName
from tsxbot.data.market_data import Tick
from tsxbot.data.sim_feed import SimDataFeed
from tsxbot.execution.engine import ExecutionEngine
from tsxbot.journal.journaler import Journaler
from tsxbot.journal.models import Decision
from tsxbot.risk.risk_governor import RiskGovernor
from tsxbot.strategies.base import BaseStrategy
from tsxbot.strategies.registry import get_strategy
from tsxbot.time.session_manager import SessionManager
from tsxbot.ui.dashboard import StrategyDashboard

logger = logging.getLogger(__name__)


class TSXBotApp:
    """Main application orchestrator."""

    def __init__(
        self,
        config_path: str = "config/config.yaml",
        strategy_name: str | None = None,
        dry_run: bool = False,
    ):
        self.config_path = Path(config_path)
        self.config: AppConfig | None = None
        self._strategy_name_override = strategy_name
        self._dry_run_override = dry_run

        # Components
        self.session: SessionManager | None = None
        self.risk: RiskGovernor | None = None
        self.broker: Broker | None = None
        self.engine: ExecutionEngine | None = None
        self.strategy: BaseStrategy | None = None
        self.feed: SimDataFeed | None = None
        self.journal: Journaler | None = None
        self.ai_advisor: AIAdvisor | None = None
        self.strategy_selector: AIStrategySelector | None = None
        self.performance_tracker: PerformanceTracker | None = None
        self.dashboard: StrategyDashboard | None = None

        # Dynamic strategy switching
        self._last_strategy_check: datetime | None = None
        self._strategy_check_interval = 1800  # 30 minutes

        self._running = False
        self._shutdown_event = asyncio.Event()

    def _setup_logging(self) -> None:
        # Basic config for now, can be enhanced
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )

    async def initialize(self) -> None:
        """Load config and initialize components."""
        self._setup_logging()
        logger.info("Initializing TSXBot...")

        # 1. Load Config
        self.config = load_config(self.config_path.absolute())

        # Overrides
        if self._dry_run_override:
            self.config.environment.dry_run = True
            logger.info("Dry run mode enabled via CLI")

        if self._strategy_name_override:
            # Assuming setter or simple replacement if mutable?
            # Config is pydantic model (default immutable?) assume mutable for now or create copy.
            # StrategyConfig active is str/Enum.
            self.config.strategy.active = self._strategy_name_override
            logger.info(f"Strategy overridden to: {self._strategy_name_override}")

        # 2. Components
        self.session = SessionManager(self.config.session)
        self.risk = RiskGovernor(self.config.risk, self.config.symbols)

        if self.config.environment.broker_mode == "sim":
            # Setup Sim Broker
            self.broker = SimBroker(self.config.symbols)
            logger.info("Using SimBroker (Internal Matcher)")

            # Setup Sim Feed
            symbols = [self.config.symbols.primary]
            if self.config.symbols.micros:
                symbols.append(self.config.symbols.micros)

            self.feed = SimDataFeed(
                symbols=symbols, callback=self.broker.process_tick, interval_sec=1.0
            )
        else:
            # ProjectX Broker
            from tsxbot.broker.projectx import ProjectXBroker

            self.broker = ProjectXBroker(self.config)
            logger.info(f"Using ProjectXBroker ({self.config.projectx.trading_environment})")

        # Journal Setup
        self.journal = Journaler(self.config.journal.database_path)
        await self.journal.initialize()
        await self.journal.start_run(self.config)

        # AI Advisor Setup
        if self.config.openai.enabled:
            self.ai_advisor = AIAdvisor(
                config=self.config.openai, dry_run=self.config.environment.dry_run
            )
            if self.ai_advisor.is_available:
                logger.info(f"AI Advisor enabled: {self.config.openai.model}")
            else:
                logger.warning("AI Advisor enabled but not available (check API key)")

        # Wire Engine
        self.engine = ExecutionEngine(
            self.broker,
            self.risk,
            self.config.symbols,
            self.journal,
            session_manager=self.session,
            ai_advisor=self.ai_advisor,
            dry_run=self.config.environment.dry_run,
        )

        # Strategy
        self.strategy = get_strategy(self.config, self.session)
        logger.info(f"Strategy initialized: {self.strategy.__class__.__name__}")

        # 3. Prime Strategy History (if supported)
        if hasattr(self.strategy, "prime_history"):
            from tsxbot.data.history_loader import prime_strategy
            await prime_strategy(self.strategy, self.config)

        # Dashboard Setup (for dry-run mode)
        if self.config.environment.dry_run:
            self.dashboard = StrategyDashboard(
                config=self.config, session_manager=self.session, ai_advisor=self.ai_advisor
            )
            logger.info("Dashboard enabled for dry-run mode")

        # AI Strategy Selector Setup
        if self.config.openai.enabled and self.ai_advisor:
            self.strategy_selector = AIStrategySelector(
                config=self.config, ai_advisor=self.ai_advisor
            )
            logger.info("AI Strategy Selector enabled")

        # Performance Tracker Setup
        self.performance_tracker = PerformanceTracker(data_dir="data")
        logger.info("Performance Tracker enabled")

        # Wire Ticks: Broker -> App -> Strategy -> Engine
        self.broker.add_tick_callback(self._on_tick)

    async def _on_tick(self, tick: Tick) -> None:
        """Handle incoming ticks from broker."""
        # Update dashboard if enabled
        if self.dashboard:
            self.dashboard.update_tick(tick.symbol, tick.price, tick.volume, tick.timestamp)

        # Update strategy selector with market data
        if self.strategy_selector:
            await self._update_strategy_selector(tick)

        # Check for dynamic strategy switch
        await self._check_strategy_switch()

        # Strategy Logic
        try:
            signals = self.strategy.on_tick(tick)

            # Log Decision
            if self.journal:
                if signals:
                    for signal in signals:
                        d = Decision(
                            timestamp=tick.timestamp,
                            symbol=tick.symbol,
                            strategy_name=self.strategy.__class__.__name__,
                            signal=signal,
                            features={"price": str(tick.price), "volume": tick.volume},
                            reason="Signal Generated",
                        )
                        await self.journal.log_decision(d)
                elif self.config.journal.log_all_decisions:
                    d = Decision(
                        timestamp=tick.timestamp,
                        symbol=tick.symbol,
                        strategy_name=self.strategy.__class__.__name__,
                        signal=None,
                        features={"price": str(tick.price), "volume": tick.volume},
                        reason="No Signal",
                    )
                    await self.journal.log_decision(d)

            if signals:
                for signal in signals:
                    await self.engine.process_signal(signal)
        except Exception as e:
            logger.error(f"Error in tick processing: {e}", exc_info=True)

    async def _update_strategy_selector(self, tick: Tick) -> None:
        """Update strategy selector with current market data."""
        if not self.strategy_selector:
            return

        # Get session data
        session_high = (
            self.strategy.session_high if hasattr(self.strategy, "session_high") else tick.price
        )
        session_low = (
            self.strategy.session_low if hasattr(self.strategy, "session_low") else tick.price
        )
        vwap = self.strategy.vwap if hasattr(self.strategy, "vwap") else tick.price
        rsi = self.strategy.rsi if hasattr(self.strategy, "rsi") else 50.0
        trend = self.strategy.trend if hasattr(self.strategy, "trend") else "NEUTRAL"

        # Minutes since RTH open
        minutes_since_open = 0
        if self.session and self.session.rth_start_time:
            from datetime import datetime

            now = (
                datetime.now(self.session.rth_start_time.tzinfo)
                if self.session.rth_start_time.tzinfo
                else datetime.now()
            )
            delta = now - self.session.rth_start_time
            minutes_since_open = int(delta.total_seconds() / 60)

        self.strategy_selector.update_market_data(
            current_price=tick.price,
            session_high=session_high,
            session_low=session_low,
            vwap=vwap,
            rsi=rsi,
            trend=trend,
            minutes_since_open=minutes_since_open,
        )

    async def _check_strategy_switch(self) -> None:
        """Check if we should switch strategies based on AI recommendation."""
        if not self.strategy_selector:
            return

        now = datetime.now()

        # Only check every 30 minutes
        if self._last_strategy_check:
            elapsed = (now - self._last_strategy_check).total_seconds()
            if elapsed < self._strategy_check_interval:
                return

        self._last_strategy_check = now

        # Don't switch if we have an open position
        if self.engine and self.engine.has_open_position:
            return

        try:
            selection = await self.strategy_selector.select_strategy()
            if not selection:
                return

            # Check if different from current
            current_name = self.strategy.__class__.__name__.lower().replace("strategy", "")
            selected_name = selection.primary_strategy.lower().replace("_", "")

            if selected_name not in current_name:
                # Switch to new strategy
                logger.info(
                    f"🔄 AI recommends switching from {self.strategy.__class__.__name__} to {selection.primary_strategy}"
                )
                logger.info(f"   Reason: {selection.reason}")

                # Map selection to StrategyName enum
                strategy_map = {
                    "orb": StrategyName.ORB,
                    "vwap_bounce": StrategyName.VWAP_BOUNCE,
                    "mean_reversion": StrategyName.MEAN_REVERSION,
                }

                new_strategy_name = strategy_map.get(selection.primary_strategy.lower())
                if new_strategy_name:
                    self.config.strategy.active = new_strategy_name
                    self.strategy = get_strategy(self.config, self.session)
                    logger.info(f"✅ Switched to {self.strategy.__class__.__name__}")

        except Exception as e:
            logger.warning(f"Strategy switch check failed: {e}")

    def record_trade_outcome(
        self, strategy: str, regime: str, direction: str, pnl_ticks: int, pnl_dollars: float
    ) -> None:
        """Record a trade outcome for performance tracking."""
        if self.performance_tracker:
            outcome = TradeOutcome(
                strategy=strategy,
                regime=regime,
                direction=direction,
                pnl_ticks=pnl_ticks,
                pnl_dollars=pnl_dollars,
            )
            self.performance_tracker.record_trade(outcome)

    async def run(self) -> None:
        """Run the application loop."""
        if not self.config:
            await self.initialize()

        logger.info("Starting run loop...")

        # Trap signals
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: self._handle_signal())
        except NotImplementedError:
            # Windows workaround usually requires generic signal handling or just rely on KeyboardInterrupt
            logger.warning(
                "Signal handlers not supported in this environment (likely Windows). Use Ctrl+C to stop."
            )

        # Start Broker
        await self.broker.connect()

        # Subscribe if ProjectX
        if hasattr(self.broker, "subscribe_ticks"):
            # Build full contract IDs from symbol names
            contract_ids = [self.config.symbols.get_contract_id(self.config.symbols.primary)]
            if self.config.symbols.micros:
                contract_ids.append(self.config.symbols.get_contract_id(self.config.symbols.micros))
            logger.info(f"Subscribing to contract IDs: {contract_ids}")
            await self.broker.subscribe_ticks(contract_ids)

        # Start Feed (if Sim)
        feed_task = None
        if self.feed:
            feed_task = asyncio.create_task(self.feed.start())

        self._running = True

        # Wait for shutdown
        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Shutting down...")
            if self.feed:
                self.feed.stop()
                if feed_task:
                    await feed_task

            await self.broker.disconnect()

            if self.journal:
                await self.journal.close()

            logger.info("Shutdown complete.")

    def _handle_signal(self) -> None:
        logger.info("Signal received, initiating shutdown...")
        self._shutdown_event.set()
