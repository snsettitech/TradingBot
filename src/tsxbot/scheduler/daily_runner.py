"""Daily Runner - Automated daily trading session executor.

Runs the full signal generation pipeline during RTH:
1. Wait for RTH open
2. Initialize components (LevelStore, InteractionDetector, etc.)
3. Monitor market and generate signals
4. AI validates signals before alerting
5. Send alerts via email
6. Shutdown at RTH end
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tsxbot.config_loader import load_config
from tsxbot.data.tick_archiver import TickArchiver
from tsxbot.inference.regime_classifier import RegimeClassifier
from tsxbot.inference.signal_generator import SignalGenerator
from tsxbot.inference.strategy_selector import StrategySelector
from tsxbot.intelligence.feature_snapshot import FeatureEngine
from tsxbot.intelligence.interaction_detector import InteractionDetector
from tsxbot.intelligence.level_store import LevelStore
from tsxbot.scheduler.alert_engine import Alert, AlertEngine
from tsxbot.scheduler.email_sender import EmailSender
from tsxbot.time.session_manager import SessionManager

if TYPE_CHECKING:
    from tsxbot.broker.base import BaseBroker
    from tsxbot.config_loader import AppConfig
    from tsxbot.data.market_data import Tick

logger = logging.getLogger(__name__)

# Default config path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "config.yaml"

# AI validation threshold
AI_CONFIDENCE_THRESHOLD = 0.6


class DailyRunner:
    """
    Orchestrates daily automated signal generation with AI validation and data archival.

    Lifecycle:
    1. Initialize before RTH
    2. Run during RTH (archive ticks, generate signals)
    3. AI validates each signal before alerting
    4. Cleanup and report after RTH
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        broker: BaseBroker | None = None,
        config_path: Path | str | None = None,
        enable_ai: bool = True,
    ):
        if config is not None:
            self.config = config
        else:
            cfg_path = config_path or DEFAULT_CONFIG_PATH
            self.config = load_config(cfg_path)

        self.broker = broker
        self.enable_ai = enable_ai

        # Core components
        self.session_manager = SessionManager(self.config.session)
        self.level_store = LevelStore(
            rth_start=self.session_manager.rth_start_time,
            rth_end=self.session_manager.rth_end_time,
        )
        self.interaction_detector = InteractionDetector(
            tick_size=self.config.symbols.es.tick_size,
        )
        self.feature_engine = FeatureEngine(
            tick_size=self.config.symbols.es.tick_size,
        )
        self.regime_classifier = RegimeClassifier()
        self.strategy_selector = StrategySelector(self.config)
        self.signal_generator = SignalGenerator(
            flatten_time_str=self.config.session.flatten_time,
        )

        # Data Archival
        self.tick_archiver = TickArchiver(
            data_dir="data/live",
            enable_supabase=True,  # Auto-push to cloud if available
        )

        # AI components
        self.ai_advisor = None
        if self.enable_ai:
            self._init_ai_advisor()

        # Alerting
        self.email_sender = EmailSender()
        self.alert_engine = AlertEngine(on_alert=self._handle_alert)

        # State
        self._running = False
        self._tick_count = 0
        self._signals_generated = 0
        self._signals_ai_validated = 0
        self._signals_ai_rejected = 0
        self._bar_data: list[tuple[datetime, Decimal]] = []

    def _init_ai_advisor(self) -> None:
        """Initialize AI advisor if OpenAI is configured."""
        try:
            from tsxbot.ai.advisor import AIAdvisor

            self.ai_advisor = AIAdvisor(
                config=self.config.openai,
                dry_run=self.config.is_dry_run,
            )
            if self.ai_advisor.is_available:
                logger.info("AI Advisor initialized")
            else:
                logger.warning("AI Advisor not available (check OPENAI_API_KEY)")
                self.ai_advisor = None
        except ImportError:
            logger.warning("AI Advisor module not available")
            self.ai_advisor = None

    async def run(self) -> None:
        """
        Main entry point for daily run.

        Waits for RTH, runs during session, and cleanly exits.
        """
        logger.info("DailyRunner starting...")

        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()

        # Wait for RTH if not already in session
        await self._wait_for_rth()

        if not self._running:
            return

        # Send session start alert
        self.alert_engine.on_session_start(self.session_manager.now())

        # Main loop
        try:
            await self._run_session()
        except Exception as e:
            logger.error(f"Session error: {e}", exc_info=True)
            self.alert_engine.on_error(f"Session error: {e}")

        # Session end
        summary = self._generate_session_summary()
        self.alert_engine.on_session_end(self.session_manager.now(), summary)

        logger.info("DailyRunner complete")

    async def _wait_for_rth(self) -> None:
        """Wait until RTH opens."""
        self._running = True

        while self._running:
            if self.session_manager.is_rth():
                logger.info("RTH is open, starting session")
                break

            wait_time = self.session_manager.time_until_rth_open()
            if wait_time.total_seconds() > 0:
                wait_secs = min(wait_time.total_seconds(), 60)
                logger.info(f"Waiting {wait_time} for RTH open...")
                await asyncio.sleep(wait_secs)
            else:
                await asyncio.sleep(1)

    async def _run_session(self) -> None:
        """Run the main trading session loop."""
        logger.info("Starting trading session loop")

        if self.broker is None:
            logger.warning("No broker configured, running in simulation mode")
            await self._run_simulation_loop()
        else:
            await self._run_live_loop()

    async def _run_live_loop(self) -> None:
        """Run with live broker tick data."""
        # Subscribe to tick stream
        async for tick in self.broker.stream_ticks(symbol=self.config.symbols.primary):  # type: ignore
            if not self._running:
                break

            if not self.session_manager.is_rth(tick.timestamp):
                if self.session_manager.should_flatten(tick.timestamp):
                    break
                continue

            await self._process_tick(tick)

    async def _run_simulation_loop(self) -> None:
        """Run in simulation mode (no live data)."""
        logger.info("Running in simulation mode - will check periodically")

        while self._running and self.session_manager.is_rth():
            now = self.session_manager.now()

            if self.session_manager.should_flatten(now):
                logger.info("Flatten time reached, ending session")
                break

            # Log status every 5 minutes
            if now.minute % 5 == 0 and now.second < 5:
                logger.info(f"Session active at {now.strftime('%H:%M')}")

            await asyncio.sleep(5)

    async def _process_tick(self, tick: Tick) -> None:
        """Process a single tick through the pipeline."""
        self._tick_count += 1

        # Update level store
        self.level_store.on_tick(tick)

        # Check for large moves
        self.alert_engine.on_tick(tick)

        # Archive tick data
        self.tick_archiver.on_tick(tick)

        # Store bar data (1-minute aggregation)
        self._bar_data.append((tick.timestamp, tick.price))

        # Process every minute
        if self._tick_count % 60 == 0:
            await self._process_bar(tick.timestamp, tick.price)

    async def _process_bar(self, timestamp: datetime, close_price: Decimal) -> None:
        """Process a 1-minute bar close."""
        levels = self.level_store.get_current_levels()

        # Update interaction detector
        if levels:
            self.interaction_detector.update_levels(levels)

        interactions = self.interaction_detector.on_bar_close(close_price, timestamp)

        # Process any interactions
        for interaction in interactions:
            await self._process_interaction(interaction, close_price, timestamp)

        # Get feature snapshot
        snapshot = self.feature_engine.compute_snapshot(
            price=close_price,
            timestamp=timestamp,
            levels=levels,
        )

        # Classify regime
        regime_analysis = self.regime_classifier.classify(snapshot)

        # Log periodically
        if timestamp.minute % 15 == 0:
            logger.debug(
                f"Regime: {regime_analysis.regime.value} ({regime_analysis.confidence:.0%})"
            )

    async def _process_interaction(
        self,
        interaction,
        current_price: Decimal,
        timestamp: datetime,
    ) -> None:
        """Process a detected level interaction with AI validation."""
        levels = self.level_store.get_current_levels()

        # Get current snapshot
        snapshot = self.feature_engine.compute_snapshot(
            price=current_price,
            timestamp=timestamp,
            levels=levels,
        )

        # Classify regime
        regime_analysis = self.regime_classifier.classify(snapshot)

        # Select strategy
        selection = self.strategy_selector.select(regime_analysis, self.session_manager)

        if not selection.should_trade:
            logger.debug(f"Skip: {selection.skip_reasons}")
            return

        # Instantiate playbook and check for signal
        playbook = selection.playbook_class(self.config, self.session_manager)

        if hasattr(playbook, "on_interaction"):
            signal = playbook.on_interaction(interaction, current_price, timestamp)

            if signal:
                self._signals_generated += 1

                # AI Validation Gate
                if self.ai_advisor and self.ai_advisor.is_available:
                    validation = await self._validate_with_ai(signal, snapshot)

                    if validation is None or validation.confidence < AI_CONFIDENCE_THRESHOLD:
                        self._signals_ai_rejected += 1
                        conf_str = f"{validation.confidence:.0%}" if validation else "N/A"
                        logger.info(
                            f"Signal rejected by AI: {signal.direction.value} (confidence: {conf_str})"
                        )
                        return

                    self._signals_ai_validated += 1
                    logger.info(f"Signal validated by AI: {validation.confidence:.0%}")

                # Generate packet
                packet = self.signal_generator.generate(
                    signal=signal,
                    regime_analysis=regime_analysis,
                    selection_result=selection,
                )

                # Send alert
                self.alert_engine.on_signal(packet)

    async def _validate_with_ai(self, signal, snapshot) -> Any:
        """Validate signal with AI advisor."""
        try:
            from tsxbot.ai.models import MarketContext

            context = MarketContext(
                regime=snapshot.regime.value if snapshot.regime else "unknown",
                trend=snapshot.trend_direction if snapshot.trend_direction else "unknown",
                time_of_day=snapshot.time_of_day if snapshot.time_of_day else "unknown",
                vwap_distance=float(snapshot.distance_from_vwap_pct) if snapshot.distance_from_vwap_pct else 0,
            )

            return await self.ai_advisor.validate_trade(signal, context)
        except Exception as e:
            logger.warning(f"AI validation failed: {e}")
            return None

    def _handle_alert(self, alert: Alert) -> None:
        """Handle an alert by sending email."""
        try:
            self.email_sender.send_alert(
                subject=alert.title,
                body_text=alert.message,
            )
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")

    def _generate_session_summary(self) -> str:
        """Generate end-of-session summary with AI stats."""
        ai_status = "N/A"
        if self.ai_advisor:
            ai_status = f"Validated: {self._signals_ai_validated}, Rejected: {self._signals_ai_rejected}"

        return f"""
Session Summary:
- Ticks processed: {self._tick_count}
- Signals generated: {self._signals_generated}
- AI validation: {ai_status}
- Session ended: {self.session_manager.now().strftime("%H:%M ET")}
        """.strip()

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown handlers."""

        def shutdown(_signum, _frame):
            logger.info("Shutdown signal received")
            self._running = False

        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)

    def stop(self) -> None:
        """Stop the runner."""
        self._running = False


async def main():
    """Entry point for daily runner."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    import os

    from tsxbot.broker.projectx import ProjectXBroker
    from tsxbot.config_loader import load_config

    cfg = load_config("config/config.yaml")
    broker = None

    # Check for credentials
    api_key = os.getenv("PROJECTX_API_KEY")
    username = os.getenv("PROJECTX_USERNAME")

    if cfg.environment.dry_run:
        logger.info("Bot is in DRY_RUN mode (from config or DRY_RUN=true). Simulation mode enabled.")
    elif not api_key or not username:
        logger.warning("ProjectX credentials (PROJECTX_API_KEY/USERNAME) missing. Simulation mode enabled.")
    else:
        logger.info(f"Initializing ProjectXBroker for DailyRunner live loop (User: {username})")
        try:
            broker = ProjectXBroker(cfg)
            await broker.connect()  # type: ignore
        except Exception as e:
            logger.error(f"Failed to connect live broker: {e}. Falling back to simulation.")
            broker = None

    runner = DailyRunner(config=cfg, broker=broker)
    try:
        await runner.run()
    finally:
        if broker:
            await broker.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
