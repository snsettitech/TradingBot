"""Parameter scoring for offline analysis.

Evaluates and ranks strategy parameter sets based on backtest performance.
Uses a weighted score of Profit Factor, Win Rate, and Total P&L.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.learning.param_store import StrategyParams

logger = logging.getLogger(__name__)


@dataclass
class ScoreWeights:
    """Weights for scoring components."""

    profit_factor: float = 0.4
    win_rate: float = 0.3
    total_pnl: float = 0.3
    sample_size_penalty: bool = True


class ParameterScorer:
    """
    Scores parameter sets to identify the best performers.

    Formula:
        Score = (PF * w1) + (WR * w2) + (PnL_Norm * w3)

    where PnL_Norm is normalized against the best PnL in the set.
    """

    def __init__(self, weights: ScoreWeights | None = None):
        self.weights = weights or ScoreWeights()

    def score(self, params: StrategyParams, max_pnl: float = 1.0) -> float:
        """
        Calculate score for a single parameter set.

        Args:
            params: The parameters and metrics to score
            max_pnl: The maximum P&L seen in the comparison set (for normalization)

        Returns:
            Float score (higher is better)
        """
        if params.sample_size == 0:
            return 0.0

        # Metrics
        pf = min(params.profit_factor, 10.0)  # Cap PF at 10 to avoid skewing
        wr = params.win_rate  # 0.0 to 1.0

        # Normalize PnL (0 to 1 relative to best performer)
        # Avoid division by zero
        pnl_score = 0.0
        # We can't easily get total PnL from StrategyParams directly unless we calculate it
        # StrategyParams has avg_win/loss and sample size, we can estimate
        total_pnl = (params.avg_win_ticks * params.win_rate * params.sample_size) - (
            abs(params.avg_loss_ticks) * (1 - params.win_rate) * params.sample_size
        )

        if max_pnl > 0:
            pnl_score = max(0.0, total_pnl / max_pnl)

        # Weighted sum
        raw_score = (
            (pf / 3.0 * self.weights.profit_factor)  # Normalize PF ~3.0 as good
            + (wr * self.weights.win_rate)
            + (pnl_score * self.weights.total_pnl)
        )

        # Penalty for small sample size (if trusted threshold not met)
        # We trust 20+, so scale down if less
        if self.weights.sample_size_penalty and params.sample_size < 20:
            penalty_factor = params.sample_size / 20.0
            raw_score *= penalty_factor

        return round(raw_score, 4)

    def rank_parameters(
        self, param_list: list[StrategyParams]
    ) -> list[tuple[float, StrategyParams]]:
        """
        Rank a list of parameter sets.

        Returns:
            List of (score, params) tuples, sorted descending.
        """
        if not param_list:
            return []

        # Calculate max PnL for normalization
        max_pnl = 1.0
        for p in param_list:
            pnl = (p.avg_win_ticks * p.win_rate * p.sample_size) - (
                abs(p.avg_loss_ticks) * (1 - p.win_rate) * p.sample_size
            )
            if pnl > max_pnl:
                max_pnl = pnl

        # Score all
        scored = []
        for p in param_list:
            score = self.score(p, max_pnl)
            scored.append((score, p))

        # Sort
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def get_best(self, param_list: list[StrategyParams]) -> StrategyParams | None:
        """Get the single best parameter set from a list."""
        ranked = self.rank_parameters(param_list)
        if ranked:
            return ranked[0][1]
        return None
