"""Signal Generator - Creates the final signal packet.

Produces a complete signal packet with:
- Entry/Stop/Target/Time Stop
- Regime and playbook info
- Backtest evidence
- Skip conditions
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tsxbot.inference.regime_classifier import RegimeAnalysis
    from tsxbot.inference.strategy_selector import SelectionResult
    from tsxbot.strategies.base import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class BacktestEvidence:
    """Summary of backtest results for similar conditions."""

    sample_size: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    walk_forward_valid: bool = False
    period: str = ""  # e.g., "Last 6 months"

    def to_dict(self) -> dict:
        return {
            "sample_size": self.sample_size,
            "win_rate": f"{self.win_rate:.0%}",
            "profit_factor": f"{self.profit_factor:.2f}",
            "expectancy_r": f"{self.expectancy_r:.2f}R",
            "walk_forward_valid": self.walk_forward_valid,
            "period": self.period,
        }


@dataclass
class SignalPacket:
    """
    Complete signal packet for delivery.

    Contains everything needed to understand and act on a signal.
    """

    # Metadata
    timestamp: datetime
    session_date: str

    # Regime
    regime: str
    regime_confidence: float
    regime_rationale: str

    # Strategy
    playbook: str
    playbook_score: float
    playbook_rationale: str

    # Execution Parameters
    direction: str
    entry_price: Decimal
    stop_loss: Decimal
    profit_target: Decimal
    time_stop: datetime | None
    flatten_time: datetime | None

    # Evidence
    backtest_evidence: BacktestEvidence | None = None

    # Skip Conditions
    skip_reasons: list[str] = field(default_factory=list)
    should_trade: bool = True

    # Raw signal (for reference)
    raw_signal: dict | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "session_date": self.session_date,
            "regime": {
                "type": self.regime,
                "confidence": f"{self.regime_confidence:.0%}",
                "rationale": self.regime_rationale,
            },
            "strategy": {
                "playbook": self.playbook,
                "score": f"{self.playbook_score:.2f}",
                "rationale": self.playbook_rationale,
            },
            "execution": {
                "direction": self.direction,
                "entry_price": str(self.entry_price),
                "stop_loss": str(self.stop_loss),
                "profit_target": str(self.profit_target),
                "time_stop": self.time_stop.isoformat() if self.time_stop else None,
                "flatten_time": self.flatten_time.isoformat() if self.flatten_time else None,
            },
            "evidence": self.backtest_evidence.to_dict() if self.backtest_evidence else None,
            "skip_reasons": self.skip_reasons,
            "should_trade": self.should_trade,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_markdown(self) -> str:
        """Generate markdown summary for notifications."""
        lines = [
            f"# 📊 Signal Alert - {self.session_date}",
            "",
            f"**Time**: {self.timestamp.strftime('%H:%M:%S ET')}",
            "",
            "## Regime",
            f"- **Type**: {self.regime.upper()}",
            f"- **Confidence**: {self.regime_confidence:.0%}",
            f"- **Rationale**: {self.regime_rationale}",
            "",
            "## Strategy",
            f"- **Playbook**: {self.playbook}",
            f"- **Score**: {self.playbook_score:.2f}",
            "",
        ]

        if self.should_trade:
            lines.extend(
                [
                    "## 🎯 Trade Setup",
                    f"- **Direction**: {self.direction.upper()}",
                    f"- **Entry**: {self.entry_price}",
                    f"- **Stop Loss**: {self.stop_loss}",
                    f"- **Target**: {self.profit_target}",
                ]
            )
            if self.time_stop:
                lines.append(f"- **Time Stop**: {self.time_stop.strftime('%H:%M ET')}")
            if self.flatten_time:
                lines.append(f"- **Flatten**: {self.flatten_time.strftime('%H:%M ET')}")
        else:
            lines.extend(
                [
                    "## ⚠️ SKIP SIGNAL",
                    "",
                    "Reasons:",
                ]
            )
            for reason in self.skip_reasons:
                lines.append(f"- {reason}")

        if self.backtest_evidence:
            lines.extend(
                [
                    "",
                    "## 📈 Backtest Evidence",
                    f"- Period: {self.backtest_evidence.period}",
                    f"- Trades: {self.backtest_evidence.sample_size}",
                    f"- Win Rate: {self.backtest_evidence.win_rate:.0%}",
                    f"- Profit Factor: {self.backtest_evidence.profit_factor:.2f}",
                    f"- WFV Valid: {'✅' if self.backtest_evidence.walk_forward_valid else '❌'}",
                ]
            )

        return "\n".join(lines)


class SignalGenerator:
    """
    Generates signal packets from trade signals.

    Combines:
    - Trade signal (from playbook)
    - Regime analysis
    - Selection result
    - Backtest evidence
    """

    def __init__(self, flatten_time_str: str = "15:55"):
        self.flatten_time_str = flatten_time_str

    def generate(
        self,
        signal: TradeSignal,
        regime_analysis: RegimeAnalysis,
        selection_result: SelectionResult,
        backtest_evidence: BacktestEvidence | None = None,
    ) -> SignalPacket:
        """
        Generate a complete signal packet.

        Args:
            signal: Trade signal from playbook
            regime_analysis: Current regime classification
            selection_result: Strategy selection result
            backtest_evidence: Optional backtest summary

        Returns:
            SignalPacket ready for delivery
        """
        # Parse flatten time for today
        flatten_time = None
        if self.flatten_time_str:
            try:
                h, m = map(int, self.flatten_time_str.split(":"))
                flatten_time = signal.timestamp.replace(hour=h, minute=m, second=0)
            except Exception:
                pass

        # Extract time stop from metadata
        time_stop = None
        if signal.metadata and "time_stop" in signal.metadata:
            with contextlib.suppress(Exception):
                time_stop = datetime.fromisoformat(signal.metadata["time_stop"])

        packet = SignalPacket(
            timestamp=signal.timestamp,
            session_date=signal.timestamp.strftime("%Y-%m-%d"),
            regime=regime_analysis.regime.value,
            regime_confidence=regime_analysis.confidence,
            regime_rationale=regime_analysis.rationale,
            playbook=selection_result.playbook_name,
            playbook_score=selection_result.score,
            playbook_rationale=selection_result.rationale,
            direction=signal.direction.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_price,
            profit_target=signal.target_price,
            time_stop=time_stop,
            flatten_time=flatten_time,
            backtest_evidence=backtest_evidence,
            skip_reasons=selection_result.skip_reasons,
            should_trade=selection_result.should_trade,
            raw_signal=signal.metadata,
        )

        logger.info(f"Generated signal packet: {packet.playbook} {packet.direction}")

        return packet

    def generate_skip_packet(
        self,
        regime_analysis: RegimeAnalysis,
        selection_result: SelectionResult,
        timestamp: datetime,
    ) -> SignalPacket:
        """Generate a packet for when we're skipping (no trade)."""
        return SignalPacket(
            timestamp=timestamp,
            session_date=timestamp.strftime("%Y-%m-%d"),
            regime=regime_analysis.regime.value,
            regime_confidence=regime_analysis.confidence,
            regime_rationale=regime_analysis.rationale,
            playbook=selection_result.playbook_name,
            playbook_score=selection_result.score,
            playbook_rationale=selection_result.rationale,
            direction="none",
            entry_price=Decimal("0"),
            stop_loss=Decimal("0"),
            profit_target=Decimal("0"),
            time_stop=None,
            flatten_time=None,
            skip_reasons=selection_result.skip_reasons or ["No signal generated"],
            should_trade=False,
        )
