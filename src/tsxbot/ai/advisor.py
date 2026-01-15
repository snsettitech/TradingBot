"""AI-powered trade intelligence advisor using OpenAI."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

try:
    from openai import AsyncOpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    AsyncOpenAI = None  # type: ignore

from tsxbot.ai.models import MarketContext, TradeAnalysis, TradeResult, TradeValidation
from tsxbot.ai.prompts import (
    POST_TRADE_SYSTEM_PROMPT,
    PRE_TRADE_SYSTEM_PROMPT,
    build_post_trade_prompt,
    build_pre_trade_prompt,
)

if TYPE_CHECKING:
    from tsxbot.config_loader import OpenAIConfig
    from tsxbot.strategies.base import TradeSignal

logger = logging.getLogger(__name__)

# ANSI color code for cyan console output
CYAN = "\033[96m"
RESET = "\033[0m"
BRAIN_EMOJI = "🧠"


class AIAdvisor:
    """
    OpenAI-powered trade intelligence advisor.

    Provides:
    - Pre-trade validation with confidence scoring and commentary
    - Post-trade analysis for learning and strategy improvement

    Commentary only - never rejects trades.
    """

    def __init__(
        self,
        config: OpenAIConfig,
        dry_run: bool = False,
    ) -> None:
        """
        Initialize AI Advisor.

        Args:
            config: OpenAI configuration.
            dry_run: If True, enables console streaming when configured.
        """
        self.config = config
        self.dry_run = dry_run
        self._client: AsyncOpenAI | None = None
        self._request_count = 0
        self._last_request_time = 0.0

        if not config.enabled:
            logger.info("AI Advisor disabled in config")
            return

        if not OPENAI_AVAILABLE:
            logger.warning("OpenAI package not installed. Run: pip install openai")
            return

        if not config.api_key or config.api_key.startswith("${"):
            logger.warning("OpenAI API key not configured. Set OPENAI_API_KEY in .env")
            return

        self._client = AsyncOpenAI(api_key=config.api_key)
        logger.info(f"AI Advisor initialized with model: {config.model}")

    @property
    def is_available(self) -> bool:
        """Check if AI advisor is available and configured."""
        return self._client is not None and self.config.enabled

    def _should_stream_to_console(self) -> bool:
        """Check if we should print AI output to console."""
        return self.dry_run and self.config.console_stream and self.is_available

    def _print_ai_output(self, message: str) -> None:
        """Print AI commentary to console in cyan."""
        print(f"{CYAN}[AI {BRAIN_EMOJI}] {message}{RESET}")

    async def _rate_limit_wait(self) -> None:
        """Simple rate limiting based on config."""
        if self.config.max_requests_per_minute <= 0:
            return

        min_interval = 60.0 / self.config.max_requests_per_minute
        elapsed = time.time() - self._last_request_time

        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)

        self._last_request_time = time.time()
        self._request_count += 1

    async def validate_trade(
        self,
        signal: TradeSignal,
        context: MarketContext,
        recent_lessons: list[str] | None = None,
    ) -> TradeValidation | None:
        """
        Pre-trade validation: Analyze signal with market context.

        Args:
            signal: The trade signal from strategy.
            context: Current market context snapshot.
            recent_lessons: Optional lessons from previous trades for learning.

        Returns:
            TradeValidation with confidence and observations, or None on failure.
        """
        if not self.is_available or not self.config.pre_trade.enabled:
            return None

        start_time = time.time()

        try:
            await self._rate_limit_wait()

            # Build signal info string
            signal_info = (
                f"Direction: {signal.direction.value}\n"
                f"Symbol: {signal.symbol}\n"
                f"Quantity: {signal.quantity}\n"
                f"Entry Type: {signal.entry_type.value}\n"
                f"Stop: {signal.stop_ticks} ticks\n"
                f"Target: {signal.target_ticks} ticks\n"
                f"Reason: {signal.reason}"
            )

            # Build the prompt
            user_prompt = build_pre_trade_prompt(
                signal_info=signal_info,
                market_context=context.to_prompt_context(),
                recent_lessons=recent_lessons,
            )

            # Call OpenAI
            response = await asyncio.wait_for(
                self._client.chat.completions.create(  # type: ignore
                    model=self.config.model,
                    messages=[
                        {"role": "system", "content": PRE_TRADE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,  # Lower temperature for more consistent analysis
                    max_tokens=300,
                ),
                timeout=self.config.pre_trade.timeout_seconds,
            )

            latency_ms = int((time.time() - start_time) * 1000)
            raw_response = response.choices[0].message.content or ""

            # Parse JSON response
            validation = self._parse_validation_response(raw_response, latency_ms)

            # Console streaming for dry-run
            if self._should_stream_to_console():
                self._print_ai_output(validation.format_console_output())

            logger.debug(f"AI validation completed in {latency_ms}ms: conf={validation.confidence}")
            return validation

        except TimeoutError:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.warning(f"AI validation timed out after {latency_ms}ms")
            if self._should_stream_to_console():
                self._print_ai_output("Timeout - proceeding without AI confirmation")
            return None

        except Exception as e:
            logger.warning(f"AI validation failed: {e}")
            if self._should_stream_to_console():
                self._print_ai_output(f"Error: {e}")
            return None

    def _parse_validation_response(self, raw_response: str, latency_ms: int) -> TradeValidation:
        """Parse JSON response from OpenAI into TradeValidation."""
        try:
            # Clean up response (remove any markdown code blocks if present)
            clean = raw_response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            clean = clean.strip()

            data = json.loads(clean)

            return TradeValidation(
                confidence=int(data.get("confidence", 5)),
                observations=data.get("observations", []),
                risks=data.get("risks", []),
                suggestions=data.get("suggestions", []),
                raw_response=raw_response,
                latency_ms=latency_ms,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse AI response: {e}")
            return TradeValidation(
                confidence=5,
                observations=["[Parse error - raw response stored]"],
                raw_response=raw_response,
                latency_ms=latency_ms,
            )

    async def analyze_completed_trade(self, trade_result: TradeResult) -> TradeAnalysis | None:
        """
        Post-trade analysis: What worked, what didn't, lessons learned.

        Args:
            trade_result: Completed trade data.

        Returns:
            TradeAnalysis with grade and lessons, or None on failure.
        """
        if not self.is_available or not self.config.post_trade.enabled:
            return None

        start_time = time.time()

        try:
            await self._rate_limit_wait()

            # Build trade result string
            result_str = (
                f"Symbol: {trade_result.symbol}\n"
                f"Direction: {trade_result.direction}\n"
                f"Entry: {trade_result.entry_price}\n"
                f"Exit: {trade_result.exit_price}\n"
                f"Quantity: {trade_result.quantity}\n"
                f"P&L: {trade_result.pnl_ticks} ticks (${trade_result.pnl_usd})\n"
                f"Duration: {trade_result.duration_seconds}s\n"
                f"Exit Reason: {trade_result.exit_reason}\n"
                f"Signal Reason: {trade_result.signal_reason}"
            )

            if trade_result.ai_confidence_at_entry:
                result_str += f"\nAI Confidence at Entry: {trade_result.ai_confidence_at_entry}/10"

            if trade_result.entry_context:
                result_str += f"\n\nMarket Context at Entry:\n{trade_result.entry_context.to_prompt_context()}"

            user_prompt = build_post_trade_prompt(result_str)

            # Call OpenAI
            response = await asyncio.wait_for(
                self._client.chat.completions.create(  # type: ignore
                    model=self.config.model,
                    messages=[
                        {"role": "system", "content": POST_TRADE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.4,
                    max_tokens=400,
                ),
                timeout=10.0,  # Post-trade is not time-sensitive
            )

            latency_ms = int((time.time() - start_time) * 1000)
            raw_response = response.choices[0].message.content or ""

            analysis = self._parse_analysis_response(raw_response, latency_ms)

            if self._should_stream_to_console():
                self._print_ai_output(
                    f"Trade Grade: {analysis.grade} | "
                    f"Lesson: {analysis.lessons[0] if analysis.lessons else 'N/A'}"
                )

            logger.debug(f"AI analysis completed in {latency_ms}ms: grade={analysis.grade}")
            return analysis

        except Exception as e:
            logger.warning(f"AI analysis failed: {e}")
            return None

    def _parse_analysis_response(self, raw_response: str, latency_ms: int) -> TradeAnalysis:
        """Parse JSON response from OpenAI into TradeAnalysis."""
        try:
            clean = raw_response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            clean = clean.strip()

            data = json.loads(clean)

            return TradeAnalysis(
                grade=data.get("grade", "C"),
                what_worked=data.get("what_worked", []),
                what_didnt=data.get("what_didnt", []),
                lessons=data.get("lessons", []),
                raw_response=raw_response,
                latency_ms=latency_ms,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse AI analysis: {e}")
            return TradeAnalysis(
                grade="?",
                lessons=["[Parse error]"],
                raw_response=raw_response,
                latency_ms=latency_ms,
            )
