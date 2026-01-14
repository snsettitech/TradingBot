"""State persistence for risk and trading state."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PersistedState:
    """State that survives bot restarts."""

    trading_date: str  # YYYY-MM-DD format
    daily_pnl: Decimal = Decimal("0.0")
    trade_count: int = 0
    peak_balance: Decimal = Decimal("0.0")
    current_balance: Decimal = Decimal("0.0")
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def new_day(cls, balance: Decimal) -> PersistedState:
        """Create fresh state for a new trading day."""
        return cls(
            trading_date=date.today().isoformat(),
            peak_balance=balance,
            current_balance=balance,
        )


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


class StateStore:
    """
    Persist and load trading state to/from disk.
    
    Uses JSON file for simplicity. State includes:
    - Daily P&L tracking
    - Trade count
    - Kill switch status
    - Peak/current balance for drawdown tracking
    """

    def __init__(self, data_dir: str | Path = "./data"):
        self.data_dir = Path(data_dir)
        self.state_file = self.data_dir / "trading_state.json"
        self._state: PersistedState | None = None

    def load(self, current_balance: Decimal) -> PersistedState:
        """
        Load state from disk.
        
        If state file doesn't exist or is from a different day,
        creates fresh state for today.
        """
        self.data_dir.mkdir(parents=True, exist_ok=True)

        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)

                # Check if same trading day
                saved_date = data.get("trading_date", "")
                today = date.today().isoformat()

                if saved_date == today:
                    # Same day - restore state
                    self._state = PersistedState(
                        trading_date=data["trading_date"],
                        daily_pnl=Decimal(data.get("daily_pnl", "0")),
                        trade_count=int(data.get("trade_count", 0)),
                        peak_balance=Decimal(data.get("peak_balance", str(current_balance))),
                        current_balance=Decimal(data.get("current_balance", str(current_balance))),
                        kill_switch_active=bool(data.get("kill_switch_active", False)),
                        kill_switch_reason=str(data.get("kill_switch_reason", "")),
                        last_updated=data.get("last_updated", datetime.now().isoformat()),
                    )
                    logger.info(
                        f"Restored state from {saved_date}: "
                        f"PnL=${self._state.daily_pnl}, Trades={self._state.trade_count}"
                    )
                    return self._state
                else:
                    # Different day - start fresh
                    logger.info(f"New trading day ({today}). Starting fresh state.")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load state file: {e}. Starting fresh.")

        # Create new state for today
        self._state = PersistedState.new_day(current_balance)
        self.save()
        return self._state

    def save(self) -> None:
        """Save current state to disk."""
        if self._state is None:
            return

        self._state.last_updated = datetime.now().isoformat()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        with open(self.state_file, "w") as f:
            json.dump(asdict(self._state), f, cls=DecimalEncoder, indent=2)

        logger.debug(f"State saved: PnL=${self._state.daily_pnl}, Trades={self._state.trade_count}")

    @property
    def state(self) -> PersistedState | None:
        """Get current state."""
        return self._state

    def update_pnl(self, pnl_change: Decimal, new_balance: Decimal) -> None:
        """Update P&L and balance after a trade."""
        if self._state is None:
            return

        self._state.daily_pnl += pnl_change
        self._state.current_balance = new_balance

        # Update peak for drawdown tracking
        if new_balance > self._state.peak_balance:
            self._state.peak_balance = new_balance

        self.save()

    def increment_trade_count(self) -> None:
        """Increment trade count."""
        if self._state is None:
            return
        self._state.trade_count += 1
        self.save()

    def set_kill_switch(self, active: bool, reason: str = "") -> None:
        """Set kill switch status."""
        if self._state is None:
            return
        self._state.kill_switch_active = active
        self._state.kill_switch_reason = reason
        self.save()

    def get_current_drawdown(self) -> Decimal:
        """Calculate current drawdown from peak."""
        if self._state is None:
            return Decimal("0.0")
        return self._state.peak_balance - self._state.current_balance
