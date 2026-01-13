"""
Monte Carlo Simulation for ORB Strategy Validation - FIXED VERSION

Fixes:
1. Uses actual fill prices from broker callbacks (not tick prices)
2. Tracks intraday drawdown (not just end-of-day)
3. Detailed trade logging for verification
"""

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Optional
import statistics

from tsxbot.broker.sim import SimBroker
from tsxbot.broker.models import Fill
from tsxbot.config_loader import (
    AppConfig, EnvironmentConfig, SymbolsConfig, SymbolSpecConfig,
    SessionConfig, RiskConfig, ExecutionConfig, StrategyConfig, 
    ORBStrategyConfig, JournalConfig, BracketConfig
)
from tsxbot.constants import OrderSide, SignalDirection, BrokerMode, TradingEnvironment, StrategyName
from tsxbot.data.market_data import Tick
from tsxbot.execution.engine import ExecutionEngine
from tsxbot.risk.risk_governor import RiskGovernor
from tsxbot.strategies.orb import ORBStrategy
from tsxbot.time.session_manager import SessionManager


@dataclass
class TradeResult:
    """Result of a single trade."""
    entry_fill: Fill
    exit_fill: Fill
    
    @property
    def pnl(self) -> Decimal:
        """Calculate PnL from actual fills."""
        qty = abs(self.entry_fill.qty)
        if self.entry_fill.side == OrderSide.BUY:
            # Long: profit when exit > entry
            price_diff = self.exit_fill.price - self.entry_fill.price
        else:
            # Short: profit when entry > exit  
            price_diff = self.entry_fill.price - self.exit_fill.price
        
        # ES: $50 per point (1 point = 4 ticks @ $12.50/tick)
        return price_diff * Decimal("50") * qty
    
    @property
    def is_win(self) -> bool:
        return self.pnl > 0
    
    @property
    def entry_price(self) -> Decimal:
        return self.entry_fill.price
    
    @property
    def exit_price(self) -> Decimal:
        return self.exit_fill.price
    
    @property
    def side(self) -> OrderSide:
        return self.entry_fill.side


@dataclass
class DayResult:
    """Result of a single trading day."""
    day_number: int
    trades: list[TradeResult] = field(default_factory=list)
    daily_pnl: Decimal = Decimal("0")
    high_water_mark: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    max_intraday_drawdown: Decimal = Decimal("0")  # NEW: Track intraday DD
    hit_drawdown_limit: bool = False
    
    @property
    def num_trades(self) -> int:
        return len(self.trades)
    
    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.is_win)
    
    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.wins / len(self.trades) * 100


@dataclass
class SimulationResult:
    """Aggregate results of the Monte Carlo simulation."""
    days: list[DayResult] = field(default_factory=list)
    starting_balance: Decimal = Decimal("50000")
    max_drawdown_limit: Decimal = Decimal("5000")
    
    @property
    def total_trades(self) -> int:
        return sum(d.num_trades for d in self.days)
    
    @property
    def total_wins(self) -> int:
        return sum(d.wins for d in self.days)
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_wins / self.total_trades * 100
    
    @property
    def total_pnl(self) -> Decimal:
        return sum(d.daily_pnl for d in self.days)
    
    @property
    def expectancy(self) -> Decimal:
        if self.total_trades == 0:
            return Decimal("0")
        return self.total_pnl / self.total_trades
    
    @property
    def max_drawdown(self) -> Decimal:
        """Max drawdown across all days (closed-trade equity)."""
        return max((d.max_drawdown for d in self.days), default=Decimal("0"))
    
    @property
    def max_intraday_drawdown(self) -> Decimal:
        """Max intraday drawdown (including open positions)."""
        return max((d.max_intraday_drawdown for d in self.days), default=Decimal("0"))
    
    @property
    def survived(self) -> bool:
        return not any(d.hit_drawdown_limit for d in self.days)
    
    @property
    def days_survived(self) -> int:
        for i, d in enumerate(self.days):
            if d.hit_drawdown_limit:
                return i + 1
        return len(self.days)


def generate_minute_bars(
    day_type: str,
    base_price: Decimal = Decimal("5000.00"),
    num_bars: int = 390,
    tick_size: Decimal = Decimal("0.25")
) -> list[Decimal]:
    """Generate simulated minute bar close prices."""
    prices = [base_price]
    price = float(base_price)
    
    # Realistic intraday volatility for ES (roughly 10-20 points range per day)
    if day_type == "trending_up":
        drift = 0.00005
        volatility = 0.0002
    elif day_type == "trending_down":
        drift = -0.00005
        volatility = 0.0002
    else:  # choppy
        drift = 0.0
        volatility = 0.00025
    
    for _ in range(num_bars - 1):
        change = random.gauss(drift, volatility)
        price = price * (1 + change)
        ticks = round(price / float(tick_size))
        price = ticks * float(tick_size)
        prices.append(Decimal(str(price)))
    
    return prices


def create_test_config() -> AppConfig:
    """Create a config for simulation."""
    return AppConfig(
        environment=EnvironmentConfig(
            dry_run=False,
            broker_mode=BrokerMode.SIM
        ),
        symbols=SymbolsConfig(
            primary="ES",
            micros="MES",
            es=SymbolSpecConfig(
                tick_size=Decimal("0.25"),
                tick_value=Decimal("12.50"),
                contract_id_prefix="CON.F.US.ES"
            ),
            mes=SymbolSpecConfig(
                tick_size=Decimal("0.25"),
                tick_value=Decimal("1.25"),
                contract_id_prefix="CON.F.US.MES"
            )
        ),
        session=SessionConfig(
            timezone="America/New_York",
            rth_start="09:30",
            rth_end="16:00",
            flatten_time="15:55",
            trading_days=[0, 1, 2, 3, 4]
        ),
        risk=RiskConfig(
            daily_loss_limit_usd=Decimal("2000"),
            max_loss_limit_usd=Decimal("5000"),
            max_trades_per_day=5,
            max_risk_per_trade_usd=Decimal("500"),
            max_contracts_es=2,
            max_contracts_mes=10,
            kill_switch=False
        ),
        execution=ExecutionConfig(
            bracket=BracketConfig(
                stop_ticks_default=8,  # 2 points = $100 risk
                target_ticks_default=16  # 4 points = $200 target
            )
        ),
        strategy=StrategyConfig(
            active=StrategyName.ORB,
            orb=ORBStrategyConfig(
                opening_range_minutes=15,
                breakout_buffer_ticks=2,
                stop_ticks=8,
                target_ticks=16,
                max_trades=2
            )
        ),
        journal=JournalConfig(
            database_path=":memory:"
        )
    )


class MonteCarloSimulator:
    """Runs Monte Carlo simulation with CORRECT fill price tracking."""
    
    def __init__(self, config: AppConfig, starting_balance: Decimal = Decimal("50000")):
        self.config = config
        self.starting_balance = starting_balance
        self.result = SimulationResult(
            starting_balance=starting_balance,
            max_drawdown_limit=config.risk.max_loss_limit_usd
        )
        
        # Track fills for each day
        self.entry_fills: list[Fill] = []
        self.exit_fills: list[Fill] = []
        
    async def simulate_day(self, day_number: int) -> DayResult:
        """Simulate a single trading day."""
        # Reset fill tracking
        self.entry_fills = []
        self.exit_fills = []
        
        # Choose day type
        day_types = ["trending_up", "trending_down", "choppy"]
        weights = [0.3, 0.3, 0.4]
        day_type = random.choices(day_types, weights=weights)[0]
        
        # Generate minute bars
        base_price = Decimal("5000.00") + Decimal(str(random.uniform(-50, 50)))
        prices = generate_minute_bars(day_type, base_price)
        
        # Setup components
        broker = SimBroker(self.config.symbols)
        session = SessionManager(self.config.session)
        risk = RiskGovernor(self.config.risk, self.config.symbols)
        
        # Track balance
        current_balance = self.starting_balance + sum(d.daily_pnl for d in self.result.days)
        risk.update_account_status(current_balance, Decimal("0"))
        
        strategy = ORBStrategy(self.config, session)
        engine = ExecutionEngine(
            broker, risk, self.config.symbols,
            journal=None, session_manager=session, dry_run=False
        )
        
        # Register fill callback to track ACTUAL fills
        async def on_fill(fill: Fill):
            # Determine if this is entry or exit based on position before fill
            pos_before = await broker.get_position(fill.symbol)
            if pos_before.qty == 0:
                # Was flat, this is an entry
                self.entry_fills.append(fill)
            else:
                # Had position, this is an exit
                self.exit_fills.append(fill)
        
        broker.add_fill_callback(on_fill)
        
        # Track intraday equity for drawdown
        day_result = DayResult(day_number=day_number)
        prior_balance = current_balance
        peak_equity = prior_balance
        max_intraday_dd = Decimal("0")
        
        # Override session for simulation
        session.is_rth = lambda dt=None: True
        session.is_trading_allowed = lambda: True
        session.is_trading_day = lambda dt=None: True
        
        # Process each minute
        sim_time = datetime(2026, 1, 1, 9, 30)
        
        for i, price in enumerate(prices):
            tick = Tick(
                symbol="ES",
                timestamp=sim_time,
                price=price,
                volume=random.randint(100, 1000)
            )
            
            # Process tick through broker
            await broker.process_tick(tick)
            
            # Calculate current equity (cash + open position P&L)
            position = await broker.get_position("ES")
            open_pnl = Decimal("0")
            if position.qty != 0:
                # Calculate unrealized P&L
                if position.qty > 0:  # Long
                    open_pnl = (price - position.avg_price) * Decimal("50") * abs(position.qty)
                else:  # Short
                    open_pnl = (position.avg_price - price) * Decimal("50") * abs(position.qty)
            
            current_equity = prior_balance + open_pnl
            
            # Track peak and drawdown
            peak_equity = max(peak_equity, current_equity)
            intraday_dd = peak_equity - current_equity
            max_intraday_dd = max(max_intraday_dd, intraday_dd)
            
            # Check intraday drawdown limit
            if intraday_dd >= self.config.risk.max_loss_limit_usd:
                day_result.hit_drawdown_limit = True
                break
            
            # Generate signals
            signals = strategy.on_tick(tick)
            for signal in signals:
                await engine.process_signal(signal)
            
            sim_time += timedelta(minutes=1)
        
        # Close any open position at end of day
        position = await broker.get_position("ES")
        if position.qty != 0:
            from tsxbot.broker.models import OrderRequest, OrderType
            close_req = OrderRequest(
                symbol="ES",
                side=OrderSide.SELL if position.qty > 0 else OrderSide.BUY,
                qty=abs(position.qty),
                type=OrderType.MARKET
            )
            await broker.place_order(close_req)
            # Process one more tick to fill the close
            await broker.process_tick(Tick("ES", prices[-1], 100, datetime.now()))
        
        # Match entry/exit fills to create trades
        trades: list[TradeResult] = []
        for i, entry_fill in enumerate(self.entry_fills):
            if i < len(self.exit_fills):
                trades.append(TradeResult(
                    entry_fill=entry_fill,
                    exit_fill=self.exit_fills[i]
                ))
        
        # Calculate results
        day_result.trades = trades
        day_result.daily_pnl = sum(t.pnl for t in trades)
        day_result.max_intraday_drawdown = max_intraday_dd
        
        # Track closed-trade drawdown
        new_balance = prior_balance + day_result.daily_pnl
        prior_hwm = max((d.high_water_mark for d in self.result.days), default=self.starting_balance)
        day_result.high_water_mark = max(prior_hwm, new_balance)
        day_result.max_drawdown = day_result.high_water_mark - new_balance
        
        if day_result.max_drawdown >= self.config.risk.max_loss_limit_usd:
            day_result.hit_drawdown_limit = True
        
        return day_result
    
    async def run(self, num_days: int = 100, verbose: bool = False) -> SimulationResult:
        """Run the full Monte Carlo simulation."""
        print(f"\n{'='*70}")
        print(f"MONTE CARLO SIMULATION - ORB STRATEGY (FIXED)")
        print(f"{'='*70}")
        print(f"Starting Balance: ${self.starting_balance:,.2f}")
        print(f"Max Drawdown Limit: ${self.config.risk.max_loss_limit_usd:,.2f}")
        print(f"Days to Simulate: {num_days}")
        print(f"Position Size: 1 ES contract (FIXED)")
        print(f"Stop: 8 ticks ($100) | Target: 16 ticks ($200)")
        print(f"{'='*70}\n")
        
        for day in range(1, num_days + 1):
            day_result = await self.simulate_day(day)
            self.result.days.append(day_result)
            
            # Verbose trade logging
            if verbose and day_result.trades:
                for trade in day_result.trades:
                    print(f"  Trade: {trade.side.value} @ ${trade.entry_price} → ${trade.exit_price} = ${trade.pnl:,.2f}")
            
            # Progress output every 10 days
            if day % 10 == 0 or day_result.hit_drawdown_limit:
                running_balance = self.starting_balance + self.result.total_pnl
                print(f"Day {day:3d}: PnL ${day_result.daily_pnl:>8,.2f} | "
                      f"Balance ${running_balance:>10,.2f} | "
                      f"Trades: {day_result.num_trades} | "
                      f"Intraday DD: ${day_result.max_intraday_drawdown:>8,.2f} | "
                      f"{'⚠️ LIMIT HIT' if day_result.hit_drawdown_limit else '✓'}")
            
            if day_result.hit_drawdown_limit:
                print(f"\n🛑 SIMULATION STOPPED: Drawdown limit hit on day {day}")
                break
        
        return self.result


def print_results(result: SimulationResult):
    """Print simulation results."""
    print(f"\n{'='*70}")
    print(f"SIMULATION RESULTS")
    print(f"{'='*70}")
    
    print(f"\n📊 PERFORMANCE METRICS")
    print(f"   Days Simulated:    {len(result.days)}")
    print(f"   Total Trades:      {result.total_trades}")
    print(f"   Winning Trades:    {result.total_wins}")
    print(f"   Win Rate:          {result.win_rate:.1f}%")
    
    print(f"\n💰 P&L SUMMARY")
    print(f"   Starting Balance:  ${result.starting_balance:,.2f}")
    print(f"   Total P&L:         ${result.total_pnl:,.2f}")
    print(f"   Final Balance:     ${result.starting_balance + result.total_pnl:,.2f}")
    
    print(f"\n📉 RISK METRICS")
    print(f"   Max Drawdown (Closed):  ${result.max_drawdown:,.2f}")
    print(f"   Max Drawdown (Intraday): ${result.max_intraday_drawdown:,.2f}")
    print(f"   Drawdown Limit:         ${result.max_drawdown_limit:,.2f}")
    
    print(f"\n🎯 EXPECTANCY")
    print(f"   Avg $ per Trade:   ${result.expectancy:,.2f}")
    
    # Survival status
    print(f"\n{'='*70}")
    if result.survived:
        print(f"✅ SURVIVED: Strategy survived all {len(result.days)} days!")
        print(f"   Did NOT hit the max drawdown limit of ${result.max_drawdown_limit:,.2f}")
    else:
        print(f"❌ BLOWN: Strategy hit max drawdown on day {result.days_survived}")
        print(f"   Failed to survive the full simulation period")
    print(f"{'='*70}\n")
    
    # Trade distribution
    if result.total_trades > 0:
        all_pnls = [t.pnl for d in result.days for t in d.trades]
        if all_pnls:
            print(f"📈 TRADE DISTRIBUTION")
            print(f"   Best Trade:        ${max(all_pnls):,.2f}")
            print(f"   Worst Trade:       ${min(all_pnls):,.2f}")
            if len(all_pnls) > 1:
                print(f"   Std Dev:           ${statistics.stdev([float(p) for p in all_pnls]):,.2f}")
            
            # SANITY CHECK
            print(f"\n🔍 SANITY CHECK")
            print(f"   Expected max win:  ~$200 (16 tick target)")
            print(f"   Expected max loss: ~$100 (8 tick stop)")
            if max(all_pnls) > Decimal("250"):
                print(f"   ⚠️  WARNING: Max win ${max(all_pnls):,.2f} exceeds target!")
            if min(all_pnls) < Decimal("-150"):
                print(f"   ⚠️  WARNING: Max loss ${min(all_pnls):,.2f} exceeds stop!")


async def main():
    """Run the Monte Carlo simulation."""
    config = create_test_config()
    simulator = MonteCarloSimulator(config)
    
    # Run with verbose trade logging for first 5 days
    result = await simulator.run(num_days=100, verbose=False)
    print_results(result)
    
    # Save results
    with open("simulation_results_fixed.txt", "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("MONTE CARLO SIMULATION RESULTS - ORB STRATEGY (FIXED)\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Days Simulated:    {len(result.days)}\n")
        f.write(f"Total Trades:      {result.total_trades}\n")
        f.write(f"Winning Trades:    {result.total_wins}\n")
        f.write(f"Win Rate:          {result.win_rate:.1f}%\n\n")
        f.write(f"Starting Balance:  ${result.starting_balance:,.2f}\n")
        f.write(f"Total P&L:         ${result.total_pnl:,.2f}\n")
        f.write(f"Final Balance:     ${result.starting_balance + result.total_pnl:,.2f}\n\n")
        f.write(f"Max Drawdown (Closed):  ${result.max_drawdown:,.2f}\n")
        f.write(f"Max Drawdown (Intraday): ${result.max_intraday_drawdown:,.2f}\n")
        f.write(f"Drawdown Limit:         ${result.max_drawdown_limit:,.2f}\n\n")
        f.write(f"EXPECTANCY:        ${result.expectancy:,.2f} per trade\n\n")
        f.write(f"SURVIVED: {'YES' if result.survived else 'NO - hit drawdown on day ' + str(result.days_survived)}\n")
    
    print("\nResults saved to simulation_results_fixed.txt")
    
    return result


if __name__ == "__main__":
    asyncio.run(main())
