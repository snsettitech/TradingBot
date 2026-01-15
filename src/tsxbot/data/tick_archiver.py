"""Tick Archiver - Collect and persist live tick data for backtesting.

Stores live tick data to Supabase and/or local CSV for later backtesting.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from tsxbot.data.market_data import Tick

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


@dataclass
class TickBuffer:
    """Buffer for batching tick writes."""

    ticks: list[Tick]
    max_size: int = 1000
    last_flush: datetime | None = None

    def add(self, tick: Tick) -> bool:
        """Add tick to buffer. Returns True if buffer is full."""
        self.ticks.append(tick)
        return len(self.ticks) >= self.max_size

    def clear(self):
        self.ticks = []
        self.last_flush = datetime.now(ET)


class TickArchiver:
    """
    Archives live tick data for later backtesting.

    Stores ticks to:
    1. Local CSV files (always)
    2. Supabase tick_data table (if configured)

    Usage:
        archiver = TickArchiver()
        archiver.on_tick(tick)  # Called during live session
        archiver.flush()  # Called periodically or at session end
    """

    def __init__(
        self,
        data_dir: str = "data/live",
        buffer_size: int = 500,
        enable_supabase: bool = True,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.buffer = TickBuffer(ticks=[], max_size=buffer_size)
        self.enable_supabase = enable_supabase
        self._supabase = None

        # Current day's file
        self._current_date: date | None = None
        self._csv_file = None
        self._csv_writer = None

        self._tick_count = 0

    def _get_supabase(self):
        """Lazy-load Supabase client."""
        if self._supabase is None and self.enable_supabase:
            try:
                from tsxbot.db.supabase_client import get_supabase_client

                self._supabase = get_supabase_client()
            except Exception as e:
                logger.warning(f"Supabase not available: {e}")
                self._supabase = None
        return self._supabase

    def _get_csv_writer(self, tick_date: date):
        """Get or create CSV writer for the given date."""
        if tick_date != self._current_date:
            # Close previous file
            if self._csv_file:
                self._csv_file.close()

            # Open new file
            self._current_date = tick_date
            filename = f"ticks_{tick_date.isoformat()}.csv"
            filepath = self.data_dir / filename

            file_exists = filepath.exists()
            self._csv_file = open(filepath, "a", newline="")
            self._csv_writer = csv.writer(self._csv_file)

            if not file_exists:
                self._csv_writer.writerow(["timestamp", "symbol", "price", "volume"])
                logger.info(f"Created new tick file: {filepath}")

        return self._csv_writer

    def on_tick(self, tick: Tick) -> None:
        """Process incoming tick."""
        self._tick_count += 1

        # Write immediately to CSV
        writer = self._get_csv_writer(tick.timestamp.date())
        writer.writerow(
            [
                tick.timestamp.isoformat(),
                tick.symbol,
                str(tick.price),
                tick.volume,
            ]
        )

        # Buffer for Supabase batch insert
        if self.enable_supabase:
            if self.buffer.add(tick):
                self._flush_to_supabase()

    def _flush_to_supabase(self) -> None:
        """Flush buffer to Supabase."""
        if not self.buffer.ticks:
            return

        client = self._get_supabase()
        if not client:
            self.buffer.clear()
            return

        try:
            records = [
                {
                    "timestamp": t.timestamp.isoformat(),
                    "symbol": t.symbol,
                    "price": float(t.price),
                    "volume": t.volume,
                }
                for t in self.buffer.ticks
            ]

            client.table("tick_data").insert(records).execute()
            logger.debug(f"Flushed {len(records)} ticks to Supabase")

        except Exception as e:
            logger.error(f"Failed to flush to Supabase: {e}")

        self.buffer.clear()

    def flush(self) -> None:
        """Flush all pending data."""
        if self._csv_file:
            self._csv_file.flush()

        if self.enable_supabase and self.buffer.ticks:
            self._flush_to_supabase()

        logger.info(f"Tick archiver flushed. Total ticks: {self._tick_count}")

    def close(self) -> None:
        """Close all resources."""
        self.flush()
        if self._csv_file:
            self._csv_file.close()

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def get_summary(self) -> str:
        """Get archive summary."""
        return f"Archived {self._tick_count} ticks to {self.data_dir}"
