"""HTML Report Generator for Backtest Results.

Generates interactive HTML reports with charts and tables.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.backtest.results import BacktestResult

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate HTML backtest reports with charts."""

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def generate(self, result: BacktestResult, filename: str = None) -> str:
        """
        Generate HTML report from backtest result.

        Returns path to generated report.
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"backtest_{result.strategy}_{timestamp}.html"

        output_path = self.output_dir / filename

        html = self._build_html(result)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info(f"Report generated: {output_path}")
        return str(output_path)

    def _build_html(self, result: BacktestResult) -> str:
        """Build complete HTML document."""
        # Build equity curve data
        equity_data = self._build_equity_curve(result)

        # Build trade distribution data
        trade_dist = self._build_trade_distribution(result)

        # Build regime performance data
        regime_data = self._build_regime_data(result)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest Report - {result.strategy}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            margin-bottom: 30px;
            color: #00d4ff;
            font-size: 2.5rem;
        }}
        .header {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .header h2 {{
            color: #00d4ff;
            margin-bottom: 10px;
        }}
        .header p {{
            color: #888;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        .metric-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.1);
            transition: transform 0.2s;
        }}
        .metric-card:hover {{
            transform: translateY(-3px);
        }}
        .metric-value {{
            font-size: 2rem;
            font-weight: bold;
            color: #00d4ff;
        }}
        .metric-value.positive {{ color: #00ff88; }}
        .metric-value.negative {{ color: #ff4444; }}
        .metric-label {{
            color: #888;
            margin-top: 5px;
        }}
        .chart-container {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .chart-container h3 {{
            color: #00d4ff;
            margin-bottom: 15px;
        }}
        .chart-wrapper {{
            height: 300px;
        }}
        .regime-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        .regime-table th, .regime-table td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        .regime-table th {{
            color: #00d4ff;
        }}
        .ai-insight {{
            background: linear-gradient(135deg, rgba(0,212,255,0.1) 0%, rgba(0,255,136,0.1) 100%);
            border-radius: 12px;
            padding: 20px;
            margin-top: 20px;
            border: 1px solid rgba(0,212,255,0.3);
        }}
        .ai-insight h3 {{
            color: #00d4ff;
            margin-bottom: 10px;
        }}
        .trades-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }}
        .trades-table th, .trades-table td {{
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        .trades-table th {{
            color: #00d4ff;
            background: rgba(0,0,0,0.2);
        }}
        .win {{ color: #00ff88; }}
        .loss {{ color: #ff4444; }}
        footer {{
            text-align: center;
            margin-top: 30px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Backtest Report</h1>

        <div class="header">
            <h2>{result.strategy}</h2>
            <p>Period: {result.start_date.strftime("%Y-%m-%d")} to {result.end_date.strftime("%Y-%m-%d")} | Symbol: {result.symbol}</p>
        </div>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-value">{result.total_trades}</div>
                <div class="metric-label">Total Trades</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{result.win_rate:.1%}</div>
                <div class="metric-label">Win Rate</div>
            </div>
            <div class="metric-card">
                <div class="metric-value {"positive" if result.net_pnl >= 0 else "negative"}">${result.net_pnl:.2f}</div>
                <div class="metric-label">Net P&L</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{result.profit_factor:.2f}</div>
                <div class="metric-label">Profit Factor</div>
            </div>
            <div class="metric-card">
                <div class="metric-value negative">-${result.max_drawdown:.2f}</div>
                <div class="metric-label">Max Drawdown</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{result.winners} / {result.losers}</div>
                <div class="metric-label">Winners / Losers</div>
            </div>
        </div>

        <div class="chart-container">
            <h3>📈 Equity Curve</h3>
            <div class="chart-wrapper">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <div class="chart-container">
            <h3>📊 P&L by Regime</h3>
            <table class="regime-table">
                <thead>
                    <tr>
                        <th>Regime</th>
                        <th>Trades</th>
                        <th>Win Rate</th>
                        <th>P&L</th>
                    </tr>
                </thead>
                <tbody>
                    {regime_data}
                </tbody>
            </table>
        </div>

        {self._build_ai_insight(result)}

        <div class="chart-container">
            <h3>📝 Trade Log (Last 20)</h3>
            <table class="trades-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Direction</th>
                        <th>Entry</th>
                        <th>Exit</th>
                        <th>P&L</th>
                        <th>Regime</th>
                    </tr>
                </thead>
                <tbody>
                    {self._build_trade_rows(result)}
                </tbody>
            </table>
        </div>

        <footer>
            Generated by TSXBot | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        </footer>
    </div>

    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {equity_data["labels"]},
                datasets: [{{
                    label: 'Equity Curve',
                    data: {equity_data["values"]},
                    borderColor: '#00d4ff',
                    backgroundColor: 'rgba(0, 212, 255, 0.1)',
                    fill: true,
                    tension: 0.4
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#888' }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#888' }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""

        return html

    def _build_equity_curve(self, result: BacktestResult) -> dict:
        """Build equity curve data for chart."""
        labels = []
        values = []
        running_pnl = 0

        for trade in result.trades:
            running_pnl += float(trade.pnl_dollars)
            labels.append(trade.exit_time.strftime("%m/%d"))
            values.append(round(running_pnl, 2))

        return {"labels": labels, "values": values}

    def _build_trade_distribution(self, result: BacktestResult) -> dict:
        """Build trade distribution data."""
        wins = result.winners
        losses = result.losers
        return {"wins": wins, "losses": losses}

    def _build_regime_data(self, result: BacktestResult) -> str:
        """Build regime performance table rows."""
        rows = []
        for regime, stats in result.regime_performance.items():
            pnl_class = "win" if stats["pnl"] >= 0 else "loss"
            rows.append(f'''
                <tr>
                    <td>{regime.title()}</td>
                    <td>{stats["trades"]}</td>
                    <td>{stats["win_rate"]:.0%}</td>
                    <td class="{pnl_class}">${stats["pnl"]:.2f}</td>
                </tr>
            ''')
        return "\n".join(rows) if rows else "<tr><td colspan='4'>No trades</td></tr>"

    def _build_ai_insight(self, result: BacktestResult) -> str:
        """Build AI insight section."""
        if not result.ai_recommendation:
            return ""
        return f"""
        <div class="ai-insight">
            <h3>🧠 AI Recommendation</h3>
            <p>{result.ai_recommendation}</p>
        </div>
        """

    def _build_trade_rows(self, result: BacktestResult) -> str:
        """Build trade log table rows."""
        rows = []
        for trade in result.trades[-20:]:  # Last 20 trades
            pnl_class = "win" if trade.is_winner else "loss"
            rows.append(f'''
                <tr>
                    <td>{trade.entry_time.strftime("%m/%d %H:%M")}</td>
                    <td>{trade.direction}</td>
                    <td>${trade.entry_price:.2f}</td>
                    <td>${trade.exit_price:.2f}</td>
                    <td class="{pnl_class}">${trade.pnl_dollars:.2f}</td>
                    <td>{trade.regime}</td>
                </tr>
            ''')
        return "\n".join(rows) if rows else "<tr><td colspan='6'>No trades</td></tr>"
