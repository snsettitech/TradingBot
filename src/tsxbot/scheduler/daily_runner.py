"""Daily Runner - Automated daily trading session executor.

Runs the full signal generation pipeline during RTH:
1. Wait for RTH open
2. Initialize components (LevelStore, InteractionDetector, etc.)
3. Monitor market and generate signals
4. Send alerts via email
5. Shutdown at RTH end
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from tsxbot.config_loader import load_config
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


class DailyRunner:
    """
    Orchestrates daily automated signal generation.

    Lifecycle:
    1. Initialize before RTH
    2. Run during RTH
    3. Cleanup and report after RTH
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        broker: BaseBroker | None = None,
        config_path: Path | str | None = None,
    ):
        if config is not None:
            self.config = config
        else:
            cfg_path = config_path or DEFAULT_CONFIG_PATH
            self.config = load_config(cfg_path)

        self.broker = broker

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

        # Alerting
        self.email_sender = EmailSender()
        self.alert_engine = AlertEngine(on_alert=self._handle_alert)

        # State
        self._running = False
        self._tick_count = 0
        self._signals_generated = 0
        self._bar_data: list[tuple[datetime, Decimal]] = []

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
        async for tick in self.broker.stream_ticks(self.config.symbols.primary):
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
        """Process a detected level interaction."""
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

                # Generate packet
                packet = self.signal_generator.generate(
                    signal=signal,
                    regime_analysis=regime_analysis,
                    selection_result=selection,
                )

                # Send alert
                self.alert_engine.on_signal(packet)

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
        """Generate end-of-session summary."""
        return f"""
Session Summary:
- Ticks processed: {self._tick_count}
- Signals generated: {self._signals_generated}
- Session ended: {self.session_manager.now().strftime("%H:%M ET")}
        """.strip()

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown handlers."""

        def shutdown(signum, frame):
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

    runner = DailyRunner()
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
