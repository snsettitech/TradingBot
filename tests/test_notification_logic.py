from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from tsxbot.scheduler.alert_engine import Alert, AlertConfig, AlertEngine, AlertType


def test_large_move_alert_disabled():
    """Test that large move alerts are disabled by default."""
    config = AlertConfig(send_large_moves=False)
    engine = AlertEngine(config=config)

    # Simulate a large move
    tick1 = MagicMock()
    tick1.timestamp = datetime.now()
    tick1.price = 100

    tick2 = MagicMock()
    tick2.timestamp = datetime.now() + timedelta(minutes=1)
    tick2.price = 102  # 2% move

    alert1 = engine.on_tick(tick1)
    alert2 = engine.on_tick(tick2)

    assert alert1 is None
    assert alert2 is None  # Should be None because send_large_moves is False


def test_market_update_alert():
    """Test that market update alerts work."""
    config = AlertConfig(send_hourly_updates=True)
    engine = AlertEngine(config=config)

    timestamp = datetime.now()
    alert = engine.on_market_update(timestamp, "Stats")

    assert alert is not None
    assert alert.alert_type == AlertType.MARKET_UPDATE
    assert alert.title == "Hourly Market Update"


def test_market_update_disabled():
    """Test that market update alerts can be disabled."""
    config = AlertConfig(send_hourly_updates=False)
    engine = AlertEngine(config=config)

    timestamp = datetime.now()
    alert = engine.on_market_update(timestamp, "Stats")

    assert alert is None
