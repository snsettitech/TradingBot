"""Alert Engine - Monitors for signals and large movements.

Triggers alerts for:
- Trade signals from strategy selector
- Large price movements (configurable threshold)
- Session start/end notifications
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.data.market_data import Tick
    from tsxbot.inference.signal_generator import SignalPacket

logger = logging.getLogger(__name__)


class AlertType(Enum):
    """Types of alerts."""

    SIGNAL = "signal"
    LARGE_MOVE = "large_move"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    ERROR = "error"


@dataclass
class Alert:
    """An alert to be sent."""

    timestamp: datetime
    alert_type: AlertType
    title: str
    message: str
    priority: int = 1  # 1 = low, 3 = high
    data: dict = None

    def __post_init__(self):
        if self.data is None:
            self.data = {}


@dataclass
class AlertConfig:
    """Configuration for alert thresholds."""

    # Large move detection
    large_move_threshold_pct: float = 1.0  # 1% move
    large_move_window_minutes: int = 5

    # Alert cooldown (prevent spam)
    min_alert_interval_seconds: int = 60

    # Session alerts
    send_session_start: bool = True
    send_session_end: bool = True


class AlertEngine:
    """
    Monitors market and system for alertable events.

    Usage:
        engine = AlertEngine(on_alert=my_callback)
        engine.on_tick(tick)
        engine.on_signal(signal_packet)
    """

    def __init__(
        self,
        config: AlertConfig | None = None,
        on_alert: Callable[[Alert], None] | None = None,
    ):
        self.config = config or AlertConfig()
        self.on_alert = on_alert

        # Price history for move detection
        self._price_history: list[tuple[datetime, Decimal]] = []
        self._window = timedelta(minutes=self.config.large_move_window_minutes)

        # Cooldown tracking
        self._last_alert_time: dict[AlertType, datetime] = {}

        # Session state
        self._session_started = False

    def on_tick(self, tick: Tick) -> Alert | None:
        """
        Process a tick and check for large moves.

        Args:
            tick: Market tick

        Returns:
            Alert if triggered, None otherwise
        """
        now = tick.timestamp
        price = tick.price

        # Add to history
        self._price_history.append((now, price))

        # Prune old entries
        cutoff = now - self._window
        self._price_history = [(t, p) for t, p in self._price_history if t >= cutoff]

        # Check for large move
        if len(self._price_history) >= 2:
            oldest_price = self._price_history[0][1]
            if oldest_price > 0:
                move_pct = abs(float((price - oldest_price) / oldest_price) * 100)

                if move_pct >= self.config.large_move_threshold_pct:
                    direction = "UP" if price > oldest_price else "DOWN"
                    alert = Alert(
                        timestamp=now,
                        alert_type=AlertType.LARGE_MOVE,
                        title=f"Large Move: {direction} {move_pct:.1f}%",
                        message=f"Price moved {move_pct:.1f}% in {self.config.large_move_window_minutes} minutes. Current: {price}",
                        priority=2,
                        data={"move_pct": move_pct, "direction": direction, "price": str(price)},
                    )

                    if self._should_send(AlertType.LARGE_MOVE):
                        self._send_alert(alert)
                        return alert

        return None

    def on_signal(self, packet: SignalPacket) -> Alert | None:
        """
        Send alert for a trade signal.

        Args:
            packet: Signal packet from SignalGenerator

        Returns:
            Alert that was sent
        """
        priority = 3 if packet.should_trade else 1
        title = f"Signal: {packet.playbook}" if packet.should_trade else "Skip Signal"

        alert = Alert(
            timestamp=packet.timestamp,
            alert_type=AlertType.SIGNAL,
            title=title,
            message=packet.to_markdown(),
            priority=priority,
            data=packet.to_dict(),
        )

        if self._should_send(AlertType.SIGNAL):
            self._send_alert(alert)
            return alert

        return None

    def on_session_start(self, timestamp: datetime) -> Alert | None:
        """Send session start notification."""
        if not self.config.send_session_start:
            return None

        self._session_started = True

        alert = Alert(
            timestamp=timestamp,
            alert_type=AlertType.SESSION_START,
            title="RTH Session Started",
            message=f"Trading session started at {timestamp.strftime('%H:%M ET')}. Monitoring for signals.",
            priority=1,
        )

        self._send_alert(alert)
        return alert

    def on_session_end(self, timestamp: datetime, summary: str = "") -> Alert | None:
        """Send session end notification with summary."""
        if not self.config.send_session_end:
            return None

        self._session_started = False

        alert = Alert(
            timestamp=timestamp,
            alert_type=AlertType.SESSION_END,
            title="RTH Session Ended",
            message=f"Trading session ended at {timestamp.strftime('%H:%M ET')}.\n\n{summary}",
            priority=1,
        )

        self._send_alert(alert)
        return alert

    def on_error(self, error_msg: str, timestamp: datetime | None = None) -> Alert:
        """Send error alert."""
        alert = Alert(
            timestamp=timestamp or datetime.now(),
            alert_type=AlertType.ERROR,
            title="Error Alert",
            message=error_msg,
            priority=3,
        )

        self._send_alert(alert)
        return alert

    def _should_send(self, alert_type: AlertType) -> bool:
        """Check if we should send this alert type (cooldown check)."""
        now = datetime.now()
        last_sent = self._last_alert_time.get(alert_type)

        if last_sent is None:
            return True

        elapsed = (now - last_sent).total_seconds()
        return elapsed >= self.config.min_alert_interval_seconds

    def _send_alert(self, alert: Alert) -> None:
        """Send the alert via callback."""
        self._last_alert_time[alert.alert_type] = alert.timestamp

        if self.on_alert:
            try:
                self.on_alert(alert)
            except Exception as e:
                logger.error(f"Alert callback failed: {e}")
        else:
            logger.info(f"Alert: {alert.title}")

    def reset(self) -> None:
        """Reset engine state."""
        self._price_history.clear()
        self._last_alert_time.clear()
        self._session_started = False
