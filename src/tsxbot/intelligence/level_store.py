"""LevelStore - Computes and tracks key price levels.

Levels tracked:
- PDH/PDL/PDC: Prior Day High/Low/Close (RTH only)
- ORH/ORL: Opening Range High/Low (first 30 minutes of RTH)
- VWAP: Volume Weighted Average Price (anchored to RTH start)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tsxbot.data.market_data import Tick

logger = logging.getLogger(__name__)


@dataclass
class SessionLevels:
    """Computed levels for a trading session."""
    
    date: date
    symbol: str
    
    # Prior Day Levels
    pdh: Decimal | None = None
    pdl: Decimal | None = None
    pdc: Decimal | None = None
    
    # Opening Range (first 30 min of RTH)
    orh: Decimal | None = None
    orl: Decimal | None = None
    or_formed: bool = False
    
    # VWAP
    vwap: Decimal | None = None
    
    # Internal VWAP calculation state
    _cumulative_pv: Decimal = field(default=Decimal("0"), repr=False)
    _cumulative_volume: int = field(default=0, repr=False)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "date": self.date.isoformat(),
            "symbol": self.symbol,
            "pdh": str(self.pdh) if self.pdh else None,
            "pdl": str(self.pdl) if self.pdl else None,
            "pdc": str(self.pdc) if self.pdc else None,
            "orh": str(self.orh) if self.orh else None,
            "orl": str(self.orl) if self.orl else None,
            "vwap": str(self.vwap) if self.vwap else None,
        }


class LevelStore:
    """
    Computes and stores key price levels for trading sessions.
    
    Usage:
        store = LevelStore(rth_start=time(9, 30), rth_end=time(16, 0))
        store.set_prior_day_levels(pdh, pdl, pdc)
        
        for tick in ticks:
            store.on_tick(tick)
        
        levels = store.get_current_levels()
    """
    
    def __init__(
        self,
        rth_start: time = time(9, 30),
        rth_end: time = time(16, 0),
        opening_range_minutes: int = 30,
    ):
        self.rth_start = rth_start
        self.rth_end = rth_end
        self.opening_range_minutes = opening_range_minutes
        self._or_end_time = self._add_minutes(rth_start, opening_range_minutes)
        
        # Current session state
        self._current_date: date | None = None
        self._levels: SessionLevels | None = None
        
        # Intraday tracking
        self._session_high: Decimal = Decimal("-Infinity")
        self._session_low: Decimal = Decimal("Infinity")
        self._last_price: Decimal | None = None
    
    def _add_minutes(self, t: time, minutes: int) -> time:
        """Add minutes to a time object."""
        dt = datetime.combine(date.today(), t) + timedelta(minutes=minutes)
        return dt.time()
    
    def set_prior_day_levels(
        self,
        pdh: Decimal,
        pdl: Decimal,
        pdc: Decimal,
        symbol: str = "ES",
    ) -> None:
        """Set prior day levels before session starts."""
        today = date.today()
        self._current_date = today
        self._levels = SessionLevels(
            date=today,
            symbol=symbol,
            pdh=pdh,
            pdl=pdl,
            pdc=pdc,
        )
        logger.info(f"Set prior day levels: PDH={pdh}, PDL={pdl}, PDC={pdc}")
    
    def on_tick(self, tick: Tick) -> None:
        """Process a new tick and update levels."""
        if self._levels is None:
            # Auto-initialize for today if not set
            self._levels = SessionLevels(date=tick.timestamp.date(), symbol=tick.symbol)
            self._current_date = tick.timestamp.date()
        
        # Check if new day
        tick_date = tick.timestamp.date()
        if tick_date != self._current_date:
            self._rollover_session(tick)
        
        tick_time = tick.timestamp.time()
        
        # Only process RTH ticks
        if not (self.rth_start <= tick_time < self.rth_end):
            return
        
        price = tick.price
        volume = tick.volume or 1
        
        # Update session high/low
        if price > self._session_high:
            self._session_high = price
        if price < self._session_low:
            self._session_low = price
        
        # Opening Range calculation
        if tick_time < self._or_end_time:
            # Still in OR window
            if self._levels.orh is None or price > self._levels.orh:
                self._levels.orh = price
            if self._levels.orl is None or price < self._levels.orl:
                self._levels.orl = price
        elif not self._levels.or_formed:
            # OR just completed
            self._levels.or_formed = True
            logger.info(f"Opening Range formed: ORH={self._levels.orh}, ORL={self._levels.orl}")
        
        # VWAP calculation
        self._levels._cumulative_pv += price * volume
        self._levels._cumulative_volume += volume
        if self._levels._cumulative_volume > 0:
            self._levels.vwap = self._levels._cumulative_pv / self._levels._cumulative_volume
        
        self._last_price = price
    
    def _rollover_session(self, tick: Tick) -> None:
        """Handle day rollover: prior day becomes today's PDH/PDL/PDC."""
        if self._levels is not None and self._last_price is not None:
            # Store prior session as new PDH/PDL/PDC
            pdh = self._session_high if self._session_high != Decimal("-Infinity") else None
            pdl = self._session_low if self._session_low != Decimal("Infinity") else None
            pdc = self._last_price
            
            self._levels = SessionLevels(
                date=tick.timestamp.date(),
                symbol=tick.symbol,
                pdh=pdh,
                pdl=pdl,
                pdc=pdc,
            )
            logger.info(f"Session rollover: PDH={pdh}, PDL={pdl}, PDC={pdc}")
        else:
            self._levels = SessionLevels(date=tick.timestamp.date(), symbol=tick.symbol)
        
        self._current_date = tick.timestamp.date()
        self._session_high = Decimal("-Infinity")
        self._session_low = Decimal("Infinity")
        self._last_price = None
    
    def get_current_levels(self) -> SessionLevels | None:
        """Get current session levels."""
        return self._levels
    
    def get_level_value(self, level_name: str) -> Decimal | None:
        """Get a specific level by name."""
        if self._levels is None:
            return None
        return getattr(self._levels, level_name.lower(), None)
    
    def all_levels_as_dict(self) -> dict[str, Decimal | None]:
        """Get all levels as a dictionary."""
        if self._levels is None:
            return {}
        return {
            "pdh": self._levels.pdh,
            "pdl": self._levels.pdl,
            "pdc": self._levels.pdc,
            "orh": self._levels.orh,
            "orl": self._levels.orl,
            "vwap": self._levels.vwap,
        }
