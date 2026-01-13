"""Simulation Data Feed."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

from tsxbot.data.market_data import Tick

logger = logging.getLogger(__name__)


class SimDataFeed:
    """
    Generates synthetic market data for simulation/dry-run.
    Produces a random walk price path.
    """

    def __init__(
        self,
        symbols: list[str],
        callback: Callable[[Tick], Awaitable[None]],
        interval_sec: float = 1.0,
        start_price: Decimal = Decimal("5000.00"),
    ):
        self.symbols = symbols
        self.callback = callback
        self.interval_sec = interval_sec
        self.current_prices = dict.fromkeys(symbols, start_price)
        self._running = False

    async def start(self) -> None:
        """Start generating ticks."""
        self._running = True
        logger.info(f"SimDataFeed started for {self.symbols}")

        while self._running:
            for symbol in self.symbols:
                # Random walk
                change = Decimal(str(random.choice([-0.25, 0, 0.25, 0.50, -0.50])))
                self.current_prices[symbol] += change

                tick = Tick(
                    symbol=symbol,
                    timestamp=datetime.now(),  # Use current sim time?
                    price=self.current_prices[symbol],
                    volume=random.randint(1, 100),
                )

                await self.callback(tick)

            await asyncio.sleep(self.interval_sec)

    def stop(self) -> None:
        """Stop generation."""
        self._running = False
        logger.info("SimDataFeed stopped")
