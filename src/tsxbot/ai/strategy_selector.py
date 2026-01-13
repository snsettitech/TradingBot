"""AI Strategy Selector.

Uses GPT to analyze market conditions and select the optimal strategy.
Includes:
- Pre-session analysis (gap, overnight action)
- Intraday regime detection (trending vs choppy)
- Dynamic strategy switching
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.ai.advisor import AIAdvisor
    from tsxbot.config_loader import AppConfig

logger = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    """Market regime classification."""

    TRENDING_BULL = "trending_bullish"
    TRENDING_BEAR = "trending_bearish"
    CHOPPY = "choppy"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"


@dataclass
class RegimeContext:
    """Current market regime context."""

    regime: MarketRegime
    confidence: float  # 0-1

    # Metrics
    session_range_pts: float = 0.0
    vwap_distance_pts: float = 0.0
    rsi: float = 50.0
    trend_direction: str = "neutral"
    minutes_since_open: int = 0
    gap_direction: str = "none"  # "up", "down", "none"
    gap_size_pts: float = 0.0

    # Derived
    volatility_level: str = "normal"  # "low", "normal", "high"

    def to_prompt_context(self) -> str:
        """Format as context string for AI prompt."""
        return (
            f"Market Regime: {self.regime.value} (confidence: {self.confidence:.0%})\n"
            f"Session: {self.minutes_since_open} mins into RTH\n"
            f"Gap: {self.gap_direction} {self.gap_size_pts:.2f} pts\n"
            f"Range: {self.session_range_pts:.2f} pts | VWAP Dist: {self.vwap_distance_pts:+.2f} pts\n"
            f"RSI: {self.rsi:.1f} | Trend: {self.trend_direction}\n"
            f"Volatility: {self.volatility_level}"
        )


@dataclass
class StrategySelection:
    """AI strategy selection result."""

    primary_strategy: str
    reason: str
    confidence: float  # 0-1

    fallback_strategy: str | None = None
    position_sizing: float = 1.0  # 0.5 = half size, 1.0 = full
    avoid_strategies: list[str] = field(default_factory=list)

    timestamp: datetime = field(default_factory=datetime.now)


# System prompt for strategy selection
STRATEGY_SELECTOR_SYSTEM_PROMPT = """You are an expert ES futures trading strategy selector.

Your job is to analyze market conditions and select the BEST strategy for current conditions.

Available Strategies:
1. ORB (Opening Range Breakout) - Best for trending days, first 30 mins, high volume
2. VWAP_BOUNCE - Best for established trends with pullbacks to VWAP
3. MEAN_REVERSION - Best for choppy/range-bound markets, RSI extremes

Selection Guidelines:
- TRENDING + HIGH VOLUME → ORB or VWAP_BOUNCE
- CHOPPY + LOW VOLUME → MEAN_REVERSION or SKIP
- HIGH VOLATILITY → Reduce size, use wider stops
- FIRST 30 MINS → Prefer ORB
- AFTER 10:30 AM → Consider VWAP_BOUNCE or MEAN_REVERSION

Respond ONLY with valid JSON:
{
    "primary_strategy": "ORB" | "VWAP_BOUNCE" | "MEAN_REVERSION",
    "reason": "Brief explanation (1 sentence)",
    "confidence": 0.0-1.0,
    "fallback_strategy": "strategy_name" | null,
    "position_sizing": 0.5-1.0,
    "avoid_strategies": ["list", "of", "strategies"]
}"""


class AIStrategySelector:
    """
    AI-driven strategy selection engine.

    Analyzes market conditions and recommends the optimal strategy.
    Uses GPT-4o-mini for cost-effective intelligence.
    """

    def __init__(self, config: AppConfig, ai_advisor: AIAdvisor | None = None):
        self.config = config
        self.ai_advisor = ai_advisor

        # State tracking
        self.current_regime = RegimeContext(regime=MarketRegime.UNKNOWN, confidence=0.0)
        self.current_selection: StrategySelection | None = None
        self.last_selection_time: datetime | None = None

        # Session data
        self.session_open_price: Decimal | None = None
        self.prior_close: Decimal | None = None

        # Regime check interval
        self.regime_check_interval = timedelta(minutes=30)

        # History for learning
        self.selection_history: list[StrategySelection] = []

    @property
    def is_available(self) -> bool:
        """Check if AI selector is available."""
        return self.ai_advisor is not None and self.ai_advisor.is_available

    def update_market_data(
        self,
        current_price: Decimal,
        session_high: Decimal,
        session_low: Decimal,
        vwap: Decimal,
        rsi: float,
        trend: str,
        minutes_since_open: int,
    ) -> None:
        """Update regime context with latest market data."""
        # Calculate metrics
        session_range = float(session_high - session_low)
        vwap_distance = float(current_price - vwap) if vwap > 0 else 0

        # Determine gap
        gap_direction = "none"
        gap_size = 0.0
        if self.session_open_price and self.prior_close:
            gap_size = float(self.session_open_price - self.prior_close)
            if gap_size > 2:
                gap_direction = "up"
            elif gap_size < -2:
                gap_direction = "down"

        # Classify volatility
        volatility = "normal"
        if session_range > 20:
            volatility = "high"
        elif session_range < 8:
            volatility = "low"

        # Classify regime
        regime = MarketRegime.UNKNOWN
        confidence = 0.5

        if abs(vwap_distance) > 5 and trend in ["BULLISH", "BEARISH"]:
            regime = (
                MarketRegime.TRENDING_BULL if trend == "BULLISH" else MarketRegime.TRENDING_BEAR
            )
            confidence = 0.8
        elif session_range < 10 and abs(vwap_distance) < 3:
            regime = MarketRegime.CHOPPY
            confidence = 0.7
        elif session_range > 25:
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = 0.75
        elif session_range < 6:
            regime = MarketRegime.LOW_VOLATILITY
            confidence = 0.7

        self.current_regime = RegimeContext(
            regime=regime,
            confidence=confidence,
            session_range_pts=session_range,
            vwap_distance_pts=vwap_distance,
            rsi=rsi,
            trend_direction=trend.lower(),
            minutes_since_open=minutes_since_open,
            gap_direction=gap_direction,
            gap_size_pts=abs(gap_size),
            volatility_level=volatility,
        )

    async def select_strategy(self, force: bool = False) -> StrategySelection | None:
        """
        Select optimal strategy based on current conditions.

        Args:
            force: Force re-selection even if interval hasn't passed

        Returns:
            StrategySelection or None if AI unavailable
        """
        # Check if selection is needed
        if not force and self.last_selection_time:
            elapsed = datetime.now() - self.last_selection_time
            if elapsed < self.regime_check_interval:
                return self.current_selection

        if not self.is_available:
            # Fallback: rule-based selection
            return self._rule_based_selection()

        try:
            selection = await self._ai_select_strategy()
            if selection:
                self.current_selection = selection
                self.last_selection_time = datetime.now()
                self.selection_history.append(selection)

                logger.info(
                    f"[AI SELECTOR] Selected: {selection.primary_strategy} "
                    f"(confidence: {selection.confidence:.0%}) - {selection.reason}"
                )

            return selection

        except Exception as e:
            logger.error(f"AI strategy selection failed: {e}")
            return self._rule_based_selection()

    async def _ai_select_strategy(self) -> StrategySelection | None:
        """Use AI to select strategy."""
        if not self.ai_advisor:
            return None

        # Build prompt
        context = self.current_regime.to_prompt_context()
        user_prompt = f"""Current Market Context:
{context}

Current Time: {datetime.now().strftime("%H:%M")} ET

Select the best strategy for these conditions."""

        try:
            # Call OpenAI
            from openai import AsyncOpenAI

            client = AsyncOpenAI()

            response = await client.chat.completions.create(
                model=self.config.openai.model,
                messages=[
                    {"role": "system", "content": STRATEGY_SELECTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=300,
            )

            result_text = response.choices[0].message.content.strip()

            # Parse JSON
            # Handle markdown code blocks
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            data = json.loads(result_text)

            return StrategySelection(
                primary_strategy=data.get("primary_strategy", "ORB").upper(),
                reason=data.get("reason", "AI selection"),
                confidence=float(data.get("confidence", 0.5)),
                fallback_strategy=data.get("fallback_strategy"),
                position_sizing=float(data.get("position_sizing", 1.0)),
                avoid_strategies=data.get("avoid_strategies", []),
            )

        except Exception as e:
            logger.warning(f"AI selection parse error: {e}")
            return None

    def _rule_based_selection(self) -> StrategySelection:
        """Fallback rule-based strategy selection."""
        regime = self.current_regime

        # Simple rules
        if regime.minutes_since_open < 35:
            # Opening period - ORB
            return StrategySelection(
                primary_strategy="ORB",
                reason="Within opening range period",
                confidence=0.7,
                fallback_strategy="VWAP_BOUNCE",
            )

        if regime.regime in [MarketRegime.TRENDING_BULL, MarketRegime.TRENDING_BEAR]:
            return StrategySelection(
                primary_strategy="VWAP_BOUNCE",
                reason=f"Trending market ({regime.trend_direction})",
                confidence=0.6,
                fallback_strategy="ORB",
            )

        if regime.regime == MarketRegime.CHOPPY:
            return StrategySelection(
                primary_strategy="MEAN_REVERSION",
                reason="Choppy/range-bound conditions",
                confidence=0.6,
                position_sizing=0.75,  # Reduce size in chop
            )

        if regime.regime == MarketRegime.HIGH_VOLATILITY:
            return StrategySelection(
                primary_strategy="ORB",
                reason="High volatility - use breakout strategy",
                confidence=0.5,
                position_sizing=0.5,  # Half size
            )

        # Default
        return StrategySelection(primary_strategy="ORB", reason="Default selection", confidence=0.4)

    def get_recommendation_summary(self) -> str:
        """Get current recommendation as formatted string."""
        if not self.current_selection:
            return "No selection yet"

        sel = self.current_selection
        summary = (
            f"Strategy: {sel.primary_strategy} ({sel.confidence:.0%} confidence)\n"
            f"Reason: {sel.reason}\n"
        )
        if sel.fallback_strategy:
            summary += f"Fallback: {sel.fallback_strategy}\n"
        if sel.position_sizing != 1.0:
            summary += f"Size: {sel.position_sizing:.0%}\n"

        return summary
