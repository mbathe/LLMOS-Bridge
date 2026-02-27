"""Tests — IoT module security decorator coverage."""
from __future__ import annotations

import pytest

from llmos_bridge.modules.iot.interfaces import MockGPIO
from llmos_bridge.modules.iot.module import IoTModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestIoTSecurity:
    def setup_method(self):
        self.module = IoTModule(gpio=MockGPIO())

    def test_set_pin_mode_requires_gpio_write(self):
        meta = collect_security_metadata(self.module._action_set_pin_mode)
        assert "iot.gpio.write" in meta.get("permissions", [])

    def test_digital_write_requires_gpio_write_and_standard_audit(self):
        meta = collect_security_metadata(self.module._action_digital_write)
        assert "iot.gpio.write" in meta.get("permissions", [])
        assert meta.get("audit_level") == "standard"

    def test_pwm_start_requires_actuator_and_standard_audit(self):
        meta = collect_security_metadata(self.module._action_pwm_start)
        assert "iot.actuator" in meta.get("permissions", [])
        assert meta.get("audit_level") == "standard"

    def test_cleanup_requires_gpio_write_and_medium_risk(self):
        meta = collect_security_metadata(self.module._action_cleanup)
        assert "iot.gpio.write" in meta.get("permissions", [])
        assert meta.get("risk_level") == "medium"

    def test_digital_read_requires_gpio_read(self):
        meta = collect_security_metadata(self.module._action_digital_read)
        assert "iot.gpio.read" in meta.get("permissions", [])

    def test_get_pin_state_requires_gpio_read(self):
        meta = collect_security_metadata(self.module._action_get_pin_state)
        assert "iot.gpio.read" in meta.get("permissions", [])
