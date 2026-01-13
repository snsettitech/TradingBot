"""
Live Market Data Stream Verification Script

Connects to ProjectX using credentials from .env file,
subscribes to ES and MES futures, and validates the price format.

CRITICAL CHECK: Prices must look like 5750.25, not 57502500 or 57.50
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Import tsxapipy
try:
    from tsxapipy.api import APIClient as TSXClient
    from tsxapipy.real_time import DataStream
    from tsxapipy.auth import authenticate as tsx_authenticate
except ImportError as e:
    logger.error(f"Failed to import tsxapipy: {e}")
    logger.error("Install with: pip install tsxapipy")
    sys.exit(1)


class StreamVerifier:
    """Verifies live market data stream from ProjectX."""
    
    def __init__(self):
        self.client = None
        self.streams: Dict[str, DataStream] = {}  # contract_id -> stream
        self.latest_quotes = {}  # symbol -> {bid, ask, last, timestamp}
        self.tick_count = 0
        self.errors = []
        
    def authenticate(self) -> tuple:
        """Authenticate with ProjectX API."""
        username = os.getenv("PROJECTX_USERNAME")
        api_key = os.getenv("PROJECTX_API_KEY")
        
        if not username or not api_key:
            raise ValueError(
                "Missing credentials!\n"
                "Set PROJECTX_USERNAME and PROJECTX_API_KEY in .env file"
            )
        
        logger.info(f"Authenticating as {username}...")
        token, token_time = tsx_authenticate(username, api_key)
        
        if not token:
            raise ConnectionError("Authentication failed - check credentials")
            
        logger.info("✓ Authenticated successfully")
        return token, token_time
    
    def connect(self, token: str, token_time):
        """Initialize API client."""
        username = os.getenv("PROJECTX_USERNAME")
        api_key = os.getenv("PROJECTX_API_KEY")
        
        self.client = TSXClient(
            initial_token=token,
            token_acquired_at=token_time,
            reauth_username=username,
            reauth_api_key=api_key
        )
        
        # Get accounts to verify connection
        accounts = self.client.get_accounts()
        if accounts:
            acc_id = getattr(accounts[0], 'id', None) or accounts[0].get('id', 'unknown')
            logger.info(f"✓ Connected to account: {acc_id}")
        else:
            logger.warning("No accounts found")
        
    def _on_quote(self, contract_id: str):
        """Create quote handler for a specific contract."""
        def handler(quote_data: Dict[str, Any]):
            try:
                # ProjectX uses these field names
                bid = quote_data.get('bid') or quote_data.get('bestBid') or quote_data.get('Bid')
                ask = quote_data.get('ask') or quote_data.get('bestAsk') or quote_data.get('Ask')
                last = quote_data.get('lastPrice') or quote_data.get('LastPrice') or quote_data.get('last')
                
                if contract_id not in self.latest_quotes:
                    self.latest_quotes[contract_id] = {}
                
                if bid is not None:
                    self.latest_quotes[contract_id]['bid'] = Decimal(str(bid))
                if ask is not None:
                    self.latest_quotes[contract_id]['ask'] = Decimal(str(ask))
                if last is not None:
                    self.latest_quotes[contract_id]['last'] = Decimal(str(last))
                self.latest_quotes[contract_id]['timestamp'] = datetime.now()
                
                self.tick_count += 1
                
            except Exception as e:
                self.errors.append(f"Quote parse error for {contract_id}: {e}")
                logger.error(f"Error parsing quote: {e}, data: {quote_data}")
        
        return handler
    
    def _on_trade(self, contract_id: str):
        """Create trade handler for a specific contract."""
        def handler(trade_data: Dict[str, Any]):
            try:
                price = trade_data.get('price') or trade_data.get('Price')
                
                if contract_id not in self.latest_quotes:
                    self.latest_quotes[contract_id] = {}
                
                if price is not None:
                    self.latest_quotes[contract_id]['last'] = Decimal(str(price))
                self.latest_quotes[contract_id]['timestamp'] = datetime.now()
                
                self.tick_count += 1
                logger.debug(f"Trade {contract_id}: {price}")
                
            except Exception as e:
                self.errors.append(f"Trade parse error for {contract_id}: {e}")
                logger.error(f"Error parsing trade: {e}, data: {trade_data}")
        
        return handler
        
    def subscribe(self, contract_ids: list[str]):
        """Subscribe to market data for multiple contracts."""
        for contract_id in contract_ids:
            logger.info(f"Creating stream for: {contract_id}")
            
            # Create a stream for each contract with callbacks
            stream = DataStream(
                api_client=self.client,
                contract_id_to_subscribe=contract_id,
                on_quote_callback=self._on_quote(contract_id),
                on_trade_callback=self._on_trade(contract_id),
                on_depth_callback=None,  # Not needed for price verification
                auto_subscribe_quotes=True,
                auto_subscribe_trades=True,
                auto_subscribe_depth=False
            )
            
            self.streams[contract_id] = stream
            logger.info(f"✓ Stream created for {contract_id}")
    
    def start_streams(self):
        """Start all data streams."""
        for contract_id, stream in self.streams.items():
            logger.info(f"Starting stream for {contract_id}...")
            success = stream.start()
            if success:
                logger.info(f"✓ Stream started for {contract_id}")
            else:
                logger.error(f"✗ Failed to start stream for {contract_id}")
        
        # Give streams time to connect
        time.sleep(2)
    
    def stop_streams(self):
        """Stop all data streams."""
        for contract_id, stream in self.streams.items():
            try:
                stream.stop()
                logger.info(f"Stream stopped for {contract_id}")
            except Exception as e:
                logger.error(f"Error stopping stream {contract_id}: {e}")
    
    def validate_price(self, price: Decimal, symbol: str) -> tuple[bool, str]:
        """
        Validate that price looks correct for ES/MES futures.
        
        Expected: ~5750.25 (around 4500-7500 range for 2024-2026)
        Bad: 57502500 (integer cents), 57.50 (scaled down), 0 (missing)
        """
        if price is None:
            return False, "Price is None"
        
        price_float = float(price)
        
        # Check for missing/zero
        if price_float == 0:
            return False, "Price is 0"
        
        # Check for integer scaling issue (price in cents/ticks)
        if price_float > 100000:
            return False, f"Price too high ({price_float}) - likely scaled up (cents?)"
        
        # Check for decimal scaling issue (price divided too much)
        if price_float < 1000:
            return False, f"Price too low ({price_float}) - likely scaled down"
        
        # Check for valid ES/MES range (roughly 4000-7500 for S&P 500 futures in 2024-2026)
        if not (3500 <= price_float <= 8000):
            return False, f"Price {price_float} outside expected range [3500, 8000]"
        
        # Check tick size (should be divisible by 0.25)
        remainder = price_float % 0.25
        if remainder > 0.001:  # Small tolerance for float precision
            return False, f"Price {price_float} not on 0.25 tick increment"
        
        return True, "OK"
    
    def print_prices(self):
        """Print current prices for all subscribed symbols."""
        if not self.latest_quotes:
            print("  [No data yet]")
            return
        
        for symbol, data in sorted(self.latest_quotes.items()):
            bid = data.get('bid', '-')
            ask = data.get('ask', '-')
            last = data.get('last', '-')
            
            # Format prices
            bid_str = f"{bid:.2f}" if isinstance(bid, Decimal) else str(bid)
            ask_str = f"{ask:.2f}" if isinstance(ask, Decimal) else str(ask)
            last_str = f"{last:.2f}" if isinstance(last, Decimal) else str(last)
            
            # Validate
            validation_msgs = []
            if isinstance(last, Decimal):
                ok, msg = self.validate_price(last, symbol)
                if not ok:
                    validation_msgs.append(f"⚠️ {msg}")
            
            status = " ".join(validation_msgs) if validation_msgs else "✓"
            
            # Truncate symbol for display
            symbol_short = symbol[-15:] if len(symbol) > 15 else symbol
            print(f"  {symbol_short:15s} | Bid: {bid_str:>10s} | Ask: {ask_str:>10s} | Last: {last_str:>10s} | {status}")


def main():
    """Run the stream verification."""
    print("\n" + "=" * 70)
    print("PROJECTX LIVE DATA STREAM VERIFICATION")
    print("=" * 70)
    print()
    
    verifier = StreamVerifier()
    
    try:
        # 1. Authenticate
        token, token_time = verifier.authenticate()
        
        # 2. Connect
        verifier.connect(token, token_time)
        
        # 3. Define symbols to subscribe
        # CORRECT contract IDs from ProjectX API search
        symbols = [
            "CON.F.US.EP.H26",   # ES March 2026
            "CON.F.US.MES.H26",  # MES March 2026
        ]
        
        # 4. Create streams and subscribe
        verifier.subscribe(symbols)
        
        # 5. Start streams
        verifier.start_streams()
        
        # 6. Print prices every second for 1 minute
        print("\n" + "-" * 70)
        print("Streaming live prices for 60 seconds...")
        print("Compare these values with your trading platform (TradingView/TopstepX)")
        print("-" * 70 + "\n")
        
        start_time = datetime.now()
        duration = timedelta(seconds=60)
        
        while datetime.now() - start_time < duration:
            elapsed = (datetime.now() - start_time).seconds
            remaining = 60 - elapsed
            
            print(f"\n[{elapsed:02d}s elapsed, {remaining:02d}s remaining] Tick count: {verifier.tick_count}")
            verifier.print_prices()
            
            time.sleep(1)
        
        # 7. Final summary
        print("\n" + "=" * 70)
        print("VERIFICATION SUMMARY")
        print("=" * 70)
        
        print(f"\nTotal ticks received: {verifier.tick_count}")
        print(f"Errors encountered: {len(verifier.errors)}")
        
        if verifier.errors:
            print("\nErrors:")
            for err in verifier.errors[:10]:  # Show first 10
                print(f"  - {err}")
        
        # Validate final prices
        print("\nFinal Price Validation:")
        all_valid = True
        for symbol, data in verifier.latest_quotes.items():
            last = data.get('last')
            if last:
                ok, msg = verifier.validate_price(last, symbol)
                status = "✓ PASS" if ok else f"✗ FAIL: {msg}"
                print(f"  {symbol}: {last:.2f} -> {status}")
                if not ok:
                    all_valid = False
        
        print("\n" + "=" * 70)
        if all_valid and verifier.tick_count > 0:
            print("✅ VERIFICATION PASSED - Prices look correct!")
        elif verifier.tick_count == 0:
            print("⚠️  WARNING: No ticks received - is the market open?")
        else:
            print("❌ VERIFICATION FAILED - Check price scaling!")
        print("=" * 70 + "\n")
        
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        verifier.stop_streams()


if __name__ == "__main__":
    main()
