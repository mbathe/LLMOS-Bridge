"""IoT module — Abstract GPIO interface and concrete implementations.

Architecture:
  - :class:`GPIOInterface` is the abstract contract.  All code in the module
    layer talks to this interface only.
  - :class:`RaspberryPiGPIO` wraps the ``RPi.GPIO`` library.
  - :class:`MockGPIO` is a fully deterministic in-memory implementation for
    tests and for deployments without physical hardware.

GPIO design decisions:
  - Pin numbering uses BCM mode by default on Raspberry Pi (same convention
    as the GPIO spec).  BCM 18 = physical pin 12 on a standard 40-pin header.
  - All methods are synchronous because the RPi.GPIO library is not async-safe.
    The module wraps calls in ``asyncio.get_event_loop().run_in_executor`` to
    avoid blocking the event loop.
  - The abstract interface uses plain int/float/bool types — no RPi.GPIO
    constants leak across the abstraction boundary.
  - The MockGPIO records all calls in ``call_log`` so tests can assert on
    exactly which operations were performed.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------


class PinMode(IntEnum):
    INPUT = 0
    OUTPUT = 1


class PullResistor(IntEnum):
    NONE = 0
    UP = 1
    DOWN = 2


class EdgeType(IntEnum):
    RISING = 0
    FALLING = 1
    BOTH = 2


@dataclass
class PWMChannel:
    """Represents an active PWM channel."""

    pin: int
    frequency_hz: float
    duty_cycle: float  # 0.0–100.0


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class GPIOInterface(ABC):
    """Abstract GPIO interface.

    All concrete implementations must fulfil this contract.  The interface
    exposes the minimum required GPIO operations for LLMOS modules — digital
    I/O, PWM, and simple analog reading via SPI/ADC where available.
    """

    @abstractmethod
    def setup(self, pin: int, mode: PinMode, pull: PullResistor = PullResistor.NONE) -> None:
        """Configure *pin* as input or output with optional pull resistor."""

    @abstractmethod
    def digital_read(self, pin: int) -> int:
        """Return the digital value of *pin* (0 or 1)."""

    @abstractmethod
    def digital_write(self, pin: int, value: int) -> None:
        """Set the digital output of *pin* to *value* (0 or 1)."""

    @abstractmethod
    def pwm_start(self, pin: int, frequency_hz: float, duty_cycle: float) -> None:
        """Start PWM on *pin* with the given frequency and duty cycle (0–100)."""

    @abstractmethod
    def pwm_set_duty_cycle(self, pin: int, duty_cycle: float) -> None:
        """Update the duty cycle on an active PWM channel."""

    @abstractmethod
    def pwm_stop(self, pin: int) -> None:
        """Stop PWM on *pin*."""

    @abstractmethod
    def add_event_detect(
        self, pin: int, edge: EdgeType, callback: Any = None, bouncetime_ms: int = 200
    ) -> None:
        """Register an edge-detection event on *pin*."""

    @abstractmethod
    def remove_event_detect(self, pin: int) -> None:
        """Remove edge-detection from *pin*."""

    @abstractmethod
    def cleanup(self, pins: list[int] | None = None) -> None:
        """Release GPIO resources.  Pass ``None`` to release all pins."""

    # ------------------------------------------------------------------
    # Async wrappers — default implementation delegates to a thread pool.
    # ------------------------------------------------------------------

    async def async_digital_read(self, pin: int) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.digital_read, pin)

    async def async_digital_write(self, pin: int, value: int) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.digital_write, pin, value)


# ---------------------------------------------------------------------------
# Raspberry Pi implementation
# ---------------------------------------------------------------------------


class RaspberryPiGPIO(GPIOInterface):
    """GPIO implementation backed by the ``RPi.GPIO`` library.

    Raises :class:`~llmos_bridge.exceptions.ModuleLoadError` at instantiation
    if ``RPi.GPIO`` is not installed or if we are not running on a Raspberry Pi.
    This ensures ``IoTModule._check_dependencies()`` surfaces the error cleanly.
    """

    def __init__(self) -> None:
        try:
            import RPi.GPIO as GPIO  # type: ignore[import]

            self._gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
        except ImportError as exc:
            from llmos_bridge.exceptions import ModuleLoadError

            raise ModuleLoadError(
                module_id="iot",
                reason=(
                    "RPi.GPIO is not installed.  "
                    "Install it with: pip install RPi.GPIO  "
                    "(requires running on a Raspberry Pi with GPIO hardware)."
                ),
            ) from exc

        self._pwm_channels: dict[int, Any] = {}

    def setup(self, pin: int, mode: PinMode, pull: PullResistor = PullResistor.NONE) -> None:
        gpio_mode = self._gpio.OUT if mode == PinMode.OUTPUT else self._gpio.IN
        pull_map = {
            PullResistor.NONE: self._gpio.PUD_OFF,
            PullResistor.UP: self._gpio.PUD_UP,
            PullResistor.DOWN: self._gpio.PUD_DOWN,
        }
        self._gpio.setup(pin, gpio_mode, pull_up_down=pull_map[pull])

    def digital_read(self, pin: int) -> int:
        return int(self._gpio.input(pin))

    def digital_write(self, pin: int, value: int) -> None:
        self._gpio.output(pin, bool(value))

    def pwm_start(self, pin: int, frequency_hz: float, duty_cycle: float) -> None:
        self.setup(pin, PinMode.OUTPUT)
        pwm = self._gpio.PWM(pin, frequency_hz)
        pwm.start(duty_cycle)
        self._pwm_channels[pin] = pwm

    def pwm_set_duty_cycle(self, pin: int, duty_cycle: float) -> None:
        if pin not in self._pwm_channels:
            raise ValueError(f"No active PWM channel on pin {pin}.")
        self._pwm_channels[pin].ChangeDutyCycle(duty_cycle)

    def pwm_stop(self, pin: int) -> None:
        if pin in self._pwm_channels:
            self._pwm_channels[pin].stop()
            del self._pwm_channels[pin]

    def add_event_detect(
        self, pin: int, edge: EdgeType, callback: Any = None, bouncetime_ms: int = 200
    ) -> None:
        edge_map = {
            EdgeType.RISING: self._gpio.RISING,
            EdgeType.FALLING: self._gpio.FALLING,
            EdgeType.BOTH: self._gpio.BOTH,
        }
        kwargs: dict[str, Any] = {"bouncetime": bouncetime_ms}
        if callback is not None:
            kwargs["callback"] = callback
        self._gpio.add_event_detect(pin, edge_map[edge], **kwargs)

    def remove_event_detect(self, pin: int) -> None:
        self._gpio.remove_event_detect(pin)

    def cleanup(self, pins: list[int] | None = None) -> None:
        if pins:
            self._gpio.cleanup(pins)
        else:
            self._gpio.cleanup()


# ---------------------------------------------------------------------------
# Mock implementation (tests + non-Pi environments)
# ---------------------------------------------------------------------------


@dataclass
class GPIOCall:
    """A recorded GPIO method call for test assertions."""

    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class MockGPIO(GPIOInterface):
    """Fully deterministic in-memory GPIO for tests and non-Pi environments.

    All state is stored in plain dicts:
      - ``pin_modes``    — {pin: PinMode}
      - ``pin_values``   — {pin: int (0 or 1)}
      - ``pwm_channels`` — {pin: PWMChannel}
      - ``call_log``     — list[GPIOCall]

    Usage::

        gpio = MockGPIO()
        gpio.setup(18, PinMode.OUTPUT)
        gpio.digital_write(18, 1)
        assert gpio.pin_values[18] == 1
        assert gpio.call_log[-1].method == "digital_write"

    To simulate an input pin returning a specific value::

        gpio.pin_values[17] = 1   # simulate HIGH on pin 17
    """

    def __init__(self) -> None:
        self.pin_modes: dict[int, PinMode] = {}
        self.pin_values: dict[int, int] = {}
        self.pwm_channels: dict[int, PWMChannel] = {}
        self.event_callbacks: dict[int, Any] = {}
        self.call_log: list[GPIOCall] = []

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.call_log.append(GPIOCall(method=method, args=args, kwargs=kwargs))

    def setup(self, pin: int, mode: PinMode, pull: PullResistor = PullResistor.NONE) -> None:
        self._record("setup", pin, mode, pull)
        self.pin_modes[pin] = mode
        if pin not in self.pin_values:
            self.pin_values[pin] = 0

    def digital_read(self, pin: int) -> int:
        self._record("digital_read", pin)
        return self.pin_values.get(pin, 0)

    def digital_write(self, pin: int, value: int) -> None:
        self._record("digital_write", pin, value)
        self.pin_values[pin] = int(bool(value))

    def pwm_start(self, pin: int, frequency_hz: float, duty_cycle: float) -> None:
        self._record("pwm_start", pin, frequency_hz, duty_cycle)
        self.pwm_channels[pin] = PWMChannel(
            pin=pin, frequency_hz=frequency_hz, duty_cycle=duty_cycle
        )

    def pwm_set_duty_cycle(self, pin: int, duty_cycle: float) -> None:
        self._record("pwm_set_duty_cycle", pin, duty_cycle)
        if pin in self.pwm_channels:
            self.pwm_channels[pin].duty_cycle = duty_cycle

    def pwm_stop(self, pin: int) -> None:
        self._record("pwm_stop", pin)
        self.pwm_channels.pop(pin, None)

    def add_event_detect(
        self, pin: int, edge: EdgeType, callback: Any = None, bouncetime_ms: int = 200
    ) -> None:
        self._record("add_event_detect", pin, edge, callback, bouncetime_ms)
        if callback:
            self.event_callbacks[pin] = callback

    def remove_event_detect(self, pin: int) -> None:
        self._record("remove_event_detect", pin)
        self.event_callbacks.pop(pin, None)

    def cleanup(self, pins: list[int] | None = None) -> None:
        self._record("cleanup", pins)
        if pins:
            for pin in pins:
                self.pin_modes.pop(pin, None)
                self.pin_values.pop(pin, None)
                self.pwm_channels.pop(pin, None)
                self.event_callbacks.pop(pin, None)
        else:
            self.pin_modes.clear()
            self.pin_values.clear()
            self.pwm_channels.clear()
            self.event_callbacks.clear()

    def simulate_edge(self, pin: int) -> None:
        """Trigger the edge-detection callback for *pin* (useful in tests)."""
        callback = self.event_callbacks.get(pin)
        if callback:
            callback(pin)
