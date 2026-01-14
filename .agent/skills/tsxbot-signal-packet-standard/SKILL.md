# tsxbot-signal-packet-standard

This skill defines the mandatory fields and format for all signal packets sent to the user.

## Mandatory Fields

### 1. Strategy Identity
- **Regime**: Current market regime (e.g., Trend Up, Range, High Volatility).
- **Playbook**: The selected strategy (e.g., ORB Pullback).
- **Rationale**: A brief explanation of why this playbook was selected.

### 2. Execution Parameters
- **Entry**: Exact price level for entry.
- **Stop Loss**: Exact price level for stop loss.
- **Profit Target**: Exact price level for profit target.
- **Time Stop**: Time when the trade should be closed if still open.
- **Flatten Time**: Time when all positions MUST be flattened regardless of state.

### 3. Evidential Support
- **Backtest Summary**: Performance metrics for the last 6 months in similar regimes.
- **Walk-Forward Proof**: Brief note on WFV results.

### 4. Skip Conditions
- Explicit conditions under which the signal should be ignored (e.g., "Skip if price is > 10 ticks from VWAP at entry time").
