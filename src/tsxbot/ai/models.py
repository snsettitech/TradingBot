"""Data models for AI trade intelligence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class MarketContext:
    """
    Snapshot of market state at signal time.

    Pre-computed metrics designed for LLM interpretation
    (GPT-4o-mini struggles with raw number math).
    """

    # Basic identifiers
    symbol: str
    timestamp: datetime

    # Price levels
    current_price: Decimal
    session_high: Decimal
    session_low: Decimal
    opening_range_high: Decimal | None = None
    opening_range_low: Decimal | None = None
    vwap: Decimal | None = None
    ema20: Decimal | None = None

    # Time context
    minutes_since_open: int = 0
    session_phase: str = "unknown"  # "opening", "morning", "midday", "afternoon", "close"

    # Risk state
    daily_pnl: Decimal = Decimal("0")
    trade_count_today: int = 0

    # Pre-computed interpreted metrics (user requirement - don't make LLM do math)
    volatility_description: str = ""  # "Low (5 tick range)", "High (25 tick range)"
    rvol: float | None = None  # Relative volume multiplier (e.g., 2.5x)
    rvol_description: str = ""  # "RVOL: 2.5x average"

    # Distance to key levels (in ticks - pre-computed)
    dist_to_hod_ticks: int | None = None  # Distance to High of Day
    dist_to_lod_ticks: int | None = None  # Distance to Low of Day
    dist_to_vwap_ticks: int | None = None
    dist_to_or_high_ticks: int | None = None  # Distance to Opening Range High
    dist_to_or_low_ticks: int | None = None

    # Trend interpretation
    trend_description: str = ""  # "Strongly Bullish (Above EMA20)", "Choppy"

    def to_prompt_context(self) -> str:
        """Format context for LLM prompt."""
        lines = [
            f"Symbol: {self.symbol}",
            f"Time: {self.minutes_since_open} mins since RTH open ({self.session_phase})",
            f"Price: {self.current_price}",
            f"Session Range: {self.session_low} - {self.session_high}",
        ]

        if self.opening_range_high and self.opening_range_low:
            lines.append(f"Opening Range: {self.opening_range_low} - {self.opening_range_high}")

        if self.volatility_description:
            lines.append(f"Volatility: {self.volatility_description}")

        if self.rvol_description:
            lines.append(f"Volume: {self.rvol_description}")

        if self.trend_description:
            lines.append(f"Trend: {self.trend_description}")

        # Key level distances
        level_info = []
        if self.dist_to_hod_ticks is not None:
            level_info.append(f"HOD: {self.dist_to_hod_ticks} ticks away")
        if self.dist_to_lod_ticks is not None:
            level_info.append(f"LOD: {self.dist_to_lod_ticks} ticks away")
        if self.dist_to_vwap_ticks is not None:
            level_info.append(f"VWAP: {self.dist_to_vwap_ticks} ticks")
        if level_info:
            lines.append(f"Key Levels: {', '.join(level_info)}")

        # Risk state
        lines.append(f"Today: {self.trade_count_today} trades, P&L: ${self.daily_pnl}")

        return "\n".join(lines)


@dataclass
class TradeValidation:
    """AI validation result (pre-trade commentary)."""

    confidence: int  # 1-10
    observations: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    raw_response: str = ""  # For debugging
    latency_ms: int = 0

    def format_console_output(self) -> str:
        """Format for cyan console output during dry-run."""
        parts = [f"Confidence: {self.confidence}/10"]

        if self.observations:
            parts.append(f'"{self.observations[0]}"')

        if self.risks:
            parts.append(f"⚠️ {self.risks[0]}")

        return " | ".join(parts)


@dataclass
class TradeResult:
    """Completed trade data for post-trade analysis."""

    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    pnl_ticks: int
    pnl_usd: Decimal
    duration_seconds: int
    exit_reason: str  # "target", "stop", "flatten"

    # Context at entry
    entry_context: MarketContext | None = None
    signal_reason: str = ""
    ai_confidence_at_entry: int | None = None


@dataclass
class TradeAnalysis:
    """AI analysis result (post-trade learning)."""

    grade: str  # A, B, C, D, F
    what_worked: list[str] = field(default_factory=list)
    what_didnt: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    raw_response: str = ""
    latency_ms: int = 0
