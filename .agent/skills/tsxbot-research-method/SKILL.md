# tsxbot-research-method

This skill enforces the standards for all strategy research and backtesting within the ES/MES signal system.

## Standards

### 1. Walk-Forward Validation
- All backtests must utilize walk-forward validation (WFV).
- Divide data into "In-Sample" (optimization) and "Out-of-Sample" (validation).
- Optimization must be performed on a moving window to ensure the strategy adapts to market regime changes without overfitting.

### 2. Slippage and Commission Assumptions
- **Slippage**: Assume 1 tick of slippage for ES/MES entries and exits.
- **Commissions**: Include realistic commissions (e.g., $2.00 per side for ES, $0.50 for MES).
- Backtests without these assumptions are considered invalid.

### 3. Overfitting Prevention
- Maximum of 3 tunable parameters per strategy playbook.
- No brute-force parameter sweeps without economic/logical rationale.
- Parameter stability check: Small changes in parameters should not result in drastically different performance.

### 4. Bounded Logic
- Strategy logic must be deterministic.
- No vague or discretionary "AI-based" entry rules.
- Results must be reproducible.
