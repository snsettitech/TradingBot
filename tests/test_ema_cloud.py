"""Tests for Ripster EMA Cloud Strategy."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from tsxbot.config_loader import (
    AppConfig,
    EMACloudStrategyConfig,
    SessionConfig,
    StrategyConfig,
    SymbolsConfig,
    SymbolSpecConfig,
)
from tsxbot.data.indicators import Bar, calculate_ema, calculate_ema_series
from tsxbot.strategies.ema_cloud import (
    EMACloudStrategy,
    EMAValues,
    MarketBias,
    StrategyState,
)

# ============================================
# EMA Calculation Tests
# ============================================


class TestEMACalculation:
    """Test EMA calculation functions."""

    def test_calculate_ema_insufficient_data(self):
        """EMA returns 0 with insufficient data."""
        prices = [Decimal("100"), Decimal("101"), Decimal("102")]
        result = calculate_ema(prices, period=5)
        assert result == Decimal("0")

    def test_calculate_ema_exact_period(self):
        """EMA with exactly period prices returns SMA."""
        prices = [Decimal("100"), Decimal("102"), Decimal("104"), Decimal("106"), Decimal("108")]
        result = calculate_ema(prices, period=5)
        # SMA = (100 + 102 + 104 + 106 + 108) / 5 = 104
        assert result == Decimal("104")

    def test_calculate_ema_with_more_data(self):
        """EMA calculation with more than period prices."""
        prices = [
            Decimal("100"),
            Decimal("102"),
            Decimal("104"),
            Decimal("106"),
            Decimal("108"),
            Decimal("110"),
        ]
        result = calculate_ema(prices, period=5)
        # First EMA = SMA of first 5 = 104
        # k = 2 / (5 + 1) = 0.333...
        # EMA = 110 * 0.333 + 104 * 0.667 = 36.67 + 69.33 = 106
        assert result > Decimal("104")  # Should move toward 110

    def test_calculate_ema_series_length(self):
        """EMA series has same length as input."""
        bars = [
            Bar(datetime.now(), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 100)
            for _ in range(10)
        ]
        result = calculate_ema_series(bars, period=5)
        assert len(result) == 10

    def test_calculate_ema_series_early_zeros(self):
        """EMA series has zeros for insufficient data."""
        bars = [
            Bar(
                datetime.now(),
                Decimal("100"),
                Decimal("101"),
                Decimal("99"),
                Decimal(str(100 + i)),
                100,
            )
            for i in range(10)
        ]
        result = calculate_ema_series(bars, period=5)
        # First 4 values should be zero
        assert result[0] == Decimal("0")
        assert result[3] == Decimal("0")
        # 5th value should be non-zero
        assert result[4] != Decimal("0")


# ============================================
# EMAValues Tests
# ============================================


class TestEMAValues:
    """Test EMAValues dataclass."""

    def test_cloud_calculations_bullish(self):
        """Test cloud top/bottom in bullish setup."""
        ema = EMAValues(
            ema_5=Decimal("5010"),
            ema_12=Decimal("5008"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4995"),
        )
        assert ema.fast_cloud_top == Decimal("5010")
        assert ema.fast_cloud_bottom == Decimal("5008")
        assert ema.trend_cloud_top == Decimal("5000")
        assert ema.trend_cloud_bottom == Decimal("4995")
        assert ema.trend_cloud_separation == Decimal("5")

    def test_cloud_calculations_bearish(self):
        """Test cloud top/bottom in bearish setup."""
        ema = EMAValues(
            ema_5=Decimal("4990"),
            ema_12=Decimal("4992"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("5005"),
        )
        assert ema.fast_cloud_top == Decimal("4992")
        assert ema.fast_cloud_bottom == Decimal("4990")
        assert ema.trend_cloud_top == Decimal("5005")
        assert ema.trend_cloud_bottom == Decimal("5000")


# ============================================
# Bias Detection Tests
# ============================================


@pytest.fixture
def mock_config():
    """Create mock config for tests."""
    config = MagicMock()

    # Set up strategy config
    config.strategy = MagicMock()
    config.strategy.ema_cloud = EMACloudStrategyConfig()

    # Set up symbols config
    config.symbols = SymbolsConfig(
        primary="ES",
        micros="MES",
        es=SymbolSpecConfig(tick_size=Decimal("0.25"), tick_value=Decimal("12.50")),
        mes=SymbolSpecConfig(tick_size=Decimal("0.25"), tick_value=Decimal("1.25")),
    )
    return config


@pytest.fixture
def mock_session():
    """Create mock session manager."""
    session = MagicMock()
    session.is_rth.return_value = True
    session.rth_start_time = datetime.now().replace(hour=9, minute=30)
    return session


class TestBiasDetection:
    """Test market bias determination."""

    def test_bullish_bias(self, mock_config, mock_session):
        """Bullish bias when price above trend cloud and 34 > 50."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("5010"),
            ema_12=Decimal("5008"),
            ema_34=Decimal("5000"),  # 34 > 50
            ema_50=Decimal("4995"),
        )

        # Price above trend cloud top (5000)
        bias = strategy._determine_bias(Decimal("5015"))
        assert bias == MarketBias.BULLISH

    def test_bearish_bias(self, mock_config, mock_session):
        """Bearish bias when price below trend cloud and 34 < 50."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("4990"),
            ema_12=Decimal("4992"),
            ema_34=Decimal("5000"),  # 34 < 50
            ema_50=Decimal("5005"),
        )

        # Price below trend cloud bottom (5000)
        bias = strategy._determine_bias(Decimal("4985"))
        assert bias == MarketBias.BEARISH

    def test_neutral_inside_cloud(self, mock_config, mock_session):
        """Neutral bias when price inside trend cloud."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("5002"),
            ema_12=Decimal("5001"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4995"),
        )

        # Price inside trend cloud (between 4995 and 5000)
        bias = strategy._determine_bias(Decimal("4998"))
        assert bias == MarketBias.NEUTRAL

    def test_neutral_flat_emas(self, mock_config, mock_session):
        """Neutral bias when EMAs are flat/compressed."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        # Separation < 2 ticks (0.5 pts)
        strategy.emas = EMAValues(
            ema_5=Decimal("5001"),
            ema_12=Decimal("5000.5"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4999.75"),  # Only 0.25 separation
        )

        bias = strategy._determine_bias(Decimal("5010"))
        assert bias == MarketBias.NEUTRAL


# ============================================
# Entry Logic Tests
# ============================================


class TestEntryLogic:
    """Test entry signal generation."""

    def test_no_entry_without_pullback(self, mock_config, mock_session):
        """No entry if no pullback detected."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("5010"),
            ema_12=Decimal("5008"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4995"),
        )
        strategy.bias = MarketBias.BULLISH
        strategy.state = StrategyState.WAITING_PULLBACK

        # Bar above everything - no pullback
        bar = Bar(
            datetime.now(),
            open=Decimal("5015"),
            high=Decimal("5020"),
            low=Decimal("5014"),
            close=Decimal("5018"),
            volume=1000,
        )

        is_pullback = strategy._is_pullback_into_fast_cloud(bar)
        assert is_pullback is False

    def test_pullback_detected(self, mock_config, mock_session):
        """Pullback detected when bar touches fast cloud."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("5010"),
            ema_12=Decimal("5008"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4995"),
        )
        strategy.bias = MarketBias.BULLISH

        # Bar low touched fast cloud top (5010)
        bar = Bar(
            datetime.now(),
            open=Decimal("5015"),
            high=Decimal("5016"),
            low=Decimal("5009"),  # Below fast cloud top
            close=Decimal("5012"),
            volume=1000,
        )

        is_pullback = strategy._is_pullback_into_fast_cloud(bar)
        assert is_pullback is True

    def test_entry_candle_bullish(self, mock_config, mock_session):
        """Entry candle valid when bullish close above fast cloud."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("5010"),
            ema_12=Decimal("5008"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4995"),
        )
        strategy.bias = MarketBias.BULLISH

        # Bullish candle (close > open) closing above fast cloud
        bar = Bar(
            datetime.now(),
            open=Decimal("5008"),
            high=Decimal("5015"),
            low=Decimal("5007"),
            close=Decimal("5014"),  # Above fast cloud top (5010)
            volume=1000,
        )

        is_entry = strategy._is_entry_candle(bar)
        assert is_entry is True

    def test_no_entry_bearish_candle_in_bullish_bias(self, mock_config, mock_session):
        """No entry if candle is bearish in bullish bias."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("5010"),
            ema_12=Decimal("5008"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4995"),
        )
        strategy.bias = MarketBias.BULLISH

        # Bearish candle (close < open)
        bar = Bar(
            datetime.now(),
            open=Decimal("5015"),
            high=Decimal("5016"),
            low=Decimal("5010"),
            close=Decimal("5012"),  # Close < Open
            volume=1000,
        )

        is_entry = strategy._is_entry_candle(bar)
        assert is_entry is False


# ============================================
# Exit Logic Tests
# ============================================


class TestExitLogic:
    """Test exit signal generation."""

    def test_exit_on_cloud_violation_long(self, mock_config, mock_session):
        """Exit long when close below fast cloud."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("5010"),
            ema_12=Decimal("5008"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4995"),
        )
        strategy.entry_direction = MagicMock()
        strategy.entry_direction.value = "long"

        from tsxbot.constants import SignalDirection

        strategy.entry_direction = SignalDirection.LONG

        # Bar closes below fast cloud bottom (5008)
        bar = Bar(
            datetime.now(),
            open=Decimal("5010"),
            high=Decimal("5011"),
            low=Decimal("5005"),
            close=Decimal("5006"),  # Below fast cloud bottom
            volume=1000,
        )

        is_exit = strategy._is_exit_condition(bar)
        assert is_exit is True

    def test_no_exit_above_cloud_long(self, mock_config, mock_session):
        """No exit for long when still above fast cloud."""
        strategy = EMACloudStrategy(mock_config, mock_session)
        strategy.emas = EMAValues(
            ema_5=Decimal("5010"),
            ema_12=Decimal("5008"),
            ema_34=Decimal("5000"),
            ema_50=Decimal("4995"),
        )

        from tsxbot.constants import SignalDirection

        strategy.entry_direction = SignalDirection.LONG

        # Bar closes above fast cloud
        bar = Bar(
            datetime.now(),
            open=Decimal("5012"),
            high=Decimal("5015"),
            low=Decimal("5009"),
            close=Decimal("5013"),  # Above fast cloud top
            volume=1000,
        )

        is_exit = strategy._is_exit_condition(bar)
        assert is_exit is False
