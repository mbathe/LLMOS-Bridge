"""Typed parameter models for the ``iot`` module.

Platform note: GPIO actions are only available on Raspberry Pi.
MQTT actions are available on all platforms when ``paho-mqtt`` is installed.
The module guard will raise ``ModuleLoadError`` if required hardware is absent.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SetGpioPinParams(BaseModel):
    pin: Annotated[int, Field(ge=1, le=40)] = Field(description="BCM GPIO pin number.")
    state: Literal["HIGH", "LOW"]
    mode: Literal["BCM", "BOARD"] = "BCM"


class GetGpioPinParams(BaseModel):
    pin: Annotated[int, Field(ge=1, le=40)]
    mode: Literal["BCM", "BOARD"] = "BCM"


class SetPwmParams(BaseModel):
    pin: Annotated[int, Field(ge=1, le=40)]
    frequency: Annotated[float, Field(ge=1.0, le=1_000_000.0)]
    duty_cycle: Annotated[float, Field(ge=0.0, le=100.0)]
    mode: Literal["BCM", "BOARD"] = "BCM"


class SendI2cParams(BaseModel):
    address: Annotated[int, Field(ge=0x00, le=0x7F)] = Field(
        description="7-bit I2C device address (decimal or hex)."
    )
    data: list[Annotated[int, Field(ge=0, le=255)]] = Field(
        description="Bytes to write."
    )
    bus: int = Field(default=1, ge=0, le=10)


class ReceiveI2cParams(BaseModel):
    address: Annotated[int, Field(ge=0x00, le=0x7F)]
    num_bytes: Annotated[int, Field(ge=1, le=256)]
    bus: int = Field(default=1, ge=0, le=10)


class SendUartParams(BaseModel):
    port: str = Field(default="/dev/ttyS0", description="Serial port path.")
    data: str = Field(description="Data string to send.")
    baud_rate: Literal[9600, 19200, 38400, 57600, 115200, 230400] = 9600
    timeout: Annotated[float, Field(ge=0.1, le=30.0)] = 1.0


class ReceiveUartParams(BaseModel):
    port: str = "/dev/ttyS0"
    num_bytes: Annotated[int, Field(ge=1, le=4096)] = 64
    baud_rate: Literal[9600, 19200, 38400, 57600, 115200, 230400] = 9600
    timeout: Annotated[float, Field(ge=0.1, le=30.0)] = 1.0


class ReadSensorParams(BaseModel):
    sensor_type: Literal["DHT11", "DHT22", "DS18B20", "BMP280", "generic"] = "generic"
    pin: Annotated[int, Field(ge=1, le=40)] | None = None
    address: Annotated[int, Field(ge=0x00, le=0x7F)] | None = None
    unit: Literal["celsius", "fahrenheit"] = "celsius"


class PublishMqttParams(BaseModel):
    broker: str = Field(description="MQTT broker hostname or IP.")
    port: Annotated[int, Field(ge=1, le=65535)] = 1883
    topic: str
    payload: str
    qos: Literal[0, 1, 2] = 0
    retain: bool = False
    username: str | None = None
    password: str | None = None
    tls: bool = False
    timeout: Annotated[int, Field(ge=1, le=60)] = 10


class SubscribeMqttParams(BaseModel):
    broker: str
    port: Annotated[int, Field(ge=1, le=65535)] = 1883
    topic: str
    qos: Literal[0, 1, 2] = 0
    duration: Annotated[int, Field(ge=1, le=3600)] = 30
    username: str | None = None
    password: str | None = None
    tls: bool = False


# ---------------------------------------------------------------------------
# GPIO interface param models (IoTModule native actions)
# ---------------------------------------------------------------------------


class SetPinModeParams(BaseModel):
    """Parameters for set_pin_mode action."""

    pin: Annotated[int, Field(ge=0, le=53)] = Field(description="BCM pin number.")
    mode: Literal["input", "output"] = Field(description="Pin mode.")
    pull: Literal["none", "up", "down"] | None = Field(
        default="none",
        description="Optional pull resistor configuration.",
    )


class DigitalReadParams(BaseModel):
    """Parameters for digital_read action."""

    pin: Annotated[int, Field(ge=0, le=53)] = Field(description="BCM pin number to read.")


class DigitalWriteParams(BaseModel):
    """Parameters for digital_write action."""

    pin: Annotated[int, Field(ge=0, le=53)] = Field(description="BCM pin number.")
    value: Literal[0, 1] = Field(description="Output value: 0 (LOW) or 1 (HIGH).")


class PwmStartParams(BaseModel):
    """Parameters for pwm_start action."""

    pin: Annotated[int, Field(ge=0, le=53)]
    frequency_hz: Annotated[float, Field(ge=1.0, le=1_000_000.0)]
    duty_cycle: Annotated[float, Field(ge=0.0, le=100.0)] = 50.0


class PwmSetDutyCycleParams(BaseModel):
    """Parameters for pwm_set_duty_cycle action."""

    pin: Annotated[int, Field(ge=0, le=53)]
    duty_cycle: Annotated[float, Field(ge=0.0, le=100.0)]


class PwmStopParams(BaseModel):
    """Parameters for pwm_stop action."""

    pin: Annotated[int, Field(ge=0, le=53)]


class AddEventDetectParams(BaseModel):
    """Parameters for add_event_detect action."""

    pin: Annotated[int, Field(ge=0, le=53)]
    edge: Literal["rising", "falling", "both"] = "both"
    bouncetime_ms: Annotated[int, Field(ge=0, le=10_000)] = 200


class RemoveEventDetectParams(BaseModel):
    """Parameters for remove_event_detect action."""

    pin: Annotated[int, Field(ge=0, le=53)]


class CleanupParams(BaseModel):
    """Parameters for cleanup action."""

    pins: list[Annotated[int, Field(ge=0, le=53)]] | None = Field(
        default=None,
        description="Specific pins to release, or null to release all.",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    # Legacy models (kept for backward compatibility with existing plans)
    "set_gpio_pin": SetGpioPinParams,
    "get_gpio_pin": GetGpioPinParams,
    "set_pwm": SetPwmParams,
    "send_i2c": SendI2cParams,
    "receive_i2c": ReceiveI2cParams,
    "send_uart": SendUartParams,
    "receive_uart": ReceiveUartParams,
    "read_sensor": ReadSensorParams,
    "publish_mqtt": PublishMqttParams,
    "subscribe_mqtt": SubscribeMqttParams,
    # Native IoTModule actions
    "set_pin_mode": SetPinModeParams,
    "digital_read": DigitalReadParams,
    "digital_write": DigitalWriteParams,
    "pwm_start": PwmStartParams,
    "pwm_set_duty_cycle": PwmSetDutyCycleParams,
    "pwm_stop": PwmStopParams,
    "add_event_detect": AddEventDetectParams,
    "remove_event_detect": RemoveEventDetectParams,
    "cleanup": CleanupParams,
}
