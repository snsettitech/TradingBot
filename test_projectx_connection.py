"""Test ProjectX Broker Connection.

This script verifies:
1. Authentication with TopstepX API
2. Fetching trading accounts
3. Initializing data streams (market + user)
"""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from tsxbot.config_loader import AppConfig
from tsxbot.constants import TradingEnvironment


async def test_projectx_broker():
    """Test the full ProjectXBroker connection flow."""
    
    print("=" * 60)
    print("ProjectX Broker Connection Test")
    print("=" * 60)
    
    # Step 1: Check credentials
    username = os.environ.get("PROJECTX_USERNAME")
    api_key = os.environ.get("PROJECTX_API_KEY")
    env = os.environ.get("TRADING_ENVIRONMENT", "DEMO")
    
    print(f"\n[1/5] Checking credentials...")
    print(f"      Username: {username}")
    print(f"      API Key: {'*' * 20}...{api_key[-4:] if api_key else 'MISSING'}")
    print(f"      Environment: {env}")
    
    if not username or not api_key:
        print("\n❌ FAILED: Missing credentials in .env file")
        return False
    print("      ✅ Credentials present")
    
    # Step 2: Create config
    print(f"\n[2/5] Creating AppConfig...")
    config = AppConfig()
    config.projectx.username = username
    config.projectx.api_key = api_key
    config.projectx.trading_environment = TradingEnvironment(env.upper())
    print("      ✅ Config created")
    
    # Step 3: Import and create broker
    print(f"\n[3/5] Importing ProjectXBroker...")
    try:
        from tsxbot.broker.projectx import ProjectXBroker, HAS_TSX
        if not HAS_TSX:
            print("      ❌ tsxapipy library not found!")
            return False
        print("      ✅ ProjectXBroker imported")
        
        broker = ProjectXBroker(config)
        print("      ✅ Broker instance created")
    except ImportError as e:
        print(f"      ❌ Import error: {e}")
        return False
    
    # Step 4: Connect
    print(f"\n[4/5] Connecting to ProjectX API...")
    try:
        await broker.connect()
        print(f"      ✅ Connected successfully!")
        print(f"      Account ID: {broker.account_id}")
        print(f"      REST Client: {'Active' if broker.client else 'None'}")
        print(f"      Data Stream: {'Active' if broker.data_stream else 'None'}")
        print(f"      User Stream: {'Active' if broker.user_stream else 'None'}")
    except Exception as e:
        print(f"      ❌ Connection failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 5: Disconnect
    print(f"\n[5/5] Disconnecting...")
    try:
        await broker.disconnect()
        print("      ✅ Disconnected cleanly")
    except Exception as e:
        print(f"      ⚠️  Disconnect issue (non-fatal): {e}")
    
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED - ProjectX connection is working!")
    print("=" * 60)
    
    print("\n📋 NEXT STEPS:")
    print("   1. Update config/config.yaml:")
    print("      - Set broker_mode: 'projectx'")
    print("      - Keep dry_run: true (for safety)")
    print("   2. Run: python -m tsxbot")
    print("   3. Verify live market data is received")
    print("   4. Test a trade signal with dry_run enabled")
    print("   5. When ready, set dry_run: false for live trading")
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_projectx_broker())
    sys.exit(0 if success else 1)
