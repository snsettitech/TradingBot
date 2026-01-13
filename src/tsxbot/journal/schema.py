"""Journal Database Schema."""

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_time TEXT NOT NULL,
        end_time TEXT,
        config_json TEXT,
        broker_mode TEXT,
        tags TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        strategy TEXT NOT NULL,
        signal_direction TEXT,
        signal_qty INTEGER,
        features_json TEXT,
        reason TEXT,
        FOREIGN KEY(run_id) REFERENCES runs(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        run_id INTEGER,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        qty INTEGER NOT NULL,
        order_type TEXT NOT NULL,
        limit_price TEXT,
        stop_price TEXT,
        status TEXT,
        details_json TEXT,
        FOREIGN KEY(run_id) REFERENCES runs(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        run_id INTEGER,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        qty INTEGER NOT NULL,
        price TEXT NOT NULL,
        FOREIGN KEY(order_id) REFERENCES orders(order_id),
        FOREIGN KEY(run_id) REFERENCES runs(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        timestamp TEXT NOT NULL,
        insight_type TEXT NOT NULL,
        symbol TEXT,
        signal_direction TEXT,
        confidence INTEGER,
        grade TEXT,
        observations_json TEXT,
        risks_json TEXT,
        lessons_json TEXT,
        raw_response TEXT,
        latency_ms INTEGER,
        FOREIGN KEY(run_id) REFERENCES runs(id)
    );
    """,
]
