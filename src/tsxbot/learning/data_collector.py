"""Data Collector - Nightly data collection for learning.

Pulls day's data and prepares it for the learning loop:
1. Fetch tick data from broker/API
2. Compute and store levels
3. Trigger backtest and WFV
4. Update learned parameters
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from tsxbot.db.supabase_client import SupabaseStore
from tsxbot.intelligence.level_store import LevelStore

if TYPE_CHECKING:
    from tsxbot.backtest.data_loader import Bar
    from tsxbot.config_loader import AppConfig

logger = logging.getLogger(__name__)


class DataCollector:
    """
    Collects and processes daily market data for learning.
    
    Run nightly after RTH close to:
    1. Store day's levels
    2. Archive tick data (optional)
    3. Trigger learning pipeline
    """
    
    def __init__(
        self,
        config: AppConfig,
        supabase: SupabaseStore | None = None,
    ):
        self.config = config
        self.supabase = supabase or SupabaseStore()
        self.level_store = LevelStore()
    
    async def collect_daily(
        self,
        target_date: date | None = None,
    ) -> dict:
        """
        Collect and store data for a specific date.
        
        Args:
            target_date: Date to collect (default: today)
        
        Returns:
            Summary of collection results
        """
        target_date = target_date or date.today()
        logger.info(f"Collecting data for {target_date}")
        
        results = {
            "date": target_date.isoformat(),
            "levels_stored": False,
            "ticks_stored": 0,
            "errors": [],
        }
        
        # 1. Get levels from level store (if available)
        levels = self.level_store.get_current_levels()
        if levels and self.supabase.is_available:
            try:
                success = self.supabase.upsert_levels(levels.to_dict())
                results["levels_stored"] = success
                logger.info(f"Levels stored: {levels.to_dict()}")
            except Exception as e:
                results["errors"].append(f"Level storage failed: {e}")
                logger.error(f"Failed to store levels: {e}")
        
        # 2. Archive tick data (placeholder - would need broker integration)
        # This would typically come from the broker's historical API
        logger.info("Tick archival not implemented - requires broker API")
        
        return results
    
    async def run_learning_pipeline(
        self,
        strategy_classes: list,
        lookback_days: int = 180,
    ) -> dict:
        """
        Run the full learning pipeline.
        
        Steps:
        1. Load historical data
        2. Run WFV for each strategy
        3. Update parameters
        
        Args:
            strategy_classes: List of strategy classes to evaluate
            lookback_days: Days of data to use (default: 180 = 6 months)
        
        Returns:
            Pipeline results summary
        """
        from tsxbot.learning.param_store import ParameterStore
        from tsxbot.learning.updater import ParameterUpdater
        from tsxbot.learning.walk_forward import WalkForwardValidator
        
        logger.info(f"Starting learning pipeline with {lookback_days} days lookback")
        
        results = {
            "strategies_evaluated": 0,
            "parameters_updated": 0,
            "wfv_results": [],
        }
        
        param_store = ParameterStore()
        updater = ParameterUpdater(param_store)
        wfv = WalkForwardValidator(self.config)
        
        # Load historical bars (placeholder)
        bars = await self._load_historical_bars(lookback_days)
        if not bars:
            logger.warning("No historical data available for learning")
            return results
        
        wfv.load_data(bars)
        
        for strategy_class in strategy_classes:
            try:
                wfv_result = wfv.run(strategy_class)
                results["wfv_results"].append(wfv_result.to_dict())
                results["strategies_evaluated"] += 1
                
                if wfv_result.is_valid and wfv_result.best_params:
                    # Convert to StrategyParams and update
                    from tsxbot.learning.param_store import StrategyParams
                    
                    for regime in ["trend_up", "trend_down", "range"]:
                        params = StrategyParams(
                            strategy=wfv_result.strategy,
                            regime=regime,
                            win_rate=wfv_result.avg_out_sample_wr,
                            profit_factor=wfv_result.avg_out_sample_pf,
                            sample_size=sum(
                                w.out_sample_metrics.get("total_trades", 0)
                                for w in wfv_result.windows
                            ),
                            confidence=wfv_result.stability_score,
                            backtest_source="learning_pipeline",
                        )
                        
                        update_result = updater.try_update(params, source="wfv_pipeline")
                        if update_result.allowed:
                            results["parameters_updated"] += 1
                
            except Exception as e:
                logger.error(f"Failed to process {strategy_class.__name__}: {e}")
        
        logger.info(
            f"Learning pipeline complete: {results['strategies_evaluated']} strategies, "
            f"{results['parameters_updated']} updates"
        )
        
        return results
    
    async def _load_historical_bars(self, days: int) -> list[Bar]:
        """Load historical bars from storage or API."""
        # Placeholder - would load from Supabase or file storage
        logger.warning("Historical bar loading not implemented")
        return []


async def run_nightly_collection():
    """Entry point for nightly collection job."""
    logging.basicConfig(level=logging.INFO)
    
    from tsxbot.config_loader import load_config
    from tsxbot.strategies.playbooks import (
        BreakoutAcceptancePlaybook,
        FakeoutReversalPlaybook,
        LevelBouncePlaybook,
        ORBPullbackPlaybook,
    )
    
    config = load_config()
    collector = DataCollector(config)
    
    # Collect day's data
    await collector.collect_daily()
    
    # Run learning pipeline
    await collector.run_learning_pipeline([
        BreakoutAcceptancePlaybook,
        FakeoutReversalPlaybook,
        LevelBouncePlaybook,
        ORBPullbackPlaybook,
    ])


if __name__ == "__main__":
    asyncio.run(run_nightly_collection())
