"""IoT module — GPIO and sensor control.

Provides:
  - :class:`~llmos_bridge.modules.iot.interfaces.GPIOInterface` — abstract GPIO API
  - :class:`~llmos_bridge.modules.iot.interfaces.RaspberryPiGPIO` — RPi.GPIO backend
  - :class:`~llmos_bridge.modules.iot.interfaces.MockGPIO` — deterministic test backend
  - :class:`~llmos_bridge.modules.iot.module.IoTModule` — BaseModule integration

Example usage (outside of IML plan context)::

    from llmos_bridge.modules.iot import IoTModule

    module = IoTModule()
    await module.execute("set_pin_mode", {"pin": 18, "mode": "output"})
    await module.execute("digital_write", {"pin": 18, "value": 1})
"""

from llmos_bridge.modules.iot.interfaces import GPIOInterface, MockGPIO, RaspberryPiGPIO
from llmos_bridge.modules.iot.module import IoTModule

__all__ = [
    "GPIOInterface",
    "MockGPIO",
    "RaspberryPiGPIO",
    "IoTModule",
]
