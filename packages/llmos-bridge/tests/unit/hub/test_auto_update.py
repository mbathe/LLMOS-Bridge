"""Tests for hub.auto_update — AutoUpdateChecker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.hub.auto_update import AutoUpdateChecker


@pytest.fixture()
def mock_hub_client():
    client = AsyncMock()
    client.check_updates = AsyncMock(return_value=[])
    return client


@pytest.fixture()
def mock_index():
    index = AsyncMock()
    index.list_all = AsyncMock(return_value=[])
    return index


@pytest.fixture()
def checker(mock_hub_client, mock_index):
    return AutoUpdateChecker(
        mock_hub_client,
        mock_index,
        check_interval=300.0,
    )


class TestAutoUpdateChecker:
    async def test_check_now_no_modules(self, checker):
        updates = await checker.check_now()
        assert updates == []

    async def test_check_now_detects_updates(self, mock_hub_client, mock_index):
        mod = MagicMock()
        mod.module_id = "test_mod"
        mod.version = "1.0.0"
        mod.enabled = True
        mock_index.list_all.return_value = [mod]
        mock_hub_client.check_updates.return_value = [
            {"module_id": "test_mod", "current_version": "1.0.0", "latest_version": "2.0.0"}
        ]

        checker = AutoUpdateChecker(mock_hub_client, mock_index)
        updates = await checker.check_now()
        assert len(updates) == 1
        assert updates[0]["latest_version"] == "2.0.0"
        assert checker.available_updates == updates

    async def test_check_now_skips_current(self, mock_hub_client, mock_index):
        mod = MagicMock()
        mod.module_id = "up_to_date"
        mod.version = "2.0.0"
        mod.enabled = True
        mock_index.list_all.return_value = [mod]
        mock_hub_client.check_updates.return_value = []

        checker = AutoUpdateChecker(mock_hub_client, mock_index)
        updates = await checker.check_now()
        assert len(updates) == 0

    async def test_check_now_hub_unreachable(self, mock_hub_client, mock_index):
        mod = MagicMock()
        mod.module_id = "some_mod"
        mod.version = "1.0.0"
        mod.enabled = True
        mock_index.list_all.return_value = [mod]
        mock_hub_client.check_updates.side_effect = Exception("Connection refused")

        checker = AutoUpdateChecker(mock_hub_client, mock_index)
        updates = await checker.check_now()
        assert updates == []

    async def test_publishes_to_event_bus(self, mock_hub_client, mock_index):
        mod = MagicMock()
        mod.module_id = "mod_a"
        mod.version = "1.0.0"
        mod.enabled = True
        mock_index.list_all.return_value = [mod]
        mock_hub_client.check_updates.return_value = [
            {"module_id": "mod_a", "current_version": "1.0.0", "latest_version": "2.0.0"}
        ]

        event_bus = AsyncMock()
        checker = AutoUpdateChecker(mock_hub_client, mock_index, event_bus=event_bus)
        await checker.check_now()

        event_bus.publish.assert_called_once()
        call_args = event_bus.publish.call_args
        assert call_args[0][0] == "llmos.modules"
        assert call_args[0][1]["event"] == "updates_available"

    async def test_start_and_stop(self, checker):
        await checker.start()
        assert checker._task is not None
        await checker.stop()
        assert checker._task is None
