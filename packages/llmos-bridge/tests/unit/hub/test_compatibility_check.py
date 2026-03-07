"""Tests for HubModuleInfo Phase 4 fields and _parse_module_info compatibility."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llmos_bridge.hub.client import HubClient, HubModuleInfo


class TestHubModuleInfoNewFields:
    def test_hub_module_info_has_new_fields(self):
        """All Phase 4 fields exist with correct defaults."""
        info = HubModuleInfo(
            module_id="test_mod",
            version="1.0.0",
            description="A test module",
            author="Dev",
        )
        assert info.average_rating == 0.0
        assert info.rating_count == 0
        assert info.category == ""
        assert info.deprecated is False
        assert info.deprecated_message == ""
        assert info.replacement_module_id == ""

    def test_hub_module_info_full_fields(self):
        """All Phase 4 fields can be set explicitly."""
        info = HubModuleInfo(
            module_id="old_mod",
            version="2.1.0",
            description="Deprecated module",
            author="Alice",
            average_rating=3.8,
            rating_count=42,
            category="automation",
            deprecated=True,
            deprecated_message="Use new_mod instead",
            replacement_module_id="new_mod",
        )
        assert info.average_rating == 3.8
        assert info.rating_count == 42
        assert info.category == "automation"
        assert info.deprecated is True
        assert info.deprecated_message == "Use new_mod instead"
        assert info.replacement_module_id == "new_mod"


class TestParseModuleInfo:
    def test_parse_module_info_full(self):
        """_parse_module_info populates all Phase 4 fields from a full dict."""
        data = {
            "module_id": "smart_sensor",
            "version": "1.2.0",
            "description": "Smart sensor integration",
            "author": "Jane",
            "downloads": 250,
            "license": "MIT",
            "tags": ["iot", "sensor"],
            "average_rating": 4.5,
            "rating_count": 30,
            "category": "iot",
            "deprecated": False,
            "deprecated_message": "",
            "replacement_module_id": "",
        }
        info = HubClient._parse_module_info(data)
        assert info.module_id == "smart_sensor"
        assert info.version == "1.2.0"
        assert info.downloads == 250
        assert info.average_rating == 4.5
        assert info.rating_count == 30
        assert info.category == "iot"
        assert info.deprecated is False

    def test_parse_module_info_minimal(self):
        """Missing Phase 4 fields fall back to defaults."""
        data = {
            "module_id": "basic_mod",
            "version": "0.1.0",
            "description": "Bare minimum",
            "author": "Bob",
        }
        info = HubClient._parse_module_info(data)
        assert info.module_id == "basic_mod"
        assert info.version == "0.1.0"
        assert info.average_rating == 0.0
        assert info.rating_count == 0
        assert info.category == ""
        assert info.deprecated is False
        assert info.deprecated_message == ""
        assert info.replacement_module_id == ""

    def test_parse_module_info_uses_latest_version(self):
        """_parse_module_info prefers latest_version over version."""
        data = {
            "module_id": "mod",
            "latest_version": "3.0.0",
            "version": "1.0.0",
            "description": "Desc",
            "author": "X",
        }
        info = HubClient._parse_module_info(data)
        assert info.version == "3.0.0"


class TestSearchPopulatesNewFields:
    async def test_search_populates_new_fields(self):
        """search() results include Phase 4 fields from hub response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "modules": [
                {
                    "module_id": "rated_mod",
                    "version": "1.0.0",
                    "description": "A rated module",
                    "author": "Alice",
                    "downloads": 100,
                    "tags": ["automation"],
                    "average_rating": 4.7,
                    "rating_count": 55,
                    "category": "automation",
                    "deprecated": True,
                    "deprecated_message": "Superseded by rated_mod_v2",
                    "replacement_module_id": "rated_mod_v2",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        client = HubClient("http://hub.test/api/v1")
        client._client = mock_http

        results = await client.search("rated")
        assert len(results) == 1
        mod = results[0]
        assert mod.average_rating == 4.7
        assert mod.rating_count == 55
        assert mod.category == "automation"
        assert mod.deprecated is True
        assert mod.deprecated_message == "Superseded by rated_mod_v2"
        assert mod.replacement_module_id == "rated_mod_v2"


class TestGetModuleInfoPopulatesNewFields:
    async def test_get_module_info_populates_new_fields(self):
        """get_module_info() result includes Phase 4 fields."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "module_id": "cool_mod",
            "version": "2.0.0",
            "description": "A cool module",
            "author": "Charlie",
            "average_rating": 3.9,
            "rating_count": 12,
            "category": "productivity",
            "deprecated": False,
            "deprecated_message": "",
            "replacement_module_id": "",
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        client = HubClient("http://hub.test/api/v1")
        client._client = mock_http

        info = await client.get_module_info("cool_mod")
        assert info is not None
        assert info.average_rating == 3.9
        assert info.rating_count == 12
        assert info.category == "productivity"
        assert info.deprecated is False
