"""Parameter Updater - Safe learning parameter updates.

Implements safeguards:
- Cooldown: No more than 1 update per regime per week
- Improvement threshold: Only update if 10%+ better
- No risk limit changes
- Full audit logging
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tsxbot.learning.param_store import ParameterStore, StrategyParams

logger = logging.getLogger(__name__)


@dataclass
class UpdateResult:
    """Result of an update attempt."""

    strategy: str
    regime: str
    allowed: bool
    reason: str
    old_score: float | None = None
    new_score: float | None = None
    improvement_pct: float | None = None


class ParameterUpdater:
    """
    Safe parameter updater with cooldowns and thresholds.

    Rules:
    1. Minimum improvement threshold (default 10%)
    2. Cooldown period between updates (default 7 days)
    3. Never modify risk limits
    4. Log all update attempts
    """

    def __init__(
        self,
        param_store: ParameterStore,
        improvement_threshold: float = 0.10,  # 10% improvement required
        cooldown_days: int = 7,
        audit_log_path: Path | None = None,
    ):
        self.param_store = param_store
        self.improvement_threshold = improvement_threshold
        self.cooldown_days = cooldown_days
        self.cooldown = timedelta(days=cooldown_days)

        # Audit log
        self.audit_log_path = audit_log_path or Path("data/learning_audit.jsonl")

        # Track last update times
        self._last_update: dict[str, datetime] = {}

    def try_update(
        self,
        new_params: StrategyParams,
        source: str = "backtest",
    ) -> UpdateResult:
        """
        Attempt to update parameters with safety checks.

        Args:
            new_params: Proposed new parameters
            source: Origin of the update (for audit)

        Returns:
            UpdateResult indicating if update was applied
        """
        key = f"{new_params.strategy}:{new_params.regime}"
        now = datetime.now()

        # 1. Check cooldown
        last_update = self._last_update.get(key)
        if last_update and (now - last_update) < self.cooldown:
            remaining = self.cooldown - (now - last_update)
            result = UpdateResult(
                strategy=new_params.strategy,
                regime=new_params.regime,
                allowed=False,
                reason=f"Cooldown active ({remaining.days} days remaining)",
            )
            self._log_attempt(result, new_params, source)
            return result

        # 2. Get current params
        current = self.param_store.get_parameters(
            new_params.strategy,
            new_params.regime,
        )

        # 3. Calculate scores
        new_score = self._calculate_score(new_params)
        old_score = self._calculate_score(current) if current else 0.0

        # 4. Check improvement threshold
        if old_score > 0:
            improvement = (new_score - old_score) / old_score
        else:
            improvement = 1.0  # First update always allowed

        if improvement < self.improvement_threshold:
            result = UpdateResult(
                strategy=new_params.strategy,
                regime=new_params.regime,
                allowed=False,
                reason=f"Insufficient improvement ({improvement:.1%} < {self.improvement_threshold:.0%})",
                old_score=old_score,
                new_score=new_score,
                improvement_pct=improvement * 100,
            )
            self._log_attempt(result, new_params, source)
            return result

        # 5. Check sample size
        if new_params.sample_size < 10:
            result = UpdateResult(
                strategy=new_params.strategy,
                regime=new_params.regime,
                allowed=False,
                reason=f"Insufficient sample size ({new_params.sample_size} < 10)",
                new_score=new_score,
            )
            self._log_attempt(result, new_params, source)
            return result

        # 6. Apply update
        self.param_store.update_parameters(
            new_params,
            reason=f"Auto-update from {source}: {improvement:.1%} improvement",
        )
        self._last_update[key] = now

        result = UpdateResult(
            strategy=new_params.strategy,
            regime=new_params.regime,
            allowed=True,
            reason=f"Updated with {improvement:.1%} improvement",
            old_score=old_score,
            new_score=new_score,
            improvement_pct=improvement * 100,
        )

        self._log_attempt(result, new_params, source)
        logger.info(
            f"Parameters updated: {new_params.strategy}/{new_params.regime} "
            f"({improvement:.1%} improvement)"
        )

        return result

    def _calculate_score(self, params: StrategyParams | None) -> float:
        """
        Calculate weighted score for parameters.

        Score = 0.4*Expectancy + 0.3*ProfitFactor + 0.2*WinRate + 0.1*Confidence
        """
        if params is None:
            return 0.0

        # Expectancy from avg win/loss
        if params.avg_loss_ticks != 0:
            expectancy = params.win_rate * params.avg_win_ticks - (1 - params.win_rate) * abs(
                params.avg_loss_ticks
            )
        else:
            expectancy = params.avg_win_ticks * params.win_rate

        # Normalize components
        pf_normalized = min(params.profit_factor / 3.0, 1.0)  # Cap at 3.0
        wr_normalized = params.win_rate
        exp_normalized = min(max(expectancy / 10.0, 0), 1.0)  # 10 ticks = 1.0
        conf_normalized = params.confidence

        score = (
            0.4 * exp_normalized + 0.3 * pf_normalized + 0.2 * wr_normalized + 0.1 * conf_normalized
        )

        return score

    def _log_attempt(
        self,
        result: UpdateResult,
        params: StrategyParams,
        source: str,
    ) -> None:
        """Log update attempt to audit file."""
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)

            entry = {
                "timestamp": datetime.now().isoformat(),
                "strategy": result.strategy,
                "regime": result.regime,
                "allowed": result.allowed,
                "reason": result.reason,
                "source": source,
                "old_score": result.old_score,
                "new_score": result.new_score,
                "improvement_pct": result.improvement_pct,
                "new_params": {
                    "win_rate": params.win_rate,
                    "profit_factor": params.profit_factor,
                    "sample_size": params.sample_size,
                },
            }

            with open(self.audit_log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

        except Exception as e:
            logger.warning(f"Failed to write audit log: {e}")

    def get_update_history(self, limit: int = 50) -> list[dict]:
        """Get recent update history from audit log."""
        if not self.audit_log_path.exists():
            return []

        entries = []
        try:
            with open(self.audit_log_path) as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to read audit log: {e}")
            return []

        return entries[-limit:]
