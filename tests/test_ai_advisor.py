"""Unit tests for AI Advisor module."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from tsxbot.ai.models import MarketContext, TradeAnalysis, TradeValidation
from tsxbot.ai.prompts import POST_TRADE_SYSTEM_PROMPT, PRE_TRADE_SYSTEM_PROMPT


class TestMarketContext:
    """Tests for MarketContext model."""

    def test_to_prompt_context_basic(self):
        """Test basic context formatting."""
        ctx = MarketContext(
            symbol="ES",
            timestamp=datetime.now(),
            current_price=Decimal("5750.00"),
            session_high=Decimal("5760.00"),
            session_low=Decimal("5740.00"),
            minutes_since_open=15,
            session_phase="opening",
            daily_pnl=Decimal("100.00"),
            trade_count_today=1,
            volatility_description="Normal (20 tick range)",
            dist_to_hod_ticks=10,
            dist_to_lod_ticks=10,
        )

        prompt = ctx.to_prompt_context()

        assert "ES" in prompt
        assert "5750" in prompt
        assert "15 mins" in prompt
        assert "opening" in prompt
        assert "HOD: 10 ticks" in prompt

    def test_to_prompt_context_with_rvol(self):
        """Test context with RVOL description."""
        ctx = MarketContext(
            symbol="MES",
            timestamp=datetime.now(),
            current_price=Decimal("5750.00"),
            session_high=Decimal("5760.00"),
            session_low=Decimal("5740.00"),
            rvol=2.5,
            rvol_description="RVOL: 2.5x average",
        )

        prompt = ctx.to_prompt_context()

        assert "RVOL: 2.5x" in prompt


class TestTradeValidation:
    """Tests for TradeValidation model."""

    def test_format_console_output(self):
        """Test console output formatting."""
        validation = TradeValidation(
            confidence=8,
            observations=["Strong volume on breakout"],
            risks=["Near resistance"],
            latency_ms=450,
        )

        output = validation.format_console_output()

        assert "8/10" in output
        assert "Strong volume" in output
        assert "resistance" in output

    def test_format_console_output_empty(self):
        """Test console output with no observations."""
        validation = TradeValidation(confidence=5)

        output = validation.format_console_output()

        assert "5/10" in output


class TestTradeAnalysis:
    """Tests for TradeAnalysis model."""

    def test_creation(self):
        """Test basic creation."""
        analysis = TradeAnalysis(
            grade="B",
            what_worked=["Good entry timing"],
            what_didnt=["Exit was late"],
            lessons=["Consider tighter trailing stop"],
        )

        assert analysis.grade == "B"
        assert len(analysis.lessons) == 1


class TestPrompts:
    """Tests for prompt templates."""

    def test_pre_trade_prompt_contains_json_instruction(self):
        """System prompt should instruct JSON response."""
        assert "JSON" in PRE_TRADE_SYSTEM_PROMPT
        assert "confidence" in PRE_TRADE_SYSTEM_PROMPT
        assert "observations" in PRE_TRADE_SYSTEM_PROMPT

    def test_post_trade_prompt_contains_grades(self):
        """Post trade prompt should mention grading."""
        assert "grade" in POST_TRADE_SYSTEM_PROMPT
        assert "lessons" in POST_TRADE_SYSTEM_PROMPT


class TestAIAdvisorParsing:
    """Tests for AI response parsing."""

    def test_parse_valid_json_response(self):
        """Test parsing a valid JSON response."""
        from tsxbot.ai.advisor import AIAdvisor
        from tsxbot.config_loader import OpenAIConfig

        # Create advisor with disabled config (no API calls)
        config = OpenAIConfig(enabled=False)
        advisor = AIAdvisor(config)

        raw = json.dumps(
            {
                "confidence": 8,
                "observations": ["Good setup"],
                "risks": ["Watch volume"],
                "suggestions": [],
            }
        )

        result = advisor._parse_validation_response(raw, latency_ms=100)

        assert result.confidence == 8
        assert "Good setup" in result.observations
        assert result.latency_ms == 100

    def test_parse_json_with_markdown_wrapper(self):
        """Test parsing JSON wrapped in markdown code block."""
        from tsxbot.ai.advisor import AIAdvisor
        from tsxbot.config_loader import OpenAIConfig

        config = OpenAIConfig(enabled=False)
        advisor = AIAdvisor(config)

        raw = """```json
{
    "confidence": 7,
    "observations": ["Volume increasing"],
    "risks": [],
    "suggestions": ["Consider smaller size"]
}
```"""

        result = advisor._parse_validation_response(raw, latency_ms=200)

        assert result.confidence == 7
        assert "Volume" in result.observations[0]

    def test_parse_invalid_json_returns_default(self):
        """Test graceful handling of invalid JSON."""
        from tsxbot.ai.advisor import AIAdvisor
        from tsxbot.config_loader import OpenAIConfig

        config = OpenAIConfig(enabled=False)
        advisor = AIAdvisor(config)

        raw = "This is not valid JSON at all"

        result = advisor._parse_validation_response(raw, latency_ms=50)

        # Should return default confidence without raising
        assert result.confidence == 5
        assert "Parse error" in result.observations[0]


class TestAIAdvisorAvailability:
    """Tests for advisor availability checks."""

    def test_disabled_config_not_available(self):
        """Advisor should not be available when disabled."""
        from tsxbot.ai.advisor import AIAdvisor
        from tsxbot.config_loader import OpenAIConfig

        config = OpenAIConfig(enabled=False)
        advisor = AIAdvisor(config)

        assert not advisor.is_available

    def test_missing_api_key_not_available(self):
        """Advisor should not be available without API key."""
        from tsxbot.ai.advisor import AIAdvisor
        from tsxbot.config_loader import OpenAIConfig

        config = OpenAIConfig(enabled=True, api_key="")
        advisor = AIAdvisor(config)

        assert not advisor.is_available

    def test_unresolved_env_var_not_available(self):
        """Advisor should detect unresolved env var placeholder."""
        from tsxbot.ai.advisor import AIAdvisor
        from tsxbot.config_loader import OpenAIConfig

        config = OpenAIConfig(enabled=True, api_key="${OPENAI_API_KEY}")
        advisor = AIAdvisor(config)

        assert not advisor.is_available
