"""AI Recommender.

Uses GPT-4o-mini to analyze backtest performance and generate parameter tuning recommendations.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

try:
    from openai import AsyncOpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    AsyncOpenAI = None  # type: ignore

if TYPE_CHECKING:
    from tsxbot.config_loader import OpenAIConfig
    from tsxbot.learning.param_store import StrategyParams

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an Expert Quant Trader specializing in parameter optimization.

Your Goal: Analyze backtest performance metrics and suggest parameter adjustments to improve Win Rate and Profit Factor.

Inputs you will receive:
- Strategy Name (e.g., ORB)
- Market Regime (e.g., Trending Bullish)
- Current Parameters (Stop, Target, etc.)
- Performance Metrics (Win Rate, PF, Avg Win/Loss)

Your Output MUST be valid JSON:
{
    "recommendation": "One sentence summary of what to change.",
    "reasoning": "Brief explanation of why based on the data.",
    "suggested_changes": {
        "parameter_name": "new_value"
    },
    "confidence": 0.0-1.0
}

Heuristics:
- High Win Rate but Low PF? -> Increase Profit Target.
- Low Win Rate? -> Widen Stop Loss or tighten Entry criteria.
- Chop Regime? -> Suggest wider stops or skipping trading.
- Trending Regime? -> Suggest trailing stops or larger targets.
"""


class AIRecommender:
    """
    AI-powered engine for strategy parameter optimization recommendations.
    """

    def __init__(self, config: OpenAIConfig):
        self.config = config
        self._client: AsyncOpenAI | None = None

        if self.config.enabled and OPENAI_AVAILABLE and self.config.api_key:
            self._client = AsyncOpenAI(api_key=self.config.api_key)

    @property
    def is_available(self) -> bool:
        return self._client is not None

    async def generate_recommendation(self, params: StrategyParams) -> StrategyParams:
        """
        Analyze strategy parameters and attach AI recommendation.
        Returns the updated StrategyParams object.
        """
        if not self.is_available:
            return params

        # Don't waste AI tokens on tiny insufficient samples
        if not params.is_trusted():
            return params

        prompt = self._build_prompt(params)

        try:
            response = await self._client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=300,
            )

            content = response.choices[0].message.content.strip()
            # Clean markdown if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            data = json.loads(content.strip())

            # Update params with AI insight
            rec_text = f"{data.get('recommendation')} ({data.get('reasoning')})"
            params.ai_recommendation = rec_text

            logger.info(f"Generated AI recommendation for {params.strategy}/{params.regime}")

        except Exception as e:
            logger.warning(f"AI Recommender failed: {e}")

        return params

    def _build_prompt(self, params: StrategyParams) -> str:
        return f"""Analyze this strategy performance:

Strategy: {params.strategy}
Regime: {params.regime}
Sample Size: {params.sample_size} trades

Parameters:
- Range: {params.opening_range_minutes} mins
- Target: {params.profit_target_ticks} ticks
- Stop: {params.stop_loss_ticks} ticks

Performance:
- Win Rate: {params.win_rate:.1%}
- Profit Factor: {params.profit_factor:.2f}
- Avg Win: {params.avg_win_ticks:.1f} ticks
- Avg Loss: {params.avg_loss_ticks:.1f} ticks

Recommend adjustments to improve performance."""
