"""Supabase Client - Singleton database connection.

Provides async-compatible Supabase client for cloud persistence.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import to avoid dependency issues
_client = None


def get_supabase_client():
    """Get or create Supabase client singleton."""
    global _client
    
    if _client is not None:
        return _client
    
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    
    if not url or not key:
        logger.warning("SUPABASE_URL or SUPABASE_KEY not set. Cloud persistence disabled.")
        return None
    
    try:
        from supabase import create_client
        _client = create_client(url, key)
        logger.info("Supabase client initialized")
        return _client
    except ImportError:
        logger.warning("supabase package not installed. Run: pip install supabase")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize Supabase: {e}")
        return None


class SupabaseStore:
    """
    High-level Supabase operations for tsxbot.
    
    Tables:
    - tick_data: Historical tick data
    - levels: Daily computed levels
    - trade_journal: Trade records
    - learned_params: Strategy parameters
    """
    
    def __init__(self):
        self.client = get_supabase_client()
    
    @property
    def is_available(self) -> bool:
        return self.client is not None
    
    # -------------------------------------------------------------------------
    # Levels
    # -------------------------------------------------------------------------
    
    def upsert_levels(self, levels_dict: dict[str, Any]) -> bool:
        """Upsert daily levels."""
        if not self.client:
            return False
        
        try:
            self.client.table("levels").upsert(levels_dict).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to upsert levels: {e}")
            return False
    
    def get_levels(self, symbol: str, date_str: str) -> dict | None:
        """Get levels for a specific date."""
        if not self.client:
            return None
        
        try:
            result = (
                self.client.table("levels")
                .select("*")
                .eq("symbol", symbol)
                .eq("date", date_str)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get levels: {e}")
            return None
    
    # -------------------------------------------------------------------------
    # Trade Journal
    # -------------------------------------------------------------------------
    
    def insert_trade(self, trade_dict: dict[str, Any]) -> bool:
        """Insert a trade record."""
        if not self.client:
            return False
        
        try:
            self.client.table("trade_journal").insert(trade_dict).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to insert trade: {e}")
            return False
    
    def get_trades_by_regime(
        self,
        regime: str,
        limit: int = 100,
    ) -> list[dict]:
        """Get trades filtered by regime."""
        if not self.client:
            return []
        
        try:
            result = (
                self.client.table("trade_journal")
                .select("*")
                .eq("regime", regime)
                .order("timestamp", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data
        except Exception as e:
            logger.error(f"Failed to get trades: {e}")
            return []
    
    # -------------------------------------------------------------------------
    # Learned Parameters
    # -------------------------------------------------------------------------
    
    def upsert_params(self, params_dict: dict[str, Any]) -> bool:
        """Upsert learned parameters."""
        if not self.client:
            return False
        
        try:
            self.client.table("learned_params").upsert(
                params_dict,
                on_conflict="strategy,regime",
            ).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to upsert params: {e}")
            return False
    
    def get_params(self, strategy: str, regime: str) -> dict | None:
        """Get parameters for strategy/regime combination."""
        if not self.client:
            return None
        
        try:
            result = (
                self.client.table("learned_params")
                .select("*")
                .eq("strategy", strategy)
                .eq("regime", regime)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get params: {e}")
            return None
    
    def get_all_params(self, strategy: str | None = None) -> list[dict]:
        """Get all learned parameters, optionally filtered by strategy."""
        if not self.client:
            return []
        
        try:
            query = self.client.table("learned_params").select("*")
            if strategy:
                query = query.eq("strategy", strategy)
            result = query.order("updated_at", desc=True).execute()
            return result.data
        except Exception as e:
            logger.error(f"Failed to get all params: {e}")
            return []
