"""ProjectX Broker Implementation wrapping tsxapipy."""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from tsxbot.broker.base import Broker
from tsxbot.broker.models import (
    Fill,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from tsxbot.config_loader import AppConfig
from tsxbot.data.market_data import Tick

logger = logging.getLogger(__name__)

# Import tsxapipy components with error handling for dev environment
try:
    from tsxapipy.api import APIClient as TSXClient
    from tsxapipy.api import schemas
    from tsxapipy.auth import authenticate as tsx_authenticate
    from tsxapipy.real_time import DataStream, UserHubStream

    HAS_TSX = True
except ImportError:
    logger.warning("tsxapipy not found. ProjectXBroker will fail at runtime.")
    HAS_TSX = False
    TSXClient = Any
    DataStream = Any
    UserHubStream = Any
    tsx_authenticate = None


class ProjectXBroker(Broker):
    """
    Concrete Broker implementation for TopstepX via tsxapipy.
    """

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.client: TSXClient | None = None

        # Refactor: DataStream is per-contract. Maintain map of symbol -> DataStream
        self._data_streams: dict[str, DataStream] = {}
        self.user_stream: UserHubStream | None = None

        self._order_map: dict[str, Order] = {}  # Local cache of active orders

        # Store event loop reference for thread-safe callback dispatch
        # DataStream callbacks run in SignalR thread, need to dispatch to main loop
        self._event_loop: asyncio.AbstractEventLoop | None = None

    async def connect(self) -> None:
        """Connect to ProjectX API and streams."""
        if not HAS_TSX:
            raise ImportError("tsxapipy library required")

        # Store event loop reference for thread-safe callback dispatch
        self._event_loop = asyncio.get_running_loop()

        settings = self.config.projectx
        if not settings.api_key or not settings.username:
            raise ValueError("ProjectX credentials missing in config.")

        logger.info(
            f"Connecting to ProjectX as {settings.username} ({settings.trading_environment})"
        )

        # 1. Authenticate to get token (synchronous call, run in executor)
        token, token_acquired_at = await tsx_authenticate(settings.username, settings.api_key)

        if not token:
            raise ConnectionError("Failed to authenticate with ProjectX API")

        logger.info(f"Authenticated successfully. Token acquired at: {token_acquired_at}")

        # 2. Initialize REST Client with the token
        self.client = TSXClient(
            initial_token=token,
            token_acquired_at=token_acquired_at,
            reauth_username=settings.username,
            reauth_api_key=settings.api_key,
        )

        # 3. Fetch Account ID
        accounts = await self.client.get_accounts()
        if not accounts:
            raise ValueError("No ProjectX accounts found.")

        # Select first account
        self.account_id = getattr(accounts[0], "id", None) or accounts[0].get("id")
        if not self.account_id:
            try:
                self.account_id = accounts[0].id
            except AttributeError:
                self.account_id = accounts[0]["id"]

        logger.info(f"Connected to ProjectX Account: {self.account_id}")

        # 4. Initialize User Stream (Account level)
        self.user_stream = UserHubStream(
            api_client=self.client, account_id_to_watch=self.account_id
        )

        # 5. Setup User Stream Callbacks
        self.user_stream.on_order = self._handle_projectx_order_update
        self.user_stream.on_fill = self._handle_projectx_fill

        # 6. Start User Stream
        await self.user_stream.start()

        # Data streams are initialized on subscribe()

        logger.info("ProjectX Connected.")

    async def disconnect(self) -> None:
        """Disconnect streams and client."""
        import asyncio

        loop = asyncio.get_running_loop()

        # Stop all data streams
        for symbol, stream in self._data_streams.items():
            try:
                await stream.stop()
                logger.info(f"Stopped DataStream for {symbol}")
            except Exception as e:
                logger.error(f"Error stopping DataStream for {symbol}: {e}")
        self._data_streams.clear()

        if self.user_stream:
            await self.user_stream.stop()

        if self.client and hasattr(self.client, "logout"):
            await self.client.logout()

    async def subscribe(self, symbol: str) -> None:
        """Subscribe to market data for a symbol."""
        if not self.client:
            raise ConnectionError("Not connected")

        if symbol in self._data_streams:
            logger.info(f"Already subscribed to {symbol}")
            return

        import asyncio

        loop = asyncio.get_running_loop()

        logger.info(f"Initializing DataStream for {symbol}")

        # Create DataStream for this symbol
        # Provide the tick callback adapter
        ds = DataStream(
            api_client=self.client,
            contract_id_to_subscribe=symbol,
            on_quote_callback=self._handle_projectx_tick,  # Callback for quotes
            on_trade_callback=self._handle_projectx_tick,  # Use same handler for trades (if tick logic supports it)
            auto_subscribe_quotes=True,
            auto_subscribe_trades=True,
            auto_subscribe_depth=False,
        )

        # Start the stream
        success = await ds.start()
        if success:
            self._data_streams[symbol] = ds
            logger.info(f"Subscribed to {symbol}")
        else:
            logger.error(f"Failed to subscribe/start DataStream for {symbol}")

    async def subscribe_ticks(self, symbols: list[str]) -> None:
        """Subscribe to market data (Batch)."""
        for symbol in symbols:
            await self.subscribe(symbol)

    async def get_orders(self) -> list[Order]:
        """Get all active/working orders."""
        return list(self._order_map.values())

    async def place_order(self, request: OrderRequest) -> Order:
        """Place order via REST API using Pydantic schemas."""
        if not self.client:
            raise ConnectionError("Not connected")

        import asyncio

        loop = asyncio.get_running_loop()

        # Map Side (0=BUY, 1=SELL)
        px_side = 0 if request.side == OrderSide.BUY else 1

        # Construct Request Model based on Type
        order_model = None

        # Ensure account_id is int
        try:
            acc_id = int(str(self.account_id))
        except (ValueError, TypeError):
            raise ValueError(f"Invalid account ID: {self.account_id}")

        tag = str(uuid4())

        if request.type == OrderType.MARKET:
            # Type 2
            order_model = schemas.PlaceMarketOrderRequest(
                accountId=acc_id,
                contractId=request.symbol,
                side=px_side,
                size=int(request.qty),
                customTag=tag,
            )
        elif request.type == OrderType.LIMIT:
            # Type 1
            if request.limit_price is None:
                raise ValueError("Limit price required for Limit order")
            order_model = schemas.PlaceLimitOrderRequest(
                accountId=acc_id,
                contractId=request.symbol,
                side=px_side,
                size=int(request.qty),
                customTag=tag,
                limitPrice=float(request.limit_price),
            )
        elif request.type == OrderType.STOP:
            # Type 3 (Stop Market)
            if request.stop_price is None:
                raise ValueError("Stop price required for Stop order")
            order_model = schemas.PlaceStopOrderRequest(
                accountId=acc_id,
                contractId=request.symbol,
                side=px_side,
                size=int(request.qty),
                customTag=tag,
                stopPrice=float(request.stop_price),
            )
        else:
            raise NotImplementedError(f"Order type {request.type} not supported")

        # Call API (Sync wrapper)
        resp = await self.client.place_order(order_model)

        # Response is OrderPlacementResponse(order_id=...)
        order_id = str(resp.order_id)

        # Construct Order object
        order = Order(id=order_id, request=request, status=OrderStatus.PENDING)
        self._order_map[order_id] = order
        return order

    async def cancel_order(self, order_id: str) -> None:
        if not self.client:
            raise ConnectionError("Not connected")

        import asyncio

        loop = asyncio.get_running_loop()

        acc_id = int(str(self.account_id))
        req = schemas.CancelOrderRequest(accountId=acc_id, orderId=int(order_id))

        await self.client.cancel_order(req)

    async def get_position(self, symbol: str) -> Position:
        # Stub - robust implementation would query /positions endpoint
        return Position(symbol=symbol, qty=0, avg_price=Decimal("0"))

    async def get_account_balance(self) -> Decimal:
        # Stub - TODO: Implement via REST API call
        return Decimal("100000.0")

    # --- Callbacks ---

    def _handle_projectx_tick(self, px_data: Any) -> None:
        """Handle incoming tick data from DataStream (quotes or trades).

        NOTE: This runs in SignalR's thread, not the main asyncio thread!
        Must use run_coroutine_threadsafe to dispatch to async callbacks.
        """
        try:
            # Extract symbol - try multiple field names
            symbol = (
                px_data.get("contractId")
                or px_data.get("symbol")
                or px_data.get("symbolId")
                or "UNKNOWN"
            )

            # Extract price - prefer lastPrice (quote) then price (trade)
            price = (
                px_data.get("lastPrice")
                or px_data.get("price")
                or px_data.get("LastPrice")
                or px_data.get("Price")
                or 0
            )

            # Extract volume
            volume = px_data.get("size") or px_data.get("volume") or px_data.get("qty") or 0

            if price == 0:
                # No price data in this message (might be just bid/ask update)
                return

            # Convert to internal Tick format
            tick = Tick(
                symbol=str(symbol),
                price=Decimal(str(price)),
                volume=int(volume) if volume else 0,
                timestamp=datetime.now(),
            )

            # Dispatch to all registered callbacks using thread-safe method
            if self._event_loop and self._tick_callbacks:
                for cb in self._tick_callbacks:
                    # Schedule coroutine on the main event loop from this thread
                    asyncio.run_coroutine_threadsafe(cb(tick), self._event_loop)

        except Exception as e:
            logger.error(f"Error handling tick: {e}, data: {px_data}")

    def _handle_projectx_order_update(self, data: Any) -> None:
        """Handle order status updates from UserHubStream."""
        try:
            # Extract order info from stream event
            # tsxapipy format may vary - handle both object and dict access
            order_id = str(getattr(data, "orderId", None) or data.get("orderId", ""))
            status_str = str(getattr(data, "status", None) or data.get("status", "UNKNOWN"))

            # Map ProjectX status to our OrderStatus enum
            status_map = {
                "PENDING": OrderStatus.PENDING,
                "WORKING": OrderStatus.ACCEPTED,
                "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
                "FILLED": OrderStatus.FILLED,
                "CANCELLED": OrderStatus.CANCELLED,
                "REJECTED": OrderStatus.REJECTED,
                "EXPIRED": OrderStatus.EXPIRED,
            }
            new_status = status_map.get(status_str.upper(), OrderStatus.PENDING)

            # Update internal order cache
            if order_id in self._order_map:
                order = self._order_map[order_id]
                order.status = new_status
                order.updated_at = datetime.now()

                # Update fill info if present
                filled_qty = getattr(data, "filledQuantity", None) or data.get("filledQuantity")
                if filled_qty is not None:
                    order.filled_qty = int(filled_qty)

                avg_price = getattr(data, "averagePrice", None) or data.get("averagePrice")
                if avg_price is not None:
                    order.avg_fill_price = Decimal(str(avg_price))

                logger.debug(f"Order {order_id} updated: {new_status}")
        except Exception as e:
            logger.error(f"Error handling order update: {e}")

    def _handle_projectx_fill(self, data: Any) -> None:
        """Handle fill events from UserHubStream."""
        try:
            # Extract fill info from stream event
            fill_id = str(
                getattr(data, "fillId", None)
                or data.get("fillId", "")
                or f"fill-{datetime.now().timestamp()}"
            )
            order_id = str(getattr(data, "orderId", None) or data.get("orderId", ""))
            symbol = str(getattr(data, "symbol", None) or data.get("symbol", ""))
            side_str = str(getattr(data, "side", None) or data.get("side", "BUY"))
            qty = int(getattr(data, "quantity", None) or data.get("quantity", 0))
            price = Decimal(str(getattr(data, "price", None) or data.get("price", 0)))

            # Map side
            side = OrderSide.BUY if side_str.upper() == "BUY" else OrderSide.SELL

            # Create Fill object
            fill = Fill(
                id=fill_id,
                order_id=order_id,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                timestamp=datetime.now(),
            )

            logger.info(
                f"Fill received: {fill_id} for order {order_id}: {side.value} {qty} @ {price}"
            )

            # Dispatch to all registered fill callbacks using thread-safe method
            # (this callback runs in SignalR's thread, not the main asyncio thread)
            if self._event_loop and self._fill_callbacks:
                for cb in self._fill_callbacks:
                    asyncio.run_coroutine_threadsafe(cb(fill), self._event_loop)

        except Exception as e:
            logger.error(f"Error handling fill: {e}")
