# ESP8266 Lite — Agent / Developer Guide

> This document is for **AI coding agents** and **developers** working on the esp8266_lite firmware.
> It describes the architecture, constraints, conventions, and critical rules to follow.

---

## Project Overview

ESP8266 Lite is a **single-unit IoT dashboard** firmware for ESP8266 NodeMCU v2.
It connects to an MQTT broker using Home Assistant discovery protocol, filters devices by a configured **serial number** (MAC prefix), and serves a local web dashboard with on/off, brightness, and color controls.

### Core Files

| File               | Purpose                                                    |
|--------------------|------------------------------------------------------------|
| `esp8266_lite.ino` | Main firmware: setup/loop, MQTT handling, HTTP handlers    |
| `config.h`         | All compile-time constants (pins, limits, timeouts, URLs)  |
| `credentials.h`    | EEPROM-backed credential storage (`CredentialStore` class) |
| `pages.h`          | All HTML pages as `PROGMEM` C string literals              |

---

## Architecture

```
┌──────────────┐      ┌────────────────┐      ┌──────────────┐
│  MQTT Broker │◄────►│  ESP8266 Lite  │◄────►│  Browser     │
│  (egycad.com)│      │                │      │  Dashboard   │
└──────────────┘      │  ┌───────────┐ │      └──────────────┘
                      │  │ Device[]  │ │
                      │  │ (RAM)     │ │
                      │  └───────────┘ │
                      │  ┌───────────┐ │
                      │  │ EEPROM    │ │
                      │  │ (creds)   │ │
                      │  └───────────┘ │
                      │  ┌───────────┐ │
                      │  │ PROGMEM   │ │
                      │  │ (HTML)    │ │
                      │  └───────────┘ │
                      └────────────────┘
```

### Two Modes

1. **AP Setup Mode** — No credentials or no serial configured. ESP creates a Wi-Fi AP, serves captive portal at `192.168.4.1`. User configures Wi-Fi, MQTT, and unit serial.
2. **Normal Mode** — Connects to Wi-Fi, logs into cloud API for broker info, connects MQTT, serves dashboard.

### Data Flow

1. MQTT broker sends `homeassistant/<type>/<accountId>/<serialId>/config` messages
2. `mqttCallback()` receives them → `parseConfig()` filters by serial → stores in `Device[]`
3. State messages update `Device[].state` via `updateState()`
4. Browser polls `/api/data` every 2 seconds → gets JSON with all devices
5. Browser sends commands via `/cmd?topic=...&payload=...` → ESP publishes to MQTT

---

## CRITICAL STABILITY RULES

**The ESP8266 has ~40KB usable heap. Violating these rules WILL cause white-page crashes or WDT resets.**

### 1. NEVER copy HTML from PROGMEM to heap

```cpp
// ✅ CORRECT — zero heap allocation
server.send_P(200, "text/html", DASHBOARD_HTML);

// ❌ WRONG — allocates 10-15KB on heap, causes fragmentation
String html = FPSTR(DASHBOARD_HTML);
server.send(200, "text/html", html);
```

### 2. NEVER create temporary String objects in handleApiData()

```cpp
// ✅ CORRECT — append directly to pre-reserved buffer
json.reserve(4096);
json += "{\"name\":\"";
jsonStr(json, devices[i].name);  // writes char-by-char, no temp String

// ❌ WRONG — creates temporary String on every call
json += "\"name\":\"" + String(devices[i].name) + "\",";
```

### 3. Use char arrays in structs, not String

```cpp
// ✅ CORRECT
struct Device {
  char name[48];
  char state[48];
};

// ❌ WRONG — String uses heap, fragments over time
struct Device {
  String name;
  String state;
};
```

### 4. Always call yield() in device iteration loops

```cpp
for (int i = 0; i < devCount; i++) {
  // ... build JSON for device ...
  yield();  // prevents watchdog timeout reset
}
```

### 5. MQTT callback must handle full payload

PubSubClient's buffer is 2048 bytes. Config messages can be 500-2000 bytes. Never truncate before parsing:

```cpp
// ✅ CORRECT — null-terminate in place, parse full payload
payload[length] = '\0';
String p((const char*)payload);
parseConfig(t, p);

// ❌ WRONG — truncates config JSON, deserializeJson fails
char buf[256];
memcpy(buf, payload, min(length, 255));
```

### 6. Message log buffer must be fixed-size char arrays

```cpp
// ✅ CORRECT — zero heap, fixed size
struct Msg { char topic[52]; char payload[42]; };

// ❌ WRONG — String grows on heap, fragments
struct Msg { String topic; String payload; };
```

---

## MQTT Discovery Protocol

### Topic Format
```
homeassistant/<type>/<account_id>/<serial_id>/config    — device registration
homeassistant/<type>/<account_id>/<serial_id>/state     — state updates
homeassistant/<type>/<account_id>/<serial_id>/set       — commands
```

- `<type>`: switch, light, sensor, binary_sensor, climate, cover, fan, lock
- `<account_id>`: shared across all devices for one account
- `<serial_id>`: unique per entity, format: `<MAC>_<channel>` (e.g., `A4CF12F03246_0`, `A4CF12F03246_LINE_1`)

### Serial Number Filtering

The configured `unitSerial` is a MAC prefix (e.g., `A4CF12F03246`). A device matches if its `serialId` **starts with** that prefix (case-insensitive).

```cpp
bool serialMatches(const char *serialId) {
  return strncasecmp(serialId, creds.unitSerial.c_str(), creds.unitSerial.length()) == 0;
}
```

### Config Payload (JSON)

Key fields extracted from config messages:
```json
{
  "name": "Light 1",
  "state_topic": "homeassistant/light/123/A4CF12F03246_0/state",
  "command_topic": "homeassistant/light/123/A4CF12F03246_0/set",
  "brightness": true,
  "supported_color_modes": ["rgb", "brightness"],
  "value_template": "{{ value_json.consumption }}",
  "device": { "name": "Living Room Unit" }
}
```

### Multi-Value Sensors

Multiple entities can share the same `state_topic` with different `value_template`:
- `{{ value_json.consumption }}` → `valueKey = "consumption"`
- `{{ value_json.voltage }}` → `valueKey = "voltage"`

The `valueKey` is extracted by parsing after `value_json.` in the template string.
In `updateState()`, if `valueKey` is set, only that specific JSON field is extracted.
The loop does NOT break after first match — all entities sharing a topic get updated.

---

## HTTP Endpoints

| Route        | Handler            | Method | Notes                                |
|--------------|--------------------|--------|--------------------------------------|
| `/`          | `handleDashboard`  | GET    | `send_P()` — zero heap              |
| `/api/data`  | `handleApiData`    | GET    | JSON, `reserve(4096)`, polled 2s     |
| `/cmd`       | `handleCmd`        | GET    | `?topic=...&payload=...&raw=0\|1`    |
| `/setup`     | `handleSetupPage`  | GET    | `send_P()` — zero heap              |
| `/save`      | `handleSave`       | POST   | JSON body, requires PIN unlock       |
| `/scan`      | `handleScan`       | GET    | Returns Wi-Fi networks as JSON       |
| `/check_pin` | `handleCheckPin`   | GET    | `?pin=...` validates setup PIN       |
| `/reset`     | `handleReset`      | GET    | Clears EEPROM, reboots               |

---

## EEPROM Layout

`StoredCredentials` struct written at offset 0:

| Field             | Size   | Description                    |
|-------------------|--------|--------------------------------|
| `magic`           | 4      | `0x4E495632` ('NIV2')          |
| `wifiSSID`        | 64     | Wi-Fi network name             |
| `wifiPassword`    | 64     | Wi-Fi password                 |
| `mqttEmail`       | 96     | MQTT login email               |
| `mqttPassword`    | 96     | MQTT login password            |
| `setupPin`        | 24     | Setup page PIN                 |
| `mqttBroker`      | 64     | Broker hostname                |
| `mqttPort`        | 4      | Broker port (int32)            |
| `discoveryPrefix` | 32     | MQTT prefix (default: homeassistant) |
| `unitSerial`      | 48     | Unit serial number (MAC prefix)|

Total: ~496 bytes within 1024-byte EEPROM allocation.

**Note:** Magic value is `0x4E495632` (different from old firmware's `0x4E495658`). This means old credentials won't be loaded — a fresh setup is required.

---

## JSON Helper Functions

Two helpers for building JSON without heap allocations:

```cpp
void jsonStr(String &j, const char *s);  // appends chars, replaces " with ' and \ with /
void jsonInt(String &j, int v);           // appends int via stack itoa()
```

Usage pattern:
```cpp
json += "{\"key\":\"";
jsonStr(json, someCharArray);
json += "\",\"num\":";
jsonInt(json, someInt);
json += "}";
```

---

## Device Struct

```cpp
struct Device {
  bool active;
  bool hasBrightness;
  bool hasRgb;
  int  brightness;           // 0-100, -1 = unknown
  int  colorR, colorG, colorB; // 0-255, -1 = unknown
  char name[48];
  char deviceId[48];
  char serialId[48];
  char type[16];             // switch, light, sensor, etc.
  char stateTopic[96];
  char cmdTopic[96];
  char state[48];
  char valueKey[20];         // JSON key for multi-value sensors
};
```

- Max devices: `MAX_DEVICES = 30` (defined in config.h)
- Slot lookup: by `serialId` match (unique per entity)
- New slots: `devCount++`, returns -1 if full

---

## Adding New Features — Checklist

When modifying this firmware, verify:

- [ ] No `String` temporaries created in frequently-called functions
- [ ] No `FPSTR()` + `String` for HTML — always `send_P()`
- [ ] `yield()` in any loop over devices or subscriptions
- [ ] New EEPROM fields added to BOTH `AppCredentials` and `StoredCredentials`
- [ ] New HTML added inside existing `PROGMEM` string literals (not separate heap strings)
- [ ] Serial filter applied in `parseConfig()` before storing device
- [ ] `json.reserve()` size sufficient for max devices × ~300 bytes each
- [ ] MQTT buffer size (`MQTT_BUFFER_SIZE = 2048`) sufficient for config payloads
- [ ] Test with serial monitor: check `[DEV]`, `[MATCH]`, `[SKIP]` logs
- [ ] Test dashboard: refresh multiple times, check heap doesn't drop below 15KB

---

## Common Pitfalls

| Mistake | Consequence | Fix |
|---------|-------------|-----|
| `String html = FPSTR(HTML)` | 10-15KB heap allocation → white page | Use `send_P()` |
| `String(value)` in loops | Heap fragmentation over hours | Use `jsonStr()`/`jsonInt()` |
| `char buf[256]` for MQTT payload | Config JSON truncated → no devices | Use full `payload[length]` |
| Matching serial by exact `==` | Misses `A4CF12F03246_LINE_1` format | Use `startsWith` / `strncasecmp` |
| `break` in `updateState()` loop | Only first multi-value sensor updated | Remove break, iterate all |
| Missing `yield()` in device loop | Watchdog timer reset (WDT) | Add `yield()` per iteration |
| `MAX_DEVICES` too low | Silent device drop, no error shown | Set to 30+, log when full |
