"""Strategy Performance Tracker.

Tracks strategy performance by regime for compound learning.
Persists data to enable AI to learn from historical performance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class StrategyPerformance:
    """Performance stats for a strategy in a specific regime."""

    strategy: str
    regime: str
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.winners / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        total_wins = self.avg_win * self.winners if self.winners > 0 else 0
        total_losses = abs(self.avg_loss * self.losers) if self.losers > 0 else 1
        return total_wins / total_losses if total_losses > 0 else 0.0


@dataclass
class TradeOutcome:
    """Single trade outcome for tracking."""

    strategy: str
    regime: str
    direction: str
    pnl_ticks: int
    pnl_dollars: float
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_winner(self) -> bool:
        return self.pnl_ticks > 0


class PerformanceTracker:
    """
    Tracks and persists strategy performance by regime.

    Enables compound learning by:
    1. Recording every trade outcome by strategy and regime
    2. Computing running performance stats
    3. Providing AI with historical performance context
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.data_file = self.data_dir / "performance_history.json"

        # In-memory cache
        self.performance: dict[str, dict[str, StrategyPerformance]] = {}
        self.recent_trades: list[TradeOutcome] = []

        # Load existing data
        self._load()

    def record_trade(self, outcome: TradeOutcome) -> None:
        """Record a trade outcome and update stats."""
        key = (outcome.strategy, outcome.regime)

        # Initialize if needed
        if outcome.strategy not in self.performance:
            self.performance[outcome.strategy] = {}
        if outcome.regime not in self.performance[outcome.strategy]:
            self.performance[outcome.strategy][outcome.regime] = StrategyPerformance(
                strategy=outcome.strategy, regime=outcome.regime
            )

        perf = self.performance[outcome.strategy][outcome.regime]

        # Update stats
        perf.total_trades += 1
        if outcome.is_winner:
            perf.winners += 1
            # Update running average win
            old_total = perf.avg_win * (perf.winners - 1)
            perf.avg_win = (old_total + outcome.pnl_dollars) / perf.winners
        else:
            perf.losers += 1
            # Update running average loss
            old_total = perf.avg_loss * (perf.losers - 1)
            perf.avg_loss = (old_total + outcome.pnl_dollars) / perf.losers

        perf.total_pnl += outcome.pnl_dollars

        # Track recent trades
        self.recent_trades.append(outcome)
        if len(self.recent_trades) > 100:
            self.recent_trades = self.recent_trades[-100:]

        # Persist
        self._save()

        logger.info(
            f"Recorded {outcome.strategy} trade in {outcome.regime}: {outcome.pnl_ticks} ticks"
        )

    def get_performance(self, strategy: str, regime: str) -> StrategyPerformance | None:
        """Get performance for a strategy in a specific regime."""
        if strategy in self.performance:
            return self.performance[strategy].get(regime)
        return None

    def get_best_strategy(self, regime: str, min_trades: int = 5) -> str | None:
        """Get best performing strategy for a regime based on historical data."""
        candidates = []

        for strategy, regimes in self.performance.items():
            if regime in regimes:
                perf = regimes[regime]
                if perf.total_trades >= min_trades:
                    candidates.append((strategy, perf.win_rate, perf.profit_factor))

        if not candidates:
            return None

        # Rank by profit factor, then win rate
        candidates.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return candidates[0][0]

    def get_performance_summary(self) -> str:
        """Get formatted summary for AI context."""
        lines = ["Historical Strategy Performance:"]

        for strategy, regimes in self.performance.items():
            lines.append(f"\n{strategy}:")
            for regime, perf in regimes.items():
                if perf.total_trades > 0:
                    lines.append(
                        f"  {regime}: {perf.total_trades} trades, "
                        f"{perf.win_rate:.0%} win rate, "
                        f"PF: {perf.profit_factor:.2f}"
                    )

        return "\n".join(lines)

    def get_regime_recommendation(self, regime: str) -> str:
        """Get AI-friendly recommendation for a regime."""
        best = self.get_best_strategy(regime)

        if best:
            perf = self.performance[best][regime]
            return (
                f"Based on {perf.total_trades} historical trades in {regime} conditions, "
                f"{best} has performed best with {perf.win_rate:.0%} win rate "
                f"and {perf.profit_factor:.2f} profit factor."
            )
        return f"No historical data for {regime} conditions yet."

    def _load(self) -> None:
        """Load performance data from disk."""
        if not self.data_file.exists():
            return

        try:
            with open(self.data_file) as f:
                data = json.load(f)

            for strategy, regimes in data.get("performance", {}).items():
                self.performance[strategy] = {}
                for regime, perf_data in regimes.items():
                    self.performance[strategy][regime] = StrategyPerformance(**perf_data)

            logger.info(f"Loaded performance history from {self.data_file}")
        except Exception as e:
            logger.warning(f"Failed to load performance history: {e}")

    def _save(self) -> None:
        """Save performance data to disk."""
        try:
            data = {
                "performance": {
                    strategy: {regime: asdict(perf) for regime, perf in regimes.items()}
                    for strategy, regimes in self.performance.items()
                },
                "last_updated": datetime.now().isoformat(),
            }

            with open(self.data_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save performance history: {e}")
