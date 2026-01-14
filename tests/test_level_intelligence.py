"""Tests for Level Intelligence components."""

from datetime import datetime, time, date
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tsxbot.data.market_data import Tick
from tsxbot.intelligence.level_store import LevelStore, SessionLevels
from tsxbot.intelligence.interaction_detector import (
    InteractionDetector,
    InteractionType,
    LevelInteraction,
)
from tsxbot.intelligence.feature_snapshot import (
    FeatureEngine,
    FeatureSnapshot,
    RegimeType,
    VolatilityState,
)


@pytest.fixture
def nyc_tz():
    return ZoneInfo("America/New_York")


# ============================================================================
# LevelStore Tests
# ============================================================================

class TestLevelStore:
    def test_prior_day_levels(self):
        """Test setting prior day levels."""
        store = LevelStore()
        store.set_prior_day_levels(
            pdh=Decimal("5100.00"),
            pdl=Decimal("5050.00"),
            pdc=Decimal("5075.00"),
        )
        
        levels = store.get_current_levels()
        assert levels is not None
        assert levels.pdh == Decimal("5100.00")
        assert levels.pdl == Decimal("5050.00")
        assert levels.pdc == Decimal("5075.00")
    
    def test_opening_range_formation(self, nyc_tz):
        """Test OR high/low is computed in first 30 minutes."""
        store = LevelStore(opening_range_minutes=30)
        
        # Simulate ticks in first 30 min
        ticks = [
            Tick(symbol="ES", timestamp=datetime(2024, 1, 3, 9, 30, tzinfo=nyc_tz), price=Decimal("5000.00"), volume=100),
            Tick(symbol="ES", timestamp=datetime(2024, 1, 3, 9, 35, tzinfo=nyc_tz), price=Decimal("5010.00"), volume=100),
            Tick(symbol="ES", timestamp=datetime(2024, 1, 3, 9, 45, tzinfo=nyc_tz), price=Decimal("4990.00"), volume=100),
            Tick(symbol="ES", timestamp=datetime(2024, 1, 3, 9, 59, tzinfo=nyc_tz), price=Decimal("5005.00"), volume=100),
        ]
        
        for tick in ticks:
            store.on_tick(tick)
        
        levels = store.get_current_levels()
        assert levels.orh == Decimal("5010.00")
        assert levels.orl == Decimal("4990.00")
        assert levels.or_formed is False  # Still in OR window
        
        # Tick after OR window
        post_or_tick = Tick(
            symbol="ES",
            timestamp=datetime(2024, 1, 3, 10, 1, tzinfo=nyc_tz),
            price=Decimal("5002.00"),
            volume=100,
        )
        store.on_tick(post_or_tick)
        
        levels = store.get_current_levels()
        assert levels.or_formed is True
        assert levels.orh == Decimal("5010.00")  # Should not change
    
    def test_vwap_calculation(self, nyc_tz):
        """Test VWAP is computed correctly."""
        store = LevelStore()
        
        # Price 100 @ volume 10, Price 110 @ volume 10
        # VWAP = (100*10 + 110*10) / 20 = 2100 / 20 = 105
        ticks = [
            Tick(symbol="ES", timestamp=datetime(2024, 1, 3, 9, 30, tzinfo=nyc_tz), price=Decimal("100"), volume=10),
            Tick(symbol="ES", timestamp=datetime(2024, 1, 3, 9, 31, tzinfo=nyc_tz), price=Decimal("110"), volume=10),
        ]
        
        for tick in ticks:
            store.on_tick(tick)
        
        levels = store.get_current_levels()
        assert levels.vwap == Decimal("105")
    
    def test_outside_rth_ignored(self, nyc_tz):
        """Test that pre-market ticks don't update levels."""
        store = LevelStore()
        
        pre_market = Tick(
            symbol="ES",
            timestamp=datetime(2024, 1, 3, 9, 0, tzinfo=nyc_tz),
            price=Decimal("5000.00"),
            volume=100,
        )
        store.on_tick(pre_market)
        
        levels = store.get_current_levels()
        assert levels.orh is None
        assert levels.orl is None
        assert levels.vwap is None


# ============================================================================
# InteractionDetector Tests
# ============================================================================

class TestInteractionDetector:
    def test_touch_detection(self, nyc_tz):
        """Test touch is detected within threshold."""
        detector = InteractionDetector(tick_size=Decimal("0.25"), touch_threshold_ticks=2)
        
        levels = SessionLevels(
            date=date(2024, 1, 3),
            symbol="ES",
            pdh=Decimal("5100.00"),
        )
        detector.update_levels(levels)
        
        # First bar: establish position below level
        detector.on_bar_close(
            bar_close_price=Decimal("5090.00"),
            timestamp=datetime(2024, 1, 3, 9, 59, tzinfo=nyc_tz),
        )
        
        # Price within 2 ticks of PDH (5100 - 0.5 = 5099.50)
        interactions = detector.on_bar_close(
            bar_close_price=Decimal("5099.50"),
            timestamp=datetime(2024, 1, 3, 10, 0, tzinfo=nyc_tz),
        )
        
        # Touch should be added to pending
        assert len(detector._pending) == 1
        pending_interaction = detector._pending[0][0]
        assert pending_interaction.interaction_type == InteractionType.TOUCH
        assert pending_interaction.level_name == "pdh"
    
    def test_break_and_hold(self, nyc_tz):
        """Test break-and-hold is detected after holding 3 bars."""
        detector = InteractionDetector(
            tick_size=Decimal("0.25"),
            hold_bars=3,
        )
        
        levels = SessionLevels(
            date=date(2024, 1, 3),
            symbol="ES",
            pdh=Decimal("5100.00"),
        )
        detector.update_levels(levels)
        
        # Start below, break above
        detector.on_bar_close(Decimal("5099.00"), datetime(2024, 1, 3, 10, 0, tzinfo=nyc_tz))
        detector.on_bar_close(Decimal("5101.00"), datetime(2024, 1, 3, 10, 1, tzinfo=nyc_tz))  # Break
        detector.on_bar_close(Decimal("5102.00"), datetime(2024, 1, 3, 10, 2, tzinfo=nyc_tz))
        detector.on_bar_close(Decimal("5103.00"), datetime(2024, 1, 3, 10, 3, tzinfo=nyc_tz))
        
        # 4th bar after break should confirm
        interactions = detector.on_bar_close(
            Decimal("5104.00"),
            datetime(2024, 1, 3, 10, 4, tzinfo=nyc_tz),
        )
        
        break_holds = [i for i in interactions if i.interaction_type == InteractionType.BREAK_AND_HOLD]
        assert len(break_holds) >= 1
        assert break_holds[0].level_name == "pdh"


# ============================================================================
# FeatureSnapshot Tests
# ============================================================================

class TestFeatureEngine:
    def test_vwap_context(self, nyc_tz):
        """Test VWAP distance and side calculation."""
        engine = FeatureEngine(tick_size=Decimal("0.25"))
        
        levels = SessionLevels(
            date=date(2024, 1, 3),
            symbol="ES",
            vwap=Decimal("5050.00"),
        )
        
        snapshot = engine.compute_snapshot(
            price=Decimal("5055.00"),
            timestamp=datetime(2024, 1, 3, 10, 0, tzinfo=nyc_tz),
            levels=levels,
        )
        
        assert snapshot.side_of_vwap == "above"
        assert snapshot.distance_to_vwap_ticks == 20.0  # (5055 - 5050) / 0.25
    
    def test_or_position(self, nyc_tz):
        """Test position relative to opening range."""
        engine = FeatureEngine()
        
        levels = SessionLevels(
            date=date(2024, 1, 3),
            symbol="ES",
            orh=Decimal("5060.00"),
            orl=Decimal("5040.00"),
        )
        
        # Price within OR
        snapshot = engine.compute_snapshot(
            price=Decimal("5050.00"),
            timestamp=datetime(2024, 1, 3, 10, 0, tzinfo=nyc_tz),
            levels=levels,
        )
        assert snapshot.position_in_or == "within"
        
        # Reset and test above
        engine.reset()
        snapshot = engine.compute_snapshot(
            price=Decimal("5065.00"),
            timestamp=datetime(2024, 1, 3, 10, 1, tzinfo=nyc_tz),
            levels=levels,
        )
        assert snapshot.position_in_or == "above"
    
    def test_regime_classification_trend_up(self, nyc_tz):
        """Test trend up regime is classified correctly."""
        engine = FeatureEngine()
        
        levels = SessionLevels(
            date=date(2024, 1, 3),
            symbol="ES",
            orh=Decimal("5060.00"),
            orl=Decimal("5040.00"),
        )
        
        # Feed rising prices to establish uptrend
        for i in range(25):
            engine.compute_snapshot(
                price=Decimal("5050") + Decimal(i),
                timestamp=datetime(2024, 1, 3, 10, i, tzinfo=nyc_tz),
                levels=levels,
            )
        
        # Final snapshot should show trend up
        snapshot = engine.compute_snapshot(
            price=Decimal("5080.00"),
            timestamp=datetime(2024, 1, 3, 10, 30, tzinfo=nyc_tz),
            levels=levels,
        )
        
        assert snapshot.regime == RegimeType.TREND_UP
        assert snapshot.position_in_or == "above"
    
    def test_time_of_day(self, nyc_tz):
        """Test time of day classification."""
        engine = FeatureEngine()
        
        # Open (first 30 min)
        snapshot = engine.compute_snapshot(
            price=Decimal("5000"),
            timestamp=datetime(2024, 1, 3, 9, 45, tzinfo=nyc_tz),
        )
        assert snapshot.time_of_day == "open"
        
        # Midday
        engine.reset()
        snapshot = engine.compute_snapshot(
            price=Decimal("5000"),
            timestamp=datetime(2024, 1, 3, 12, 0, tzinfo=nyc_tz),
        )
        assert snapshot.time_of_day == "midday"
        
        # Close
        engine.reset()
        snapshot = engine.compute_snapshot(
            price=Decimal("5000"),
            timestamp=datetime(2024, 1, 3, 15, 30, tzinfo=nyc_tz),
        )
        assert snapshot.time_of_day == "close"
