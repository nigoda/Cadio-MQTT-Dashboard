# Smart Irrigation — Developer & AI Guide

## Overview

PLC-level irrigation automation system. Flask + SocketIO backend, MQTT device control, browser-based dashboard.

## Architecture

```
smart_irrigation/
├── config.py          # MQTT broker settings, engine timing constants
├── engine.py          # State machine: Automation class + Engine class
├── app.py             # Flask app, MQTT client, REST API, SocketIO
├── templates/index.html  # Single-page dashboard
├── static/css/style.css  # Dark theme UI
├── static/js/dashboard.js # Client logic (tabs, modals, real-time updates)
├── requirements.txt   # Python deps
└── automations.json   # Persisted automation data (auto-created)
```

## State Machine (16 states)

```
IDLE → WAIT_CONDITION → INIT_SET → INIT_VERIFY → ACTION_SET → ACTION_VERIFY
→ ACTION_RUN → ACTION_REVERT → ACTION_VERIFY_REVERT → BUFFER → (next action or COMPLETED)
```

Pause states: `PAUSED_CONDITION`, `PAUSED_SCHEDULE`  
Error chain: `ERROR_SET → ERROR_VERIFY → ERROR` (locked until RESET/OFF)

### Priority Order
ERROR > RESET > STATUS > SCHEDULER > CONDITION > STATE MACHINE > STATE ENFORCEMENT

### State Enforcement
During `INIT_SET/INIT_VERIFY`, `ACTION_RUN`, and `PAUSED` states, the engine re-publishes expected switch states every tick to prevent external overrides.

## MQTT Integration

- Subscribes to Home Assistant discovery topics: `homeassistant/+/+/+/config`
- Subscribes to device state topics from discovery payloads
- Commands published to `command_topic` from discovery configs
- Device types: switch, light, sensor, binary_sensor, lock, fan, cover

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Dashboard page |
| GET | `/api/status` | System status |
| GET | `/api/devices` | All switches + sensors |
| GET | `/api/automations` | All automations |
| POST | `/api/automations` | Create automation |
| PUT | `/api/automations/<id>` | Update automation |
| DELETE | `/api/automations/<id>` | Delete automation |
| POST | `/api/automations/<id>/toggle` | Toggle ON/OFF |
| POST | `/api/automations/<id>/reset` | Reset to IDLE |
| POST | `/api/switches/<topic>/set` | Manual switch control |

## SocketIO Events

**Server → Client:**
- `mqtt_status` — `{connected: bool}`
- `full_state` — `{switches, sensors, automations}`
- `automations_update` — Full automations list
- `state_update` — Single device state change `{topic, value, type}`

## Key Automation Model Fields

```json
{
  "id": "uuid",
  "name": "Zone 1",
  "status": true,
  "schedule": {"days": ["Mon","Wed"], "startTime": "06:00", "endTime": "09:00"},
  "conditions": [{"logic": "AND", "sensor": "topic", "operator": ">", "value": "25"}],
  "initialization": [{"switch": "topic", "state": "OFF"}],
  "actions": [{"switch": "topic", "state": "ON", "duration": 120}],
  "error_state": [{"switch": "topic", "state": "OFF"}],
  "buffer_time": 5,
  "runtime": {"state": "IDLE", "current_action_index": 0, ...}
}
```

## Running

```bash
pip install -r requirements.txt
python app.py
```

Runs on `http://0.0.0.0:5000`

## Implementation Rules

1. **No blocking calls** — engine uses tick-based execution (0.5s interval)
2. **State verification** — SET→VERIFY pattern: publish command, wait for state confirmation
3. **Timeouts** — VERIFY states timeout after 10s, then retry (max 3 retries → ERROR)
4. **Persistence** — automations.json saved on every mutation
5. **Thread safety** — Engine tick runs in daemon thread; SocketIO emit is thread-safe
6. **Overnight schedule** — If startTime > endTime, schedule wraps past midnight
7. **Condition logic** — AND between all ANDs, OR between OR groups; first condition's logic is ignored (always first term)
