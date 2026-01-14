"""Strategy Selector - Chooses best playbook for current regime.

Uses learned parameters and regime analysis to select ONE playbook.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tsxbot.inference.regime_classifier import MarketRegime, RegimeAnalysis
from tsxbot.strategies.playbooks import (
    BreakoutAcceptancePlaybook,
    FakeoutReversalPlaybook,
    LevelBouncePlaybook,
    ORBPullbackPlaybook,
)

if TYPE_CHECKING:
    from tsxbot.config_loader import AppConfig
    from tsxbot.learning.param_store import ParameterStore, StrategyParams
    from tsxbot.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


# Mapping of regimes to preferred playbooks (in priority order)
REGIME_PLAYBOOK_MAPPING = {
    MarketRegime.TREND_UP: [
        "BreakoutAcceptance",
        "ORBPullback",
    ],
    MarketRegime.TREND_DOWN: [
        "BreakoutAcceptance",
        "ORBPullback",
    ],
    MarketRegime.RANGE: [
        "LevelBounce",
        "FakeoutReversal",
    ],
    MarketRegime.BREAKOUT: [
        "BreakoutAcceptance",
        "FakeoutReversal",
    ],
    MarketRegime.HIGH_VOLATILITY: [
        "FakeoutReversal",  # Counter-trend safer in high vol
    ],
    MarketRegime.UNKNOWN: [
        "LevelBounce",  # Conservative default
    ],
}

# Playbook class registry
PLAYBOOK_CLASSES = {
    "BreakoutAcceptance": BreakoutAcceptancePlaybook,
    "FakeoutReversal": FakeoutReversalPlaybook,
    "LevelBounce": LevelBouncePlaybook,
    "ORBPullback": ORBPullbackPlaybook,
}


@dataclass
class SelectionResult:
    """Result of strategy selection."""

    playbook_name: str
    playbook_class: type[BaseStrategy]
    score: float
    rationale: str
    skip_reasons: list[str]
    learned_params: dict[str, Any] | None = None

    @property
    def should_trade(self) -> bool:
        return len(self.skip_reasons) == 0

    def to_dict(self) -> dict:
        return {
            "playbook_name": self.playbook_name,
            "score": self.score,
            "rationale": self.rationale,
            "skip_reasons": self.skip_reasons,
            "should_trade": self.should_trade,
        }


class StrategySelector:
    """
    Selects the best playbook for current market conditions.

    Selection criteria:
    1. Regime alignment (does playbook fit current regime?)
    2. Historical performance (from ParameterStore)
    3. Skip conditions (from playbook)
    """

    def __init__(
        self,
        config: AppConfig,
        param_store: ParameterStore | None = None,
    ):
        self.config = config
        self.param_store = param_store

        # Minimum sample size for learned params
        self.min_sample_size = 10

    def select(
        self,
        regime_analysis: RegimeAnalysis,
        session_manager=None,
    ) -> SelectionResult:
        """
        Select best playbook for current regime.

        Args:
            regime_analysis: Current regime classification
            session_manager: Optional session manager for playbooks

        Returns:
            SelectionResult with selected playbook and rationale
        """
        regime = regime_analysis.regime
        candidates = REGIME_PLAYBOOK_MAPPING.get(regime, ["LevelBounce"])

        best_result: SelectionResult | None = None
        best_score = -1.0

        for playbook_name in candidates:
            playbook_class = PLAYBOOK_CLASSES.get(playbook_name)
            if not playbook_class:
                continue

            score, rationale = self._score_playbook(
                playbook_name,
                regime_analysis,
            )

            # Check skip conditions
            playbook_instance = playbook_class(self.config, session_manager)
            skip_reasons = playbook_instance.get_skip_conditions()

            if score > best_score:
                best_score = score
                best_result = SelectionResult(
                    playbook_name=playbook_name,
                    playbook_class=playbook_class,
                    score=score,
                    rationale=rationale,
                    skip_reasons=skip_reasons,
                    learned_params=self._get_learned_params(playbook_name, regime.value),
                )

        if best_result is None:
            # Fallback to LevelBounce
            best_result = SelectionResult(
                playbook_name="LevelBounce",
                playbook_class=LevelBouncePlaybook,
                score=0.5,
                rationale="Default fallback selection",
                skip_reasons=[],
            )

        logger.info(
            f"Selected: {best_result.playbook_name} (score={best_result.score:.2f}, "
            f"regime={regime.value})"
        )

        return best_result

    def _score_playbook(
        self,
        playbook_name: str,
        regime_analysis: RegimeAnalysis,
    ) -> tuple[float, str]:
        """Score a playbook for current conditions."""
        base_score = 0.5  # Default score
        reasons = []

        # 1. Regime confidence boost
        base_score += regime_analysis.confidence * 0.2
        reasons.append(f"Regime confidence: {regime_analysis.confidence:.0%}")

        # 2. Historical performance (if available)
        if self.param_store:
            params = self.param_store.get_parameters(
                playbook_name,
                regime_analysis.regime.value,
            )
            if params and params.sample_size >= self.min_sample_size:
                # Boost from profit factor
                pf_boost = min(0.3, (params.profit_factor - 1.0) * 0.15)
                base_score += pf_boost
                reasons.append(f"Historical PF: {params.profit_factor:.2f}")

                # Boost from win rate
                wr_boost = (params.win_rate - 0.5) * 0.2
                base_score += wr_boost
                reasons.append(f"Win rate: {params.win_rate:.0%}")

        # 3. Regime-specific adjustments
        regime = regime_analysis.regime

        if regime == MarketRegime.TREND_UP and playbook_name == "BreakoutAcceptance":
            base_score += 0.1
            reasons.append("Breakout favored in uptrend")

        if regime == MarketRegime.RANGE and playbook_name == "LevelBounce":
            base_score += 0.1
            reasons.append("Bounce favored in range")

        rationale = "; ".join(reasons)
        return min(1.0, base_score), rationale

    def _get_learned_params(
        self,
        playbook_name: str,
        regime: str,
    ) -> dict[str, Any] | None:
        """Get learned parameters for playbook/regime."""
        if not self.param_store:
            return None

        params = self.param_store.get_parameters(playbook_name, regime)
        if params:
            return {
                "profit_target_ticks": params.profit_target_ticks,
                "stop_loss_ticks": params.stop_loss_ticks,
                "win_rate": params.win_rate,
                "profit_factor": params.profit_factor,
                "sample_size": params.sample_size,
            }
        return None
