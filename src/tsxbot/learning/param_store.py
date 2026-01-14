"""Regime-based parameter storage.

Stores and retrieves optimized strategy parameters learned from backtests.
Parameters are organized by strategy and market regime.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

logger = logging.getLogger(__name__)

# Minimum trades required before trusting recommendations
MIN_SAMPLE_SIZE = 20


@dataclass
class StrategyParams:
    """Learned parameters for a strategy in a specific regime."""

    # Strategy identification
    strategy: str
    regime: str

    # ORB-specific parameters (extend for other strategies)
    opening_range_minutes: int = 30
    profit_target_ticks: int = 16
    stop_loss_ticks: int = 8

    # Performance metrics from backtest
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win_ticks: float = 0.0
    avg_loss_ticks: float = 0.0
    sample_size: int = 0

    # Metadata
    last_updated: str = ""
    backtest_source: str = ""  # Path to source backtest file
    confidence: float = 0.0  # 0-1, based on sample size and consistency

    # AI recommendation
    ai_recommendation: str = ""

    def is_trusted(self) -> bool:
        """Check if we have enough data to trust these parameters."""
        return self.sample_size >= MIN_SAMPLE_SIZE

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return asdict(self)


@dataclass
class ParameterUpdate:
    """Record of a parameter update for audit trail."""

    timestamp: str
    strategy: str
    regime: str
    old_params: dict
    new_params: dict
    reason: str
    backtest_trades: int


class ParameterStore:
    """
    Stores and retrieves optimized parameters by strategy and regime.

    Features:
    - Persists parameters to YAML/JSON file
    - Tracks confidence based on sample size
    - Maintains audit trail of updates
    - Human approval required before applying
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        self.params_file = self.data_dir / "learned_parameters.yaml"
        self.history_file = self.data_dir / "parameter_history.json"

        # In-memory cache: strategy -> regime -> StrategyParams
        self._params: dict[str, dict[str, StrategyParams]] = {}
        self._update_history: list[ParameterUpdate] = []

        # Load existing data
        self._load()

    def get_parameters(self, strategy: str, regime: str) -> StrategyParams | None:
        """
        Get learned parameters for a strategy/regime combination.

        Args:
            strategy: Strategy name (e.g., "orb", "vwap_bounce")
            regime: Market regime (e.g., "trending_bullish", "choppy")

        Returns:
            StrategyParams if found, None otherwise
        """
        strategy_lower = strategy.lower()
        regime_lower = regime.lower()

        if strategy_lower in self._params:
            return self._params[strategy_lower].get(regime_lower)
        return None

    def get_trusted_parameters(self, strategy: str, regime: str) -> StrategyParams | None:
        """
        Get parameters only if we have enough sample size to trust them.

        Returns None if sample_size < MIN_SAMPLE_SIZE.
        """
        params = self.get_parameters(strategy, regime)
        if params and params.is_trusted():
            return params
        return None

    def update_parameters(self, params: StrategyParams, reason: str = "Backtest learning") -> None:
        """
        Update stored parameters for a strategy/regime.

        Args:
            params: New parameters to store
            reason: Reason for update (for audit trail)
        """
        strategy = params.strategy.lower()
        regime = params.regime.lower()

        # Get old params for audit
        old_params = {}
        if strategy in self._params and regime in self._params[strategy]:
            old_params = self._params[strategy][regime].to_dict()

        # Update timestamp
        params.last_updated = datetime.now().isoformat()

        # Store
        if strategy not in self._params:
            self._params[strategy] = {}
        self._params[strategy][regime] = params

        # Record update for audit
        update = ParameterUpdate(
            timestamp=params.last_updated,
            strategy=strategy,
            regime=regime,
            old_params=old_params,
            new_params=params.to_dict(),
            reason=reason,
            backtest_trades=params.sample_size,
        )
        self._update_history.append(update)

        # Persist
        self._save()

        logger.info(
            f"Updated {strategy}/{regime} parameters "
            f"(sample_size={params.sample_size}, confidence={params.confidence:.2f})"
        )

    def get_recommendation(self, strategy: str, regime: str) -> str:
        """
        Get AI recommendation for a strategy/regime.

        Returns human-readable recommendation string.
        """
        params = self.get_parameters(strategy, regime)

        if not params:
            return f"No historical data for {strategy} in {regime} conditions."

        if not params.is_trusted():
            return (
                f"Insufficient data for {strategy} in {regime}: "
                f"only {params.sample_size} trades (need {MIN_SAMPLE_SIZE})"
            )

        lines = [
            f"Based on {params.sample_size} trades in {regime} conditions:",
            f"  Win Rate: {params.win_rate:.1%}",
            f"  Profit Factor: {params.profit_factor:.2f}",
        ]

        if params.ai_recommendation:
            lines.append(f"  AI Suggestion: {params.ai_recommendation}")

        return "\n".join(lines)

    def get_all_parameters(self) -> dict[str, dict[str, StrategyParams]]:
        """Get all stored parameters."""
        return self._params.copy()

    def get_regime_summary(self, regime: str) -> str:
        """
        Get summary of all strategies for a regime.

        Returns formatted string for AI context.
        """
        regime_lower = regime.lower()
        lines = [f"Historical performance in {regime} conditions:"]

        found = False
        for strategy, regimes in self._params.items():
            if regime_lower in regimes:
                params = regimes[regime_lower]
                if params.is_trusted():
                    lines.append(
                        f"  {strategy.upper()}: {params.win_rate:.1%} win rate, "
                        f"PF {params.profit_factor:.2f} ({params.sample_size} trades)"
                    )
                    found = True

        if not found:
            lines.append("  No trusted data available yet.")

        return "\n".join(lines)

    def export_for_config(self, strategy: str, regime: str) -> dict:
        """
        Export parameters in format suitable for config.yaml.

        Returns dict that can be merged into strategy config.
        """
        params = self.get_trusted_parameters(strategy, regime)
        if not params:
            return {}

        # Return only the tunable parameters
        return {
            "opening_range_minutes": params.opening_range_minutes,
            "profit_target_ticks": params.profit_target_ticks,
            "stop_loss_ticks": params.stop_loss_ticks,
        }

    def _load(self) -> None:
        """Load parameters from disk."""
        if not self.params_file.exists():
            logger.debug("No existing learned parameters file")
            return

        try:
            with open(self.params_file) as f:
                data = yaml.safe_load(f) if YAML_AVAILABLE else json.load(f)

            if not data:
                return

            for strategy, regimes in data.items():
                self._params[strategy] = {}
                for regime, params_dict in regimes.items():
                    self._params[strategy][regime] = StrategyParams(**params_dict)

            logger.info(f"Loaded learned parameters from {self.params_file}")

        except Exception as e:
            logger.warning(f"Failed to load learned parameters: {e}")

    def _save(self) -> None:
        """Save parameters to disk."""
        try:
            # Build serializable data
            data = {}
            for strategy, regimes in self._params.items():
                data[strategy] = {}
                for regime, params in regimes.items():
                    data[strategy][regime] = params.to_dict()

            with open(self.params_file, "w") as f:
                if YAML_AVAILABLE:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
                else:
                    json.dump(data, f, indent=2)

            # Also save update history
            history_data = [asdict(u) for u in self._update_history[-100:]]  # Keep last 100
            with open(self.history_file, "w") as f:
                json.dump(history_data, f, indent=2)

        except Exception as e:
            logger.warning(f"Failed to save learned parameters: {e}")

    def clear(self) -> None:
        """Clear all stored parameters (for testing)."""
        self._params = {}
        self._update_history = []
        if self.params_file.exists():
            self.params_file.unlink()
        if self.history_file.exists():
            self.history_file.unlink()
