"""Tests for Journaler."""

import sqlite3
from datetime import datetime

import pytest

from tsxbot.config_loader import AppConfig
from tsxbot.journal.journaler import Journaler
from tsxbot.journal.models import Decision


@pytest.mark.asyncio
async def test_journaler_lifecycle(tmp_path):
    db_path = tmp_path / "test.db"
    journal = Journaler(db_path)

    # 1. Initialize
    await journal.initialize()
    assert db_path.exists()

    # 2. Start Run
    config = AppConfig()  # Default config
    await journal.start_run(config)
    assert journal.run_id is not None

    # 3. Log Decision
    d = Decision(
        timestamp=datetime.now(),
        symbol="ES",
        strategy_name="TestStrat",
        signal=None,
        features={"foo": "bar"},
        reason="Test Reason",
    )
    await journal.log_decision(d)

    # 4. Close (Flushes)
    await journal.close()

    # 5. Verify Data
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Check Run
    runs = cur.execute("SELECT id, start_time, end_time FROM runs").fetchall()
    assert len(runs) == 1
    assert runs[0][0] == journal.run_id
    assert runs[0][2] is not None  # End time set

    # Check Decision
    decs = cur.execute("SELECT symbol, reason, features_json FROM decisions").fetchall()
    assert len(decs) == 1
    assert decs[0][0] == "ES"
    assert decs[0][1] == "Test Reason"
    assert "foo" in decs[0][2]

    conn.close()
