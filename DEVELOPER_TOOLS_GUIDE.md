# Nivixsa Dashboard — Developer Tools Guide

## How to Access

1. Open **http://localhost:5000** in your browser
2. Log in with your Nivixsa credentials
3. Click **Developer** (code icon `</>`) in the left sidebar

---

## MQTT Publish

Enter a **Topic** and **Payload**, then click **Publish**.

The Topic field has autocomplete — start typing and it will suggest topics from discovered devices.

---

## Your Devices

| Device Name | Serial         | Model                  | Firmware |
|-------------|----------------|------------------------|----------|
| Mqtt        | 2CF4327CA967   | Home Automation Unit   | V201     |
| Tvunit      | A4CF12F03246   | Home Automation Unit   | V201     |

---

## Switches

Switches are simple ON/OFF. Use the `/set` topic with a JSON payload.

### Topics

| Entity   | Command Topic                                                        |
|----------|----------------------------------------------------------------------|
| Line 0   | `homeassistant/switch/d11cdb3057224ee6/2CF4327CA967_0/set`           |
| Line 1   | `homeassistant/switch/d11cdb3057224ee6/2CF4327CA967_1/set`           |
| Line 2   | `homeassistant/switch/d11cdb3057224ee6/2CF4327CA967_2/set`           |
| Line 0   | `homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_0/set`           |
| Line 1   | `homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_1/set`           |
| Line 2   | `homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_2/set`           |
| Line 3   | `homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_3/set`           |
| Line 4   | `homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_4/set`           |

### Commands

**Turn ON:**
```json
{"state":"ON"}
```

**Turn OFF:**
```json
{"state":"OFF"}
```

---

## Dimmer Lights (Brightness only)

These lights support ON/OFF and brightness (0–100).

### Topics

| Entity   | Command Topic                                                       |
|----------|---------------------------------------------------------------------|
| Line 5   | `homeassistant/light/d11cdb3057224ee6/A4CF12F03246_5/set`           |
| Line 6   | `homeassistant/light/d11cdb3057224ee6/A4CF12F03246_6/set`           |
| Line 6   | `homeassistant/light/d11cdb3057224ee6/2CF4327CA967_6/set`           |
| Line 7   | `homeassistant/light/d11cdb3057224ee6/2CF4327CA967_7/set`           |

### Commands

**Turn ON:**
```json
{"state":"ON"}
```

**Turn OFF:**
```json
{"state":"OFF"}
```

**Set brightness (0–100):**
```json
{"state":"ON","brightness":75}
```

**Set brightness to minimum:**
```json
{"state":"ON","brightness":1}
```

**Set brightness to maximum:**
```json
{"state":"ON","brightness":100}
```

---

## RGB Lights (Brightness + Color)

These lights support ON/OFF, brightness (0–100), and RGB color.

### Topics

| Entity   | Command Topic                                                       |
|----------|---------------------------------------------------------------------|
| Rgb 7    | `homeassistant/light/d11cdb3057224ee6/A4CF12F03246_7/set`           |
| Rgb 8    | `homeassistant/light/d11cdb3057224ee6/2CF4327CA967_8/set`           |

### Commands

**Turn ON:**
```json
{"state":"ON"}
```

**Turn OFF:**
```json
{"state":"OFF"}
```

**Set brightness:**
```json
{"state":"ON","brightness":50}
```

**Set color (Red):**
```json
{"state":"ON","color":{"r":255,"g":0,"b":0}}
```

**Set color (Green):**
```json
{"state":"ON","color":{"r":0,"g":255,"b":0}}
```

**Set color (Blue):**
```json
{"state":"ON","color":{"r":0,"g":0,"b":255}}
```

**Set color (White):**
```json
{"state":"ON","color":{"r":255,"g":255,"b":255}}
```

**Set color (Warm Yellow):**
```json
{"state":"ON","color":{"r":255,"g":180,"b":50}}
```

**Set color + brightness together:**
```json
{"state":"ON","brightness":80,"color":{"r":128,"g":0,"b":255}}
```

---

## Binary Sensors

Binary sensors are **read-only** — you cannot send commands to them. They report their state automatically.

### State Topics (read-only)

| Entity     | State Topic                                                              |
|------------|--------------------------------------------------------------------------|
| Sensor 8   | `homeassistant/binary_sensor/d11cdb3057224ee6/A4CF12F03246_8/state`      |
| Sensor 9   | `homeassistant/binary_sensor/d11cdb3057224ee6/A4CF12F03246_9/state`      |

### State Payloads (received)

```json
{"state":"ON"}
{"state":"OFF"}
```

### Availability Topics

| Entity     | Availability Topic                                                        |
|------------|---------------------------------------------------------------------------|
| Sensor 8   | `homeassistant/binary_sensor/d11cdb3057224ee6/A4CF12F03246_8/availability` |
| Sensor 9   | `homeassistant/binary_sensor/d11cdb3057224ee6/A4CF12F03246_9/availability` |

Availability payload: `YES` (online) or `NO` (offline).

---

## Reading Current State

To check the current state of any entity, look at its `/state` topic. Replace `/set` with `/state` in any command topic above.

Example — read Line 0 switch state:

**Topic:** `homeassistant/switch/d11cdb3057224ee6/2CF4327CA967_0/state`

The payload you'll see in the Logbook:
```json
{"state":"ON"}
```
or
```json
{"state":"OFF"}
```

---

## Reading Config

To see the full discovery config of any entity, look at its `/config` topic.

**Topic:** `homeassistant/light/d11cdb3057224ee6/2CF4327CA967_8/config`

This returns the full JSON with name, supported features, device info, etc.

---

## Topic Pattern Reference

All Nivixsa topics follow this pattern:

```
homeassistant/{type}/{account_id}/{serial_channel}/{action}
```

| Part            | Example                | Description                        |
|-----------------|------------------------|------------------------------------|
| `type`          | `light`, `switch`, `binary_sensor` | Entity type               |
| `account_id`    | `d11cdb3057224ee6`     | Your Nivixsa account ID              |
| `serial_channel`| `2CF4327CA967_6`       | Device serial + channel number     |
| `action`        | `set`, `state`, `config`, `availability` | What to do        |

| Action          | Purpose                                    |
|-----------------|--------------------------------------------|
| `/config`       | Discovery config (auto-detected)           |
| `/state`        | Current state (read-only, published by device) |
| `/set`          | Send commands (write)                      |
| `/availability` | Online status (`YES` / `NO`)               |

---

## Quick Copy-Paste Examples

### Turn all Mqtt unit switches OFF
```
Topic: homeassistant/switch/d11cdb3057224ee6/2CF4327CA967_0/set
Payload: {"state":"OFF"}
```
Repeat for `_1`, `_2`.

### Set Rgb 8 to purple at 60% brightness
```
Topic: homeassistant/light/d11cdb3057224ee6/2CF4327CA967_8/set
Payload: {"state":"ON","brightness":60,"color":{"r":128,"g":0,"b":255}}
```

### Dim Line 6 to 30%
```
Topic: homeassistant/light/d11cdb3057224ee6/2CF4327CA967_6/set
Payload: {"state":"ON","brightness":30}
```
