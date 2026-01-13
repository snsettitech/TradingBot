"""Tests for configuration loading and validation."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

from tsxbot.config_loader import (
    ConfigLoader,
    interpolate_env_vars,
    load_config,
    load_config_with_overrides,
    process_config_dict,
)
from tsxbot.constants import BrokerMode, StrategyName


class TestEnvVarInterpolation:
    """Tests for environment variable interpolation."""

    def test_no_interpolation_needed(self) -> None:
        """Test that plain strings pass through unchanged."""
        assert interpolate_env_vars("hello") == "hello"
        assert interpolate_env_vars(123) == 123
        assert interpolate_env_vars(None) is None

    def test_simple_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test simple ${VAR} interpolation."""
        monkeypatch.setenv("TEST_VAR", "test_value")
        assert interpolate_env_vars("${TEST_VAR}") == "test_value"

    def test_env_var_with_default(self) -> None:
        """Test ${VAR:default} interpolation with missing var."""
        # Ensure var is not set
        os.environ.pop("MISSING_VAR", None)
        assert interpolate_env_vars("${MISSING_VAR:default_value}") == "default_value"

    def test_env_var_with_default_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test ${VAR:default} uses actual value when set."""
        monkeypatch.setenv("SET_VAR", "actual_value")
        assert interpolate_env_vars("${SET_VAR:default_value}") == "actual_value"

    def test_empty_default(self) -> None:
        """Test ${VAR:} with empty default."""
        os.environ.pop("EMPTY_DEFAULT_VAR", None)
        assert interpolate_env_vars("${EMPTY_DEFAULT_VAR:}") == ""

    def test_missing_var_no_default(self) -> None:
        """Test ${VAR} with missing var returns empty string."""
        os.environ.pop("TOTALLY_MISSING", None)
        assert interpolate_env_vars("${TOTALLY_MISSING}") == ""

    def test_mixed_text_and_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test interpolation in mixed text."""
        monkeypatch.setenv("USER_NAME", "john")
        result = interpolate_env_vars("Hello ${USER_NAME}, welcome!")
        assert result == "Hello john, welcome!"


class TestProcessConfigDict:
    """Tests for recursive config dict processing."""

    def test_nested_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test processing nested dictionaries."""
        monkeypatch.setenv("NESTED_VAR", "nested_value")
        data = {"level1": {"level2": {"value": "${NESTED_VAR}"}}}
        result = process_config_dict(data)
        assert result["level1"]["level2"]["value"] == "nested_value"

    def test_list_processing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test processing lists."""
        monkeypatch.setenv("LIST_VAR", "list_value")
        data = {"items": ["static", "${LIST_VAR}"]}
        result = process_config_dict(data)
        assert result["items"] == ["static", "list_value"]


class TestConfigLoader:
    """Tests for ConfigLoader class."""

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """Test loading a valid configuration file."""
        config_content = """
environment:
  dry_run: true
  broker_mode: sim
  log_level: INFO

risk:
  daily_loss_limit_usd: 500
  max_trades_per_day: 10
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        loader = ConfigLoader(config_file)
        config = loader.load()

        assert config.environment.dry_run is True
        assert config.environment.broker_mode == BrokerMode.SIM
        assert config.risk.daily_loss_limit_usd == Decimal("500")
        assert config.risk.max_trades_per_day == 10

    def test_file_not_found(self) -> None:
        """Test error on missing config file."""
        loader = ConfigLoader(Path("/nonexistent/path/config.yaml"))
        with pytest.raises(FileNotFoundError):
            loader.load()

    def test_empty_config_uses_defaults(self, tmp_path: Path) -> None:
        """Test that empty config file uses defaults."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        config = load_config(config_file)

        # Should have default values
        assert config.environment.dry_run is True
        assert config.session.timezone == "America/New_York"

    def test_reload_config(self, tmp_path: Path) -> None:
        """Test reloading configuration."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("environment:\n  dry_run: true")

        loader = ConfigLoader(config_file)
        config1 = loader.load()
        assert config1.environment.dry_run is True

        # Modify file
        config_file.write_text("environment:\n  dry_run: false")

        # Reload
        config2 = loader.reload()
        assert config2.environment.dry_run is False


class TestConfigWithOverrides:
    """Tests for CLI override functionality."""

    def test_dry_run_override(self, tmp_path: Path) -> None:
        """Test overriding dry_run via CLI."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("environment:\n  dry_run: false")

        config = load_config_with_overrides(config_file, dry_run=True)
        assert config.environment.dry_run is True

    def test_strategy_override(self, tmp_path: Path) -> None:
        """Test overriding strategy via CLI."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("strategy:\n  active: orb")

        config = load_config_with_overrides(config_file, strategy="sweep_reclaim")
        assert config.strategy.active == StrategyName.SWEEP_RECLAIM

    def test_broker_mode_override(self, tmp_path: Path) -> None:
        """Test overriding broker mode via CLI."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("environment:\n  broker_mode: projectx")

        config = load_config_with_overrides(config_file, broker_mode="sim")
        assert config.environment.broker_mode == BrokerMode.SIM

    def test_multiple_overrides(self, tmp_path: Path) -> None:
        """Test multiple CLI overrides together."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        config = load_config_with_overrides(
            config_file,
            dry_run=True,
            strategy="bos_pullback",
            broker_mode="sim",
        )

        assert config.environment.dry_run is True
        assert config.strategy.active == StrategyName.BOS_PULLBACK
        assert config.environment.broker_mode == BrokerMode.SIM


class TestConfigValidation:
    """Tests for configuration validation."""

    def test_invalid_time_format(self, tmp_path: Path) -> None:
        """Test validation of time format."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("session:\n  rth_start: '9:30'")  # Missing leading zero

        with pytest.raises(ValueError, match="HH:MM format"):
            load_config(config_file)

    def test_invalid_trading_days(self, tmp_path: Path) -> None:
        """Test validation of trading days."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("session:\n  trading_days: [0, 7]")  # 7 is invalid

        with pytest.raises(ValueError, match="0-6"):
            load_config(config_file)

    def test_invalid_pullback_range(self, tmp_path: Path) -> None:
        """Test validation of pullback range in BOS strategy."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
strategy:
  bos_pullback:
    pullback_min_pct: 0.8
    pullback_max_pct: 0.5
""")

        with pytest.raises(ValueError, match="pullback_min_pct"):
            load_config(config_file)

    def test_negative_risk_value(self, tmp_path: Path) -> None:
        """Test validation of negative risk values."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("risk:\n  max_contracts_es: -1")

        with pytest.raises(ValueError, match="non-negative"):
            load_config(config_file)


class TestAppConfigProperties:
    """Tests for AppConfig computed properties."""

    def test_is_dry_run_property(self, tmp_path: Path) -> None:
        """Test is_dry_run property."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("environment:\n  dry_run: true")

        config = load_config(config_file)
        assert config.is_dry_run is True

    def test_is_sim_mode_property(self, tmp_path: Path) -> None:
        """Test is_sim_mode property."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("environment:\n  broker_mode: sim")

        config = load_config(config_file)
        assert config.is_sim_mode is True

    def test_is_live_environment_property(self, tmp_path: Path) -> None:
        """Test is_live_environment property."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("projectx:\n  trading_environment: LIVE")

        config = load_config(config_file)
        assert config.is_live_environment is True
