"""Technical indicators for trading strategies."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class Bar:
    """OHLCV bar data."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @property
    def typical_price(self) -> Decimal:
        """Calculate typical price: (H + L + C) / 3."""
        return (self.high + self.low + self.close) / 3


@dataclass
class VWAPResult:
    """VWAP calculation result with bands."""

    vwap: Decimal
    upper_band_1std: Decimal
    lower_band_1std: Decimal
    upper_band_2std: Decimal
    lower_band_2std: Decimal


def calculate_vwap(bars: Sequence[Bar]) -> Decimal:
    """
    Calculate Volume Weighted Average Price.

    VWAP = Σ(Typical Price × Volume) / Σ(Volume)

    Args:
        bars: Sequence of OHLCV bars for the day (resets at RTH open)

    Returns:
        VWAP value, or Decimal("0") if no volume
    """
    if not bars:
        return Decimal("0")

    cumulative_tp_vol = Decimal("0")
    cumulative_vol = 0

    for bar in bars:
        tp = bar.typical_price
        cumulative_tp_vol += tp * bar.volume
        cumulative_vol += bar.volume

    if cumulative_vol == 0:
        return Decimal("0")

    return cumulative_tp_vol / cumulative_vol


def calculate_vwap_with_bands(bars: Sequence[Bar], num_std: float = 2.0) -> VWAPResult:
    """
    Calculate VWAP with standard deviation bands.

    Args:
        bars: Sequence of OHLCV bars for the day
        num_std: Number of standard deviations for outer bands

    Returns:
        VWAPResult with VWAP and band values
    """
    if not bars:
        return VWAPResult(
            vwap=Decimal("0"),
            upper_band_1std=Decimal("0"),
            lower_band_1std=Decimal("0"),
            upper_band_2std=Decimal("0"),
            lower_band_2std=Decimal("0"),
        )

    vwap = calculate_vwap(bars)

    if vwap == Decimal("0"):
        return VWAPResult(
            vwap=vwap,
            upper_band_1std=vwap,
            lower_band_1std=vwap,
            upper_band_2std=vwap,
            lower_band_2std=vwap,
        )

    # Calculate variance
    cumulative_vol = sum(bar.volume for bar in bars)
    if cumulative_vol == 0:
        std_dev = Decimal("0")
    else:
        # Volume-weighted variance
        variance_sum = Decimal("0")
        for bar in bars:
            diff = bar.typical_price - vwap
            variance_sum += (diff**2) * bar.volume

        variance = variance_sum / cumulative_vol
        # Square root approximation using Newton's method
        std_dev = _decimal_sqrt(variance)

    return VWAPResult(
        vwap=vwap,
        upper_band_1std=vwap + std_dev,
        lower_band_1std=vwap - std_dev,
        upper_band_2std=vwap + (std_dev * Decimal(str(num_std))),
        lower_band_2std=vwap - (std_dev * Decimal(str(num_std))),
    )


def _decimal_sqrt(n: Decimal, precision: int = 10) -> Decimal:
    """Calculate square root of a Decimal using Newton's method."""
    if n < 0:
        raise ValueError("Cannot compute square root of negative number")
    if n == 0:
        return Decimal("0")

    # Initial guess
    x = n
    for _ in range(precision):
        x = (x + n / x) / 2

    return x


def calculate_atr(bars: Sequence[Bar], period: int = 14) -> Decimal:
    """
    Calculate Average True Range.

    Args:
        bars: Sequence of OHLCV bars
        period: ATR period (default 14)

    Returns:
        ATR value
    """
    if len(bars) < 2:
        return Decimal("0")

    true_ranges: list[Decimal] = []

    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        current = bars[i]

        # True Range = max(H-L, |H-PrevClose|, |L-PrevClose|)
        tr = max(
            current.high - current.low,
            abs(current.high - prev_close),
            abs(current.low - prev_close),
        )
        true_ranges.append(tr)

    # Use last `period` values
    relevant_trs = true_ranges[-period:]
    if not relevant_trs:
        return Decimal("0")

    return sum(relevant_trs) / Decimal(str(len(relevant_trs)))


def calculate_ema(prices: Sequence[Decimal], period: int) -> Decimal:
    """
    Calculate Exponential Moving Average.

    EMA = Price(t) * k + EMA(y) * (1 - k)
    where k = 2 / (period + 1)

    Args:
        prices: Sequence of prices (oldest first)
        period: EMA period

    Returns:
        Current EMA value, or Decimal("0") if insufficient data
    """
    if len(prices) < period:
        return Decimal("0")

    # Multiplier
    k = Decimal("2") / (Decimal(str(period)) + Decimal("1"))

    # Start with SMA for first EMA value
    sma = sum(prices[:period]) / Decimal(str(period))
    ema = sma

    # Calculate EMA for remaining prices
    for price in prices[period:]:
        ema = price * k + ema * (Decimal("1") - k)

    return ema


def calculate_ema_series(bars: Sequence[Bar], period: int) -> list[Decimal]:
    """
    Calculate EMA series for all bars.

    Args:
        bars: Sequence of OHLCV bars (oldest first)
        period: EMA period

    Returns:
        List of EMA values aligned with bars (zeros for insufficient data)
    """
    if len(bars) < period:
        return [Decimal("0")] * len(bars)

    closes = [bar.close for bar in bars]
    result: list[Decimal] = []

    k = Decimal("2") / (Decimal(str(period)) + Decimal("1"))

    # First period-1 values are zero (insufficient data)
    for _ in range(period - 1):
        result.append(Decimal("0"))

    # First EMA is SMA
    sma = sum(closes[:period]) / Decimal(str(period))
    result.append(sma)
    ema = sma

    # Calculate EMA for remaining bars
    for i in range(period, len(bars)):
        ema = closes[i] * k + ema * (Decimal("1") - k)
        result.append(ema)

    return result


def aggregate_bars(bars: Sequence[Bar], target_minutes: int) -> list[Bar]:
    """
    Aggregate bars into a higher timeframe.

    Args:
        bars: Sequence of OHLCV bars (usually 1-minute)
        target_minutes: Target timeframe in minutes

    Returns:
        List of aggregated bars
    """
    if not bars or target_minutes <= 1:
        return list(bars)

    aggregated: list[Bar] = []
    current_bar: Bar | None = None

    # Sort by timestamp
    sorted_bars = sorted(bars, key=lambda b: b.timestamp)

    for bar in sorted_bars:
        # Determine bar start time
        minute = bar.timestamp.minute
        aligned_minute = (minute // target_minutes) * target_minutes
        bar_start = bar.timestamp.replace(minute=aligned_minute, second=0, microsecond=0)

        if current_bar is None or bar_start != current_bar.timestamp:
            # Start new aggregated bar
            if current_bar:
                aggregated.append(current_bar)

            current_bar = Bar(
                timestamp=bar_start,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume
            )
        else:
            # Update existing bar
            current_bar.high = max(current_bar.high, bar.high)
            current_bar.low = min(current_bar.low, bar.low)
            current_bar.close = bar.close
            current_bar.volume += bar.volume

    if current_bar:
        aggregated.append(current_bar)

    return aggregated

