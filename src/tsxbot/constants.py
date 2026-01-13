"""Core constants for TSXBot."""

from decimal import Decimal
from enum import Enum


class BrokerMode(str, Enum):
    """Broker mode selection."""

    PROJECTX = "projectx"
    SIM = "sim"


class TradingEnvironment(str, Enum):
    """Trading environment selection."""

    DEMO = "DEMO"
    LIVE = "LIVE"


class OrderType(str, Enum):
    """Order type for entries."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderSide(str, Enum):
    """Order side (buy/sell)."""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """Order lifecycle status."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SignalDirection(str, Enum):
    """Trading signal direction."""

    LONG = "long"
    SHORT = "short"


class StrategyName(str, Enum):
    """Available trading strategies."""

    ORB = "orb"
    SWEEP_RECLAIM = "sweep_reclaim"
    BOS_PULLBACK = "bos_pullback"
    VWAP_BOUNCE = "vwap_bounce"
    MEAN_REVERSION = "mean_reversion"


class LogLevel(str, Enum):
    """Logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# ============================================
# Contract Specifications
# ============================================


class ContractSpec:
    """Contract specifications for futures symbols."""

    ES_TICK_SIZE = Decimal("0.25")
    ES_TICK_VALUE = Decimal("12.50")
    ES_MULTIPLIER = Decimal("50.0")

    MES_TICK_SIZE = Decimal("0.25")
    MES_TICK_VALUE = Decimal("1.25")
    MES_MULTIPLIER = Decimal("5.0")


# ============================================
# Default Values
# ============================================

DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_RTH_START = "09:30"
DEFAULT_RTH_END = "16:00"
DEFAULT_FLATTEN_TIME = "15:55"

DEFAULT_DAILY_LOSS_LIMIT = Decimal("500.0")
DEFAULT_MAX_LOSS_LIMIT = Decimal("1000.0")
DEFAULT_MAX_RISK_PER_TRADE = Decimal("100.0")
DEFAULT_MAX_CONTRACTS_ES = 2
DEFAULT_MAX_CONTRACTS_MES = 10
DEFAULT_MAX_TRADES_PER_DAY = 10

DEFAULT_STOP_TICKS = 8
DEFAULT_TARGET_TICKS = 16

# ============================================
# Application Constants
# ============================================

APP_NAME = "tsxbot"
JOURNAL_DB_NAME = "journal.db"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_FORMAT_JSON = '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}'
