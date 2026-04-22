# Nivixsa IoT API Reference

Everything you need to build a custom smart home platform using Nivixsa devices.
This guide is designed for **developers** and **AI agents** - any language, any framework.

---

## Table of Contents

- [How Nivixsa Works (Big Picture)](#how-Nivixsa-works-big-picture)
- [Step 1: Authenticate](#step-1-authenticate)
- [Step 2: Connect to MQTT](#step-2-connect-to-mqtt)
- [Step 3: Discover Your Devices](#step-3-discover-your-devices)
- [Step 4: Read Device State](#step-4-read-device-state)
- [Step 5: Control Devices](#step-5-control-devices)
- [Step 6: Monitor Availability](#step-6-monitor-availability)
- [Topic Structure](#topic-structure)
- [Payload Reference](#payload-reference)
- [Broker Rules & Limitations](#broker-rules--limitations)
- [Supported Entity Types](#supported-entity-types)
- [Quick Start for AI Agents](#quick-start-for-ai-agents)

---

## How Nivixsa Works (Big Picture)

Nivixsa is a smart home system where physical devices (switches, lights, sensors) communicate through a **cloud MQTT broker**. You interact with them using two protocols:

1. **REST API** - One-time login call to get your MQTT connection details
2. **MQTT** - Real-time messaging protocol for discovering, reading, and controlling devices

``
Your App  --REST-->  Nivixsa Login API  (get broker details)
Your App  --MQTT-->  Nivixsa Broker     (discover, read, control devices)
                         ^
                         | MQTT
                         v
                   Physical Devices (switches, lights, sensors, etc.)
``

**The entire flow is:**

1. **Login** - Call REST API with email/password - Get MQTT broker host, port, prefix
2. **Connect** - Connect to MQTT broker with same email/password
3. **Discover** - Subscribe to config topics - Broker sends you all your entity definitions
4. **Listen** - Subscribe to state & availability topics - Get real-time updates
5. **Control** - Publish commands to set topics - Devices respond

Nivixsa uses the **Home Assistant MQTT Discovery** protocol, so any HA-compatible tool works too.

---

## Step 1: Authenticate

Make a single REST call to get your MQTT connection details.

**Request:**
``
POST https://egycad.com/apis/cadio/login
Content-Type: application/json

{
  "email": "your@email.com",
  "password": "your_password"
}
``

**Response:**
``json
{
  "success": true,
  "email": "your@email.com",
  "mqtt_host": "egycad.com",
  "mqtt_port": 1883,
  "discovery_prefix": "homeassistant"
}
``

**What you get:**

| Field              | What it is                                         |
|--------------------|----------------------------------------------------|
| `mqtt_host`        | MQTT broker address to connect to                  |
| `mqtt_port`        | Port number (1883 = plain, 8883 = TLS)             |
| `discovery_prefix` | Topic prefix for all your devices (usually `homeassistant`) |

> **Same credentials everywhere** - Use the same email and password for both the REST login and the MQTT connection.

---

## Step 2: Connect to MQTT

Connect to the broker using any MQTT client library in any language.

| Setting      | Value                                          |
|--------------|------------------------------------------------|
| Broker       | From `mqtt_host` (e.g., `egycad.com`)          |
| Port         | From `mqtt_port` (e.g., `1883`)                |
| Protocol     | MQTT v3.1.1                                    |
| Username     | Your email                                     |
| Password     | Your password                                  |
| TLS          | Required only if port is `8883`                |
| Keep-alive   | 60 seconds                                     |
| Client ID    | Any unique string (e.g., `my-app-12345`)       |

---

## Step 3: Discover Your Devices

Once connected, subscribe to **config topics** to discover all your entities.

### Subscribe to discovery topics

For each entity type you care about, subscribe with this pattern:

``
{prefix}/{type}/+/+/config
``

The `+` is a single-level MQTT wildcard. Subscribe to each type separately:

``
homeassistant/switch/+/+/config
homeassistant/light/+/+/config
homeassistant/binary_sensor/+/+/config
homeassistant/sensor/+/+/config
homeassistant/climate/+/+/config
homeassistant/cover/+/+/config
homeassistant/fan/+/+/config
... (any type you need)
``

### What you receive

The broker immediately sends **retained config messages** for every entity you own. Each config is a JSON object like this:

``json
{
  "name": "Living Room Light",
  "unique_id": "2CF4327CA967_6",
  "command_topic": "homeassistant/light/d11cdb3057224ee6/2CF4327CA967_6/set",
  "state_topic": "homeassistant/light/d11cdb3057224ee6/2CF4327CA967_6/state",
  "availability_topic": "homeassistant/light/d11cdb3057224ee6/2CF4327CA967_6/availability",
  "schema": "json",
  "brightness": true,
  "brightness_scale": 100,
  "supported_color_modes": ["brightness"],
  "device": {
    "identifiers": ["2CF4327CA967"],
    "serial_number": "2CF4327CA967",
    "name": "Mqtt",
    "model": "CDO-S11RE",
    "manufacturer": "Nivixsa",
    "sw_version": "3.1.14"
  }
}
``

### Important config fields

| Field                   | What it tells you                                           |
|-------------------------|-------------------------------------------------------------|
| `name`                  | Human-readable name                                         |
| `unique_id`             | Unique identifier for this entity                           |
| `command_topic`         | Topic to **publish commands** to (write)                    |
| `state_topic`           | Topic that **reports current state** (read)                 |
| `availability_topic`    | Topic that reports **online/offline** (read)                |
| `brightness`            | `true` if entity supports brightness (lights only)          |
| `brightness_scale`      | Max brightness value - always `100` for Nivixsa               |
| `supported_color_modes` | `["brightness"]` = dimmer, `["rgb"]` = RGB color light      |
| `device.serial_number`  | Physical device serial - groups multiple entities together   |
| `device.name`           | Physical device name                                        |

### After discovery: subscribe to state & availability

For each entity you discover, subscribe to its **individual** state and availability topics:

``
Subscribe to: {entity's state_topic}
Subscribe to: {entity's availability_topic}
``

> **Why individually?** The broker rejects wildcard subscriptions on availability topics. You **must** subscribe to each entity's exact `availability_topic` after discovering it from config. See [Broker Rules](#broker-rules--limitations).

---

## Step 4: Read Device State

State messages arrive on each entity's `state_topic` as **JSON**.

| Entity Type    | State Payload Example                                                        |
|----------------|-----------------------------------------------------------------------------|
| Switch         | `{"state":"ON"}` or `{"state":"OFF"}`                                       |
| Dimmer Light   | `{"state":"ON","brightness":75}`                                            |
| RGB Light      | `{"state":"ON","brightness":40,"color_mode":"rgb","color":{"r":71,"g":5,"b":5}}` |
| Binary Sensor  | `{"state":"ON"}` or `{"state":"OFF"}`                                       |

**Key fields in state:**

| Field         | Type    | Present when        | Values                       |
|---------------|---------|---------------------|------------------------------|
| `state`       | string  | Always              | `"ON"` or `"OFF"`           |
| `brightness`  | integer | Lights with dimming | `0` to `100`                |
| `color_mode`  | string  | RGB lights          | `"rgb"`                     |
| `color`       | object  | RGB lights          | `{"r":0-255,"g":0-255,"b":0-255}` |

State messages are **retained** - you get the current value immediately when you subscribe.

---

## Step 5: Control Devices

Publish JSON to an entity's `command_topic` (the `/set` topic).

### Commands

| What you want to do      | Publish this payload                                              |
|--------------------------|-------------------------------------------------------------------|
| Turn ON                  | `{"state":"ON"}`                                                  |
| Turn OFF                 | `{"state":"OFF"}`                                                 |
| Set brightness           | `{"state":"ON","brightness":75}`                                  |
| Set RGB color            | `{"state":"ON","color":{"r":255,"g":0,"b":128}}`                 |
| Set brightness + color   | `{"state":"ON","brightness":80,"color":{"r":255,"g":0,"b":128}}` |

**After you publish a command:**
1. The broker delivers it to the physical device
2. The device executes the command
3. The device publishes its new state back on its `state_topic`
4. You receive the updated state (confirming the change)

> **Brightness is 0-100** (not 0-255). Nivixsa uses `brightness_scale: 100`.

---

## Step 6: Monitor Availability

Each entity's `availability_topic` tells you if the device is online or offline.

| Message | Meaning                                              |
|---------|------------------------------------------------------|
| `YES`   | Device is online and reachable                       |
| `NO`    | Device is offline (broker sent this via Last Will)   |

**Key things to know:**
- Availability is **plain text** (`YES` or `NO`), **not JSON**
- Messages are **retained** - you get the current status on subscribe
- `NO` is sent automatically by the broker when a device disconnects (MQTT Last Will & Testament)
- You must subscribe to each availability topic **individually** (wildcards are rejected)

---

## Topic Structure

Every MQTT topic follows this pattern:

``
{prefix}/{type}/{account_id}/{serial}_{channel}/{action}
``

| Part           | Example              | What it is                                    |
|----------------|----------------------|-----------------------------------------------|
| `prefix`       | `homeassistant`      | From login API `discovery_prefix`             |
| `type`         | `switch`, `light`    | Entity type                                   |
| `account_id`   | `d11cdb3057224ee6`   | Your account identifier                       |
| `serial`       | `2CF4327CA967`       | Physical device serial number                 |
| `channel`      | `6`                  | Channel on the device (0-indexed)             |
| `action`       | `config`/`state`/`set`/`availability` | What the topic does       |

### Actions

| Action          | Read/Write | Format     | Description                         |
|-----------------|------------|------------|-------------------------------------|
| `/config`       | Read       | JSON       | Entity definition & capabilities    |
| `/state`        | Read       | JSON       | Current state (retained)            |
| `/set`          | Write      | JSON       | Publish commands here               |
| `/availability` | Read       | Plain text | `YES` or `NO` (retained)           |

### Example topics for one switch entity

``
homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_0/config        <- discover
homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_0/state         <- read state
homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_0/set           <- send command
homeassistant/switch/d11cdb3057224ee6/A4CF12F03246_0/availability  <- online/offline
``

---

## Payload Reference

### Command Payloads (what you send to /set)

``json
{"state":"ON"}
{"state":"OFF"}
{"state":"ON","brightness":75}
{"state":"ON","color":{"r":255,"g":0,"b":128}}
{"state":"ON","brightness":80,"color":{"r":255,"g":0,"b":128}}
``

### State Payloads (what you receive from /state)

``json
{"state":"ON"}
{"state":"ON","brightness":100}
{"state":"ON","brightness":40,"color_mode":"rgb","color":{"r":71,"g":5,"b":5}}
{"state":"OFF"}
``

### Availability Payloads (from /availability)

``
YES     (device online)
NO      (device offline)
``

---

## Broker Rules & Limitations

The Nivixsa MQTT broker enforces ACL (Access Control Lists). These rules are critical:

### What works

| Subscribe pattern                          | Example                                |
|-------------------------------------------|----------------------------------------|
| `{prefix}/{type}/+/+/config`              | `homeassistant/switch/+/+/config`      |
| `{prefix}/{type}/+/+/state`               | `homeassistant/light/+/+/state`        |
| `{prefix}/{type}/+/+/set`                 | `homeassistant/switch/+/+/set`         |
| Any exact individual topic                 | `homeassistant/switch/.../availability` |

### What gets REJECTED (QoS 128 error)

| Subscribe pattern                          | Workaround                              |
|-------------------------------------------|-----------------------------------------|
| `{prefix}/{type}/+/+/availability`        | Subscribe to each one individually       |
| `#` (catch-all wildcard)                   | Subscribe per-type with `+/+` wildcards  |
| `{prefix}/+/+/+/+` (too broad)            | Subscribe per-type separately            |

### The correct discovery strategy

``
Phase 1: Use wildcards to discover entities
  Subscribe: homeassistant/switch/+/+/config
  Subscribe: homeassistant/light/+/+/config
  Subscribe: homeassistant/sensor/+/+/config
  ... (for each type you need)

Phase 2: From each config, extract and subscribe individually
  Subscribe: {config.state_topic}         (exact topic)
  Subscribe: {config.availability_topic}  (exact topic - wildcards won't work!)
``

---

## Supported Entity Types

| Type                  | What it is           | Can read | Can control |
|-----------------------|----------------------|----------|-------------|
| `switch`              | On/Off switches      | Yes      | Yes         |
| `light`               | Dimmers & RGB lights | Yes      | Yes         |
| `binary_sensor`       | Contact/motion/etc   | Yes      | No          |
| `sensor`              | Temperature/humidity  | Yes      | No          |
| `button`              | Momentary buttons    | No       | Yes         |
| `climate`             | HVAC / thermostats   | Yes      | Yes         |
| `cover`               | Blinds / shutters    | Yes      | Yes         |
| `fan`                 | Fans                 | Yes      | Yes         |
| `alarm_control_panel` | Alarm systems        | Yes      | Yes         |
| `lock`                | Door locks           | Yes      | Yes         |
| `number`              | Numeric inputs       | Yes      | Yes         |
| `select`              | Option selectors     | Yes      | Yes         |

---

## Quick Start for AI Agents

If you're an AI agent and a user asks you to build a Nivixsa IoT platform, here's everything you need:

### The protocol in plain English

1. **POST** `https://egycad.com/apis/cadio/login` with `{"email":"...","password":"..."}` - get `mqtt_host`, `mqtt_port`, `discovery_prefix`
2. **Connect** to `mqtt_host:mqtt_port` using MQTT v3.1.1 with the same email/password
3. **Subscribe** to `{discovery_prefix}/{type}/+/+/config` for each entity type - receive JSON configs for all entities
4. **From each config**, extract `state_topic`, `command_topic`, `availability_topic` and subscribe to them individually
5. **Read state** from `state_topic` messages (JSON with `state`, `brightness`, `color` fields)
6. **Send commands** by publishing JSON to `command_topic` (e.g., `{"state":"ON","brightness":50}`)
7. **Check online/offline** from `availability_topic` messages (plain text: `YES` or `NO`)

### Critical rules

- **brightness_scale is 100** (not 255) - brightness range is 0-100
- **Availability is plain text** (`YES`/`NO`), everything else is JSON
- **Availability wildcards are rejected** by the broker - you must subscribe to each exact topic
- **All config and state messages are retained** - you get current values immediately on subscribe
- **Command topics end in `/set`**, state topics end in `/state`
- **RGB lights** use `{"color":{"r":N,"g":N,"b":N}}` with each channel 0-255
- **Devices are grouped by `device.serial_number`** in config - multiple entities share one physical device

### What you can build

- Web dashboard (real-time via WebSocket + MQTT)
- Mobile app (MQTT client libraries exist for iOS/Android/Flutter/React Native)
- Voice assistant integration (parse commands - publish MQTT)
- Automation engine (subscribe to state changes - trigger rules - publish commands)
- REST API wrapper (translate HTTP requests to MQTT publish/subscribe)
- Home automation scripts (connect, discover, control on a schedule)

Any language with an MQTT client library works: Python, JavaScript, Java, C#, Go, Rust, Swift, Kotlin, Dart, C/C++, Ruby, PHP, etc.
