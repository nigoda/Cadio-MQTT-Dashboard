# Cadio MQTT Dashboard — Nivixsa Smart Irrigation

A real-time, multi-tenant IoT dashboard for smart irrigation and home automation. It bridges Nivixsa/Cadio hardware (switches, dimmers, RGB lights, sensors) to a cloud **MQTT** broker and gives each user a live web dashboard, a deterministic automation engine, an AI agronomist, web-push alerts, and an admin command center — all installable as a Progressive Web App.

> **Live deployment:** https://nivixsa.mattrlabs.online (via Cloudflare Tunnel)

---

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Quick Start (Local)](#quick-start-local)
- [Docker Deployment](#docker-deployment)
- [Configuration (.env)](#configuration-env)
- [Firmware & Hardware](#firmware--hardware)
- [Documentation](#documentation)
- [Cloud Deployment Options](#cloud-deployment-options)
- [MQTT / API Protocol Reference](#mqtt--api-protocol-reference)

---

## Features

- **Multi-tenant isolation** — each user gets their own MQTT connection to the Nivixsa cloud broker, plus isolated device state, automations, and logs.
- **Real-time control & monitoring** — live device updates over Socket.IO (WebSocket with long-polling fallback). Controls on/off, brightness (0–100), and RGB color.
- **Home Assistant MQTT Discovery** — auto-discovers entities from retained `config` topics (`switch`, `light`, `sensor`, `binary_sensor`, and more).
- **Deterministic automation engine** — a per-unit state machine (`SET → VERIFY → RUN → BUFFER`) with hardware verification loops, drift detection (re-corrects manual overrides), and network-pause handling.
- **Watchdog & liveness** — an active watchdog pings each enabled unit and confirms liveness only from verified `set → state` responses; robust reconnect with backoff and sleep/wake detection.
- **AI Agronomist** — weather-aware irrigation recommendations via Google Gemini, using Open-Meteo historical + forecast data (ET0, rainfall, temperature, wind).
- **Web Push notifications** — VAPID push for automation and alert events, delivered through the service worker.
- **Admin command center** — user management, block/unblock, OTP-secured impersonation, and live CPU/RAM/DB telemetry.
- **Developer tools** — in-app MQTT publish/subscribe, a live message feed, and a logbook (timestamps localized per viewer).
- **Progressive Web App** — installable, offline shell, cached static assets.

---

## Tech Stack

**Backend** ([requirements.txt](requirements.txt))

| Dependency | Version | Role |
|---|---|---|
| `flask` | >=3.0 | Web framework |
| `flask-socketio` | >=5.3 | Real-time WebSocket/Socket.IO |
| `paho-mqtt` | >=1.6,<2.0 | Per-user MQTT clients |
| `google-genai` | >=0.1.0 | Gemini AI agronomist |
| `python-dotenv` | >=1.0.0 | `.env` loading |
| `bcrypt` | >=4.0 | Password hashing |
| `cryptography` | >=41.0 | Fernet (AES-128) credential encryption |
| `pywebpush` | >=2.1.2 | VAPID web push |
| `psutil` | >=5.9.0 | Admin telemetry (CPU/RAM/disk) |
| `requests` | >=2.28 | HTTP (login API, weather) |
| `gunicorn` | >=21.0.0 | Production WSGI server |
| `eventlet` | >=0.35.0 | Async green threads (production) |
| `stripe` | >=6.0.0 | Payments (planned) |

- **Database:** SQLite (WAL mode) with Fernet-encrypted credentials.
- **Async mode:** `eventlet` in Docker/production, `threading` for Windows dev.

**Frontend**

- Vanilla JavaScript (no SPA framework), HTML5, CSS3 (CSS variables, dark theme).
- Socket.IO JS client, Chart.js for analytics.
- PWA: `manifest.json` + service worker (cache-first static, network-first pages).

---

## Architecture

```
Browser (PWA)  ──Socket.IO──▶  Flask + Flask-SocketIO  ──MQTT──▶  Nivixsa Broker  ──▶  Devices
     ▲                              │  per-user paho-mqtt client        (egycad.com:1883)
     └──── web push (VAPID) ────────┘  automation engine · watchdog · AI scheduler
```

Each browser login triggers a REST call to the Nivixsa login API to obtain broker details, then the server spins up an isolated MQTT client for that user and streams device updates back over Socket.IO.

### Backend modules

| File | Responsibility |
|---|---|
| [app.py](app.py) | Flask routes, Socket.IO handlers, MQTT callbacks, automation engine (`engine_tick`), background watchdog, AI scheduler, admin telemetry, web push. |
| [db.py](db.py) | SQLite persistence & migrations; Fernet encryption; tables for users, admins, sessions, automations, automation logs, push subscriptions. |
| [session_manager.py](session_manager.py) | Thread-safe multi-tenant sessions. `UserSession` holds the MQTT client, `device_states`, `sensor_history`, `automations`, watchdog/liveness state, and connected socket IDs. |
| [ai_agent.py](ai_agent.py) | Google Gemini integration; fetches Open-Meteo weather and produces per-day irrigation decisions. |

### Background loops (in `app.py`)

- **MQTT watchdog** — reconnects dropped sessions with backoff; detects sleep/wake.
- **Automation engine** — ~1 Hz tick advancing each unit's state machine.
- **AI scheduler** — nightly weather-aware scheduling (timezone-aware).
- **Admin telemetry** — periodic CPU/RAM/DB metrics to the admin dashboard.

### Database tables (SQLite, [db.py](db.py))

`users`, `admins`, `sessions`, `admin_sessions`, `automations`, `automation_logs`, `push_subscriptions`. Passwords are bcrypt-hashed; MQTT passwords and API keys are Fernet-encrypted using an auto-generated `.encryption_key`.

---

## Project Structure

```
Cadio-MQTT-Dashboard/
├── app.py                  # Flask app: routes, Socket.IO, MQTT, automation engine, watchdog
├── db.py                   # SQLite layer + Fernet encryption + migrations
├── session_manager.py      # Multi-tenant UserSession / SessionManager
├── ai_agent.py             # Gemini AI agronomist (Open-Meteo weather)
├── requirements.txt
├── Dockerfile              # Python 3.10-slim, exposes 5000
├── docker-compose.yml      # app + cloudflared tunnel
│
├── templates/              # index.html (dashboard), admin.html, admin_login.html
├── static/
│   ├── css/style.css
│   ├── js/                 # dashboard.js, automation.js, settings.js
│   ├── manifest.json       # PWA metadata
│   ├── sw.js               # Service worker
│   └── icons/              # 72–512px PWA icons
│
├── esp8266_lite/           # ESP8266 single-unit firmware (.ino, config.h, pages.h)
├── nivixsa_app/            # Arduino CAN/serial sketches + CadioSerial library
├── mosquitto/              # Local broker config (optional)
├── models/                 # Optional local GGUF model
├── scratch/                # Utility scripts (CSV export, DB checks, icon gen)
├── exports/                # CSV exports of DB tables
└── docs/                   # Deployment & architecture documentation
```

---

## Quick Start (Local)

**Prerequisites:** Python 3.8+ (tested on 3.10), and internet access to the Nivixsa broker.

```powershell
# From the repo root
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate         # macOS/Linux

pip install -r requirements.txt

# Create your .env (see Configuration below)
python app.py
```

- Dashboard: http://localhost:5000
- Admin: http://localhost:5000/admin/login

> **Windows dev note:** set `ASYNC_MODE=threading` in `.env` — eventlet's cooperative networking can stall on blocking I/O during local development.

---

## Docker Deployment

```bash
# Build & start (app + cloudflared tunnel)
DOCKER_BUILDKIT=0 docker compose up -d --build

docker compose logs -f nivixsa-smart-agriculture   # logs
docker compose restart                              # restart
docker compose up -d --force-recreate               # apply .env changes
docker compose down                                 # stop
```

The app listens on port **5000** inside the container. See [docs/README.md](docs/README.md) and [docs/HANDOFF.md](docs/HANDOFF.md) for the full Docker + Cloudflare-tunnel runbook.

---

## Configuration (.env)

Create a `.env` file in the repo root (git-ignored):

```dotenv
# MQTT (defaults target the Nivixsa cloud broker)
MQTT_BROKER=egycad.com
MQTT_PORT=1883

# AI — Google Gemini (shared server key; users may also set a personal key in the UI)
GEMINI_API_KEY=your_gemini_key

# Admin bootstrap
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change_me

# Web Push (VAPID)
VAPID_PUBLIC_KEY=your_public_key
VAPID_PRIVATE_KEY=your_private_key

# Runtime
ASYNC_MODE=eventlet     # use "threading" for Windows dev
LOG_LEVEL=INFO

# Cloudflare Tunnel (production only)
TUNNEL_TOKEN=your_tunnel_token
```

**AI keys:** the "Shared AI" plan uses the server `GEMINI_API_KEY`; users can instead choose "Personal AI" and paste their own key in **Settings**, which is Fernet-encrypted in `cadio.db` and masked in the UI. Get a free key at [Google AI Studio](https://aistudio.google.com/app/apikey).

---

## Firmware & Hardware

### ESP8266 Lite ([esp8266_lite/](esp8266_lite/))
Lightweight single-unit firmware for ESP8266 NodeMCU v2. Connects to the MQTT broker using HA discovery, filters devices by configured serial, and serves an on-device dashboard. Two modes: AP setup (captive portal at `192.168.4.1`) and normal (WiFi + MQTT). Libraries: PubSubClient, ArduinoJson 7.x, ESP8266 core. See [esp8266_lite/SETUP_ESP.md](esp8266_lite/SETUP_ESP.md) and [esp8266_lite/AGENT.md](esp8266_lite/AGENT.md).

### Arduino CAN / Cadio Serial ([nivixsa_app/](nivixsa_app/))
Arduino Nano (ATmega328P + MCP2515) sketches bridging MQTT to local relay/dimmer hardware over a CAN bus (up to 1000 kbps):

- `cadio_serial/` — CADIO master; switch polling and relay/PWM control.
- `can_send_5_switch/` — CAN switch controller (5 devices).
- `can_send_3_dimmer/` — CAN dimmer controller (3 devices).
- `CadioSerial-4.0.5/` — Cadio serial-protocol Arduino library (8ch/16ch examples).

---

## Documentation

| Doc | Description |
|---|---|
| [docs/README.md](docs/README.md) | Live deployment runbook (Docker + Cloudflare tunnel). |
| [docs/SETUP.md](docs/SETUP.md) | Local installation guide. |
| [docs/SMART_IRRIGATION_ARCHITECTURE.md](docs/SMART_IRRIGATION_ARCHITECTURE.md) | System design: state machine, security, multi-user, AI, push. |
| [docs/agent.md](docs/agent.md) | Developer/AI-agent guide to the codebase. |
| [docs/DEVELOPER_TOOLS_GUIDE.md](docs/DEVELOPER_TOOLS_GUIDE.md) | MQTT topic reference & device command examples. |
| [docs/GCP_SETUP.md](docs/GCP_SETUP.md) | Google Cloud always-free deployment. |
| [docs/ORACLE_SETUP.md](docs/ORACLE_SETUP.md) | Oracle Cloud always-free deployment. |
| [docs/DOMAIN_SETUP.md](docs/DOMAIN_SETUP.md) | Custom domain + HTTPS (Caddy / Let's Encrypt). |
| [docs/HANDOFF.md](docs/HANDOFF.md) | Live status & operational notes. |
| [docs/ROADMAP_TO_BUSINESS.md](docs/ROADMAP_TO_BUSINESS.md) | Commercialization roadmap. |
| [docs/walkthrough.md](docs/walkthrough.md) | End-user walkthrough. |

---

## Cloud Deployment Options

| Target | Notes |
|---|---|
| **Local / dev** | `python app.py` on port 5000. |
| **Docker (prod)** | `docker compose up -d --build`; public access via Cloudflare tunnel. |
| **Google Cloud** | e2-micro always-free VM — [docs/GCP_SETUP.md](docs/GCP_SETUP.md). |
| **Oracle Cloud** | Ampere ARM always-free VM — [docs/ORACLE_SETUP.md](docs/ORACLE_SETUP.md). |
| **Custom domain** | DNS A record + Caddy reverse proxy — [docs/DOMAIN_SETUP.md](docs/DOMAIN_SETUP.md). |

---

## MQTT / API Protocol Reference

This section documents the underlying Nivixsa protocol used by the dashboard (and by any custom client, in any language).

### How it works

Nivixsa is a smart-home/irrigation system where physical devices communicate through a cloud MQTT broker, using the **Home Assistant MQTT Discovery** protocol:

1. **Login** — `POST https://egycad.com/apis/cadio/login` with `{"email","password"}` → returns `mqtt_host`, `mqtt_port`, `discovery_prefix`.
2. **Connect** — connect to the broker (MQTT v3.1.1) using the same email/password.
3. **Discover** — subscribe to `{prefix}/{type}/+/+/config`; the broker replies with retained JSON configs for every entity.
4. **Listen** — from each config, subscribe to its exact `state_topic` and `availability_topic`.
5. **Control** — publish JSON to the entity's `command_topic` (`/set`).

### Authentication

```http
POST https://egycad.com/apis/cadio/login
Content-Type: application/json

{ "email": "your@email.com", "password": "your_password" }
```

```json
{
  "success": true,
  "email": "your@email.com",
  "mqtt_host": "egycad.com",
  "mqtt_port": 1883,
  "discovery_prefix": "homeassistant"
}
```

### Topic structure

```
{prefix}/{type}/{account_id}/{serial}_{channel}/{action}
```

| Action | R/W | Format | Description |
|---|---|---|---|
| `/config` | read | JSON | Entity definition & capabilities (retained) |
| `/state` | read | JSON | Current state (retained) |
| `/set` | write | JSON | Publish commands here |
| `/availability` | read | text | `YES` / `NO` (retained; broker Last Will) |

### Payloads

```jsonc
// Commands (publish to /set)
{"state":"ON"}
{"state":"OFF"}
{"state":"ON","brightness":75}                                   // brightness is 0–100
{"state":"ON","color":{"r":255,"g":0,"b":128}}                   // RGB channels 0–255

// State (received from /state)
{"state":"ON","brightness":40,"color_mode":"rgb","color":{"r":71,"g":5,"b":5}}

// Availability (from /availability) — plain text
YES   // online
NO    // offline
```

### Broker rules (ACL)

- **Discovery uses `+/+` wildcards** per type: `homeassistant/switch/+/+/config`, `.../light/+/+/config`, etc.
- **Availability wildcards are rejected** (QoS 128) — subscribe to each exact `availability_topic` individually.
- **Avoid `#`** and overly broad wildcards; subscribe per entity type.
- All `config`/`state`/`availability` messages are **retained**, so current values arrive immediately on subscribe.

### Supported entity types

`switch`, `light` (dimmer/RGB), `binary_sensor`, `sensor`, `button`, `climate`, `cover`, `fan`, `alarm_control_panel`, `lock`, `number`, `select`.

For full topic examples and device command recipes, see [docs/DEVELOPER_TOOLS_GUIDE.md](docs/DEVELOPER_TOOLS_GUIDE.md).
