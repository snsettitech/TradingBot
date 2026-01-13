"""Tests for SessionManager."""

import zoneinfo
from datetime import datetime, time, timedelta

import pytest
from freezegun import freeze_time

from tsxbot.config_loader import SessionConfig
from tsxbot.time.session_manager import SessionManager


@pytest.fixture
def nyc_tz():
    return zoneinfo.ZoneInfo("America/New_York")


@pytest.fixture
def default_config():
    return SessionConfig(
        timezone="America/New_York",
        rth_start="09:30",
        rth_end="16:00",
        flatten_time="15:55",
        trading_days=[0, 1, 2, 3, 4],  # Mon-Fri
    )


class TestSessionManager:
    def test_init_parses_times(self, default_config):
        sm = SessionManager(default_config)
        assert sm.rth_start_time == time(9, 30)
        assert sm.rth_end_time == time(16, 0)
        assert sm.flatten_time == time(15, 55)

    def test_is_trading_day(self, default_config, nyc_tz):
        sm = SessionManager(default_config)

        # Monday Jan 1, 2024
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=nyc_tz)
        assert sm.is_trading_day(dt) is True

        # Sunday Jan 7, 2024
        dt = datetime(2024, 1, 7, 12, 0, tzinfo=nyc_tz)
        assert sm.is_trading_day(dt) is False

    def test_is_rth(self, default_config, nyc_tz):
        sm = SessionManager(default_config)

        # Pre-market: 09:29:59
        dt = datetime(2024, 1, 3, 9, 29, 59, tzinfo=nyc_tz)
        assert sm.is_rth(dt) is False

        # RTH Open: 09:30:00
        dt = datetime(2024, 1, 3, 9, 30, 0, tzinfo=nyc_tz)
        assert sm.is_rth(dt) is True

        # Mid-day
        dt = datetime(2024, 1, 3, 12, 0, 0, tzinfo=nyc_tz)
        assert sm.is_rth(dt) is True

        # RTH Close precise: 16:00:00 (end is exclusive in strict RTH logic usually, but let's check config)
        # Config def: rth_start <= t < rth_end
        dt = datetime(2024, 1, 3, 16, 0, 0, tzinfo=nyc_tz)
        assert sm.is_rth(dt) is False

        # After hours
        dt = datetime(2024, 1, 3, 16, 0, 1, tzinfo=nyc_tz)
        assert sm.is_rth(dt) is False

    def test_is_trading_allowed(self, default_config, nyc_tz):
        sm = SessionManager(default_config)

        # Normal RTH
        dt = datetime(2024, 1, 3, 10, 0, 0, tzinfo=nyc_tz)
        assert sm.is_trading_allowed(dt) is True

        # At flatten time (should disallow new entries)
        dt = datetime(2024, 1, 3, 15, 55, 0, tzinfo=nyc_tz)
        assert sm.is_trading_allowed(dt) is False

        # After flatten, before close
        dt = datetime(2024, 1, 3, 15, 58, 0, tzinfo=nyc_tz)
        assert sm.is_trading_allowed(dt) is False

    def test_should_flatten(self, default_config, nyc_tz):
        sm = SessionManager(default_config)

        # Normal RTH
        dt = datetime(2024, 1, 3, 10, 0, 0, tzinfo=nyc_tz)
        assert sm.should_flatten(dt) is False

        # At flatten time
        dt = datetime(2024, 1, 3, 15, 55, 0, tzinfo=nyc_tz)
        assert sm.should_flatten(dt) is True

        # After close
        dt = datetime(2024, 1, 3, 16, 5, 0, tzinfo=nyc_tz)
        assert sm.should_flatten(dt) is True

    def test_time_until_rth_open(self, default_config, nyc_tz):
        sm = SessionManager(default_config)

        # 09:00 -> 30 mins to open
        dt = datetime(2024, 1, 3, 9, 0, 0, tzinfo=nyc_tz)
        with freeze_time(dt):
            delta = sm.time_until_rth_open()
            assert delta == timedelta(minutes=30)

        # Friday 17:00 -> Monday 09:30
        dt = datetime(2024, 1, 5, 17, 0, 0, tzinfo=nyc_tz)
        with freeze_time(dt):
            delta = sm.time_until_rth_open()
            # 2 days + 16.5 hours
            expected = timedelta(days=2, hours=16, minutes=30)
            assert delta == expected

    def test_time_until_flatten(self, default_config, nyc_tz):
        sm = SessionManager(default_config)

        # 15:00 -> 55 mins to flatten (15:55)
        dt = datetime(2024, 1, 3, 15, 0, 0, tzinfo=nyc_tz)
        with freeze_time(dt):
            delta = sm.time_until_flatten()
            assert delta == timedelta(minutes=55)
