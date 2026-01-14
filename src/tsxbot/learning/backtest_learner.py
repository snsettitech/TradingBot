"""Backtest Learner.

Core engine that analyzes backtest results to extract learning insights.
Connects backtest data -> Scorer -> ParameterStore -> AI Advisor.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from tsxbot.learning.param_store import ParameterStore, StrategyParams
from tsxbot.learning.scorer import ParameterScorer

if TYPE_CHECKING:
    from tsxbot.backtest.results import BacktestResult, TradeRecord
    from tsxbot.config_loader import AppConfig

logger = logging.getLogger(__name__)


class BacktestLearner:
    """
    Learns from backtest execution to improve future performance.

    Responsibilities:
    1. Group trades by market regime
    2. Calculate performance metrics per regime
    3. Update ParameterStore with new findings
    4. Generate AI recommendations (via AI integration)
    """

    def __init__(self, config: AppConfig, param_store: ParameterStore | None = None):
        self.config = config
        self.param_store = param_store or ParameterStore()
        self.scorer = ParameterScorer()

    async def analyze_and_learn(
        self, result: BacktestResult, source_file: str = "backtest"
    ) -> list[StrategyParams]:
        """
        Analyze backtest result and update learned parameters.

        Args:
            result: The completed backtest result
            source_file: Origin of the backtest data for audit trail

        Returns:
            List of learned StrategyParams objects (one per regime found)
        """
        if not result.trades:
            logger.warning("No trades to learn from")
            return []

        # 1. Group trades by regime
        trades_by_regime = self._group_trades_by_regime(result.trades)

        learned_params = []

        # 2. Process each regime
        for regime, trades in trades_by_regime.items():
            if not trades:
                continue

            # Calcs
            params = self._calculate_params(
                strategy=result.strategy,
                regime=regime,
                trades=trades,
                current_config=self._extract_config_params(result),
                source=source_file,
            )

            # 3. Store/Update
            # We only overwrite if the new backtest has MORE data or better score
            # For now, we assume this backtest IS the learning source
            self.param_store.update_parameters(
                params, reason=f"Learning from backtest {source_file} ({len(trades)} trades)"
            )

            learned_params.append(params)

        logger.info(f"Learned insights for {len(learned_params)} regimes")
        return learned_params

    def _group_trades_by_regime(self, trades: list[TradeRecord]) -> dict[str, list[TradeRecord]]:
        """Group trade records by their market regime."""
        groups = defaultdict(list)
        for t in trades:
            # Default to 'unknown' if not set
            r = t.regime if t.regime else "unknown"
            groups[r].append(t)
        return groups

    def _calculate_params(
        self,
        strategy: str,
        regime: str,
        trades: list[TradeRecord],
        current_config: dict[str, Any],
        source: str,
    ) -> StrategyParams:
        """Calculate performance metrics and create StrategyParams object."""
        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if t.is_loser]

        total = len(trades)
        win_rate = len(winners) / total if total > 0 else 0.0

        # PnL stats (in ticks)
        avg_win = sum(t.pnl_ticks for t in winners) / len(winners) if winners else 0.0
        avg_loss = sum(t.pnl_ticks for t in losers) / len(losers) if losers else 0.0

        # Profit Factor
        gross_win = sum(t.pnl_dollars for t in winners) if winners else Decimal("0")
        gross_loss = abs(sum(t.pnl_dollars for t in losers)) if losers else Decimal("1")
        pf = float(gross_win / gross_loss) if gross_loss > 0 else 0.0

        return StrategyParams(
            strategy=strategy,
            regime=regime,
            # Config params (what was used to generate these results)
            opening_range_minutes=current_config.get("opening_range_minutes", 30),
            profit_target_ticks=current_config.get("profit_target_ticks", 0),
            stop_loss_ticks=current_config.get("stop_loss_ticks", 0),
            # Metrics
            win_rate=win_rate,
            profit_factor=pf,
            avg_win_ticks=float(avg_win),
            avg_loss_ticks=float(avg_loss),
            sample_size=total,
            # Meta
            backtest_source=source,
            # Simple confidence heuristic
            confidence=min(1.0, total / 20.0),  # 20 trades = 100% confidence base
        )

    def _extract_config_params(self, result: BacktestResult) -> dict[str, Any]:
        """
        Extract relevant strategy parameters from app config.
        """
        try:
            strat_name = result.strategy.lower()

            # Map strategy class name to config section
            if "orb" in strat_name:
                cfg = self.config.strategy.orb
                return {
                    "opening_range_minutes": getattr(cfg, "opening_range_minutes", 30),
                    "profit_target_ticks": getattr(cfg, "target_ticks", 16),
                    "stop_loss_ticks": getattr(cfg, "stop_ticks", 8),
                }
            elif "vwap" in strat_name:
                cfg = self.config.strategy.vwap_bounce
                return {
                    "opening_range_minutes": getattr(cfg, "skip_first_minutes", 0),  # Proxy
                    "profit_target_ticks": getattr(cfg, "target_ticks", 12),
                    "stop_loss_ticks": getattr(cfg, "stop_ticks", 6),
                }
            elif "mean" in strat_name:
                cfg = self.config.strategy.mean_reversion
                return {
                    "opening_range_minutes": getattr(cfg, "skip_first_minutes", 0),  # Proxy
                    "profit_target_ticks": getattr(cfg, "target_ticks", 8),
                    "stop_loss_ticks": getattr(cfg, "stop_ticks", 8),
                }

        except Exception as e:
            logger.warning(f"Failed to extract config params: {e}")

        return {}
