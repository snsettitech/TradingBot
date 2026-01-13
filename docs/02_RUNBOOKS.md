# Runbooks

## Deploy Checklist

Before starting a trading session:

1.  **Verify Environment**
    *   [ ] Check `.env` has valid `PROJECTX_API_KEY`.
    *   [ ] Confirm `TRADING_ENVIRONMENT` is set correctly (`DEMO` or `LIVE`).
    *   [ ] **CRITICAL**: If `LIVE`, double-check risk limits in `config.yaml`.

2.  **Pull Updates**
    *   [ ] `git pull origin main`
    *   [ ] `pip install .` (if dependencies changed)

3.  **Run Smoke Test**
    *   [ ] `python -m tsxbot smoke-test`
    *   [ ] Verify "Smoke test passed" output.

4.  **Dry Run (Optional but Recommended)**
    *   [ ] `python -m tsxbot run --dry-run`
    *   [ ] Ensure connection establishes and data arrives (watch logs for "TICK" or "BAR").

5.  **Start Trading**
    *   [ ] `python -m tsxbot run`
    *   [ ] Monitor terminal for initial 'Connected' message.

## Common Breakages & Fixes

### 1. "Authentication Failed" or 401
*   **Symptom**: Bot crashes immediately with auth error.
*   **Cause**: Invalid or expired API Key.
*   **Fix**:
    *   Log in to TopstepX dashboard.
    *   Generate a new API Key.
    *   Update `.env`.

### 2. "Data Stream Disconnected"
*   **Symptom**: Logs show silence for > 1 minute during RTH.
*   **Cause**: WebSocket drop or internet instability.
*   **Fix**:
    *   The bot *should* auto-reconnect.
    *   If not, `Ctrl+C` and restart the bot.
    *   Check `debug_log.txt` for specific disconnect codes.

### 3. "Risk Limit Breached" (Order Rejected)
*   **Symptom**: Order not placed, log says "Blocked by RiskRules".
*   **Cause**: You hit Daily Loss or Max Drawdown.
*   **Fix**:
    *   **Do not bypass**. The bot is doing its job.
    *   Stop trading for the day.
    *   Analyze losing trades in `journal/`.

## Local Debugging

### Logs
*   `run_log.txt`: High-level operational events (Orders, Fills).
*   `debug_log.txt`: Verbose low-level events (API payloads, specific calculations).

### Interactive Debugging
Use the `python -m tsxbot backtest` command to reproduce strategy behavior with reported data without risking capital.

```bash
# Replay 5 days of data for ORB strategy
python -m tsxbot backtest --strategy orb --projectx --days 5
```
