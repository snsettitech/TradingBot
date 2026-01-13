# Codebase Map

## Directory Structure

### Root
*   `pyproject.toml`: Build configuration, dependencies, and tool settings (ruff, mypy, pytest).
*   `README.md`: Project overview and setup instructions.
*   `.env`: Local secrets (API keys). **DO NOT COMMIT**.
*   `config/`: Configuration files.
    *   `config.yaml`: Active configuration.
    *   `config.example.yaml`: Template configuration.

### Source (`src/tsxbot/`)
The core application logic.

*   `app.py`: **Main Orchestrator**. Initializes components, handles shutdown, runs the main event loop.
*   `cli.py`: **Entrypoint**. Defines the command-line interface (`tsxbot run`, `backtest`, etc.).
*   `config_loader.py`: Pydantic models for configuration validation and loading.
*   `constants.py`: Project-wide constants and enums (OrderTypes, Side, etc.).

#### Modules
*   `ai/`: Artificial Intelligence integration.
    *   `advisor.py`: Interface to OpenAI for trade analysis.
*   `backtest/`: Backtesting infrastructure.
    *   `engine.py`: Runs historical simulations.
    *   `data_loader.py`: Loads CSV or fetches historical API data.
*   `broker/`: Broker abstraction layer.
    *   `projectx.py`: **Live/Demo connection** to TopstepX via `tsxapipy`.
    *   `sim.py`: Simulation broker for testing/backtesting.
*   `data/`: Market data processing.
*   `execution/`: Order management.
    *   `engine.py`: Handles order Lifecycle, bracket orders (TP/SL).
*   `journal/`: Activity logging.
    *   Stores trade history and decisions (likely SQLite or JSONL).
*   `learning/`: Offline learning modules (reinforcement learning or stat analysis).
*   `risk/`: **CRITICAL**. Risk management.
    *   `risk_governor.py`: Enforces daily loss limits, drawdown, and trade counts. Authoritative gatekeeper.
*   `strategies/`: Trading strategies.
    *   `orb.py`: Open Range Breakout implementation.
    *   `registry.py`: Factory for loading strategies.
*   `time/`: Time management.
    *   `session_manager.py`: Handles RTH (Regular Trading Hours) logic.
*   `ui/`: User Interface helpers (likely CLI output formatting).

### Tests (`tests/`)
Pytest suite mirroring the source structure.
*   `conftest.py`: Shared test fixtures.
*   `test_risk_governor.py`: Critical tests for risk limits.
*   `test_orb_strategy.py`: Strategy logic verification.

## Key Flows
1.  **Startup**: `cli.py` -> `app.py` -> `ConfigLoader` -> `SessionManager` -> `RiskGovernor` -> `Broker` -> `Strategy`.
2.  **Tick Processing**: `Broker` (WebSocket) -> `Strategy.on_tick()` -> `ExecutionEngine` (if signal) -> `RiskGovernor.check()` -> `Broker.place_order()`.
