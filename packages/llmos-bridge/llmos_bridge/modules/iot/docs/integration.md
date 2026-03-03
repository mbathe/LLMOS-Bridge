# IoT Module -- Integration Guide

## Cross-Module Workflows

### LED Blink with Permission Request

Request GPIO permissions through the security module, then blink an LED using
digital write with a timed sequence.

```yaml
actions:
  - id: grant-gpio
    module: security
    action: request_permission
    params:
      permission: gpio.write
      module_id: iot
      reason: "Need to control LED on pin 18"
      scope: session

  - id: setup-led
    module: iot
    action: set_pin_mode
    depends_on: [grant-gpio]
    params:
      pin: 18
      mode: output

  - id: led-on
    module: iot
    action: digital_write
    depends_on: [setup-led]
    params:
      pin: 18
      value: 1
```

### PWM-Controlled Servo Motor

Start a PWM signal at the correct frequency for a servo motor, then adjust
the duty cycle to set the position.

```yaml
actions:
  - id: setup-servo-pin
    module: iot
    action: set_pin_mode
    params:
      pin: 12
      mode: output

  - id: start-pwm
    module: iot
    action: pwm_start
    depends_on: [setup-servo-pin]
    params:
      pin: 12
      frequency_hz: 50
      duty_cycle: 7.5

  - id: move-to-90
    module: iot
    action: pwm_set_duty_cycle
    depends_on: [start-pwm]
    params:
      pin: 12
      duty_cycle: 7.5

  - id: move-to-180
    module: iot
    action: pwm_set_duty_cycle
    depends_on: [move-to-90]
    params:
      pin: 12
      duty_cycle: 12.5
```

### Button Input with Edge Detection

Set up a button on an input pin with pull-up resistor and edge detection,
then read the current state.

```yaml
actions:
  - id: setup-button
    module: iot
    action: set_pin_mode
    params:
      pin: 17
      mode: input
      pull: up

  - id: register-event
    module: iot
    action: add_event_detect
    depends_on: [setup-button]
    params:
      pin: 17
      edge: falling
      bouncetime_ms: 300

  - id: read-state
    module: iot
    action: digital_read
    depends_on: [register-event]
    params:
      pin: 17
```

### GPIO with GUI Feedback

Combine GPIO control with screenshot capture to verify physical device state
through a connected display.

```yaml
actions:
  - id: toggle-relay
    module: iot
    action: digital_write
    params:
      pin: 23
      value: 1

  - id: capture-screen
    module: gui
    action: screenshot
    depends_on: [toggle-relay]
    params:
      region: full

  - id: check-state
    module: iot
    action: get_pin_state
    depends_on: [toggle-relay]
```

### Safe Cleanup on Completion

After a sequence of GPIO operations, clean up all resources. Use
`on_error: continue` to ensure cleanup runs even if earlier actions fail.

```yaml
actions:
  - id: setup
    module: iot
    action: set_pin_mode
    params:
      pin: 18
      mode: output

  - id: work
    module: iot
    action: digital_write
    depends_on: [setup]
    params:
      pin: 18
      value: 1
    on_error: continue

  - id: cleanup
    module: iot
    action: cleanup
    depends_on: [work]
    on_error: continue
    params:
      pins: [18]
```

### IoT Status via Module Manager

Use the module manager to check IoT module health and inspect its current
state before running hardware operations.

```yaml
actions:
  - id: check-iot
    module: module_manager
    action: get_module_health
    params:
      module_id: iot

  - id: iot-state
    module: module_manager
    action: get_module_state
    depends_on: [check-iot]
    params:
      module_id: iot

  - id: proceed
    module: iot
    action: set_pin_mode
    depends_on: [iot-state]
    params:
      pin: 18
      mode: output
```

## GPIO Backend Architecture

The IoT module uses the `GPIOInterface` abstraction to support multiple
hardware backends:

```
IoTModule
  -> GPIOInterface (ABC)
    -> RaspberryPiGPIO  (RPi.GPIO wrapper, real hardware)
    -> MockGPIO          (in-memory, for tests and development)
```

### Backend Selection

1. `PlatformInfo.detect()` checks if the system is a Raspberry Pi
2. On Pi: attempts to instantiate `RaspberryPiGPIO`
3. Falls back to `MockGPIO` if RPi.GPIO is not installed
4. Non-Pi systems always use `MockGPIO`

### Pin Numbering

All actions use BCM (Broadcom) pin numbering. The valid range is 0-53,
matching the Broadcom BCM2835/BCM2711 GPIO numbering scheme. Physical board
pin numbers are not supported directly -- use a BCM pinout reference.

### Permission Model

| Permission | Actions |
|-----------|---------|
| `Permission.GPIO_READ` | `digital_read`, `add_event_detect`, `remove_event_detect`, `get_pin_state` |
| `Permission.GPIO_WRITE` | `set_pin_mode`, `digital_write`, `cleanup` |
| `Permission.ACTUATOR` | `pwm_start`, `pwm_set_duty_cycle`, `pwm_stop` |
