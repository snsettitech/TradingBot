"""Walk-Forward Validation Engine.

Implements rolling window validation for strategy parameter optimization.
Prevents overfitting by testing on out-of-sample data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tsxbot.backtest.data_loader import Bar
    from tsxbot.config_loader import AppConfig
    from tsxbot.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class WFVWindow:
    """A single walk-forward validation window."""
    
    in_sample_start: date
    in_sample_end: date
    out_sample_start: date
    out_sample_end: date
    
    # Results
    in_sample_metrics: dict[str, float] = field(default_factory=dict)
    out_sample_metrics: dict[str, float] = field(default_factory=dict)
    optimized_params: dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_degraded(self) -> bool:
        """Check if out-of-sample performance degrades significantly."""
        is_pf = self.in_sample_metrics.get("profit_factor", 1.0)
        os_pf = self.out_sample_metrics.get("profit_factor", 1.0)
        
        if is_pf <= 0:
            return True
        
        degradation = (is_pf - os_pf) / is_pf
        return degradation > 0.5  # More than 50% degradation


@dataclass
class WFVResult:
    """Complete walk-forward validation result."""
    
    strategy: str
    windows: list[WFVWindow] = field(default_factory=list)
    
    # Aggregate metrics
    avg_out_sample_pf: float = 0.0
    avg_out_sample_wr: float = 0.0
    stability_score: float = 0.0  # % of windows that passed
    
    # Best parameters (from best stable window)
    best_params: dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_valid(self) -> bool:
        """Check if WFV passed quality thresholds."""
        return (
            self.stability_score >= 0.6  # At least 60% of windows stable
            and self.avg_out_sample_pf >= 1.0  # Positive expectancy
        )
    
    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "num_windows": len(self.windows),
            "avg_out_sample_pf": self.avg_out_sample_pf,
            "avg_out_sample_wr": self.avg_out_sample_wr,
            "stability_score": self.stability_score,
            "is_valid": self.is_valid,
            "best_params": self.best_params,
        }


class WalkForwardValidator:
    """
    Walk-Forward Validation engine.
    
    Configuration:
    - in_sample_weeks: Weeks of data for optimization (default: 4)
    - out_sample_weeks: Weeks for validation (default: 1)
    - min_trades_per_window: Minimum trades required (default: 10)
    """
    
    def __init__(
        self,
        config: AppConfig,
        in_sample_weeks: int = 4,
        out_sample_weeks: int = 1,
        min_trades_per_window: int = 10,
        slippage_ticks: int = 1,
        commission_per_trade: Decimal = Decimal("4.00"),
    ):
        self.config = config
        self.in_sample_weeks = in_sample_weeks
        self.out_sample_weeks = out_sample_weeks
        self.min_trades = min_trades_per_window
        self.slippage_ticks = slippage_ticks
        self.commission = commission_per_trade
        
        # Internal backtest engine will be created per window
        self._bars: list[Bar] = []
    
    def load_data(self, bars: list[Bar]) -> None:
        """Load bar data for validation."""
        self._bars = sorted(bars, key=lambda b: b.timestamp)
        logger.info(f"Loaded {len(bars)} bars for WFV")
    
    def run(self, strategy_class: type[BaseStrategy]) -> WFVResult:
        """
        Run walk-forward validation.
        
        Args:
            strategy_class: Strategy class to validate
        
        Returns:
            WFVResult with all window results
        """
        if not self._bars:
            logger.warning("No data loaded for WFV")
            return WFVResult(strategy=strategy_class.__name__)
        
        # Determine date range
        start_date = self._bars[0].timestamp.date()
        end_date = self._bars[-1].timestamp.date()
        
        # Generate windows
        windows = self._generate_windows(start_date, end_date)
        logger.info(f"Generated {len(windows)} WFV windows")
        
        result = WFVResult(strategy=strategy_class.__name__)
        
        for window in windows:
            self._process_window(window, strategy_class)
            result.windows.append(window)
        
        # Calculate aggregate metrics
        self._calculate_aggregates(result)
        
        return result
    
    def _generate_windows(self, start: date, end: date) -> list[WFVWindow]:
        """Generate rolling windows for validation."""
        windows = []
        
        window_size = timedelta(weeks=self.in_sample_weeks + self.out_sample_weeks)
        step_size = timedelta(weeks=self.out_sample_weeks)
        
        current_start = start
        
        while current_start + window_size <= end:
            in_sample_end = current_start + timedelta(weeks=self.in_sample_weeks) - timedelta(days=1)
            out_sample_start = in_sample_end + timedelta(days=1)
            out_sample_end = out_sample_start + timedelta(weeks=self.out_sample_weeks) - timedelta(days=1)
            
            windows.append(WFVWindow(
                in_sample_start=current_start,
                in_sample_end=in_sample_end,
                out_sample_start=out_sample_start,
                out_sample_end=out_sample_end,
            ))
            
            current_start += step_size
        
        return windows
    
    def _process_window(
        self,
        window: WFVWindow,
        strategy_class: type[BaseStrategy],
    ) -> None:
        """Process a single WFV window."""
        from tsxbot.backtest.engine import BacktestEngine
        
        # Filter bars for in-sample period
        in_sample_bars = [
            b for b in self._bars
            if window.in_sample_start <= b.timestamp.date() <= window.in_sample_end
        ]
        
        # Filter bars for out-of-sample period
        out_sample_bars = [
            b for b in self._bars
            if window.out_sample_start <= b.timestamp.date() <= window.out_sample_end
        ]
        
        if len(in_sample_bars) < 100 or len(out_sample_bars) < 20:
            logger.warning(f"Insufficient data for window {window.in_sample_start}")
            return
        
        # Run in-sample backtest
        strategy_is = strategy_class(self.config, None)
        engine_is = BacktestEngine(
            config=self.config,
            strategy=strategy_is,
            tick_value=Decimal("12.50"),
            fee_per_trade=self.commission,
        )
        engine_is.load_data(in_sample_bars)
        result_is = engine_is.run()
        
        window.in_sample_metrics = {
            "profit_factor": result_is.profit_factor,
            "win_rate": result_is.win_rate,
            "total_trades": result_is.total_trades,
            "expectancy": result_is.expectancy_r,
        }
        
        # Extract optimized params (for now, just use current config)
        window.optimized_params = self._extract_params(strategy_is)
        
        # Run out-of-sample backtest with same params
        strategy_os = strategy_class(self.config, None)
        engine_os = BacktestEngine(
            config=self.config,
            strategy=strategy_os,
            tick_value=Decimal("12.50"),
            fee_per_trade=self.commission,
        )
        engine_os.load_data(out_sample_bars)
        result_os = engine_os.run()
        
        window.out_sample_metrics = {
            "profit_factor": result_os.profit_factor,
            "win_rate": result_os.win_rate,
            "total_trades": result_os.total_trades,
            "expectancy": result_os.expectancy_r,
        }
        
        logger.debug(
            f"Window {window.in_sample_start}: IS PF={result_is.profit_factor:.2f}, "
            f"OS PF={result_os.profit_factor:.2f}"
        )
    
    def _extract_params(self, strategy: BaseStrategy) -> dict[str, Any]:
        """Extract tunable parameters from strategy."""
        # Default implementation - override for specific strategies
        return {
            "strategy_name": strategy.__class__.__name__,
        }
    
    def _calculate_aggregates(self, result: WFVResult) -> None:
        """Calculate aggregate metrics from all windows."""
        if not result.windows:
            return
        
        valid_windows = [w for w in result.windows if w.out_sample_metrics]
        if not valid_windows:
            return
        
        # Average out-of-sample metrics
        pf_values = [w.out_sample_metrics.get("profit_factor", 0) for w in valid_windows]
        wr_values = [w.out_sample_metrics.get("win_rate", 0) for w in valid_windows]
        
        result.avg_out_sample_pf = sum(pf_values) / len(pf_values)
        result.avg_out_sample_wr = sum(wr_values) / len(wr_values)
        
        # Stability: % of windows that didn't degrade badly
        stable_windows = [w for w in valid_windows if not w.is_degraded]
        result.stability_score = len(stable_windows) / len(valid_windows)
        
        # Best params from best stable window
        if stable_windows:
            best_window = max(
                stable_windows,
                key=lambda w: w.out_sample_metrics.get("profit_factor", 0),
            )
            result.best_params = best_window.optimized_params
