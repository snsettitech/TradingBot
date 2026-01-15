"""Databento Historical Data Fetcher.

Databento provides professional-grade historical futures data with $125 free credits.
Supports 1-minute and tick data for ES, MES, and other CME futures.

Setup:
1. Create account at databento.com
2. Get API key from dashboard
3. Add DATABENTO_API_KEY to .env
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from dotenv import load_dotenv

load_dotenv()
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Re-export Bar from historical_fetcher
from tsxbot.data.historical_fetcher import Bar, HistoricalDataResult


class DatabentoFetcher:
    """
    Fetch historical ES/MES data from Databento.

    Databento provides:
    - $125 free credits for new users
    - 1-minute OHLCV bars for ES futures
    - Tick-level data if needed

    Usage:
        fetcher = DatabentoFetcher()
        result = await fetcher.fetch(symbol="ES", days=180)
        fetcher.save_to_csv(result, "data/historical/es_180d.csv")
    """

    def __init__(self, data_dir: str = "data/historical"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._api_key = os.getenv("DATABENTO_API_KEY")

    def is_available(self) -> bool:
        """Check if Databento is configured."""
        return bool(self._api_key)

    def _get_client(self):
        """Get Databento client."""
        if self._client is None:
            if not self._api_key:
                raise ValueError("DATABENTO_API_KEY not set. Get free credits at databento.com")

            try:
                import databento as db

                self._client = db.Historical(key=self._api_key)
                logger.info("Databento client initialized")
            except ImportError:
                raise ImportError("databento package not installed. Run: pip install databento")

        return self._client

    async def fetch(
        self,
        symbol: str = "ES",
        days: int = 180,
        timeframe: str = "1min",
    ) -> HistoricalDataResult:
        """
        Fetch historical bars from Databento.

        Args:
            symbol: Futures symbol (ES, MES, NQ, etc.)
            days: Number of trading days to fetch
            timeframe: Bar timeframe (currently 1min supported)

        Returns:
            HistoricalDataResult with bars
        """
        client = self._get_client()

        end = date.today()
        start = end - timedelta(days=int(days * 1.5))  # Account for weekends

        logger.info(f"Fetching {symbol} from Databento: {start} to {end}")

        # Databento uses dataset and symbol format
        # ES futures: CME.ES.FUT for continuous contract
        dataset = "GLBX.MDP3"  # CME Globex
        # Use continuous front-month contract
        symbols = ["ES.c.0"]

        result = HistoricalDataResult(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start,
            end_date=end,
            source="databento",
        )

        try:
            # Fetch OHLCV bars
            data = client.timeseries.get_range(
                dataset=dataset,
                symbols=symbols,
                stype_in="continuous",
                schema="ohlcv-1m",  # 1-minute OHLCV
                start=start.isoformat(),
                end=end.isoformat(),
            )

            for record in data:
                # Filter to RTH hours only
                ts = record.ts_event
                dt = datetime.fromtimestamp(ts / 1e9, tz=ET)

                if dt.hour < 9 or (dt.hour == 9 and dt.minute < 30):
                    continue
                if dt.hour >= 16:
                    continue
                if dt.weekday() >= 5:
                    continue

                bar = Bar(
                    timestamp=dt,
                    open=Decimal(str(record.open / 1e9)),  # Databento uses fixed-point
                    high=Decimal(str(record.high / 1e9)),
                    low=Decimal(str(record.low / 1e9)),
                    close=Decimal(str(record.close / 1e9)),
                    volume=record.volume,
                    symbol=symbol,
                )
                result.bars.append(bar)

            logger.info(f"Fetched {len(result.bars)} bars from Databento")

        except Exception as e:
            logger.error(f"Databento fetch failed: {e}")
            raise

        return result

    def save_to_csv(self, result: HistoricalDataResult, filename: str | None = None) -> Path:
        """Save bars to CSV file."""
        if filename is None:
            filename = f"{result.symbol}_{result.timeframe}_{result.count}bars_databento.csv"

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


async def fetch_es_history(days: int = 180) -> Path:
    """Convenience function to fetch ES historical data."""
    fetcher = DatabentoFetcher()

    if not fetcher.is_available():
        logger.warning("Databento not configured. Using mock data instead.")
        from tsxbot.data.historical_fetcher import fetch_and_save

        return await fetch_and_save("ES", days)

    result = await fetcher.fetch(symbol="ES", days=days)
    return fetcher.save_to_csv(result)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 180

    try:
        filepath = asyncio.run(fetch_es_history(days))
        print(f"Data saved to: {filepath}")
    except Exception as e:
        print(f"Error: {e}")
        print("\nTo use Databento:")
        print("1. Create account at https://databento.com")
        print("2. Get $125 free credits")
        print("3. Add DATABENTO_API_KEY=your_key to .env")
