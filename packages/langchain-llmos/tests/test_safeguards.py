"""Unit tests — SafeguardConfig."""

from __future__ import annotations

import pytest

from langchain_llmos.safeguards import SafeguardConfig


@pytest.mark.unit
class TestSafeguardConfig:
    def test_default_protected_windows(self) -> None:
        config = SafeguardConfig()
        assert len(config.protected_windows) > 0
        # VS Code pattern should be present.
        assert any("code" in p.lower() for p in config.protected_windows)

    def test_default_dangerous_hotkeys(self) -> None:
        config = SafeguardConfig()
        assert len(config.dangerous_hotkeys) > 0

    def test_is_hotkey_blocked_alt_f4(self) -> None:
        config = SafeguardConfig()
        reason = config.is_hotkey_blocked(["alt", "f4"])
        assert reason is not None
        assert "blocked" in reason.lower()

    def test_is_hotkey_blocked_case_insensitive(self) -> None:
        config = SafeguardConfig()
        assert config.is_hotkey_blocked(["Alt", "F4"]) is not None

    def test_is_hotkey_not_blocked(self) -> None:
        config = SafeguardConfig()
        assert config.is_hotkey_blocked(["ctrl", "c"]) is None
        assert config.is_hotkey_blocked(["enter"]) is None

    def test_is_hotkey_blocked_ctrl_alt_delete(self) -> None:
        config = SafeguardConfig()
        assert config.is_hotkey_blocked(["ctrl", "alt", "delete"]) is not None

    def test_validate_plan_steps_clean(self) -> None:
        config = SafeguardConfig()
        steps = [
            {"id": "s1", "action": "gui__key_press", "params": {"keys": ["enter"]}},
            {"id": "s2", "action": "gui__type_text", "params": {"text": "hello"}},
        ]
        warnings = config.validate_plan_steps(steps)
        assert warnings == []

    def test_validate_plan_steps_blocked_hotkey(self) -> None:
        config = SafeguardConfig()
        steps = [
            {"id": "s1", "action": "gui__key_press", "params": {"keys": ["alt", "f4"]}},
        ]
        warnings = config.validate_plan_steps(steps)
        assert len(warnings) == 1
        assert "s1" in warnings[0]

    def test_validate_plan_steps_multiple(self) -> None:
        config = SafeguardConfig()
        steps = [
            {"id": "s1", "action": "gui__key_press", "params": {"keys": ["alt", "f4"]}},
            {"id": "s2", "action": "gui__key_press", "params": {"keys": ["ctrl", "c"]}},
            {"id": "s3", "action": "gui__key_press", "params": {"keys": ["ctrl", "alt", "delete"]}},
        ]
        warnings = config.validate_plan_steps(steps)
        assert len(warnings) == 2  # s1 and s3

    def test_custom_dangerous_hotkeys(self) -> None:
        config = SafeguardConfig(dangerous_hotkeys=[["ctrl", "w"]])
        assert config.is_hotkey_blocked(["ctrl", "w"]) is not None
        assert config.is_hotkey_blocked(["alt", "f4"]) is None  # Not in custom list

    def test_max_consecutive_failures_default(self) -> None:
        config = SafeguardConfig()
        assert config.max_consecutive_failures == 3
