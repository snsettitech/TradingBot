"""History Loader Utility.

Orchestrates loading historical data from local cache or remote fetchers.
Handles bootstrapping strategies with historical bars.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tsxbot.config_loader import AppConfig
from tsxbot.data.databento_fetcher import DatabentoFetcher
from tsxbot.data.historical_fetcher import Bar, HistoricalDataFetcher
from tsxbot.data.indicators import aggregate_bars

logger = logging.getLogger(__name__)


class HistoryLoader:
    """
    Manages historical data loading and caching.

    Ensures 6 months of data is available for strategy priming.
    Automatically updates data if it's stale.
    """

    def __init__(self, config: AppConfig, data_dir: str = "data/historical"):
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.databento = DatabentoFetcher(data_dir=str(self.data_dir))
        self.projectx_fetcher = HistoricalDataFetcher(data_dir=str(self.data_dir))

    async def get_history(self, symbol: str, days: int = 180) -> list[Bar]:
        """
        Get historical bars for a symbol, using cache if possible.

        Args:
            symbol: Symbol name (ES, MES)
            days: Number of days to ensure in history

        Returns:
            List of Bar objects
        """
        # 1. Determine local file path
        filename = f"{symbol}_history_{days}d.csv"
        filepath = self.data_dir / filename

        # 2. Check if cache exists and is fresh
        if filepath.exists():
            # Get last modified time
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
            # If less than 12 hours old, use it
            if datetime.now() - mtime < timedelta(hours=12):
                logger.info(f"Using cached historical data from {filepath}")
                result = self.projectx_fetcher.load_from_csv(filepath)
                return result.bars
            else:
                logger.info(f"Cached data at {filepath} is stale (modified {mtime})")

        # 3. Fetch from remote
        logger.info(f"Fetching fresh {days} days of history for {symbol}...")

        try:
            if self.databento.is_available():
                logger.info("Using Databento for professional historical data")
                result = await self.databento.fetch(symbol=symbol, days=days)
                self.databento.save_to_csv(result, filename=filename)
                return result.bars
            else:
                logger.warning("Databento not configured. Using ProjectX mock data.")
                result = await self.projectx_fetcher.fetch(symbol=symbol, days=days)
                self.projectx_fetcher.save_to_csv(result, filename=filename)
                return result.bars
        except Exception as e:
            logger.error(f"Failed to fetch historical data: {e}")
            if filepath.exists():
                logger.warning("Falling back to stale local cache")
                result = self.projectx_fetcher.load_from_csv(filepath)
                return result.bars
            raise

    def filter_bars(self, bars: list[Bar], start_time: datetime | None = None) -> list[Bar]:
        """Filter bars to only those after start_time."""
        if not start_time:
            return bars

        return [b for b in bars if b.timestamp >= start_time]


async def prime_strategy(strategy: any, config: AppConfig) -> None:
    """Helper to bootstrap a strategy with history."""
    if not hasattr(strategy, "prime_history"):
        return

    loader = HistoryLoader(config)
    symbol = config.symbols.primary  # type: ignore

    # Get strategy-specific timeframe
    target_minutes = 1
    if hasattr(config.strategy, "ema_cloud"):
        target_minutes = config.strategy.ema_cloud.bar_minutes  # type: ignore
        logger.info(f"Target timeframe for priming: {target_minutes}m")

    # 180 days = ~6 months
    try:
        bars = await loader.get_history(symbol, days=180)

        # Aggregate to target timeframe
        if target_minutes > 1:
            logger.info(f"Aggregating {len(bars)} 1m bars to {target_minutes}m bars...")
            bars = aggregate_bars(bars, target_minutes)
            logger.info(f"Aggregation complete: {len(bars)} {target_minutes}m bars remaining")

        strategy.prime_history(bars)
    except Exception as e:
        logger.error(f"Strategy priming failed: {e}")
