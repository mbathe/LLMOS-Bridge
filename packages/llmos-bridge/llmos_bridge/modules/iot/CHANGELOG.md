# Changelog -- IoT Module

## [0.1.0] -- 2026-02-26

### Added
- Initial release with 10 GPIO actions.
- `set_pin_mode` -- Configure a pin as input or output with optional pull
  resistor (none/up/down).
- `digital_read` -- Read the digital value (0 or 1) of a GPIO pin.
- `digital_write` -- Set a GPIO output pin HIGH (1) or LOW (0). Decorated with
  `@audit_trail("standard")`.
- `pwm_start` -- Start a PWM signal with configurable frequency and duty cycle.
  Decorated with `@audit_trail("standard")`.
- `pwm_set_duty_cycle` -- Update the duty cycle of an active PWM channel.
- `pwm_stop` -- Stop a PWM channel on a pin.
- `add_event_detect` -- Register edge-detection (rising/falling/both) with
  configurable debounce time.
- `remove_event_detect` -- Remove edge-detection from a pin.
- `cleanup` -- Release GPIO resources for specific pins or all pins. Decorated
  with `@sensitive_action(RiskLevel.MEDIUM)`.
- `get_pin_state` -- Return current pin modes, values, and PWM channels
  (MockGPIO backend only).
- `GPIOInterface` abstraction with `RaspberryPiGPIO` and `MockGPIO` backends.
- Auto-detection of Raspberry Pi platform via `PlatformInfo.detect()`.
- Security decorators: `@requires_permission`, `@audit_trail`,
  `@sensitive_action` on all GPIO actions.
- Typed Pydantic parameter models for all 10 actions.
