# Agent Playbook

## Quick Start for Agents
1.  **Read Context**: Start by reading `docs/00_CODEBASE_MAP.md` to locate relevant files.
2.  **Understand Structure**: `src/tsxbot` is the package. `tests/` mirrors it.
3.  **Safety First**: **NEVER** modify `RiskGovernor` logic (in `src/tsxbot/risk`) without explicit user instruction and a specific verification plan.

## Standard Workflows

### 1. Making Code Changes
*   **Locate**: Use `find_by_name` or `grep_search` to find relevant code.
*   **Plan**: Create an `implementation_plan.md` artifact.
*   **Edit**: Use `replace_file_content` for surgical edits.
*   **Verify**: Run related tests.
    *   `pytest tests/test_target_module.py`

### 2. Adding a Strategy
*   Inherit from `Strategy` base class (`src/tsxbot/strategies/base.py`).
*   Implement `on_tick`, `on_bar`.
*   Register in `src/tsxbot/strategies/registry.py`.
*   Add unit tests in `tests/test_new_strategy.py`.
*   **Validation**: Run backtest `python -m tsxbot backtest --strategy new_strat`.

### 3. Debugging
*   Check `debug_log.txt` if available.
*   Run `python -m tsxbot smoke-test` to check wiring.

## Conventions
*   **Typing**: All new code must be fully typed (Python 3.11+).
*   **Async**: The core loop is async. Use `await` for I/O.
*   **Config**: Do not hardcode magic numbers. Put them in `config/config.yaml` (and update `config.example.yaml` + `ConfigLoader`).

## Critical Rules
*   **No "Blind" Shell Commands**: Do not run `del` or destructive commands without verifying paths.
*   **No Exfiltration**: Do not cat `.env` to the output.
*   **Test New Dependencies**: If adding a library, update `pyproject.toml` and inform the user.
