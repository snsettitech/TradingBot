"""Journaler Service."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tsxbot.broker.models import Fill, Order
from tsxbot.config_loader import AppConfig
from tsxbot.journal.models import Decision
from tsxbot.journal.schema import SCHEMA_STATEMENTS

logger = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


class Journaler:
    """
    Persists trading activity to SQLite database.
    Offloads blocking I/O to a thread executor.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="journaler")
        self.run_id: int | None = None
        self._conn: sqlite3.Connection | None = None  # Only used in the worker thread

    async def initialize(self) -> None:
        """Initialize database schema."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._init_db_sync)

    def _init_db_sync(self) -> None:
        """Synchronous DB initialization."""
        try:
            # Ensure directory exists
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON;")
                for stmt in SCHEMA_STATEMENTS:
                    conn.execute(stmt)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize journal DB: {e}", exc_info=True)
            raise

    async def start_run(self, config: AppConfig) -> None:
        """Start a new logging run session."""
        loop = asyncio.get_running_loop()
        self.run_id = await loop.run_in_executor(self._executor, self._insert_run_sync, config)
        logger.info(f"Journal run started: ID {self.run_id}")

    def _insert_run_sync(self, config: AppConfig) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO runs (start_time, config_json, broker_mode, tags)
                VALUES (?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(),
                    json.dumps(config.model_dump(mode="json"), default=_json_default),
                    config.environment.broker_mode,
                    "v1",
                ),
            )
            val = cur.lastrowid
            conn.commit()
            if val is None:
                raise ValueError("Failed to get run_id")
            return val

    async def log_decision(self, decision: Decision) -> None:
        """Log a strategy decision."""
        if self.run_id is None:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._log_decision_sync, decision, self.run_id)

    def _log_decision_sync(self, d: Decision, run_id: int) -> None:
        sig_dir = d.signal.direction.value if d.signal else None
        sig_qty = d.signal.quantity if d.signal else None

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO decisions (run_id, timestamp, symbol, strategy, signal_direction, signal_qty, features_json, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    d.timestamp.isoformat(),
                    d.symbol,
                    d.strategy_name,
                    sig_dir,
                    sig_qty,
                    json.dumps(d.features, default=_json_default),
                    d.reason,
                ),
            )
            conn.commit()

    async def log_order(self, order: Order) -> None:
        """Log an order update."""
        if self.run_id is None:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._log_order_sync, order, self.run_id)

    def _log_order_sync(self, order: Order, run_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            # Upsert logic (REPLACE INTO or INSERT OR REPLACE)
            conn.execute(
                """
                INSERT OR REPLACE INTO orders (
                    order_id, run_id, timestamp, symbol, side, qty, order_type, limit_price, stop_price, status, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.id,
                    run_id,
                    order.timestamp.isoformat(),
                    order.request.symbol,
                    order.request.side.value,
                    order.request.qty,
                    order.request.type.value,
                    str(order.request.limit_price) if order.request.limit_price else None,
                    str(order.request.stop_price) if order.request.stop_price else None,
                    order.status.value,
                    json.dumps(
                        {
                            "filled_qty": order.filled_qty,
                            "avg_fill_price": str(order.avg_fill_price)
                            if order.avg_fill_price
                            else None,
                        },
                        default=_json_default,
                    ),
                ),
            )
            conn.commit()

    async def log_fill(self, fill: Fill) -> None:
        """Log a fill event."""
        if self.run_id is None:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._log_fill_sync, fill, self.run_id)

    def _log_fill_sync(self, fill: Fill, run_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fills (
                    fill_id, order_id, run_id, timestamp, symbol, side, qty, price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.id,
                    fill.order_id,
                    run_id,
                    fill.timestamp.isoformat(),
                    fill.symbol,
                    fill.side.value,
                    fill.qty,
                    str(fill.price),
                ),
            )
            conn.commit()

    async def log_ai_validation(self, signal: Any, validation: Any) -> None:
        """Log a pre-trade AI validation."""
        if self.run_id is None:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._executor,
            self._log_ai_insight_sync,
            "pre_trade",
            getattr(signal, "symbol", ""),
            getattr(signal, "direction", None),
            getattr(validation, "confidence", None),
            None,  # grade (post-trade only)
            getattr(validation, "observations", []),
            getattr(validation, "risks", []),
            [],  # lessons (post-trade only)
            getattr(validation, "raw_response", ""),
            getattr(validation, "latency_ms", 0),
            self.run_id,
        )

    async def log_ai_analysis(self, symbol: str, analysis: Any) -> None:
        """Log a post-trade AI analysis."""
        if self.run_id is None:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._executor,
            self._log_ai_insight_sync,
            "post_trade",
            symbol,
            None,  # direction
            None,  # confidence
            getattr(analysis, "grade", None),
            getattr(analysis, "what_worked", []),
            [],  # risks
            getattr(analysis, "lessons", []),
            getattr(analysis, "raw_response", ""),
            getattr(analysis, "latency_ms", 0),
            self.run_id,
        )

    def _log_ai_insight_sync(
        self,
        insight_type: str,
        symbol: str,
        direction: Any,
        confidence: int | None,
        grade: str | None,
        observations: list,
        risks: list,
        lessons: list,
        raw_response: str,
        latency_ms: int,
        run_id: int,
    ) -> None:
        dir_val = (
            direction.value
            if hasattr(direction, "value")
            else str(direction)
            if direction
            else None
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO ai_insights (
                    run_id, timestamp, insight_type, symbol, signal_direction,
                    confidence, grade, observations_json, risks_json, lessons_json,
                    raw_response, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    datetime.now().isoformat(),
                    insight_type,
                    symbol,
                    dir_val,
                    confidence,
                    grade,
                    json.dumps(observations, default=_json_default),
                    json.dumps(risks, default=_json_default),
                    json.dumps(lessons, default=_json_default),
                    raw_response,
                    latency_ms,
                ),
            )
            conn.commit()

    async def close(self) -> None:
        """Shutdown journaler."""
        if self.run_id:
            # Update run end time?
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._close_run_sync, self.run_id)
        self._executor.shutdown(wait=True)

    def _close_run_sync(self, run_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE runs SET end_time = ? WHERE id = ?", (datetime.now().isoformat(), run_id)
            )
            conn.commit()
