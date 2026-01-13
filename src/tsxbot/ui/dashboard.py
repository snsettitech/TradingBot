"""Strategy Dashboard.

Real-time console dashboard showing strategy state, indicators, and signal readiness.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.ai.advisor import AIAdvisor
    from tsxbot.config_loader import AppConfig
    from tsxbot.time.session_manager import SessionManager

logger = logging.getLogger(__name__)


# ANSI color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    DIM = "\033[2m"


class StrategyDashboard:
    """
    Real-time console dashboard for strategy monitoring.

    Shows:
    - Session levels (high, low, range)
    - Indicators (VWAP, RSI, trend)
    - Signal readiness percentages
    - AI insights when signals fire
    """

    def __init__(
        self,
        config: AppConfig,
        session_manager: SessionManager,
        ai_advisor: AIAdvisor | None = None,
    ):
        self.config = config
        self.session = session_manager
        self.ai_advisor = ai_advisor

        # State tracking
        self.current_price = Decimal("0")
        self.session_high = Decimal("-Infinity")
        self.session_low = Decimal("Infinity")
        self.session_open: Decimal | None = None

        # Indicators
        self.vwap = Decimal("0")
        self.cumulative_volume = Decimal("0")
        self.cumulative_tp_vol = Decimal("0")

        self.rsi = 50.0
        self.price_changes: deque[Decimal] = deque(maxlen=14)
        self.last_price: Decimal | None = None

        # Trend tracking
        self.prices_above_vwap = 0
        self.prices_below_vwap = 0
        self.trend = "NEUTRAL"

        # Signal history
        self.last_signal: dict | None = None
        self.signal_history: deque[dict] = deque(maxlen=10)

        # AI last insight
        self.last_ai_insight: str = ""

        # Display timing
        self.last_display_time: datetime | None = None
        self.refresh_interval = timedelta(seconds=5)
        self.tick_count = 0

        # Active strategy tracking
        self.active_strategy = config.strategy.active.value
        self.strategy_state = "INITIALIZING"
        self.trades_taken = 0
        self.max_trades = 2

    def update_tick(self, symbol: str, price: Decimal, volume: int, timestamp: datetime) -> None:
        """Update dashboard state with new tick data."""
        self.current_price = price
        self.tick_count += 1

        # Session levels
        if self.session_open is None:
            self.session_open = price
        if price > self.session_high or self.session_high == Decimal("-Infinity"):
            self.session_high = price
        if price < self.session_low or self.session_low == Decimal("Infinity"):
            self.session_low = price

        # VWAP calculation
        if volume > 0:
            vol = Decimal(str(volume))
            self.cumulative_volume += vol
            self.cumulative_tp_vol += price * vol
            if self.cumulative_volume > 0:
                self.vwap = self.cumulative_tp_vol / self.cumulative_volume

        # RSI calculation
        if self.last_price is not None:
            change = price - self.last_price
            self.price_changes.append(change)
            if len(self.price_changes) >= 14:
                gains = [float(c) for c in self.price_changes if c > 0]
                losses = [abs(float(c)) for c in self.price_changes if c < 0]
                avg_gain = sum(gains) / 14 if gains else 0.001
                avg_loss = sum(losses) / 14 if losses else 0.001
                if avg_loss > 0:
                    rs = avg_gain / avg_loss
                    self.rsi = 100 - (100 / (1 + rs))
                else:
                    self.rsi = 100.0
        self.last_price = price

        # Trend tracking
        if self.vwap > 0:
            if price > self.vwap:
                self.prices_above_vwap += 1
                self.prices_below_vwap = max(0, self.prices_below_vwap - 1)
            else:
                self.prices_below_vwap += 1
                self.prices_above_vwap = max(0, self.prices_above_vwap - 1)

            if self.prices_above_vwap > 50:
                self.trend = "BULLISH"
            elif self.prices_below_vwap > 50:
                self.trend = "BEARISH"
            else:
                self.trend = "NEUTRAL"

        # Check if should display
        now = datetime.now()
        if self.last_display_time is None or now - self.last_display_time >= self.refresh_interval:
            self.render()
            self.last_display_time = now

    def update_strategy_state(
        self, strategy_name: str, state: str, trades: int, max_trades: int
    ) -> None:
        """Update strategy status display."""
        self.active_strategy = strategy_name
        self.strategy_state = state
        self.trades_taken = trades
        self.max_trades = max_trades

    def record_signal(self, direction: str, reason: str, ai_rationale: str = "") -> None:
        """Record a signal for display."""
        signal = {
            "timestamp": datetime.now(),
            "direction": direction,
            "reason": reason,
            "ai_rationale": ai_rationale,
        }
        self.last_signal = signal
        self.signal_history.append(signal)
        if ai_rationale:
            self.last_ai_insight = ai_rationale

        # Force immediate display on signal
        self.render_signal_alert(signal)

    def get_orb_readiness(self) -> tuple[float, str]:
        """Calculate ORB signal readiness."""
        # Simplified: based on whether range is formed and distance to breakout
        session_range = float(self.session_high - self.session_low)
        if session_range < 2:
            return 20.0, "Building range..."

        cfg = self.config.strategy.orb
        mins_since_open = 0
        if self.session:
            now = datetime.now()
            # Calculate RTH start today
            rth_start = now.replace(
                hour=self.session.rth_start_time.hour,
                minute=self.session.rth_start_time.minute,
                second=0,
                microsecond=0,
            )
            if now > rth_start:
                mins_since_open = (now - rth_start).total_seconds() / 60

        if mins_since_open < cfg.opening_range_minutes:
            pct = (mins_since_open / cfg.opening_range_minutes) * 60
            return min(
                60, pct
            ), f"Range forming {int(mins_since_open)}/{cfg.opening_range_minutes}m"

        # Range formed, check distance to breakout
        buffer = float(cfg.breakout_buffer_ticks * Decimal("0.25"))
        dist_to_high = float(self.session_high) + buffer - float(self.current_price)
        dist_to_low = float(self.current_price) - (float(self.session_low) - buffer)

        min_dist = min(abs(dist_to_high), abs(dist_to_low))
        if min_dist < 2:
            return 95.0, "Near breakout level!"
        elif min_dist < 5:
            return 80.0, f"Watching ({min_dist:.1f} pts to break)"
        else:
            return 60.0, "Range watching"

    def get_vwap_readiness(self) -> tuple[float, str]:
        """Calculate VWAP bounce signal readiness."""
        if self.vwap <= 0:
            return 10.0, "Calculating VWAP..."

        dist_to_vwap = abs(float(self.current_price - self.vwap))

        # Need established trend
        if self.trend == "NEUTRAL":
            return 20.0, "No trend established"

        # Check distance to VWAP
        if dist_to_vwap < 1.0:
            return 90.0, f"At VWAP! {self.trend} trend"
        elif dist_to_vwap < 3.0:
            return 70.0, f"Near VWAP ({dist_to_vwap:.1f} pts)"
        else:
            return 30.0, f"Dist: {dist_to_vwap:.1f} pts"

    def get_mean_rev_readiness(self) -> tuple[float, str]:
        """Calculate Mean Reversion signal readiness."""
        if len(self.price_changes) < 14:
            return 10.0, "Calculating RSI..."

        cfg = self.config.strategy.mean_reversion

        # Check RSI extremes
        if self.rsi < cfg.rsi_oversold:
            return 85.0, f"RSI oversold ({self.rsi:.1f})"
        elif self.rsi > cfg.rsi_overbought:
            return 85.0, f"RSI overbought ({self.rsi:.1f})"
        elif self.rsi < 40 or self.rsi > 60:
            return 50.0, f"RSI trending ({self.rsi:.1f})"
        else:
            return 20.0, f"RSI neutral ({self.rsi:.1f})"

    def _progress_bar(self, percent: float, width: int = 10) -> str:
        """Generate ASCII progress bar."""
        filled = int(percent / 100 * width)
        empty = width - filled
        bar = "▓" * filled + "░" * empty

        # Color based on percentage
        if percent >= 80:
            return f"{Colors.GREEN}{bar}{Colors.RESET}"
        elif percent >= 50:
            return f"{Colors.YELLOW}{bar}{Colors.RESET}"
        else:
            return f"{Colors.DIM}{bar}{Colors.RESET}"

    def _trend_color(self, trend: str) -> str:
        """Get color for trend display."""
        if trend == "BULLISH":
            return f"{Colors.GREEN}{trend}{Colors.RESET}"
        elif trend == "BEARISH":
            return f"{Colors.RED}{trend}{Colors.RESET}"
        return f"{Colors.DIM}{trend}{Colors.RESET}"

    def render(self) -> None:
        """Render full dashboard to console."""
        # Calculate RTH time remaining using time_until_flatten
        rth_remaining = "N/A"
        if self.session:
            try:
                remaining = self.session.time_until_flatten()
                if remaining.total_seconds() > 0:
                    hours = int(remaining.total_seconds() // 3600)
                    mins = int((remaining.total_seconds() % 3600) // 60)
                    rth_remaining = f"{hours}h {mins}m left"
                elif self.session.is_rth():
                    rth_remaining = "Near close"
                else:
                    rth_remaining = "CLOSED"
            except Exception:
                rth_remaining = "---"

        # Calculate values
        session_range = (
            float(self.session_high - self.session_low)
            if self.session_high > self.session_low
            else 0
        )
        vwap_dist = float(self.current_price - self.vwap) if self.vwap > 0 else 0

        # Get readiness
        orb_pct, orb_msg = self.get_orb_readiness()
        vwap_pct, vwap_msg = self.get_vwap_readiness()
        mr_pct, mr_msg = self.get_mean_rev_readiness()

        now = datetime.now()

        # Build dashboard string
        lines = []
        lines.append("")
        lines.append(f"{Colors.CYAN}╔{'═' * 78}╗{Colors.RESET}")
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  {Colors.BOLD}TSXBOT DASHBOARD{Colors.RESET}  │  "
            f"{Colors.WHITE}@ {self.current_price}{Colors.RESET}  │  "
            f"{now.strftime('%H:%M:%S')} ET  │  "
            f"RTH: {rth_remaining}  "
            f"{' ' * (29 - len(rth_remaining))}{Colors.CYAN}║{Colors.RESET}"
        )
        lines.append(f"{Colors.CYAN}╠{'═' * 78}╣{Colors.RESET}")

        # Session / Indicators / Strategy row
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  SESSION           │  INDICATORS        │  STRATEGY STATUS                  {Colors.CYAN}║{Colors.RESET}"
        )
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  ─────────────────────────────────────────────────────────────────────────  {Colors.CYAN}║{Colors.RESET}"
        )

        high_str = (
            f"{self.session_high:.2f}" if self.session_high != Decimal("-Infinity") else "---"
        )
        low_str = f"{self.session_low:.2f}" if self.session_low != Decimal("Infinity") else "---"

        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  High:   {high_str:<9} │  VWAP:   {self.vwap:>8.2f}   │  Active: {self.active_strategy:<24} {Colors.CYAN}║{Colors.RESET}"
        )
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  Low:    {low_str:<9} │  Dist:  {vwap_dist:>+8.2f}   │  State:  {self.strategy_state:<24} {Colors.CYAN}║{Colors.RESET}"
        )
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  Range:  {session_range:<8.2f}  │  RSI:    {self.rsi:>8.1f}   │  Trades: {self.trades_taken}/{self.max_trades:<22} {Colors.CYAN}║{Colors.RESET}"
        )
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  Open:   {str(self.session_open or '---'):<9} │  Trend:  {self._trend_color(self.trend):<18} │                                   {Colors.CYAN}║{Colors.RESET}"
        )

        lines.append(f"{Colors.CYAN}╠{'═' * 78}╣{Colors.RESET}")

        # Signal readiness
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  {Colors.BOLD}SIGNAL READINESS{Colors.RESET}                                                            {Colors.CYAN}║{Colors.RESET}"
        )
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  ─────────────────────────────────────────────────────────────────────────  {Colors.CYAN}║{Colors.RESET}"
        )

        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  ORB Breakout      │  {self._progress_bar(orb_pct)} {orb_pct:>3.0f}%  │  {orb_msg:<30} {Colors.CYAN}║{Colors.RESET}"
        )
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  VWAP Bounce       │  {self._progress_bar(vwap_pct)} {vwap_pct:>3.0f}%  │  {vwap_msg:<30} {Colors.CYAN}║{Colors.RESET}"
        )
        lines.append(
            f"{Colors.CYAN}║{Colors.RESET}  Mean Reversion    │  {self._progress_bar(mr_pct)} {mr_pct:>3.0f}%  │  {mr_msg:<30} {Colors.CYAN}║{Colors.RESET}"
        )

        # AI insight
        if self.last_ai_insight:
            lines.append(f"{Colors.CYAN}╠{'═' * 78}╣{Colors.RESET}")
            insight_trunc = (
                self.last_ai_insight[:68] + "..."
                if len(self.last_ai_insight) > 70
                else self.last_ai_insight
            )
            lines.append(
                f'{Colors.CYAN}║{Colors.RESET}  {Colors.MAGENTA}AI 🧠{Colors.RESET}  "{insight_trunc}"  {" " * max(0, 68 - len(insight_trunc))}{Colors.CYAN}║{Colors.RESET}'
            )

        lines.append(f"{Colors.CYAN}╚{'═' * 78}╝{Colors.RESET}")
        lines.append("")

        # Print dashboard
        output = "\n".join(lines)
        print(output)

    def render_signal_alert(self, signal: dict) -> None:
        """Render a signal alert box."""
        direction = signal["direction"]
        color = Colors.GREEN if direction == "LONG" else Colors.RED

        print("")
        print(f"{color}{'*' * 80}{Colors.RESET}")
        print(
            f"{color}*  🚨 SIGNAL: {direction} @ {datetime.now().strftime('%H:%M:%S')}  {' ' * 47}*{Colors.RESET}"
        )
        print(f"{color}*  {' ' * 76}*{Colors.RESET}")

        reason = signal.get("reason", "")[:70]
        print(f"{color}*  Reason: {reason:<67}*{Colors.RESET}")

        if signal.get("ai_rationale"):
            ai = signal["ai_rationale"][:67]
            print(f"{color}*  AI: {ai:<71}*{Colors.RESET}")

        print(f"{color}{'*' * 80}{Colors.RESET}")
        print("")

    def reset_session(self) -> None:
        """Reset for new trading session."""
        self.session_high = Decimal("-Infinity")
        self.session_low = Decimal("Infinity")
        self.session_open = None
        self.vwap = Decimal("0")
        self.cumulative_volume = Decimal("0")
        self.cumulative_tp_vol = Decimal("0")
        self.rsi = 50.0
        self.price_changes.clear()
        self.last_price = None
        self.prices_above_vwap = 0
        self.prices_below_vwap = 0
        self.trend = "NEUTRAL"
        self.trades_taken = 0
        self.last_signal = None
        self.last_ai_insight = ""
