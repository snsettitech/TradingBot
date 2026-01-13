# Architecture

## High-Level Overview

`tsxbot` is an event-driven CLI trading bot designed for the TopstepX ProjectX API. It emphasizes safety (Risk Governor) and modularity (Strategy pattern).

### Component Diagram

```mermaid
graph TD
    CLI[CLI (click)] --> App[App Orchestrator]
    App --> Config[Config Loader]
    App --> Session[Session Manager]
    App --> Journal[Journal]
    App --> Risk[Risk Governor]
    
    subgraph Execution Loop
        App --> Broker[Broker Layer]
        Broker -->|Market Data| Strategy[Strategy Layer]
        Strategy -->|Signals| Exec[Execution Engine]
        Exec -->|Risk Check| Risk
        Risk -->|Approved Orders| Broker
    end
    
    subgraph External
        Broker -->|API/WS| ProjectX[TopstepX API]
        Broker -.->|Simulated| Sim[SimBroker]
    end
```

## Core Components

### 1. Risk Governor (The Gatekeeper)
*   **Responsibility**: Prevent catastrophic loss.
*   **Authority**: Can block ANY order.
*   **Checks**:
    *   Daily Loss Limit (Hard stop).
    *   Max Drawdown (Trailing).
    *   Max Trades per Day.
    *   Position Sizing limits.
*   **Fail-safe**: If risk check fails or state is invalid, orders are rejected.

### 2. Strategy Layer
*   **Responsibility**: Analyze market data and generate signals.
*   **Input**: `on_tick(bar)`, `on_order_update(order)`.
*   **Output**: Entry signals (Long/Short) with defined Stop Loss and Take Profit levels.
*   **Isolation**: Strategies do not place orders directly; they request execution via the Engine.

### 3. Broker Abstraction
*   **ProjectXBroker**: Handles live/demo connection.
    *   Authenticates via `PROJECTX_API_KEY`.
    *   Subscribes to market data streams.
    *   Maps internal Order objects to ProjectX payload format.
*   **SimBroker**: Internal matching engine for backtests.
    *   Simulates fills, slippage, and commission.

## Data Flows

### Market Data Path
1.  **Source**: ProjectX WebSocket.
2.  **Ingest**: `ProjectXBroker` receives JSON payload.
3.  **Normalize**: Converted to internal `Bar` or `Tick` object.
4.  **Distribute**: Passed to active `Strategy.on_tick()`.

### Order Execution Path
1.  **Signal**: Strategy triggers `BUY_STOP` at price X.
2.  **Validation**: `ExecutionEngine` receives request.
3.  **Bracket Construction**: Engine calculates Stop Loss and Take Profit prices based on strategy parameters.
4.  **Risk Audit**: `RiskGovernor.check_order(order)` is called.
    *   *If Rejected*: Logged, user notified, order dropped.
    *   *If Approved*: Passed to Broker.
5.  **Submission**: Broker translates to API call `placesTrade`.

## Authentication & Security
*   **Credentials**: Stored in environment variables (`PROJECTX_API_KEY`, `PROJECTX_USERNAME`).
*   **Scope**: ProjectX API keys have full trading access.
*   **Storage**: Never committed to git. Loaded via `python-dotenv`.
