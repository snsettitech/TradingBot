"""TSXBot CLI."""

import asyncio
import logging
from datetime import datetime

import click

from tsxbot.app import TSXBotApp


@click.group()
def cli():
    """TSXBot Command Line Interface."""
    pass


@cli.command()
@click.option(
    "--config",
    type=click.Path(exists=True),
    default="config/config.yaml",
    help="Path to configuration file",
)
@click.option("--strategy", help="Override active strategy")
@click.option("--dry-run", is_flag=True, help="Force DRY_RUN mode")
def run(config, strategy, dry_run):
    """Start the trading bot."""
    # Windows-specific event loop policy if needed, but simple run usually fine
    try:
        app = TSXBotApp(config_path=config, strategy_name=strategy, dry_run=dry_run)
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass  # Graceful exit handled by app finally block usually
    except Exception as e:
        click.echo(f"Fatal error: {e}", err=True)
        # Log basic info if logging setup failed
        import traceback

        traceback.print_exc()


@cli.command()
@click.option(
    "--config",
    type=click.Path(exists=True),
    default="config/config.yaml",
    help="Path to configuration file",
)
def smoke_test(config):
    """Run a smoke test (initialize components and exit)."""
    try:
        app = TSXBotApp(config_path=config, dry_run=True)
        asyncio.run(app.initialize())
        click.echo("Smoke test passed: Components initialized successfully.")
    except Exception as e:
        click.echo(f"Smoke test failed: {e}", err=True)
        exit(1)


@cli.command()
@click.option(
    "--config",
    type=click.Path(exists=True),
    default="config/config.yaml",
    help="Path to configuration file",
)
@click.option(
    "--strategy", default="orb", help="Strategy to backtest (orb, vwap_bounce, mean_reversion)"
)
@click.option("--data", type=click.Path(exists=True), help="Path to CSV data file")
@click.option("--days", default=30, help="Days of data to fetch/generate")
@click.option("--projectx", is_flag=True, help="Use real historical data from ProjectX API")
@click.option("--ai", is_flag=True, help="Enable AI analysis and learning")
@click.option("--report", is_flag=True, help="Generate HTML report with charts")
def backtest(config, strategy, data, days, projectx, ai, report):
    """Run strategy backtest with historical data."""
    from tsxbot.backtest.data_loader import HistoricalDataLoader
    from tsxbot.backtest.engine import BacktestEngine
    from tsxbot.config_loader import load_config
    from tsxbot.strategies.registry import get_strategy
    from tsxbot.time.session_manager import SessionManager

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Load config
    cfg = load_config(config)

    # Override strategy
    from tsxbot.constants import StrategyName

    try:
        cfg.strategy.active = StrategyName(strategy.lower())
    except ValueError:
        click.echo(f"Unknown strategy: {strategy}")
        return

    session = SessionManager(cfg.session)
    strat = get_strategy(cfg, session)

    # Load or generate data
    loader = HistoricalDataLoader(symbol="ES")

    if data:
        click.echo(f"Loading data from {data}...")
        bars = loader.load_csv(data)
    elif projectx:
        click.echo(f"Fetching {days} days of real data from ProjectX API...")
        bars = loader.load_from_projectx(
            contract_id=cfg.projectx.contract_id
            if hasattr(cfg.projectx, "contract_id")
            else "CON.F.US.EP.H26",
            days=days,
        )
        if not bars:
            click.echo("Failed to load data from ProjectX. Falling back to sample data.")
            bars = loader.generate_sample_data(start=datetime(2024, 1, 2, 9, 30), days=days)
    else:
        click.echo(f"Generating {days} days of sample data...")
        bars = loader.generate_sample_data(start=datetime(2024, 1, 2, 9, 30), days=days)

    # Filter to RTH only
    bars = loader.filter_rth(bars)
    click.echo(f"Using {len(bars)} RTH bars")

    # Setup AI if requested
    ai_advisor = None
    if ai and cfg.openai.enabled:
        from tsxbot.ai.advisor import AIAdvisor

        ai_advisor = AIAdvisor(cfg.openai, dry_run=True)
        click.echo("AI analysis enabled")

    # Run backtest
    engine = BacktestEngine(config=cfg, strategy=strat, ai_advisor=ai_advisor)
    engine.load_data(bars)

    click.echo("\nRunning backtest...")
    if ai:
        result = asyncio.run(engine.run_with_ai())
    else:
        result = engine.run()

    # Print results
    click.echo("\n" + "=" * 60)
    click.echo("BACKTEST RESULTS")
    click.echo("=" * 60)
    click.echo(result.summary())
    click.echo("=" * 60)

    # Generate HTML report if requested
    if report:
        from pathlib import Path

        from tsxbot.backtest.report import ReportGenerator

        generator = ReportGenerator(output_dir="reports")
        report_path = generator.generate(result)
        abs_path = Path(report_path).resolve()
        click.echo(f"\n📊 HTML Report: {abs_path}")

        # Try to open in browser (use proper Windows path)
        import webbrowser

        webbrowser.open(abs_path.as_uri())


@cli.group()
def params():
    """Manage learned strategy parameters."""
    pass


@params.command()
def show():
    """Show all learned parameters and AI recommendations."""
    from tsxbot.learning.param_store import ParameterStore

    store = ParameterStore()
    params = store.get_all_parameters()

    if not params:
        click.echo("No learned parameters found.")
        return

    for strategy, regimes in params.items():
        click.echo(f"\nStrategy: {strategy.upper()}")
        for regime, p in regimes.items():
            trust_icon = "✅" if p.is_trusted() else "⚠️"
            click.echo(
                f"  {trust_icon} {regime}: {p.win_rate:.1%} WR, {p.profit_factor:.2f} PF ({p.sample_size} trades)"
            )
            if p.ai_recommendation:
                click.echo(f"     💡 AI: {p.ai_recommendation}")
            click.echo(
                f"     [Params] Range:{p.opening_range_minutes}m Target:{p.profit_target_ticks} Stop:{p.stop_loss_ticks}"
            )


@params.command()
@click.option("--strategy", required=True)
@click.option("--regime", required=True)
def export(strategy, regime):
    """Export trusted parameters as YAML for config."""
    import yaml

    from tsxbot.learning.param_store import ParameterStore

    store = ParameterStore()
    params = store.export_for_config(strategy, regime)

    if not params:
        click.echo(f"No trusted parameters found for {strategy}/{regime}")
        return

    click.echo(f"\n# Recommended config for {strategy} in {regime}:")
    click.echo(yaml.dump(params, default_flow_style=False))


if __name__ == "__main__":
    cli()

# Alias for __main__.py
main = cli
