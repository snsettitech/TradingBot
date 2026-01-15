"""Historical Data Loader for Backtesting.

Loads historical bar data from CSV files or API for backtesting.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)


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

    @property
    def typical_price(self) -> Decimal:
        """Return typical price (HLC average)."""
        return (self.high + self.low + self.close) / 3


class HistoricalDataLoader:
    """
    Load historical bar data for backtesting.

    Supports:
    - CSV files
    - ProjectX API (future)
    """

    def __init__(self, symbol: str = "ES"):
        self.symbol = symbol
        self.bars: list[Bar] = []

    def load_from_projectx(
        self,
        api_key: str,
        username: str,
        contract_id: str = "CON.F.US.EP.H26",
        days: int = 30,
        timeframe: str = "Minute1",
    ) -> list[Bar]:
        """
        Load historical bars from ProjectX API.

        Args:
            contract_id: ProjectX contract ID (e.g., "CON.F.US.EP.H26" for ES)
            days: Number of days of history to fetch
            timeframe: Bar timeframe (Minute1, Minute5, etc.)

        Returns:
            List of Bar objects
        """
        from datetime import timedelta

        try:
            from tsxapipy import APIClient
        except ImportError:
            logger.error("tsxapipy not installed. Cannot fetch from ProjectX.")
            return []

        try:
            from tsxapipy.auth import authenticate as tsx_authenticate

            token, token_acquired_at = tsx_authenticate(username, api_key)
            if not token:
                logger.error("Failed to authenticate with ProjectX API")
                return []

            client = APIClient(
                initial_token=token,
                token_acquired_at=token_acquired_at,
                reauth_username=username,
                reauth_api_key=api_key,
            )

            from tsxapipy.api.contract_utils import get_futures_contract_details

            # Resolve contract ID if it's a root symbol like "ES" or "MES"
            resolved_id = contract_id
            if contract_id in ["ES", "MES", "NQ", "MNQ"]:
                details = get_futures_contract_details(client, datetime.now().date(), contract_id)
                if details:
                    resolved_id = details[0]  # String ID
                    logger.info(f"Resolved {contract_id} to {resolved_id}")

            end = datetime.now()
            start = end - timedelta(days=days)

            logger.info(f"Fetching historical bars from ProjectX: {resolved_id}, {days} days")

            # API codes: 2 for Minute, unit_number 1 for 1-minute
            response = client.get_historical_bars(
                contract_id=resolved_id,
                start_time_iso=start.isoformat(),
                end_time_iso=end.isoformat(),
                unit=2,
                unit_number=1,
                limit=1000,
            )

            if not response or not response.bars:
                logger.warning("No bars returned from ProjectX API")
                return []

            bars = []
            for api_bar in response.bars:
                try:
                    bar = Bar(
                        timestamp=api_bar.t,
                        open=Decimal(str(api_bar.o)),
                        high=Decimal(str(api_bar.h)),
                        low=Decimal(str(api_bar.l)),
                        close=Decimal(str(api_bar.c)),
                        volume=int(api_bar.v) if hasattr(api_bar, "v") else 0,
                        symbol=self.symbol,
                    )
                    bars.append(bar)
                except Exception as e:
                    logger.warning(f"Skipping invalid bar: {e}")

            # Sort by timestamp
            bars.sort(key=lambda b: b.timestamp)
            self.bars = bars

            logger.info(f"Loaded {len(bars)} bars from ProjectX API")
            return bars

        except Exception as e:
            logger.error(f"Failed to fetch from ProjectX: {e}")
            return []

    def load_from_databento(
        self,
        days: int = 30,
        symbol: str = "ES",
    ) -> list[Bar]:
        """Load historical bars from Databento."""
        try:
            import asyncio

            from tsxbot.data.databento_fetcher import DatabentoFetcher

            fetcher = DatabentoFetcher()
            if not fetcher.is_available():
                logger.error("Databento API key not found in .env")
                return []

            # Run async fetch in sync context
            result = asyncio.run(fetcher.fetch(symbol=symbol, days=days))

            bars = []
            for d_bar in result.bars:
                bars.append(
                    Bar(
                        timestamp=d_bar.timestamp,
                        open=d_bar.open,
                        high=d_bar.high,
                        low=d_bar.low,
                        close=d_bar.close,
                        volume=d_bar.volume,
                        symbol=symbol,
                    )
                )

            self.bars = bars
            logger.info(f"Loaded {len(bars)} bars from Databento")
            return bars

        except Exception as e:
            logger.error(f"Failed to fetch from Databento: {e}")
            return []

    def load_csv(self, path: str, date_format: str = "%Y-%m-%d %H:%M:%S") -> list[Bar]:
        """
        Load bars from CSV file.

        Expected columns: timestamp, open, high, low, close, volume
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Data file not found: {path}")

        bars = []
        with open(file_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts_str = row["timestamp"]
                    if "T" in ts_str:
                        timestamp = datetime.fromisoformat(ts_str)
                    else:
                        timestamp = datetime.strptime(ts_str, date_format)

                    bar = Bar(
                        timestamp=timestamp,
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=int(row.get("volume", 0)),
                        symbol=self.symbol,
                    )
                    bars.append(bar)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipping invalid row: {e}")

        # Sort by timestamp
        bars.sort(key=lambda b: b.timestamp)
        self.bars = bars

        logger.info(f"Loaded {len(bars)} bars from {path}")
        return bars

    def generate_sample_data(
        self,
        start: datetime,
        days: int = 30,
        bars_per_day: int = 390,  # 6.5 hours * 60 min
    ) -> list[Bar]:
        """
        Generate sample OHLCV data for testing.

        Creates realistic-looking price movement with trends and noise.
        """
        import random
        from datetime import timedelta

        bars = []
        current_price = Decimal("5000")
        current_time = start.replace(hour=9, minute=30, second=0, microsecond=0)

        for _day in range(days):
            # Skip weekends
            if current_time.weekday() >= 5:
                current_time += timedelta(days=1)
                continue

            # Daily bias
            daily_bias = Decimal(str(random.uniform(-0.5, 0.5)))

            for _minute in range(bars_per_day):
                # Price movement
                noise = Decimal(str(random.uniform(-1, 1)))
                trend = daily_bias * Decimal("0.01")

                open_price = current_price
                movement = noise + trend

                # Generate OHLC
                high_add = Decimal(str(abs(random.uniform(0, 2))))
                low_sub = Decimal(str(abs(random.uniform(0, 2))))

                high_price = open_price + high_add
                low_price = open_price - low_sub
                close_price = open_price + movement

                # Ensure OHLC validity
                high_price = max(high_price, open_price, close_price)
                low_price = min(low_price, open_price, close_price)

                bar = Bar(
                    timestamp=current_time,
                    open=round(open_price, 2),
                    high=round(high_price, 2),
                    low=round(low_price, 2),
                    close=round(close_price, 2),
                    volume=random.randint(100, 10000),
                    symbol=self.symbol,
                )
                bars.append(bar)

                current_price = close_price
                current_time += timedelta(minutes=1)

            # Jump to next day
            current_time = (current_time + timedelta(days=1)).replace(
                hour=9, minute=30, second=0, microsecond=0
            )

        self.bars = bars
        logger.info(f"Generated {len(bars)} sample bars")
        return bars

    def filter_rth(self, bars: list[Bar]) -> list[Bar]:
        """Filter bars to only Regular Trading Hours (9:30-16:00 ET)."""
        rth_bars = []
        for bar in bars:
            hour = bar.timestamp.hour
            minute = bar.timestamp.minute
            time_val = hour * 60 + minute
            # 9:30 = 570, 16:00 = 960
            if 570 <= time_val < 960:
                rth_bars.append(bar)
        return rth_bars

    def resample(self, bars: list[Bar], minutes: int = 5) -> list[Bar]:
        """Resample 1-min bars to larger timeframe."""
        if not bars:
            return []

        resampled = []
        current_group = []
        group_start = None

        for bar in bars:
            bar_minute = bar.timestamp.minute
            group_minute = (bar_minute // minutes) * minutes

            bar_group_time = bar.timestamp.replace(minute=group_minute, second=0, microsecond=0)

            if group_start is None:
                group_start = bar_group_time
                current_group = [bar]
            elif bar_group_time == group_start:
                current_group.append(bar)
            else:
                # Finalize current group
                if current_group:
                    resampled_bar = Bar(
                        timestamp=group_start,
                        open=current_group[0].open,
                        high=max(b.high for b in current_group),
                        low=min(b.low for b in current_group),
                        close=current_group[-1].close,
                        volume=sum(b.volume for b in current_group),
                        symbol=self.symbol,
                    )
                    resampled.append(resampled_bar)

                group_start = bar_group_time
                current_group = [bar]

        # Finalize last group
        if current_group:
            resampled_bar = Bar(
                timestamp=group_start,
                open=current_group[0].open,
                high=max(b.high for b in current_group),
                low=min(b.low for b in current_group),
                close=current_group[-1].close,
                volume=sum(b.volume for b in current_group),
                symbol=self.symbol,
            )
            resampled.append(resampled_bar)

        return resampled
