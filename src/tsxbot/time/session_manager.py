"""Session manager for RTH trading window enforcement."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from tsxbot.config_loader import SessionConfig

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages trading sessions, RTH windows, and flatten times.

    All times are handled in the configured exchange timezone (default America/New_York).
    """

    def __init__(self, config: SessionConfig) -> None:
        """
        Initialize the session manager.

        Args:
            config: Session configuration.
        """
        self.config = config
        self.tz = ZoneInfo(config.timezone)

        # Parse time strings to time objects
        self.rth_start_time = self._parse_time(config.rth_start)
        self.rth_end_time = self._parse_time(config.rth_end)
        self.flatten_time = self._parse_time(config.flatten_time)

        self.trading_days = set(config.trading_days)

    def _parse_time(self, time_str: str) -> time:
        """Parse H:M string to time object."""
        hour, minute = map(int, time_str.split(":"))
        return time(hour, minute)

    def now(self) -> datetime:
        """Get current time in exchange timezone."""
        return datetime.now(self.tz)

    def is_trading_day(self, dt: datetime | None = None) -> bool:
        """Check if the given date (default now) is a configured trading day."""
        if dt is None:
            dt = self.now()

        # Check weekday (0-6)
        if dt.weekday() not in self.trading_days:
            return False

        # TODO: Add holiday calendar check here

        return True

    def is_rth(self, dt: datetime | None = None) -> bool:
        """
        Check if time is within Regular Trading Hours (RTH).

        RTH is defined as [rth_start, rth_end).
        """
        if dt is None:
            dt = self.now()

        if not self.is_trading_day(dt):
            return False

        t = dt.time()

        # Handle overnight sessions if start > end (not typical for RTH equity index, but good for robustness)
        if self.rth_start_time <= self.rth_end_time:
            return self.rth_start_time <= t < self.rth_end_time
        else:
            # Overnight: e.g. 18:00 to 17:00 next day
            # This logic only works if "day" check is inclusive of overnight start.
            # For strict RTH equity index (09:30-16:00), this branch isn't used.
            return t >= self.rth_start_time or t < self.rth_end_time

    def is_trading_allowed(self, dt: datetime | None = None) -> bool:
        """
        Check if new trade entries are allowed.

        Allowed if:
        1. It is RTH
        2. It is BEFORE the flatten time
        """
        if dt is None:
            dt = self.now()

        if not self.is_rth(dt):
            return False

        # Check flatten cutoff
        # If flatten time is within RTH, we stop new entries at flatten time
        t = dt.time()

        # Logic assumes flatten time is usually near end of RTH
        return not (self.flatten_time <= self.rth_end_time and t >= self.flatten_time)

    def should_flatten(self, dt: datetime | None = None) -> bool:
        """
        Check if positions should be flattened immediately.

        True if:
        1. Trading day but time is >= flatten_time (and still < rth_end)
        2. Or passed RTH end

        This is a trigger for the termination sequence.
        """
        if dt is None:
            dt = self.now()

        if not self.is_trading_day(dt):
            # If we somehow have positions on a non-trading day, flatten immediately
            return True

        t = dt.time()

        # Case 1: Within RTH but past flatten time
        if self.rth_start_time <= t < self.rth_end_time and t >= self.flatten_time:
            return True

        # Case 2: Past RTH end
        # Note: This depends on how often we check. If we check continuously,
        # the first condition catches it.
        return t >= self.rth_end_time

    def time_until_rth_open(self) -> timedelta:
        """Get duration until next RTH open."""
        now = self.now()

        # If currently in RTH, 0
        if self.is_rth(now):
            return timedelta(0)

        candidates = []

        # Check today
        today_open = now.replace(
            hour=self.rth_start_time.hour,
            minute=self.rth_start_time.minute,
            second=0,
            microsecond=0,
        )
        if today_open > now and self.is_trading_day(today_open):
            candidates.append(today_open)

        # Check next 7 days to find next open
        next_day = now
        for _ in range(7):
            next_day += timedelta(days=1)
            next_open = next_day.replace(
                hour=self.rth_start_time.hour,
                minute=self.rth_start_time.minute,
                second=0,
                microsecond=0,
            )
            if self.is_trading_day(next_open):
                candidates.append(next_open)
                break

        if not candidates:
            # Should not happen with default config (M-F)
            return timedelta(hours=24)

        return candidates[0] - now

    def time_until_flatten(self) -> timedelta:
        """Get duration until current session flatten time."""
        if not self.is_trading_allowed():
            return timedelta(0)

        now = self.now()
        flatten_dt = now.replace(
            hour=self.flatten_time.hour, minute=self.flatten_time.minute, second=0, microsecond=0
        )

        if flatten_dt <= now:
            # Should be covered by is_trading_allowed check, but for safety
            return timedelta(0)

        return flatten_dt - now
