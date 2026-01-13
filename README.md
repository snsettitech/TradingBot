# TSXBot 🤖📈

**Automated ES/MES Futures Trading Bot for TopstepX ProjectX API**

An extensible, safety-first trading bot designed for CME E-mini S&P 500 (ES) and Micro E-mini (MES) futures.

## Features

- 🛡️ **Risk Governor** - Hard blocks on daily loss, drawdown, position size, and trade count
- 📊 **Multiple Strategies** - ORB, Liquidity Sweep Reclaim, Break-of-Structure Pullback
- 🔒 **Safety First** - DRY_RUN mode, bracket orders with stops, kill switch
- 📝 **Full Journaling** - SQLite-based logging of all decisions, orders, and fills
- ⏰ **RTH Only** - Trades only during Regular Trading Hours with configurable flatten time
- 🧪 **Testable** - SimBroker for unit tests, comprehensive test coverage

## Quick Start

### Prerequisites

- Python 3.11+
- TopstepX ProjectX API credentials

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/tsxbot.git
cd tsxbot

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"
```

### Configuration

1. **Copy example files:**
```bash
cp .env.example .env
cp config/config.example.yaml config/config.yaml
```

2. **Edit `.env` with your credentials:**
```env
PROJECTX_API_KEY=your_api_key_here
PROJECTX_USERNAME=your_username_here
TRADING_ENVIRONMENT=DEMO
```

3. **Customize `config/config.yaml`** for your risk limits and strategy preferences.

### Running

#### Smoke Test (Verify Connectivity)
```bash
python -m tsxbot smoke-test --config config/config.yaml
```

#### DRY_RUN Mode (Log Signals, No Orders)
```bash
python -m tsxbot run --config config/config.yaml --dry-run
```

#### Live Trading (Use with Caution!)
```bash
# First, ensure you're in DEMO environment
python -m tsxbot run --config config/config.yaml
```

### CLI Options

```bash
python -m tsxbot --help
python -m tsxbot run --help
python -m tsxbot smoke-test --help
```

| Option | Description |
|--------|-------------|
| `--config`, `-c` | Path to YAML config file (required) |
| `--dry-run`, `-d` | Enable DRY_RUN mode |
| `--strategy`, `-s` | Override strategy: `orb`, `sweep_reclaim`, `bos_pullback` |
| `--broker`, `-b` | Override broker mode: `projectx`, `sim` |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                         CLI                              │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                    App (Orchestrator)                    │
│  • Main event loop                                       │
│  • Component coordination                                │
│  • Graceful shutdown                                     │
└───────┬─────────┬─────────┬─────────┬───────────────────┘
        │         │         │         │
        ▼         ▼         ▼         ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
│ Session │ │  Risk   │ │ Journal │ │ Config  │
│ Manager │ │Governor │ │         │ │ Loader  │
└────┬────┘ └────┬────┘ └────┬────┘ └─────────┘
     │           │           │
     ▼           ▼           ▼
┌─────────────────────────────────────────────────────────┐
│                   Execution Engine                       │
│  • Bracket orders (entry + stop + target)                │
│  • Risk checks before every order                        │
│  • DRY_RUN mode support                                  │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                   Broker Abstraction                     │
│  ┌──────────────┐  ┌──────────────┐                     │
│  │ ProjectXBroker│  │  SimBroker   │                     │
│  │ (tsxapi4py)  │  │  (testing)   │                     │
│  └──────────────┘  └──────────────┘                     │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                    Strategy Layer                        │
│  ┌─────┐  ┌──────────────┐  ┌──────────────┐            │
│  │ ORB │  │Sweep Reclaim │  │ BOS Pullback │            │
│  └─────┘  └──────────────┘  └──────────────┘            │
└─────────────────────────────────────────────────────────┘
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PROJECTX_API_KEY` | TopstepX API key | (required) |
| `PROJECTX_USERNAME` | TopstepX username | (required) |
| `TRADING_ENVIRONMENT` | `DEMO` or `LIVE` | `DEMO` |
| `PROJECTX_ACCOUNT_ID` | Account ID override | (auto-detect) |
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `DATA_DIR` | Data directory path | `./data` |

## Risk Management

The Risk Governor enforces the following limits:

| Limit | Config Key | Default |
|-------|------------|---------|
| Daily Loss | `risk.daily_loss_limit_usd` | $500 |
| Max Drawdown | `risk.max_loss_limit_usd` | $1000 |
| Per-Trade Risk | `risk.max_risk_per_trade_usd` | $100 |
| Max ES Contracts | `risk.max_contracts_es` | 2 |
| Max MES Contracts | `risk.max_contracts_mes` | 10 |
| Max Trades/Day | `risk.max_trades_per_day` | 10 |
| Kill Switch | `risk.kill_switch` | false |

**Non-negotiable**: Every entry order MUST have an attached protective stop.

## Strategies

### Opening Range Breakout (ORB) ✅ Implemented
Trades breakouts from the first N minutes of RTH.

### Liquidity Sweep Reclaim 🚧 Skeleton
Identifies sweeps of swing highs/lows followed by reclaim.

### Break-of-Structure Pullback 🚧 Skeleton
Enters on pullback after a break of market structure.

## Development

### Running Tests
```bash
# All tests
pytest

# With coverage
pytest --cov=tsxbot

# Specific test file
pytest tests/test_config_loader.py -v
```

### Type Checking
```bash
mypy src/tsxbot --strict
```

### Linting
```bash
ruff check src/tsxbot tests/
ruff format src/tsxbot tests/
```

## Project Structure

```
tsxbot/
├── pyproject.toml          # Package configuration
├── README.md               # This file
├── .env.example            # Environment template
├── .gitignore
├── config/
│   └── config.example.yaml # Configuration template
├── data/                   # Journal database (git-ignored)
├── src/tsxbot/
│   ├── __init__.py
│   ├── __main__.py        # Module entrypoint
│   ├── cli.py             # CLI commands
│   ├── app.py             # Main orchestrator
│   ├── config_loader.py   # Configuration with Pydantic
│   ├── constants.py       # Enums and constants
│   ├── time/              # Session management
│   ├── data/              # Market data handling
│   ├── broker/            # Broker abstraction
│   ├── risk/              # Risk governor
│   ├── execution/         # Order execution
│   ├── strategies/        # Trading strategies
│   ├── journal/           # Trade journaling
│   └── learning/          # Offline learning
└── tests/                  # Unit tests
```

## Safety Notes

⚠️ **IMPORTANT**:
- Always start with `TRADING_ENVIRONMENT=DEMO`
- Always test with `--dry-run` first
- Never commit your `.env` file
- The Risk Governor is authoritative - if it blocks, nothing trades
- Bracket orders are mandatory - naked entries are not allowed

## License

MIT License - See LICENSE for details.

## Acknowledgments

- Uses [tsxapi4py](https://github.com/mceesincus/tsxapi4py) for TopstepX API connectivity
