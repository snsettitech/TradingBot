"""Backtest Results and Trade Records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class TradeRecord:
    """Record of a completed trade."""

    entry_time: datetime
    exit_time: datetime
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_price: Decimal
    exit_price: Decimal
    quantity: int

    # P&L
    pnl_ticks: int = 0
    pnl_dollars: Decimal = Decimal("0")

    # Strategy info
    strategy: str = ""
    entry_reason: str = ""

    # AI Analysis (optional)
    ai_grade: str = ""  # A-F
    ai_analysis: str = ""
    ai_lessons: list[str] = field(default_factory=list)

    # Market context at entry
    regime: str = ""  # trending, choppy, etc.
    vwap_distance: float = 0.0
    rsi_at_entry: float = 50.0

    @property
    def is_winner(self) -> bool:
        return self.pnl_ticks > 0

    @property
    def is_loser(self) -> bool:
        return self.pnl_ticks < 0

    @property
    def hold_time_minutes(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds() / 60


@dataclass
class BacktestResult:
    """Complete backtest results with metrics."""

    # Config
    strategy: str
    symbol: str
    start_date: datetime
    end_date: datetime

    # Trade records
    trades: list[TradeRecord] = field(default_factory=list)

    # Core metrics
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    breakeven: int = 0

    # P&L
    gross_pnl: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")

    # Ratios
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")

    # Risk
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0

    # By regime
    regime_performance: dict = field(default_factory=dict)

    # AI summary
    ai_recommendation: str = ""

    def calculate_metrics(self) -> None:
        """Calculate all metrics from trades."""
        if not self.trades:
            return

        self.total_trades = len(self.trades)
        self.winners = sum(1 for t in self.trades if t.is_winner)
        self.losers = sum(1 for t in self.trades if t.is_loser)
        self.breakeven = self.total_trades - self.winners - self.losers

        # Win rate
        if self.total_trades > 0:
            self.win_rate = self.winners / self.total_trades

        # P&L
        self.gross_pnl = sum(t.pnl_dollars for t in self.trades)
        self.net_pnl = self.gross_pnl - self.total_fees

        # Avg win/loss
        winning_trades = [t for t in self.trades if t.is_winner]
        losing_trades = [t for t in self.trades if t.is_loser]

        if winning_trades:
            self.avg_win = sum(t.pnl_dollars for t in winning_trades) / len(winning_trades)
        if losing_trades:
            self.avg_loss = abs(sum(t.pnl_dollars for t in losing_trades) / len(losing_trades))

        # Profit factor
        total_wins = sum(t.pnl_dollars for t in winning_trades) if winning_trades else Decimal("0")
        total_losses = (
            abs(sum(t.pnl_dollars for t in losing_trades)) if losing_trades else Decimal("1")
        )
        if total_losses > 0:
            self.profit_factor = float(total_wins / total_losses)

        # Max drawdown
        self._calculate_drawdown()

        # Regime performance
        self._calculate_regime_performance()

    def _calculate_drawdown(self) -> None:
        """Calculate maximum drawdown."""
        if not self.trades:
            return

        equity_curve = []
        running_pnl = Decimal("0")

        for trade in self.trades:
            running_pnl += trade.pnl_dollars
            equity_curve.append(running_pnl)

        if not equity_curve:
            return

        peak = equity_curve[0]
        max_dd = Decimal("0")

        for equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        self.max_drawdown = max_dd
        if peak > 0:
            self.max_drawdown_pct = float(max_dd / peak) * 100

    def _calculate_regime_performance(self) -> None:
        """Calculate win rate by market regime."""
        regime_trades: dict[str, list[TradeRecord]] = {}

        for trade in self.trades:
            regime = trade.regime or "unknown"
            if regime not in regime_trades:
                regime_trades[regime] = []
            regime_trades[regime].append(trade)

        for regime, trades in regime_trades.items():
            winners = sum(1 for t in trades if t.is_winner)
            total = len(trades)
            win_rate = winners / total if total > 0 else 0
            pnl = sum(t.pnl_dollars for t in trades)

            self.regime_performance[regime] = {
                "trades": total,
                "win_rate": win_rate,
                "pnl": float(pnl),
            }

    def summary(self) -> str:
        """Generate text summary of results."""
        lines = [
            f"Strategy: {self.strategy} | Symbol: {self.symbol}",
            f"Period: {self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}",
            "",
            f"Total Trades: {self.total_trades}",
            f"Win Rate: {self.win_rate:.1%}",
            f"Winners: {self.winners} | Losers: {self.losers}",
            f"Net P&L: ${self.net_pnl:.2f}",
            f"Profit Factor: {self.profit_factor:.2f}",
            f"Max Drawdown: ${self.max_drawdown:.2f} ({self.max_drawdown_pct:.1f}%)",
            "",
            "Performance by Regime:",
        ]

        for regime, stats in self.regime_performance.items():
            lines.append(
                f"  {regime}: {stats['trades']} trades, {stats['win_rate']:.0%} win rate, ${stats['pnl']:.2f}"
            )

        if self.ai_recommendation:
            lines.append("")
            lines.append(f"AI Recommendation: {self.ai_recommendation}")

        return "\n".join(lines)
