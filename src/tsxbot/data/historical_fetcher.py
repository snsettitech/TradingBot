"""Historical Data Fetcher - Fetch historical bars from ProjectX API.

Provides utilities to download and store historical market data for backtesting.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


@dataclass
class Bar:
    """OHLCV Bar data."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    symbol: str = "ES"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": self.volume,
            "symbol": self.symbol,
        }


@dataclass
class HistoricalDataResult:
    """Result of historical data fetch."""

    bars: list[Bar] = field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    symbol: str = "ES"
    timeframe: str = "1min"
    source: str = "projectx"

    @property
    def count(self) -> int:
        return len(self.bars)


class HistoricalDataFetcher:
    """
    Fetch historical bar data from ProjectX API.

    Usage:
        fetcher = HistoricalDataFetcher()
        result = await fetcher.fetch(symbol="ES", days=30)
        fetcher.save_to_csv(result, "data/historical/es_30d.csv")
    """

    def __init__(self, data_dir: str = "data/historical"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._client = None

    async def _get_client(self):
        """Lazy-load ProjectX client."""
        if self._client is None:
            try:
                from tsxapipy import ProjectXClient

                self._client = ProjectXClient()
                await self._client.connect()
                logger.info("Connected to ProjectX API")
            except Exception as e:
                logger.error(f"Failed to connect to ProjectX: {e}")
                raise
        return self._client

    async def fetch(
        self,
        symbol: str = "ES",
        days: int = 30,
        timeframe: str = "1min",
        end_date: date | None = None,
    ) -> HistoricalDataResult:
        """
        Fetch historical bars from ProjectX.

        Args:
            symbol: Contract symbol (ES, MES)
            days: Number of trading days to fetch
            timeframe: Bar timeframe (1min, 5min, 15min, 1hour)
            end_date: End date (defaults to today)

        Returns:
            HistoricalDataResult with bars
        """
        client = await self._get_client()

        end = end_date or date.today()
        start = end - timedelta(days=int(days * 1.5))  # Account for weekends

        logger.info(f"Fetching {symbol} bars from {start} to {end}")

        result = HistoricalDataResult(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start,
            end_date=end,
        )

        try:
            # ProjectX API call (adjust based on actual API)
            # This is a template - actual implementation depends on tsxapipy
            bars_data = await client.get_historical_bars(
                symbol=symbol,
                start_date=start,
                end_date=end,
                timeframe=timeframe,
            )

            for bar_data in bars_data:
                bar = Bar(
                    timestamp=bar_data.timestamp,
                    open=Decimal(str(bar_data.open)),
                    high=Decimal(str(bar_data.high)),
                    low=Decimal(str(bar_data.low)),
                    close=Decimal(str(bar_data.close)),
                    volume=bar_data.volume,
                    symbol=symbol,
                )
                result.bars.append(bar)

            logger.info(f"Fetched {len(result.bars)} bars")

        except AttributeError:
            # Fallback: ProjectX may not have historical API
            # Try using trades/tick data and aggregating
            logger.warning("Historical bars API not available, using mock data")
            result = await self._generate_mock_data(symbol, days)

        return result

    async def _generate_mock_data(self, symbol: str, days: int) -> HistoricalDataResult:
        """Generate mock data for testing when API unavailable."""
        import random

        result = HistoricalDataResult(symbol=symbol, timeframe="1min")
        base_price = Decimal("5000.00")

        end = datetime.now(ET)
        start = end - timedelta(days=days)

        current = start.replace(hour=9, minute=30, second=0, microsecond=0)

        while current <= end:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            # RTH hours only
            if current.hour < 9 or (current.hour == 9 and current.minute < 30):
                current = current.replace(hour=9, minute=30)
            if current.hour >= 16:
                current += timedelta(days=1)
                current = current.replace(hour=9, minute=30)
                continue

            # Generate bar
            change = Decimal(str(random.uniform(-2, 2)))
            base_price += change

            bar = Bar(
                timestamp=current,
                open=base_price,
                high=base_price + Decimal(str(random.uniform(0, 3))),
                low=base_price - Decimal(str(random.uniform(0, 3))),
                close=base_price + Decimal(str(random.uniform(-1, 1))),
                volume=random.randint(100, 5000),
                symbol=symbol,
            )
            result.bars.append(bar)

            current += timedelta(minutes=1)

        return result

    def save_to_csv(self, result: HistoricalDataResult, filename: str | None = None) -> Path:
        """Save bars to CSV file."""
        if filename is None:
            filename = f"{result.symbol}_{result.timeframe}_{result.count}bars.csv"

        filepath = self.data_dir / filename

        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["timestamp", "open", "high", "low", "close", "volume", "symbol"]
            )
            writer.writeheader()
            for bar in result.bars:
                writer.writerow(bar.to_dict())

        logger.info(f"Saved {result.count} bars to {filepath}")
        return filepath

    def load_from_csv(self, filepath: str | Path) -> HistoricalDataResult:
        """Load bars from CSV file."""
        filepath = Path(filepath)
        result = HistoricalDataResult(source=str(filepath))

        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bar = Bar(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=int(row["volume"]),
                    symbol=row.get("symbol", "ES"),
                )
                result.bars.append(bar)

        if result.bars:
            result.start_date = result.bars[0].timestamp.date()
            result.end_date = result.bars[-1].timestamp.date()
            result.symbol = result.bars[0].symbol

        logger.info(f"Loaded {result.count} bars from {filepath}")
        return result


async def fetch_and_save(symbol: str = "ES", days: int = 180) -> Path:
    """Convenience function to fetch and save historical data."""
    fetcher = HistoricalDataFetcher()
    result = await fetcher.fetch(symbol=symbol, days=days)
    return fetcher.save_to_csv(result)


if __name__ == "__main__":
    # CLI usage
    import sys

    logging.basicConfig(level=logging.INFO)

    symbol = sys.argv[1] if len(sys.argv) > 1 else "ES"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    filepath = asyncio.run(fetch_and_save(symbol, days))
    print(f"Data saved to: {filepath}")
