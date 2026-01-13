"""Liquidity Sweep Reclaim Strategy (Placeholder)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tsxbot.strategies.base import BaseStrategy, TradeSignal

if TYPE_CHECKING:
    from tsxbot.data.bars import Bar
    from tsxbot.data.market_data import Tick


class SweepReclaimStrategy(BaseStrategy):
    """
    Liquidity Sweep Reclaim strategy implementation.
    Placeholder for future implementation.
    """

    def on_tick(self, tick: Tick) -> list[TradeSignal]:
        return []

    def on_bar(self, bar: Bar) -> list[TradeSignal]:
        return []

    def reset(self) -> None:
        pass
