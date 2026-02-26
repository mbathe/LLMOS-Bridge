"""IoT module — IML action implementation.

Exposes GPIO and sensor operations as IML actions, backed by
:class:`~llmos_bridge.modules.iot.interfaces.GPIOInterface`.

Platform support:
  - Raspberry Pi (primary target, RPi.GPIO backend)
  - Linux (Mock backend for development / testing)

The module selects the backend automatically:
  - On a Raspberry Pi with RPi.GPIO installed: RaspberryPiGPIO
  - Otherwise (tests, dev boxes): MockGPIO

To force a specific backend, inject it via the constructor::

    from llmos_bridge.modules.iot.interfaces import MockGPIO
    module = IoTModule(gpio=MockGPIO())

Actions (10):
  - set_pin_mode       — configure a pin as input or output
  - digital_read       — read the digital value of a pin (0 or 1)
  - digital_write      — set a pin HIGH or LOW
  - pwm_start          — start a PWM signal on a pin
  - pwm_set_duty_cycle — update the duty cycle of an active PWM channel
  - pwm_stop           — stop a PWM channel
  - add_event_detect   — register an edge-detection callback
  - remove_event_detect — remove edge-detection from a pin
  - cleanup            — release GPIO resources
  - get_pin_state      — return the current known state of all configured pins
"""

from __future__ import annotations

from typing import Any

from llmos_bridge.exceptions import ModuleLoadError
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.iot.interfaces import (
    EdgeType,
    GPIOInterface,
    MockGPIO,
    PinMode,
    PullResistor,
    RaspberryPiGPIO,
)
from llmos_bridge.modules.manifest import ActionSpec, ModuleManifest, ParamSpec
from llmos_bridge.modules.platform import PlatformInfo


class IoTModule(BaseModule):
    """IoT module providing GPIO control for Raspberry Pi and Linux SBCs."""

    MODULE_ID = "iot"
    VERSION = "0.1.0"
    SUPPORTED_PLATFORMS = [Platform.RASPBERRY_PI, Platform.LINUX]

    def __init__(self, gpio: GPIOInterface | None = None) -> None:
        # Bypass super().__init__() to allow injecting gpio before _check_dependencies.
        self._gpio: GPIOInterface | None = gpio
        super().__init__()

    def _check_dependencies(self) -> None:
        """Select and initialise the GPIO backend."""
        if self._gpio is not None:
            return  # Backend already injected (test mode).

        platform_info = PlatformInfo.detect()
        if platform_info.is_raspberry_pi:
            try:
                self._gpio = RaspberryPiGPIO()
            except ModuleLoadError:
                # RPi.GPIO not installed even though we're on a Pi.
                # Fall through to MockGPIO with a warning.
                self._gpio = MockGPIO()
        else:
            # Non-Pi environment — use Mock for development/CI.
            self._gpio = MockGPIO()

    @property
    def gpio(self) -> GPIOInterface:
        assert self._gpio is not None
        return self._gpio

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _action_set_pin_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import SetPinModeParams

        p = SetPinModeParams.model_validate(params)
        mode = PinMode.OUTPUT if p.mode == "output" else PinMode.INPUT
        pull_map = {"none": PullResistor.NONE, "up": PullResistor.UP, "down": PullResistor.DOWN}
        pull = pull_map.get(p.pull or "none", PullResistor.NONE)
        self.gpio.setup(p.pin, mode, pull)
        return {"pin": p.pin, "mode": p.mode, "pull": p.pull}

    async def _action_digital_read(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import DigitalReadParams

        p = DigitalReadParams.model_validate(params)
        value = await self.gpio.async_digital_read(p.pin)
        return {"pin": p.pin, "value": value}

    async def _action_digital_write(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import DigitalWriteParams

        p = DigitalWriteParams.model_validate(params)
        await self.gpio.async_digital_write(p.pin, p.value)
        return {"pin": p.pin, "value": p.value}

    async def _action_pwm_start(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import PwmStartParams

        p = PwmStartParams.model_validate(params)
        self.gpio.pwm_start(p.pin, p.frequency_hz, p.duty_cycle)
        return {"pin": p.pin, "frequency_hz": p.frequency_hz, "duty_cycle": p.duty_cycle}

    async def _action_pwm_set_duty_cycle(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import PwmSetDutyCycleParams

        p = PwmSetDutyCycleParams.model_validate(params)
        self.gpio.pwm_set_duty_cycle(p.pin, p.duty_cycle)
        return {"pin": p.pin, "duty_cycle": p.duty_cycle}

    async def _action_pwm_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import PwmStopParams

        p = PwmStopParams.model_validate(params)
        self.gpio.pwm_stop(p.pin)
        return {"pin": p.pin, "stopped": True}

    async def _action_add_event_detect(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import AddEventDetectParams

        p = AddEventDetectParams.model_validate(params)
        edge_map = {"rising": EdgeType.RISING, "falling": EdgeType.FALLING, "both": EdgeType.BOTH}
        edge = edge_map.get(p.edge, EdgeType.BOTH)
        self.gpio.add_event_detect(p.pin, edge, bouncetime_ms=p.bouncetime_ms)
        return {"pin": p.pin, "edge": p.edge, "bouncetime_ms": p.bouncetime_ms}

    async def _action_remove_event_detect(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import RemoveEventDetectParams

        p = RemoveEventDetectParams.model_validate(params)
        self.gpio.remove_event_detect(p.pin)
        return {"pin": p.pin, "removed": True}

    async def _action_cleanup(self, params: dict[str, Any]) -> dict[str, Any]:
        from llmos_bridge.protocol.params.iot import CleanupParams

        p = CleanupParams.model_validate(params)
        self.gpio.cleanup(pins=p.pins or None)
        return {"pins": p.pins, "cleaned": True}

    async def _action_get_pin_state(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the known state of all configured pins (MockGPIO only)."""
        if isinstance(self.gpio, MockGPIO):
            return {
                "pin_modes": {str(k): v.name for k, v in self.gpio.pin_modes.items()},
                "pin_values": self.gpio.pin_values,
                "pwm_channels": {
                    str(k): {
                        "frequency_hz": v.frequency_hz,
                        "duty_cycle": v.duty_cycle,
                    }
                    for k, v in self.gpio.pwm_channels.items()
                },
            }
        return {"error": "get_pin_state is only available with MockGPIO backend."}

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description=(
                "GPIO and sensor control for Raspberry Pi and Linux SBCs. "
                "Supports digital I/O, PWM, and edge-detection events."
            ),
            author="LLMOS Bridge Contributors",
            homepage="https://github.com/llmos-bridge/llmos-bridge",
            platforms=["raspberry_pi", "linux"],
            tags=["iot", "gpio", "raspberry-pi", "hardware", "embedded"],
            declared_permissions=["gpio_access"],
            actions=[
                ActionSpec(
                    name="set_pin_mode",
                    description="Configure a GPIO pin as input or output.",
                    params=[
                        ParamSpec("pin", "integer", "BCM pin number."),
                        ParamSpec("mode", "string", "Pin mode: 'input' or 'output'.",
                                  enum=["input", "output"]),
                        ParamSpec("pull", "string",
                                  "Pull resistor: 'none', 'up', or 'down'.",
                                  required=False, default="none",
                                  enum=["none", "up", "down"]),
                    ],
                    returns="object",
                    returns_description='{"pin": int, "mode": str, "pull": str}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="digital_read",
                    description="Read the digital value (0 or 1) of a GPIO pin.",
                    params=[ParamSpec("pin", "integer", "BCM pin number.")],
                    returns="object",
                    returns_description='{"pin": int, "value": 0 | 1}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="digital_write",
                    description="Set a GPIO output pin HIGH (1) or LOW (0).",
                    params=[
                        ParamSpec("pin", "integer", "BCM pin number."),
                        ParamSpec("value", "integer", "Output value: 0 or 1."),
                    ],
                    returns="object",
                    returns_description='{"pin": int, "value": int}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="pwm_start",
                    description="Start a PWM signal on a GPIO pin.",
                    params=[
                        ParamSpec("pin", "integer", "BCM pin number."),
                        ParamSpec("frequency_hz", "number", "PWM frequency in Hz."),
                        ParamSpec("duty_cycle", "number",
                                  "Initial duty cycle (0.0–100.0)."),
                    ],
                    returns="object",
                    returns_description='{"pin": int, "frequency_hz": float, "duty_cycle": float}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="pwm_set_duty_cycle",
                    description="Update the duty cycle of an active PWM channel.",
                    params=[
                        ParamSpec("pin", "integer", "BCM pin number."),
                        ParamSpec("duty_cycle", "number", "New duty cycle (0.0–100.0)."),
                    ],
                    returns="object",
                    returns_description='{"pin": int, "duty_cycle": float}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="pwm_stop",
                    description="Stop the PWM channel on a pin.",
                    params=[ParamSpec("pin", "integer", "BCM pin number.")],
                    returns="object",
                    returns_description='{"pin": int, "stopped": true}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="add_event_detect",
                    description="Register an edge-detection event on a GPIO input pin.",
                    params=[
                        ParamSpec("pin", "integer", "BCM pin number."),
                        ParamSpec("edge", "string", "Edge type: 'rising', 'falling', or 'both'.",
                                  enum=["rising", "falling", "both"]),
                        ParamSpec("bouncetime_ms", "integer",
                                  "Debounce time in milliseconds.",
                                  required=False, default=200),
                    ],
                    returns="object",
                    returns_description='{"pin": int, "edge": str, "bouncetime_ms": int}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="remove_event_detect",
                    description="Remove edge-detection from a GPIO pin.",
                    params=[ParamSpec("pin", "integer", "BCM pin number.")],
                    returns="object",
                    returns_description='{"pin": int, "removed": true}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="cleanup",
                    description="Release GPIO resources.",
                    params=[
                        ParamSpec("pins", "array", "List of BCM pin numbers to release. "
                                  "Omit to release all.",
                                  required=False, default=None),
                    ],
                    returns="object",
                    returns_description='{"pins": list | null, "cleaned": true}',
                    permission_required="power_user",
                    platforms=["raspberry_pi", "linux"],
                ),
                ActionSpec(
                    name="get_pin_state",
                    description="Return the current state of all configured pins (MockGPIO only).",
                    params=[],
                    returns="object",
                    returns_description=(
                        '{"pin_modes": {str: str}, '
                        '"pin_values": {str: int}, '
                        '"pwm_channels": {str: {"frequency_hz": float, "duty_cycle": float}}}'
                    ),
                    permission_required="readonly",
                    platforms=["raspberry_pi", "linux"],
                ),
            ],
        )
