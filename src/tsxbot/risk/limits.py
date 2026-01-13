"""Risk limit data structures and state tracking."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class RiskState:
    """Mutable state for risk tracking within a single session."""

    # Cumulative realized PnL for the day (USD)
    daily_pnl: Decimal = Decimal("0.0")

    # Number of executed trades today
    trade_count: int = 0

    # Peak account balance seen today (for trailing drawdown)
    # Start with initial balance, update on account updates
    high_water_mark: Decimal = Decimal("-Infinity")

    # Current account balance
    current_balance: Decimal = Decimal("0.0")

    # Kill switch activation status
    kill_switch_active: bool = False
    kill_switch_reason: str = ""

    def update_balance(self, balance: Decimal) -> None:
        """Update balance and high water mark."""
        self.current_balance = balance
        if self.high_water_mark == Decimal("-Infinity"):
            self.high_water_mark = balance
        else:
            self.high_water_mark = max(self.high_water_mark, balance)

    @property
    def current_drawdown(self) -> Decimal:
        """Calculate current drawdown from high water mark (positive value)."""
        if self.high_water_mark == Decimal("-Infinity"):
            return Decimal("0.0")
        return max(Decimal("0.0"), self.high_water_mark - self.current_balance)

    def reset_daily(self, starting_balance: Decimal | None = None) -> None:
        """
        Reset daily counters for new trading session.

        Call this at RTH open to reset daily limits.
        NOTE: Kill switch is NOT reset automatically for safety -
        requires explicit reset via risk_governor.

        Args:
            starting_balance: Optional starting balance for new day.
                If provided, resets HWM to this value.
        """
        self.daily_pnl = Decimal("0.0")
        self.trade_count = 0

        if starting_balance is not None:
            self.current_balance = starting_balance
            self.high_water_mark = starting_balance
        else:
            # Reset HWM to current balance for new day
            self.high_water_mark = self.current_balance
