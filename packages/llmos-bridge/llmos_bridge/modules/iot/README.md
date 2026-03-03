# IoT Module

GPIO and sensor control for Raspberry Pi and Linux SBCs. Supports digital I/O,
PWM, and edge-detection events.

## Overview

The IoT module provides 10 IML-callable actions for GPIO control, backed by
the `GPIOInterface` abstraction. It automatically selects the appropriate
backend at startup:

- **Raspberry Pi** with RPi.GPIO installed: `RaspberryPiGPIO` backend
- **Linux / development / CI**: `MockGPIO` backend (safe for testing)

All pin numbers use the BCM (Broadcom) numbering scheme. The module supports
digital read/write, PWM signal generation, and edge-detection event
registration with configurable debounce.

Security decorators (`@requires_permission`, `@audit_trail`,
`@sensitive_action`) are applied to all actions. GPIO write operations require
`Permission.GPIO_WRITE`, read operations require `Permission.GPIO_READ`, and
PWM/actuator operations require `Permission.ACTUATOR`.

## Actions

| Action | Description | Risk | Permission |
|--------|-------------|------|------------|
| `set_pin_mode` | Configure a GPIO pin as input or output | Medium | `power_user` |
| `digital_read` | Read the digital value (0 or 1) of a pin | Low | `power_user` |
| `digital_write` | Set a GPIO output pin HIGH or LOW | Medium | `power_user` |
| `pwm_start` | Start a PWM signal on a pin | Medium | `power_user` |
| `pwm_set_duty_cycle` | Update the duty cycle of an active PWM channel | Low | `power_user` |
| `pwm_stop` | Stop the PWM channel on a pin | Low | `power_user` |
| `add_event_detect` | Register an edge-detection event on an input pin | Low | `power_user` |
| `remove_event_detect` | Remove edge-detection from a pin | Low | `power_user` |
| `cleanup` | Release GPIO resources | Medium | `power_user` |
| `get_pin_state` | Return the current state of all configured pins | Low | `readonly` |

## Quick Start

```yaml
actions:
  - id: setup-led
    module: iot
    action: set_pin_mode
    params:
      pin: 18
      mode: output

  - id: turn-on
    module: iot
    action: digital_write
    depends_on: [setup-led]
    params:
      pin: 18
      value: 1
```

## Requirements

- **Raspberry Pi**: `RPi.GPIO` (optional; falls back to MockGPIO if missing)
- **Linux / dev**: No external dependencies (MockGPIO used automatically)

Install the hardware backend:

```bash
pip install RPi.GPIO
```

## Configuration

The IoT module auto-detects the platform via `PlatformInfo.detect()`. To force
a specific backend, inject it at construction time:

```python
from llmos_bridge.modules.iot.interfaces import MockGPIO
module = IoTModule(gpio=MockGPIO())
```

## Platform Support

| Platform | Status | Backend |
|----------|--------|---------|
| Raspberry Pi | Supported | RaspberryPiGPIO |
| Linux | Supported | MockGPIO (dev/test) |
| macOS | Not supported | -- |
| Windows | Not supported | -- |

## Related Modules

- **security** -- GPIO operations require `gpio.read`, `gpio.write`, and
  `actuator` permissions managed by the security module.
- **gui** -- GUI automation may be combined with IoT actions for physical
  device control workflows.
- **os_exec** -- System commands for advanced hardware configuration not
  covered by GPIO actions (e.g., I2C bus setup).
