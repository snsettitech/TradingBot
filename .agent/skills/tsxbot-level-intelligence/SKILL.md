# tsxbot-level-intelligence

This skill defines how key price levels are calculated and how interactions with those levels are classified.

## Level Definitions

### 1. Prior Session Levels (PDC, PDH, PDL)
- **PDH**: Prior Day High.
- **PDL**: Prior Day Low.
- **PDC**: Prior Day Close.
- Basis: RTH (Regular Trading Hours) only.

### 2. Opening Range (ORH, ORL)
- **ORH**: High of the first 30 minutes of RTH.
- **ORL**: Low of the first 30 minutes of RTH.

### 3. Dynamic Levels
- **VWAP**: Volume Weighted Average Price (anchored to RTH start).
- **VP Levels**: (Optional) Point of Control (POC), Value Area High (VAH), Value Area Low (VAL).

## Interaction Classification

- **Touch**: Price comes within 2 ticks of a level.
- **Reject**: Price touches a level and reverses by at least 1 ATR (1-min) within 3 bars.
- **Break-and-Hold**: Price crosses a level and remains on the other side for 3+ consecutive bars.
- **Fakeout-Reclaim**: Price breaks a level, fails to hold (reverses back within 5 bars), and crosses the level again in the opposite direction.
