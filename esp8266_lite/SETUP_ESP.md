# Nivixsa IoT Dashboard — ESP8266 Lite Firmware

## Overview

A lightweight firmware for **ESP8266 NodeMCU v2** that connects to an MQTT broker, discovers devices via Home Assistant MQTT discovery protocol, and serves a local web dashboard. Only devices matching a configured **unit serial number** are displayed.

---

## Required Hardware

- **ESP8266 NodeMCU v2** (or compatible)
- USB cable for flashing
- GPIO0 (D3) — FLASH / Factory Reset button (hold 3 seconds)
- GPIO2 (D4) — Built-in status LED (active LOW)

---

## Required Libraries

Install these in Arduino IDE via **Sketch → Include Library → Manage Libraries**:

| Library         | Version  | Purpose                |
|-----------------|----------|------------------------|
| PubSubClient    | 2.8+     | MQTT client            |
| ArduinoJson     | 7.x      | JSON parsing           |
| ESP8266WiFi     | built-in | Wi-Fi (STA + AP)       |
| ESP8266WebServer| built-in | HTTP server            |
| ESP8266HTTPClient| built-in| HTTPS API calls        |
| DNSServer       | built-in | Captive portal         |
| EEPROM          | built-in | Credential storage     |

---

## Arduino IDE Board Settings

1. **File → Preferences → Additional Board URLs:**
   ```
   http://arduino.esp8266.com/stable/package_esp8266com_index.json
   ```
2. **Tools → Board → ESP8266 Boards → NodeMCU 1.0 (ESP-12E Module)**
3. Settings:
   - **CPU Frequency:** 80 MHz
   - **Flash Size:** 4MB (FS: 1MB, OTA: ~1MB)
   - **Upload Speed:** 115200
   - **Port:** Select the COM port for your NodeMCU

---

## Flashing

1. Open `esp8266_lite/esp8266_lite.ino` in Arduino IDE
2. Ensure all 3 header files are in the same folder:
   - `config.h` — compile-time constants
   - `credentials.h` — EEPROM credential storage
   - `pages.h` — HTML pages (PROGMEM)
3. Click **Upload** (Ctrl+U)
4. Open **Serial Monitor** at **115200 baud** to see logs

---

## First-Time Setup (AP Mode)

When no credentials are saved (or after a factory reset), the ESP starts in **Access Point mode**:

1. The ESP creates a Wi-Fi network named **`Nivixsa-Setup-XXXX`** (open, no password)
2. Connect your phone/laptop to this network
3. A captive portal page should **pop up automatically**
   - If it doesn't, open a browser and go to `http://192.168.4.1`
4. Enter the **Setup PIN** (default: `1234`)
5. Fill in the configuration:

   | Field              | Description                                        |
   |--------------------|----------------------------------------------------|
   | **Wi-Fi SSID**     | Select from scan or type manually                  |
   | **Wi-Fi Password** | Your Wi-Fi password                                |
   | **Email**          | Your Nivixsa/Cadio account email                   |
   | **MQTT Password**  | Your MQTT password                                 |
   | **Unit Serial**    | The MAC prefix of your physical unit (e.g. `A4CF12F03246`) |
   | **New PIN**        | Optional — change the setup PIN                    |

6. Click **Save & Restart**
7. The ESP reboots, connects to Wi-Fi, logs into the API, and connects to MQTT

### How to find your Unit Serial

The unit serial is the **base MAC address** of the physical unit (the part before the first `_` in MQTT entity serial IDs). You can find it in:
- The Nivixsa/Cadio app under device info
- MQTT discovery topics: `homeassistant/switch/<account_id>/<SERIAL>_0/config`
- The serial printed on the physical unit

---

## Dashboard

Once connected, access the dashboard at:

```
http://<ESP-IP-ADDRESS>/
```

The IP address is printed in the serial monitor on boot:
```
[WiFi] Connected. IP: 192.168.1.100
[WEB] http://192.168.1.100/
```

### Dashboard Features

- **Network info** — Wi-Fi SSID, IP, signal strength
- **MQTT status** — broker, connection status, message count
- **Unit info** — configured serial, uptime, free heap memory
- **Device cards** — only devices matching the configured serial:
  - **Switches** — on/off toggle
  - **Lights** — on/off toggle + brightness slider + color picker (if supported)
  - **Sensors** — displays current value
  - **Binary sensors** — displays state
  - **Covers, fans, locks** — on/off toggle
- **MQTT message log** — last 10 messages received
- Auto-refreshes every **2 seconds**

### Dashboard Endpoints

| URL           | Method | Description                    |
|---------------|--------|--------------------------------|
| `/`           | GET    | Dashboard page                 |
| `/api/data`   | GET    | JSON API (devices, status)     |
| `/cmd`        | GET    | Send command to device         |
| `/setup`      | GET    | Setup/reconfigure page         |
| `/reset`      | GET    | Factory reset (wipes EEPROM)   |

---

## LED Indicator

| Pattern         | Meaning                          |
|-----------------|----------------------------------|
| **Fast blink**  | Wi-Fi disconnected / AP mode     |
| **Solid ON**    | Wi-Fi connected                  |

---

## Factory Reset

Two methods:

1. **Hardware:** Hold the **FLASH button** (GPIO0) for **3 seconds** → clears EEPROM and reboots into AP mode
2. **Software:** Navigate to `http://<ESP-IP>/reset` and confirm

---

## Serial Monitor Logs

Key log prefixes at 115200 baud:

| Prefix    | Meaning                                     |
|-----------|---------------------------------------------|
| `[CFG]`   | Configuration loaded from EEPROM            |
| `[WiFi]`  | Wi-Fi connection status                     |
| `[API]`   | Cloud API login result                      |
| `[MQTT]`  | MQTT connection/subscription status         |
| `[MATCH]` | Device config accepted (serial matches)     |
| `[SKIP]`  | Device config rejected (serial mismatch)    |
| `[DEV]`   | Device registered/updated                   |
| `[UNIT]`  | Physical unit name detected                 |
| `[BTN]`   | Factory reset triggered via button          |
| `[DEV] FULL!` | MAX_DEVICES (30) reached, can't add more |

---

## Stability

The firmware is designed to run **24/7** without hanging or white-page crashes:

- HTML pages are served directly from **flash (PROGMEM)** — zero heap allocation
- API JSON response uses a **pre-reserved buffer** — no temporary String objects
- MQTT message buffer uses **fixed-size char arrays** — no heap fragmentation
- `yield()` is called in device loops to prevent watchdog resets
- Wi-Fi and MQTT auto-reconnect on disconnection

---

## File Structure

```
esp8266_lite/
├── esp8266_lite.ino   — Main firmware (setup, loop, MQTT, HTTP handlers)
├── config.h           — Compile-time constants (pins, timeouts, limits)
├── credentials.h      — EEPROM read/write for credentials + serial
├── pages.h            — Setup + Dashboard HTML (PROGMEM, zero heap)
└── SETUP_ESP.md       — This file
```

---

## Troubleshooting

| Problem                        | Solution                                              |
|--------------------------------|-------------------------------------------------------|
| White/blank page               | Should not happen in lite firmware. Check serial monitor for heap value |
| No devices showing             | Verify serial number matches. Check `[SKIP]` / `[MATCH]` logs |
| Some devices missing           | Check `[DEV] FULL!` log. MAX_DEVICES is 30            |
| MQTT not connecting            | Check email/password. Look for `[MQTT] Failed rc=` log |
| Can't reach dashboard          | Check IP in serial monitor. Ensure same Wi-Fi network  |
| Captive portal not popping up  | Manually browse to `http://192.168.4.1`               |
| Setup PIN forgotten            | Hold FLASH button 3 seconds for factory reset          |
