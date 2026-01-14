"""Regime Classifier - Determines current market regime.

Uses FeatureSnapshot to classify market into regimes:
- TREND_UP: Strong upward momentum, price above OR and VWAP
- TREND_DOWN: Strong downward momentum, price below OR and VWAP
- RANGE: Choppy, mean-reverting action within OR
- HIGH_VOLATILITY: Elevated ATR, increased risk
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.intelligence.feature_snapshot import FeatureSnapshot

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regime classification."""
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    BREAKOUT = "breakout"
    HIGH_VOLATILITY = "high_volatility"
    UNKNOWN = "unknown"


@dataclass
class RegimeAnalysis:
    """Result of regime classification."""
    
    regime: MarketRegime
    confidence: float  # 0.0 to 1.0
    rationale: str
    supporting_factors: list[str]
    
    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "supporting_factors": self.supporting_factors,
        }


class RegimeClassifier:
    """
    Classifies current market regime based on features.
    
    Uses a weighted scoring system combining:
    - Trend direction (EMA relationship)
    - Position relative to OR and VWAP
    - Volatility state
    - Time of day
    """
    
    def __init__(self):
        # Weights for scoring
        self.weights = {
            "trend": 0.35,
            "position": 0.30,
            "volatility": 0.20,
            "time": 0.15,
        }
    
    def classify(self, snapshot: FeatureSnapshot) -> RegimeAnalysis:
        """
        Classify current market regime.
        
        Args:
            snapshot: Current FeatureSnapshot
        
        Returns:
            RegimeAnalysis with regime and confidence
        """
        factors = []
        scores = {
            MarketRegime.TREND_UP: 0.0,
            MarketRegime.TREND_DOWN: 0.0,
            MarketRegime.RANGE: 0.0,
            MarketRegime.BREAKOUT: 0.0,
            MarketRegime.HIGH_VOLATILITY: 0.0,
        }
        
        # 1. Trend scoring
        if snapshot.trend_direction == "up":
            scores[MarketRegime.TREND_UP] += self.weights["trend"]
            factors.append("EMA shows uptrend")
        elif snapshot.trend_direction == "down":
            scores[MarketRegime.TREND_DOWN] += self.weights["trend"]
            factors.append("EMA shows downtrend")
        else:
            scores[MarketRegime.RANGE] += self.weights["trend"]
            factors.append("EMA flat, sideways action")
        
        # 2. Position relative to OR
        if snapshot.position_in_or == "above":
            scores[MarketRegime.TREND_UP] += self.weights["position"] * 0.5
            scores[MarketRegime.BREAKOUT] += self.weights["position"] * 0.5
            factors.append("Price above Opening Range")
        elif snapshot.position_in_or == "below":
            scores[MarketRegime.TREND_DOWN] += self.weights["position"] * 0.5
            scores[MarketRegime.BREAKOUT] += self.weights["position"] * 0.5
            factors.append("Price below Opening Range")
        else:
            scores[MarketRegime.RANGE] += self.weights["position"]
            factors.append("Price within Opening Range")
        
        # 3. VWAP relationship
        if snapshot.side_of_vwap == "above" and snapshot.distance_to_vwap_ticks > 10:
            scores[MarketRegime.TREND_UP] += 0.1
            factors.append("Extended above VWAP")
        elif snapshot.side_of_vwap == "below" and snapshot.distance_to_vwap_ticks < -10:
            scores[MarketRegime.TREND_DOWN] += 0.1
            factors.append("Extended below VWAP")
        
        # 4. Volatility
        vol_state = snapshot.volatility.value if hasattr(snapshot, 'volatility') else "normal"
        if vol_state == "high" or vol_state == "extreme":
            scores[MarketRegime.HIGH_VOLATILITY] += self.weights["volatility"]
            factors.append(f"Volatility is {vol_state}")
        elif vol_state == "low":
            scores[MarketRegime.RANGE] += self.weights["volatility"] * 0.5
            factors.append("Low volatility environment")
        
        # 5. Time of day influence
        if snapshot.time_of_day == "open":
            scores[MarketRegime.BREAKOUT] += self.weights["time"]
            factors.append("In opening period (breakout prone)")
        elif snapshot.time_of_day == "close":
            scores[MarketRegime.RANGE] += self.weights["time"] * 0.5
            factors.append("End of day (reversion prone)")
        
        # Find winning regime
        best_regime = max(scores, key=scores.get)
        best_score = scores[best_regime]
        
        # Calculate confidence (normalize by max possible score)
        max_possible = sum(self.weights.values()) + 0.2  # Extra from position details
        confidence = min(1.0, best_score / max_possible * 1.5)  # Scale up
        
        # Generate rationale
        rationale = self._generate_rationale(best_regime, factors)
        
        return RegimeAnalysis(
            regime=best_regime,
            confidence=confidence,
            rationale=rationale,
            supporting_factors=factors,
        )
    
    def _generate_rationale(self, regime: MarketRegime, factors: list[str]) -> str:
        """Generate human-readable rationale."""
        if regime == MarketRegime.TREND_UP:
            return "Market in uptrend. Favor breakout and trend-following strategies."
        elif regime == MarketRegime.TREND_DOWN:
            return "Market in downtrend. Favor breakout shorts and trend-following."
        elif regime == MarketRegime.RANGE:
            return "Market in range. Favor mean-reversion and level bounce strategies."
        elif regime == MarketRegime.BREAKOUT:
            return "Breakout conditions. Watch for breakout acceptance or fakeout."
        elif regime == MarketRegime.HIGH_VOLATILITY:
            return "High volatility. Reduce size or skip marginal setups."
        else:
            return "Unclear regime. Wait for better conditions."
