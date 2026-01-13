"""Tests for ORB Strategy."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tsxbot.config_loader import (
    AppConfig,
    EnvironmentConfig,
    ORBStrategyConfig,
    SessionConfig,
    StrategyConfig,
    SymbolsConfig,
    SymbolSpecConfig,
)
from tsxbot.constants import SignalDirection
from tsxbot.data.market_data import Tick
from tsxbot.strategies.orb import ORBStrategy
from tsxbot.time.session_manager import SessionManager


@pytest.fixture
def nyc_tz():
    return ZoneInfo("America/New_York")


@pytest.fixture
def app_config():
    return AppConfig(
        environment=EnvironmentConfig(dry_run=True),
        session=SessionConfig(
            timezone="America/New_York", rth_start="09:30", rth_end="16:00", flatten_time="15:55"
        ),
        strategy=StrategyConfig(
            active="orb",
            orb=ORBStrategyConfig(
                opening_range_minutes=5,
                breakout_buffer_ticks=2,
                stop_ticks=8,
                target_ticks=16,
                max_trades=2,
                direction="both",
            ),
        ),
        symbols=SymbolsConfig(
            primary="ES",
            micros="MES",
            es=SymbolSpecConfig(tick_size=Decimal("0.25")),
            mes=SymbolSpecConfig(tick_size=Decimal("0.25")),
        ),
    )


@pytest.fixture
def session_manager(app_config):
    return SessionManager(app_config.session)


@pytest.fixture
def strategy(app_config, session_manager):
    return ORBStrategy(app_config, session_manager)


def create_tick(price_str: str, time_str: str, tz) -> Tick:
    """Helper to create tick."""
    dt = datetime.strptime(f"2024-01-03 {time_str}", "%Y-%m-%d %H:%M:%S")
    dt = dt.replace(tzinfo=tz)
    return Tick(symbol="ES", timestamp=dt, price=Decimal(price_str), volume=1)


class TestORBStrategy:
    def test_range_formation(self, strategy, nyc_tz):
        # 09:30: Start
        t1 = create_tick("5000.00", "09:30:00", nyc_tz)
        t2 = create_tick("5005.00", "09:32:00", nyc_tz)  # High
        t3 = create_tick("4995.00", "09:34:00", nyc_tz)  # Low

        strategy.on_tick(t1)
        strategy.on_tick(t2)
        strategy.on_tick(t3)

        assert strategy.range_formed is False
        assert strategy.range_high == Decimal("5005.00")
        assert strategy.range_low == Decimal("4995.00")

        # 09:35:00: Range End (inclusive in logic: <= range_end)
        t4 = create_tick("5002.00", "09:35:00", nyc_tz)
        strategy.on_tick(t4)
        assert strategy.range_formed is False

        # 09:35:01: Breakout window opens
        t5 = create_tick("5003.00", "09:35:01", nyc_tz)
        strategy.on_tick(t5)
        assert strategy.range_formed is True

    def test_breakout_long(self, strategy, nyc_tz):
        # Establish range 4995 - 5005
        # Range ends 09:35:00
        strategy.on_tick(create_tick("5000.00", "09:30:00", nyc_tz))
        strategy.on_tick(create_tick("5005.00", "09:32:00", nyc_tz))
        strategy.on_tick(create_tick("4995.00", "09:34:00", nyc_tz))

        # Trigger range formed
        strategy.on_tick(create_tick("5000.00", "09:35:01", nyc_tz))
        assert strategy.range_formed is True

        # Buffer = 2 ticks = 0.50
        # High = 5005.00 + 0.50 = 5005.50 to trigger

        # Test 5005.25 (1 tick above, no trigger)
        signals = strategy.on_tick(create_tick("5005.25", "09:36:00", nyc_tz))
        assert len(signals) == 0

        # Test 5005.50 (Trigger)
        signals = strategy.on_tick(create_tick("5005.50", "09:36:05", nyc_tz))
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.LONG
        assert signals[0].reason.startswith("ORB High Breakout")

        # Test 5006.00 (Should not trigger again due to latch)
        signals = strategy.on_tick(create_tick("5006.00", "09:36:10", nyc_tz))
        assert len(signals) == 0

    def test_breakout_short(self, strategy, nyc_tz):
        # Establish range 4995 - 5005
        strategy.on_tick(create_tick("5000.00", "09:30:00", nyc_tz))
        strategy.on_tick(create_tick("5005.00", "09:32:00", nyc_tz))
        strategy.on_tick(create_tick("4995.00", "09:34:00", nyc_tz))
        strategy.on_tick(create_tick("5000.00", "09:35:01", nyc_tz))

        # Buffer = 0.50
        # Low = 4995.00 - 0.50 = 4994.50 to trigger

        # Test 4994.75 (No trigger)
        signals = strategy.on_tick(create_tick("4994.75", "09:40:00", nyc_tz))
        assert len(signals) == 0

        # Test 4994.50 (Trigger)
        signals = strategy.on_tick(create_tick("4994.50", "09:40:05", nyc_tz))
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SHORT

    def test_outside_rth(self, strategy, nyc_tz):
        # Pre-market tick
        signals = strategy.on_tick(create_tick("5000.00", "09:29:59", nyc_tz))
        assert len(signals) == 0

        # No range update
        assert strategy.range_high == Decimal("-Infinity")
