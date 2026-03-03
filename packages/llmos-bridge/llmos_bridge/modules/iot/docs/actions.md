# IoT Module -- Action Reference

## set_pin_mode

Configure a GPIO pin as input or output.

**Permission required:** `power_user`
**Risk level:** Medium
**Security:** `@requires_permission(Permission.GPIO_WRITE)`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `pin` | integer | Yes | -- | BCM pin number (0-53) |
| `mode` | string | Yes | -- | Pin mode: `input` or `output` |
| `pull` | string | No | `"none"` | Pull resistor: `none`, `up`, or `down` |

### Returns

```json
{
  "pin": 18,
  "mode": "output",
  "pull": "none"
}
```

---

## digital_read

Read the digital value (0 or 1) of a GPIO pin.

**Permission required:** `power_user`
**Risk level:** Low
**Security:** `@requires_permission(Permission.GPIO_READ)`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `pin` | integer | Yes | BCM pin number (0-53) |

### Returns

```json
{
  "pin": 18,
  "value": 1
}
```

---

## digital_write

Set a GPIO output pin HIGH (1) or LOW (0).

**Permission required:** `power_user`
**Risk level:** Medium
**Security:** `@requires_permission(Permission.GPIO_WRITE)`, `@audit_trail("standard")`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `pin` | integer | Yes | BCM pin number (0-53) |
| `value` | integer | Yes | Output value: `0` (LOW) or `1` (HIGH) |

### Returns

```json
{
  "pin": 18,
  "value": 1
}
```

---

## pwm_start

Start a PWM signal on a GPIO pin.

**Permission required:** `power_user`
**Risk level:** Medium
**Security:** `@requires_permission(Permission.ACTUATOR)`, `@audit_trail("standard")`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `pin` | integer | Yes | -- | BCM pin number (0-53) |
| `frequency_hz` | number | Yes | -- | PWM frequency in Hz (1.0-1000000.0) |
| `duty_cycle` | number | No | `50.0` | Initial duty cycle (0.0-100.0) |

### Returns

```json
{
  "pin": 18,
  "frequency_hz": 1000.0,
  "duty_cycle": 50.0
}
```

---

## pwm_set_duty_cycle

Update the duty cycle of an active PWM channel.

**Permission required:** `power_user`
**Risk level:** Low
**Security:** `@requires_permission(Permission.ACTUATOR)`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `pin` | integer | Yes | BCM pin number (0-53) |
| `duty_cycle` | number | Yes | New duty cycle (0.0-100.0) |

### Returns

```json
{
  "pin": 18,
  "duty_cycle": 75.0
}
```

---

## pwm_stop

Stop the PWM channel on a pin.

**Permission required:** `power_user`
**Risk level:** Low
**Security:** `@requires_permission(Permission.ACTUATOR)`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `pin` | integer | Yes | BCM pin number (0-53) |

### Returns

```json
{
  "pin": 18,
  "stopped": true
}
```

---

## add_event_detect

Register an edge-detection event on a GPIO input pin.

**Permission required:** `power_user`
**Risk level:** Low
**Security:** `@requires_permission(Permission.GPIO_READ)`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `pin` | integer | Yes | -- | BCM pin number (0-53) |
| `edge` | string | No | `"both"` | Edge type: `rising`, `falling`, or `both` |
| `bouncetime_ms` | integer | No | `200` | Debounce time in milliseconds (0-10000) |

### Returns

```json
{
  "pin": 17,
  "edge": "rising",
  "bouncetime_ms": 200
}
```

---

## remove_event_detect

Remove edge-detection from a GPIO pin.

**Permission required:** `power_user`
**Risk level:** Low
**Security:** `@requires_permission(Permission.GPIO_READ)`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `pin` | integer | Yes | BCM pin number (0-53) |

### Returns

```json
{
  "pin": 17,
  "removed": true
}
```

---

## cleanup

Release GPIO resources for specific pins or all pins.

**Permission required:** `power_user`
**Risk level:** Medium
**Security:** `@requires_permission(Permission.GPIO_WRITE)`, `@sensitive_action(RiskLevel.MEDIUM)`
**Platforms:** raspberry_pi, linux

### Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `pins` | array | No | `null` | List of BCM pin numbers to release. Omit to release all |

### Returns

```json
{
  "pins": [17, 18],
  "cleaned": true
}
```

---

## get_pin_state

Return the current state of all configured pins. Only available with the
MockGPIO backend (development/testing).

**Permission required:** `readonly`
**Risk level:** Low
**Security:** `@requires_permission(Permission.GPIO_READ)`
**Platforms:** raspberry_pi, linux

### Parameters

None.

### Returns

```json
{
  "pin_modes": {"17": "INPUT", "18": "OUTPUT"},
  "pin_values": {"17": 0, "18": 1},
  "pwm_channels": {
    "12": {
      "frequency_hz": 1000.0,
      "duty_cycle": 50.0
    }
  }
}
```
