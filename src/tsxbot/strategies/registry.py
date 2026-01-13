"""Strategy registry."""

import logging

from tsxbot.config_loader import AppConfig
from tsxbot.constants import StrategyName
from tsxbot.strategies.base import BaseStrategy
from tsxbot.strategies.bos_pullback import BOSPullbackStrategy
from tsxbot.strategies.mean_reversion import MeanReversionStrategy
from tsxbot.strategies.orb import ORBStrategy
from tsxbot.strategies.sweep_reclaim import SweepReclaimStrategy
from tsxbot.strategies.vwap_bounce import VWAPBounceStrategy
from tsxbot.time.session_manager import SessionManager

logger = logging.getLogger(__name__)


STRATEGY_MAP: dict[StrategyName, type[BaseStrategy]] = {
    StrategyName.ORB: ORBStrategy,
    StrategyName.SWEEP_RECLAIM: SweepReclaimStrategy,
    StrategyName.BOS_PULLBACK: BOSPullbackStrategy,
    StrategyName.VWAP_BOUNCE: VWAPBounceStrategy,
    StrategyName.MEAN_REVERSION: MeanReversionStrategy,
}


def get_available_strategies() -> list[str]:
    """Return list of available strategy names."""
    return [s.value for s in STRATEGY_MAP]


def get_strategy(config: AppConfig, session_manager: SessionManager) -> BaseStrategy:
    """
    Factory to instantiate active strategy.

    Args:
        config: Application configuration.
        session_manager: Session manager for RTH checks.

    Returns:
        Instantiated strategy.

    Raises:
        ValueError: If strategy name is unknown (with helpful message).
    """
    active_name = config.strategy.active

    strategy_cls = STRATEGY_MAP.get(active_name)
    if not strategy_cls:
        available = get_available_strategies()
        logger.error(f"Unknown strategy '{active_name}'. Available: {available}")
        raise ValueError(
            f"Unknown strategy: '{active_name}'. "
            f"Available strategies: {', '.join(available)}. "
            f"Check your config.yaml 'strategy.active' setting."
        )

    return strategy_cls(config, session_manager)
