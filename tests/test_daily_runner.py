"""Tests for DailyRunner."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from tsxbot.scheduler.daily_runner import DailyRunner
from tsxbot.data.market_data import Tick
from tsxbot.config_loader import AppConfig

@pytest.fixture
def mock_config():
    config = MagicMock(spec=AppConfig)
    config.session = MagicMock()
    config.session.rth_start = "09:30"
    config.session.rth_end = "16:00"
    config.session.flatten_time = "15:59"
    config.session.timezone = "America/New_York"
    config.symbols = MagicMock()
    config.symbols.es = MagicMock()
    config.symbols.es.tick_size = 0.25
    config.symbols.primary = "ES"
    config.openai = MagicMock()
    config.is_dry_run = True
    return config

@pytest.mark.asyncio
async def test_daily_runner_tick_archival(mock_config):
    """Test that ticks are archived during processing."""
    
    # Mock broker
    broker = MagicMock()
    
    # Initialize runner with mocked config
    runner = DailyRunner(config=mock_config, broker=broker, enable_ai=False)
    
    # Mock components
    runner.tick_archiver = MagicMock()
    runner.level_store = MagicMock()
    runner.alert_engine = MagicMock()
    
    # Create a tick
    tick = Tick(
        timestamp=datetime.now(),
        price=5000.0,
        volume=10,
        symbol="ES"
    )
    
    # Process tick
    await runner._process_tick(tick)
    
    # Verify archiver was called
    runner.tick_archiver.on_tick.assert_called_once_with(tick)
    runner.alert_engine.on_tick.assert_called_once_with(tick)
    assert len(runner._bar_data) == 1

@pytest.mark.asyncio
async def test_daily_runner_ai_init(mock_config):
    """Test AI initialization."""
    with patch("tsxbot.ai.advisor.AIAdvisor") as mock_ai:
        mock_ai.return_value.is_available.return_value = True
        
        runner = DailyRunner(config=mock_config, enable_ai=True)
        assert runner.ai_advisor is not None
        
    with patch("tsxbot.ai.advisor.AIAdvisor") as mock_ai:
        mock_ai.return_value.is_available.return_value = False
        
        runner = DailyRunner(config=mock_config, enable_ai=True)
        assert runner.ai_advisor is None
