"""Tests for publisher authentication."""

from __future__ import annotations

import pytest

from llmos_hub.auth import generate_api_key, hash_api_key


class TestAuth:
    def test_hash_api_key_deterministic(self):
        key = "llmos_hub_test_key"
        h1 = hash_api_key(key)
        h2 = hash_api_key(key)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_generate_api_key_format(self):
        key = generate_api_key()
        assert key.startswith("llmos_hub_")
        assert len(key) > 20

    def test_different_keys_different_hashes(self):
        k1 = generate_api_key()
        k2 = generate_api_key()
        assert hash_api_key(k1) != hash_api_key(k2)
