"""Tests for ProjectX Broker."""

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tsxbot.broker.models import OrderRequest, OrderSide, OrderType
from tsxbot.broker.projectx import ProjectXBroker
from tsxbot.config_loader import AppConfig


class TestProjectXBroker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = AppConfig()
        self.config.projectx.api_key = "dummy"
        self.config.projectx.username = "user"

    async def test_connect(self):
        # Patch dependencies in projectx module
        with (
            patch("tsxbot.broker.projectx.TSXClient") as MockClient,
            patch("tsxbot.broker.projectx.DataStream") as MockDS,
            patch("tsxbot.broker.projectx.UserHubStream") as MockUHS,
            patch("tsxbot.broker.projectx.tsx_authenticate", new_callable=AsyncMock) as MockAuth,
        ):
            MockAuth.return_value = ("fake_token", datetime.now())
            # Setup Mock Client
            client_instance = MockClient.return_value
            client_instance.initial_authenticate_app = AsyncMock()

            # Mock Account Response (object with id)
            acc = MagicMock()
            acc.id = 12345
            client_instance.get_accounts = AsyncMock(return_value=[acc])

            # Setup Mock Streams
            ds_instance = MockDS.return_value
            ds_instance.start = AsyncMock()
            uhs_instance = MockUHS.return_value
            uhs_instance.start = AsyncMock()

            broker = ProjectXBroker(self.config)
            await broker.connect()

            # Verifications
            # client_instance.initial_authenticate_app.assert_awaited()
            client_instance.get_accounts.assert_awaited()
            # ds_instance.start.assert_awaited()  # DataStream not started in connect
            uhs_instance.start.assert_awaited()

            # Verify stream init args
            MockUHS.assert_called_with(api_client=client_instance, account_id_to_watch=12345)

    async def test_place_order(self):
        with patch("tsxbot.broker.projectx.TSXClient") as MockClient:
            client_instance = MockClient.return_value
            # return dictionary as expected by code logic
            mock_resp = MagicMock()
            mock_resp.order_id = "123"
            mock_resp.status = "WORKING"
            client_instance.place_order = AsyncMock(return_value=mock_resp)

            broker = ProjectXBroker(self.config)
            broker.client = client_instance
            broker.account_id = 12345
            broker._order_map = {}  # Ensure map init

            req = OrderRequest("ES", OrderSide.BUY, 1, OrderType.MARKET)
            order = await broker.place_order(req)

            self.assertEqual(order.id, "123")
            client_instance.place_order.assert_awaited()
