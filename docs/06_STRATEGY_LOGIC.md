# Strategy Logic & Implementation Guide

This document explains the internal logic, indicators, and rules for the strategies available in `tsxbot`.

## 1. EMA Cloud (Ripster Clouds)
**Status:** Active | **Type:** Trend Following | **Timeframe:** 10-minute bars

This strategy is designed to catch large trend moves by identifying alignment between short-term momentum and long-term trend.

### Indicators
- **Fast Cloud:** 5 EMA and 12 EMA
- **Trend Cloud:** 34 EMA and 50 EMA
- **Market Bias:**
  - **Bullish:** Fast Cloud > Trend Cloud (Price > 5 > 12 > 34 > 50)
  - **Bearish:** Fast Cloud < Trend Cloud (Price < 5 < 12 < 34 < 50)
  - **Neutral:** Mixed EMAs or clouds overlapping (No Trading)

### Entry Logic
1.  **Trend Establishment:** The strategy waits for the "Trend Cloud" (34/50) to fully separate from the "Fast Cloud" (5/12).
2.  **Pullback:** Once a trend is established, it waits for price to pull back and *touch* the Fast Cloud (5-12 EMA area) without breaking the Trend Cloud.
3.  **Trigger:**
    - **Long:** Bias is Bullish. Price dips into 5-12 EMA zone.
    - **Short:** Bias is Bearish. Price rallies into 5-12 EMA zone.
4.  **Confirmation:** The pullback bar must close, and the next bar must respect the trend direction.

### Exit Logic
- **Stop Loss:** Placed just beyond the Trend Cloud (34/50 EMA). If price breaks the long-term trend, the trade is invalid.
- **Take Profit:** Open-ended trend following. Position is held as long as the 5 EMA stays above the 12 EMA (for longs).
- **Hard Exit:** Violation of the 12 EMA (Fast Cloud bottom) closes the trade to lock in profits.

### Performance Note
In our 6-month AI backtest, this strategy had a **100% win rate** on 4 trades, generating **$81k profit**. This is because it is extremely selective, only trading when the trend is undeniable, and riding it for hundreds of points.

---

## 2. VWAP Bounce
**Status:** Active | **Type:** Reversion to Mean (in Trend) | **Timeframe:** Tick-based (1-minute inputs)

This strategy trades pullbacks to the Volume Weighted Average Price (VWAP) within an intraday trend.

### Indicators
- **VWAP:** Standard intraday VWAP.
- **Trend Detection:** Checks if price has been predominantly above or below VWAP for the last 50 ticks.

### Entry Logic
1.  **Trend Filter:**
    - **Bullish:** Price has been above VWAP for >50 ticks.
    - **Bearish:** Price has been below VWAP for >50 ticks.
2.  **The Bounce:**
    - **Long:** Price drops to within 3 ticks of VWAP.
    - **Short:** Price rallies to within 3 ticks of VWAP.
3.  **Rejection:** The price must "reject" the level (touch it and immediately move away) to trigger an entry.

### Exit Logic
- **Stop Loss:** 6 ticks (fixed tight stop).
- **Take Profit:** 12 ticks (2:1 Reward/Risk ratio).

---

## 3. Mean Reversion (Session Fade)
**Status:** Active | **Type:** Counter-Trend Scalp | **Timeframe:** Tick-based

This strategy fades the edges of the daily range (Session High/Low) when RSI is overextended.

### Indicators
- **RSI:** 14-period Relative Strength Index.
- **Session High/Low:** Tracks the highest and lowest price of the current trading day.

### Entry Logic
- **Long:**
  - Price is within 8 ticks of **Session Low**.
  - RSI is **Oversold (< 30)**.
- **Short:**
  - Price is within 8 ticks of **Session High**.
  - RSI is **Overbought (> 70)**.

### Exit Logic
- **Stop Loss:** 8 ticks.
- **Take Profit:** 8 ticks (Scalping for small, frequent wins).

### Performance Note
This strategy is "choppier" than EMA Cloud because it fights the trend. It requires a ranging market to work well. In trending markets (like the one EMA Cloud exploited), Mean Reversion often gets stopped out.

---

## 4. Placeholders
The following strategies are currently defined but have no active logic (returning 0 trades by design):

- **Sweep Reclaim:** Intended to trade liquidity sweeps of key levels. Logic currently empty.
- **BOS Pullback:** Intended to trade "Break of Structure" retests. Logic currently empty.
