"""Configuration loader with Pydantic validation and environment variable interpolation."""

from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

# Load environment variables at module level
load_dotenv(find_dotenv(usecwd=True))

from tsxbot.constants import (
    BrokerMode,
    LogLevel,
    OrderType,
    StrategyName,
    TradingEnvironment,
)


def interpolate_env_vars(value: Any) -> Any:
    """
    Interpolate environment variables in string values.

    Supports formats:
    - ${VAR_NAME} - required, raises if not set
    - ${VAR_NAME:default} - optional with default value
    """
    if not isinstance(value, str):
        return value

    pattern = r"\$\{([^}:]+)(?::([^}]*))?\}"

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)

        env_value = os.environ.get(var_name)

        if env_value is not None:
            return env_value
        elif default is not None:
            return default
        else:
            # Return empty string for optional unset vars without default
            return ""

    return re.sub(pattern, replacer, value)


def process_config_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively process config dict to interpolate env vars."""
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = process_config_dict(value)
        elif isinstance(value, list):
            result[key] = [
                process_config_dict(item) if isinstance(item, dict) else interpolate_env_vars(item)
                for item in value
            ]
        else:
            result[key] = interpolate_env_vars(value)
    return result


# ============================================
# Pydantic Configuration Models
# ============================================


class EnvironmentConfig(BaseModel):
    """Environment and runtime settings."""

    dry_run: bool = True
    broker_mode: BrokerMode = BrokerMode.SIM
    log_level: LogLevel = LogLevel.INFO
    data_dir: str = "./data"


class ProjectXConfig(BaseModel):
    """ProjectX API configuration."""

    api_key: str = ""
    username: str = ""
    trading_environment: TradingEnvironment = TradingEnvironment.DEMO
    account_id: str = ""

    @field_validator("api_key", "username")
    @classmethod
    def warn_empty_credentials(cls, v: str, info: Any) -> str:
        """Warn if credentials are empty (will fail on actual connection)."""
        # Empty is allowed for sim mode, validation happens at runtime
        return v


class SymbolSpecConfig(BaseModel):
    """Symbol-specific configuration."""

    tick_size: Decimal = Decimal("0.25")
    tick_value: Decimal = Decimal("12.50")
    contract_id_prefix: str = ""


class SymbolsConfig(BaseModel):
    """Symbol configuration."""

    primary: str = "ES"
    micros: str = "MES"
    contract_month: str = "H26"  # Current front-month contract (H=Mar, M=Jun, U=Sep, Z=Dec)
    es: SymbolSpecConfig = Field(
        default_factory=lambda: SymbolSpecConfig(
            tick_size=Decimal("0.25"), tick_value=Decimal("12.50"), contract_id_prefix="CON.F.US.EP"
        )
    )
    mes: SymbolSpecConfig = Field(
        default_factory=lambda: SymbolSpecConfig(
            tick_size=Decimal("0.25"), tick_value=Decimal("1.25"), contract_id_prefix="CON.F.US.MES"
        )
    )

    def get_contract_id(self, symbol: str) -> str:
        """Build full contract ID from symbol name (ES or MES)."""
        if symbol.upper() == "ES" or symbol == self.primary:
            return f"{self.es.contract_id_prefix}.{self.contract_month}"
        elif symbol.upper() == "MES" or symbol == self.micros:
            return f"{self.mes.contract_id_prefix}.{self.contract_month}"
        else:
            # Already a full contract ID or unknown
            return symbol


class SessionConfig(BaseModel):
    """Trading session configuration."""

    timezone: str = "America/New_York"
    rth_start: str = "09:30"
    rth_end: str = "16:00"
    flatten_time: str = "15:55"
    trading_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])

    @field_validator("rth_start", "rth_end", "flatten_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        """Validate time is in HH:MM format."""
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError(f"Time must be in HH:MM format, got: {v}")
        hours, minutes = map(int, v.split(":"))
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise ValueError(f"Invalid time value: {v}")
        return v

    @field_validator("trading_days")
    @classmethod
    def validate_trading_days(cls, v: list[int]) -> list[int]:
        """Validate trading days are 0-6 (Monday-Sunday)."""
        for day in v:
            if not 0 <= day <= 6:
                raise ValueError(f"Trading day must be 0-6, got: {day}")
        return v


class RiskConfig(BaseModel):
    """Risk management configuration."""

    daily_loss_limit_usd: Decimal = Decimal("500.0")
    max_loss_limit_usd: Decimal = Decimal("1000.0")
    max_risk_per_trade_usd: Decimal = Decimal("100.0")
    max_contracts_es: int = 2
    max_contracts_mes: int = 10
    max_trades_per_day: int = 10
    kill_switch: bool = False

    @field_validator(
        "daily_loss_limit_usd", "max_loss_limit_usd", "max_risk_per_trade_usd", mode="before"
    )
    @classmethod
    def convert_to_decimal(cls, v: Any) -> Decimal:
        """Convert numeric values to Decimal."""
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))

    @field_validator("max_contracts_es", "max_contracts_mes", "max_trades_per_day")
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        """Validate positive integers."""
        if v < 0:
            raise ValueError(f"Value must be non-negative, got: {v}")
        return v


class BracketConfig(BaseModel):
    """Bracket order configuration."""

    stop_ticks_default: int = 8
    target_ticks_default: int = 16
    use_trailing_stop: bool = False
    trailing_activation_ticks: int = 8
    trailing_distance_ticks: int = 4

    @field_validator(
        "stop_ticks_default",
        "target_ticks_default",
        "trailing_activation_ticks",
        "trailing_distance_ticks",
    )
    @classmethod
    def validate_positive_ticks(cls, v: int) -> int:
        """Validate tick values are positive."""
        if v <= 0:
            raise ValueError(f"Tick value must be positive, got: {v}")
        return v


class CommissionConfig(BaseModel):
    """Commission configuration for TopstepX."""

    es_round_turn: Decimal = Decimal("2.80")  # ES commission per round-turn
    mes_round_turn: Decimal = Decimal("0.74")  # MES commission per round-turn

    @field_validator("es_round_turn", "mes_round_turn", mode="before")
    @classmethod
    def convert_to_decimal(cls, v: Any) -> Decimal:
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))


class ExecutionConfig(BaseModel):
    """Execution settings configuration."""

    order_type: OrderType = OrderType.MARKET
    slippage_ticks: int = 1  # Assumed slippage in ticks
    commissions: CommissionConfig = Field(default_factory=CommissionConfig)
    bracket: BracketConfig = Field(default_factory=BracketConfig)


class ORBStrategyConfig(BaseModel):
    """Opening Range Breakout strategy configuration."""

    opening_range_minutes: int = 5
    breakout_buffer_ticks: int = 2
    stop_ticks: int = 8
    target_ticks: int = 16
    max_trades: int = 2
    min_range_ticks: int = 4
    max_range_ticks: int = 40
    direction: str = "both"

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        """Validate direction is valid."""
        valid = {"long", "short", "both"}
        if v.lower() not in valid:
            raise ValueError(f"Direction must be one of {valid}, got: {v}")
        return v.lower()


class SweepReclaimStrategyConfig(BaseModel):
    """Liquidity Sweep Reclaim strategy configuration."""

    lookback_bars: int = 20
    min_sweep_ticks: int = 2
    reclaim_window_bars: int = 5
    stop_ticks: int = 6
    target_ticks: int = 12
    max_trades: int = 3
    direction: str = "both"

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        """Validate direction."""
        valid = {"long", "short", "both"}
        if v.lower() not in valid:
            raise ValueError(f"Direction must be one of {valid}, got: {v}")
        return v.lower()


class BOSPullbackStrategyConfig(BaseModel):
    """Break of Structure Pullback strategy configuration."""

    min_swing_ticks: int = 10
    pullback_min_pct: float = 0.382
    pullback_max_pct: float = 0.618
    entry_trigger: str = "limit"
    stop_ticks: int = 4
    target_rr_ratio: float = 2.0
    max_trades: int = 2
    direction: str = "both"

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        """Validate direction."""
        valid = {"long", "short", "both"}
        if v.lower() not in valid:
            raise ValueError(f"Direction must be one of {valid}, got: {v}")
        return v.lower()

    @field_validator("entry_trigger")
    @classmethod
    def validate_entry_trigger(cls, v: str) -> str:
        """Validate entry trigger type."""
        valid = {"limit", "market"}
        if v.lower() not in valid:
            raise ValueError(f"Entry trigger must be one of {valid}, got: {v}")
        return v.lower()

    @model_validator(mode="after")
    def validate_pullback_range(self) -> BOSPullbackStrategyConfig:
        """Validate pullback range is valid."""
        if self.pullback_min_pct >= self.pullback_max_pct:
            raise ValueError(
                f"pullback_min_pct ({self.pullback_min_pct}) must be less than "
                f"pullback_max_pct ({self.pullback_max_pct})"
            )
        return self


class VWAPBounceStrategyConfig(BaseModel):
    """VWAP Bounce/Rejection strategy configuration."""

    # Entry parameters
    touch_threshold_ticks: int = 3  # How close to VWAP for a "touch"
    skip_first_minutes: int = 35  # Skip ORB time

    # Risk parameters
    stop_ticks: int = 6
    target_ticks: int = 12

    # Trade limits
    max_trades: int = 3
    cooldown_minutes: int = 10  # Time between trades

    # Direction filter
    direction: str = "both"

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        valid = {"long", "short", "both"}
        if v.lower() not in valid:
            raise ValueError(f"Direction must be one of {valid}, got: {v}")
        return v.lower()


class MeanReversionStrategyConfig(BaseModel):
    """Mean Reversion strategy configuration."""

    # RSI parameters
    rsi_oversold: int = 30
    rsi_overbought: int = 70

    # Level detection
    level_threshold_ticks: int = 8  # How close to session high/low
    skip_first_minutes: int = 35  # Skip opening volatility

    # Risk parameters
    stop_ticks: int = 8
    target_ticks: int = 8  # 1:1 R:R but high win rate

    # Trade limits
    max_trades: int = 4
    cooldown_minutes: int = 15

    # Direction filter
    direction: str = "both"

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        valid = {"long", "short", "both"}
        if v.lower() not in valid:
            raise ValueError(f"Direction must be one of {valid}, got: {v}")
        return v.lower()


class EMACloudStrategyConfig(BaseModel):
    """Ripster EMA Cloud strategy configuration."""

    # EMA periods
    fast_ema_short: int = 5
    fast_ema_long: int = 12
    trend_ema_short: int = 34
    trend_ema_long: int = 50

    # Bar aggregation
    bar_minutes: int = 10

    # Trade limits
    max_trades: int = 3
    cooldown_minutes: int = 15

    # Filters
    min_cloud_separation_ticks: int = 2  # Prevent trading flat EMAs
    min_volume_ratio: float = 0.5  # Bar volume must be > 50% of session avg

    # Stop buffer (ticks beyond trend cloud)
    stop_buffer_ticks: int = 2

    # Direction filter
    direction: str = "both"

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        valid = {"long", "short", "both"}
        if v.lower() not in valid:
            raise ValueError(f"Direction must be one of {valid}, got: {v}")
        return v.lower()


class StrategyConfig(BaseModel):
    """Strategy selection and parameters."""

    active: StrategyName = StrategyName.ORB
    allow_user_override: bool = True
    orb: ORBStrategyConfig = Field(default_factory=ORBStrategyConfig)
    sweep_reclaim: SweepReclaimStrategyConfig = Field(default_factory=SweepReclaimStrategyConfig)
    bos_pullback: BOSPullbackStrategyConfig = Field(default_factory=BOSPullbackStrategyConfig)
    vwap_bounce: VWAPBounceStrategyConfig = Field(default_factory=VWAPBounceStrategyConfig)
    mean_reversion: MeanReversionStrategyConfig = Field(default_factory=MeanReversionStrategyConfig)
    ema_cloud: EMACloudStrategyConfig = Field(default_factory=EMACloudStrategyConfig)


class JournalConfig(BaseModel):
    """Journal configuration."""

    database_path: str = "./data/journal.db"
    log_all_decisions: bool = True
    retention_days: int = 90


class PreTradeConfig(BaseModel):
    """Pre-trade AI validation settings."""

    enabled: bool = True
    timeout_seconds: float = 5.0


class PostTradeConfig(BaseModel):
    """Post-trade AI analysis settings."""

    enabled: bool = True


class OpenAIConfig(BaseModel):
    """OpenAI AI Advisor configuration."""

    enabled: bool = False
    api_key: str = ""
    model: str = "gpt-4o-mini"
    pre_trade: PreTradeConfig = Field(default_factory=PreTradeConfig)
    post_trade: PostTradeConfig = Field(default_factory=PostTradeConfig)
    max_requests_per_minute: int = 20
    console_stream: bool = True  # Stream AI output to console during dry-run


class AppConfig(BaseModel):
    """Root application configuration."""

    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    projectx: ProjectXConfig = Field(default_factory=ProjectXConfig)
    symbols: SymbolsConfig = Field(default_factory=SymbolsConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    journal: JournalConfig = Field(default_factory=JournalConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)

    @property
    def is_dry_run(self) -> bool:
        """Check if running in dry run mode."""
        return self.environment.dry_run

    @property
    def is_sim_mode(self) -> bool:
        """Check if using simulated broker."""
        return self.environment.broker_mode == BrokerMode.SIM

    @property
    def is_live_environment(self) -> bool:
        """Check if trading in live environment."""
        return self.projectx.trading_environment == TradingEnvironment.LIVE


# ============================================
# Configuration Loader
# ============================================


class ConfigLoader:
    """Load and validate configuration from YAML files with env var interpolation."""

    def __init__(self, config_path: str | Path) -> None:
        """
        Initialize config loader.

        Args:
            config_path: Path to the YAML configuration file.
        """
        self.config_path = Path(config_path)
        self._config: AppConfig | None = None

    def load(self) -> AppConfig:
        """
        Load and validate configuration.

        Returns:
            Validated AppConfig instance.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            yaml.YAMLError: If YAML is invalid.
            pydantic.ValidationError: If config validation fails.
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)

        if raw_config is None:
            raw_config = {}

        # Interpolate environment variables
        processed_config = process_config_dict(raw_config)

        # Validate with Pydantic
        self._config = AppConfig.model_validate(processed_config)

        return self._config

    @property
    def config(self) -> AppConfig:
        """Get loaded config, loading if necessary."""
        if self._config is None:
            return self.load()
        return self._config

    def reload(self) -> AppConfig:
        """Force reload configuration from disk."""
        self._config = None
        return self.load()


def load_config(config_path: str | Path) -> AppConfig:
    """
    Convenience function to load configuration.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Validated AppConfig instance.
    """
    loader = ConfigLoader(config_path)
    return loader.load()


def load_config_with_overrides(
    config_path: str | Path,
    *,
    dry_run: bool | None = None,
    strategy: str | None = None,
    broker_mode: str | None = None,
) -> AppConfig:
    """
    Load configuration with CLI overrides.

    Args:
        config_path: Path to the YAML configuration file.
        dry_run: Override dry_run setting.
        strategy: Override active strategy.
        broker_mode: Override broker mode.

    Returns:
        Validated AppConfig instance with overrides applied.
    """
    config = load_config(config_path)

    # Apply overrides by creating new config with modified values
    updates: dict[str, Any] = {}

    if dry_run is not None:
        updates["environment"] = config.environment.model_copy(update={"dry_run": dry_run})

    if strategy is not None:
        strategy_enum = StrategyName(strategy.lower())
        updates["strategy"] = config.strategy.model_copy(update={"active": strategy_enum})

    if broker_mode is not None:
        broker_enum = BrokerMode(broker_mode.lower())
        if "environment" in updates:
            updates["environment"] = updates["environment"].model_copy(
                update={"broker_mode": broker_enum}
            )
        else:
            updates["environment"] = config.environment.model_copy(
                update={"broker_mode": broker_enum}
            )

    if updates:
        return config.model_copy(update=updates)

    return config
