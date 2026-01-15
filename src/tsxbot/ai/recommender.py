"""AI Recommender - AI-powered parameter recommendations.

Uses OpenAI to analyze backtest results and suggest parameter adjustments.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tsxbot.learning.param_store import StrategyParams

logger = logging.getLogger(__name__)


@dataclass
class ParameterRecommendation:
    """AI-generated parameter recommendation."""

    playbook: str
    regime: str
    current_params: dict[str, Any]
    recommended_params: dict[str, Any]
    reasoning: str
    confidence: float
    expected_improvement: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "playbook": self.playbook,
            "regime": self.regime,
            "current_params": self.current_params,
            "recommended_params": self.recommended_params,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "expected_improvement": self.expected_improvement,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AIAnalysis:
    """Comprehensive AI analysis of backtest results."""

    summary: str
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[ParameterRecommendation]
    regime_insights: dict[str, str]
    overall_grade: str  # A-F
    action_items: list[str]


class AIRecommender:
    """
    AI-powered parameter recommendation engine.

    Analyzes aggregated backtest results and suggests:
    - Parameter adjustments (stop/target ticks)
    - Regime-specific optimizations
    - Playbook enable/disable recommendations
    """

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self._client = None

    def _get_client(self):
        """Lazy-load OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
                import os

                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    logger.warning("OPENAI_API_KEY not set")
                    return None
                self._client = OpenAI(api_key=api_key)
            except ImportError:
                logger.warning("openai package not installed")
                return None
        return self._client

    def analyze_results(
        self,
        results: list[StrategyParams],
        playbook_name: str | None = None,
    ) -> AIAnalysis | None:
        """
        Analyze backtest results and generate recommendations.

        Args:
            results: List of StrategyParams from backtest learning
            playbook_name: Optional filter to specific playbook

        Returns:
            AIAnalysis with recommendations
        """
        client = self._get_client()
        if not client:
            return self._fallback_analysis(results)

        # Format results for prompt
        formatted = self._format_results(results, playbook_name)

        prompt = f"""You are an expert trading systems analyst. Analyze these backtest results and provide recommendations.

## Backtest Results by Regime

{formatted}

## Instructions

Analyze the performance and provide:
1. Overall assessment (grade A-F)
2. Strengths (what's working)
3. Weaknesses (what's not working)
4. Parameter recommendations for each underperforming regime
5. Regime-specific insights

Output valid JSON:
{{
    "grade": "B",
    "summary": "Brief overall assessment...",
    "strengths": ["Strong win rate in TREND_UP", "..."],
    "weaknesses": ["Poor profit factor in RANGE", "..."],
    "regime_insights": {{
        "TREND_UP": "Performs well, consider increasing position size",
        "RANGE": "Needs tighter stops, consider disabling"
    }},
    "action_items": ["Increase target to 20 ticks in TREND_UP", "..."],
    "recommendations": [
        {{
            "playbook": "BreakoutAcceptance",
            "regime": "RANGE",
            "current_stop": 8,
            "current_target": 16,
            "recommended_stop": 6,
            "recommended_target": 10,
            "confidence": 0.75,
            "reasoning": "In range-bound markets, tighter risk is needed..."
        }}
    ]
}}
"""

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.3,
            )

            raw = response.choices[0].message.content
            return self._parse_response(raw)

        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            return self._fallback_analysis(results)

    def _format_results(
        self, results: list[StrategyParams], playbook_filter: str | None
    ) -> str:
        """Format results for AI prompt."""
        lines = []
        for r in results:
            if playbook_filter and r.strategy != playbook_filter:
                continue

            lines.append(f"### {r.strategy} - {r.regime}")
            lines.append(f"- Win Rate: {r.win_rate:.1%}")
            lines.append(f"- Profit Factor: {r.profit_factor:.2f}")
            lines.append(f"- Avg Win: {r.avg_win_ticks:.1f} ticks")
            lines.append(f"- Avg Loss: {r.avg_loss_ticks:.1f} ticks")
            lines.append(f"- Sample Size: {r.sample_size} trades")
            lines.append(f"- Stop: {r.stop_loss_ticks} ticks, Target: {r.profit_target_ticks} ticks")
            lines.append("")

        return "\n".join(lines) if lines else "No results available"

    def _parse_response(self, raw: str) -> AIAnalysis:
        """Parse AI JSON response into AIAnalysis."""
        data = json.loads(raw)

        recommendations = []
        for rec in data.get("recommendations", []):
            recommendations.append(
                ParameterRecommendation(
                    playbook=rec.get("playbook", ""),
                    regime=rec.get("regime", ""),
                    current_params={
                        "stop_ticks": rec.get("current_stop", 8),
                        "target_ticks": rec.get("current_target", 16),
                    },
                    recommended_params={
                        "stop_ticks": rec.get("recommended_stop", 8),
                        "target_ticks": rec.get("recommended_target", 16),
                    },
                    reasoning=rec.get("reasoning", ""),
                    confidence=rec.get("confidence", 0.5),
                    expected_improvement=rec.get("expected_improvement", "Unknown"),
                )
            )

        return AIAnalysis(
            summary=data.get("summary", ""),
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            recommendations=recommendations,
            regime_insights=data.get("regime_insights", {}),
            overall_grade=data.get("grade", "C"),
            action_items=data.get("action_items", []),
        )

    def _fallback_analysis(self, results: list[StrategyParams]) -> AIAnalysis:
        """Rule-based analysis when AI unavailable."""
        recommendations = []
        weaknesses = []
        strengths = []

        for r in results:
            # Identify issues
            if r.profit_factor < 1.0:
                weaknesses.append(f"{r.strategy}/{r.regime}: PF {r.profit_factor:.2f} < 1.0")

                # Suggest tighter stops for losing regimes
                recommendations.append(
                    ParameterRecommendation(
                        playbook=r.strategy,
                        regime=r.regime,
                        current_params={
                            "stop_ticks": r.stop_loss_ticks,
                            "target_ticks": r.profit_target_ticks,
                        },
                        recommended_params={
                            "stop_ticks": max(4, r.stop_loss_ticks - 2),
                            "target_ticks": r.profit_target_ticks,
                        },
                        reasoning="Profit factor below 1.0, reduce risk with tighter stops",
                        confidence=0.6,
                        expected_improvement="Reduce losses by ~10-15%",
                    )
                )
            elif r.profit_factor > 1.5:
                strengths.append(f"{r.strategy}/{r.regime}: PF {r.profit_factor:.2f}")

        return AIAnalysis(
            summary=f"Analyzed {len(results)} regime configurations",
            strengths=strengths,
            weaknesses=weaknesses,
            recommendations=recommendations,
            regime_insights={},
            overall_grade="C" if len(weaknesses) > len(strengths) else "B",
            action_items=[f"Review {w}" for w in weaknesses[:3]],
        )

    def generate_report(self, analysis: AIAnalysis) -> str:
        """Generate markdown report from analysis."""
        lines = [
            f"# AI Backtest Analysis Report",
            f"",
            f"**Overall Grade**: {analysis.overall_grade}",
            f"",
            f"## Summary",
            analysis.summary,
            f"",
            f"## Strengths",
        ]

        for s in analysis.strengths:
            lines.append(f"- ✅ {s}")

        lines.append("")
        lines.append("## Weaknesses")
        for w in analysis.weaknesses:
            lines.append(f"- ⚠️ {w}")

        lines.append("")
        lines.append("## Recommendations")
        for rec in analysis.recommendations:
            lines.append(f"### {rec.playbook} - {rec.regime}")
            lines.append(f"- **Current**: Stop={rec.current_params['stop_ticks']}, Target={rec.current_params['target_ticks']}")
            lines.append(f"- **Recommended**: Stop={rec.recommended_params['stop_ticks']}, Target={rec.recommended_params['target_ticks']}")
            lines.append(f"- **Confidence**: {rec.confidence:.0%}")
            lines.append(f"- **Reasoning**: {rec.reasoning}")
            lines.append("")

        lines.append("## Action Items")
        for i, item in enumerate(analysis.action_items, 1):
            lines.append(f"{i}. {item}")

        return "\n".join(lines)
