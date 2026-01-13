"""Risk governor for enforcing trading limits and safety checks."""

from __future__ import annotations

import logging
from decimal import Decimal

from tsxbot.config_loader import RiskConfig, SymbolsConfig
from tsxbot.risk.limits import RiskState

logger = logging.getLogger(__name__)


class RiskGovernor:
    """
    Authoritative risk manager.

    Checks:
    - Daily Loss Limit
    - Trailing Drawdown
    - Max Trades Per Day
    - Per-Trade Risk (USD)
    - Max Position Size (Contracts)
    - Kill Switch
    """

    def __init__(self, risk_config: RiskConfig, symbols_config: SymbolsConfig) -> None:
        """
        Initialize Risk Governor.

        Args:
            risk_config: Risk configurations (limits).
            symbols_config: Symbol specifications (tick values).
        """
        self.config = risk_config
        self.symbols = symbols_config
        self.state = RiskState()

        # Initial check for kill switch from config
        if self.config.kill_switch:
            self.trip_kill_switch("Configured kill switch is ON")

    def update_account_status(self, balance: Decimal, daily_pnl: Decimal) -> None:
        """
        Update risk state with latest account info.

        Should be called on every account update event.
        """
        self.state.update_balance(balance)
        self.state.daily_pnl = daily_pnl

        # Check circuit breakers after update
        self._check_circuit_breakers()

    def record_trade_execution(self) -> None:
        """Record that a trade has been executed."""
        self.state.trade_count += 1
        self._check_circuit_breakers()

    def trip_kill_switch(self, reason: str) -> None:
        """Manually trip the kill switch."""
        if not self.state.kill_switch_active:
            logger.critical(f"KILL SWITCH ACTIVATED: {reason}")
            self.state.kill_switch_active = True
            self.state.kill_switch_reason = reason

    def reset_kill_switch(self) -> None:
        """
        Manually reset the kill switch.

        CAUTION: Only call this when you are certain it is safe to resume trading.
        This should typically be called at the start of a new trading day after
        reviewing the reason for the previous kill switch activation.
        """
        if self.state.kill_switch_active:
            logger.warning(f"KILL SWITCH RESET. Previous reason: {self.state.kill_switch_reason}")
            self.state.kill_switch_active = False
            self.state.kill_switch_reason = ""

    def reset_daily(self, starting_balance: Decimal | None = None) -> None:
        """
        Reset daily risk counters for new trading session.

        Call this at RTH open to reset daily limits. Does NOT reset kill switch.

        Args:
            starting_balance: Optional starting balance for new day.
        """
        logger.info("Resetting daily risk counters")
        self.state.reset_daily(starting_balance)

        # Re-check if config kill_switch was set
        if self.config.kill_switch and not self.state.kill_switch_active:
            self.trip_kill_switch("Configured kill switch is ON")

    def _check_circuit_breakers(self) -> None:
        """Check generic circuit breakers (loss limits, trade counts)."""
        if self.state.kill_switch_active:
            return

        # 1. Daily Loss Limit
        # daily_pnl is usually signed. If negative and exceeds limit absolute value.
        if self.state.daily_pnl < -self.config.daily_loss_limit_usd:
            self.trip_kill_switch(
                f"Daily loss limit hit: {self.state.daily_pnl} < -{self.config.daily_loss_limit_usd}"
            )
            return

        # 2. Trailing Drawdown
        if self.state.current_drawdown > self.config.max_loss_limit_usd:
            self.trip_kill_switch(
                f"Max drawdown limit hit: {self.state.current_drawdown} > {self.config.max_loss_limit_usd}"
            )
            return

        # 3. Max Trades Per Day
        if self.state.trade_count >= self.config.max_trades_per_day:
            # This is a "soft" stop - we just don't allow new trades,
            # but we don't necessarily need to "trip" the global kill switch
            # if we want to allow management of existing.
            # However, for MVP, let's treat it as a block on new entries.
            # We won't trip kill_switch_active (which might force flatten),
            # but can_trade() will return False.
            pass

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is generally allowed (kill switch, daily limits)."""
        if self.state.kill_switch_active:
            return False, f"Kill switch active: {self.state.kill_switch_reason}"

        if self.state.daily_pnl < -self.config.daily_loss_limit_usd:
            return False, "Daily loss limit exceeded"

        if self.state.current_drawdown > self.config.max_loss_limit_usd:
            return False, "Max drawdown exceeded"

        if self.state.trade_count >= self.config.max_trades_per_day:
            return False, "Max trades per day reached"

        return True, "OK"

    def check_trade_risk(
        self,
        symbol: str,
        qty: int,
        entry_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> tuple[bool, str]:
        """
        Check risk for a specific proposed trade.

        Args:
            symbol: Symbol string (e.g. "ES", "MES").
            qty: Quantity (contracts).
            entry_price: Planned entry price (optional, needed for risk $ calc).
            stop_price: Planned stop price (optional, needed for risk $ calc).

        Returns:
            (Allowed, Reason)
        """
        # 1. General Status
        allowed, reason = self.can_trade()
        if not allowed:
            return False, reason

        # 2. Max Contracts Check
        # Normalize symbol to check against config
        sym_upper = symbol.upper()

        # Heuristic matching for MVP
        limit = 0
        tick_value = Decimal("0.0")
        tick_size = Decimal("1.0")

        if "MES" in sym_upper or sym_upper == self.symbols.micros:
            limit = self.config.max_contracts_mes
            tick_value = self.symbols.mes.tick_value
            tick_size = self.symbols.mes.tick_size
        elif "ES" in sym_upper or sym_upper == self.symbols.primary:
            limit = self.config.max_contracts_es
            tick_value = self.symbols.es.tick_value
            tick_size = self.symbols.es.tick_size
        else:
            # Unknown symbol? Warn but maybe allow with default safe limit?
            # Or strict block. Strict block for safety.
            return False, f"Unknown symbol for risk checks: {symbol}"

        if qty > limit:
            return False, f"Size {qty} exceeds max {limit} for {symbol}"

        # 3. Per Trade Risk (USD)
        if entry_price is not None and stop_price is not None:
            # Calculate risk USD
            price_diff = abs(entry_price - stop_price)
            ticks = price_diff / tick_size
            risk_usd = ticks * tick_value * qty

            if risk_usd > self.config.max_risk_per_trade_usd:
                return (
                    False,
                    f"Risk ${risk_usd:.2f} exceeds limit ${self.config.max_risk_per_trade_usd}",
                )

        elif entry_price is None and stop_price is None:
            # If prices not provided, we can't check USD risk, assuming caller handles or just pre-check
            pass
        else:
            # If only one provided, can't calc
            pass

        return True, "OK"
