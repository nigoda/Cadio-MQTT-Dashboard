# How to Run the Nivixsa Dashboard

## Prerequisites

- **Python 3.8+** (tested on 3.9.13)
- **pip** (comes with Python)
- Internet connection (to reach `egycad.com` MQTT broker)

---

## Quick Start

```bash
# 1. Clone or download this repository
cd Nivixsa-dashboard

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate the virtual environment
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run the dashboard
python app.py
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force; cd $path$; .venv\Scripts\python.exe app.py
```

Open **http://localhost:5000** in your browser.

---

## What Happens on Startup

1. The app calls the Nivixsa login API (`https://egycad.com/apis/cadio/login`) to get MQTT broker details
2. Connects to the MQTT broker (`egycad.com:1883`) and subscribes to all entity discovery topics
3. Entities are auto-discovered and their state/availability topics are subscribed
4. Flask serves the dashboard on port 5000 with real-time WebSocket updates

---

## Configuration

Default credentials are **not** included in the code. Enter them on the login screen or set environment variables:

| Variable        | Default                  | Description         |
|-----------------|--------------------------|---------------------|
| `MQTT_USERNAME` | *(none)*                 | Nivixsa account email |
| `MQTT_PASSWORD` | *(none)*                 | Nivixsa password      |
| `MQTT_BROKER`   | `egycad.com`             | MQTT broker host    |
| `MQTT_PORT`     | `1883`                   | MQTT broker port    |

### Using Environment Variables

```bash
# Windows PowerShell
$env:MQTT_USERNAME = "your@email.com"
$env:MQTT_PASSWORD = "your_password"
python app.py

# macOS / Linux
MQTT_USERNAME="your@email.com" MQTT_PASSWORD="your_password" python app.py
```

---

## Project Structure

```
Nivixsa-dashboard/
├── app.py                  # Flask backend — MQTT bridge + SocketIO
├── requirements.txt        # Python dependencies
├── check_availability.py   # Standalone entity checker script
├── README.md               # API reference documentation
├── SETUP.md                # This file
├── templates/
│   └── index.html          # Dashboard HTML template
└── static/
    ├── css/
    │   └── style.css       # Dashboard styling (HA dark theme)
    └── js/
        └── dashboard.js    # Dashboard frontend logic
```

---

## Running the Availability Checker

A standalone script to list all entities and their online status:

```bash
python check_availability.py
```

This connects to the broker, discovers all entities, and prints a table:

```
Entity                     Name             Type             Status       State
----------------------------------------------------------------------------------------------------
  2CF4327CA967_0           Line 0           switch           [+] ONLINE   OFF
  2CF4327CA967_6           Line 1           light            [+] ONLINE   ON | bri=100
  A4CF12F03246_0           Line 0           switch           [+] ONLINE   OFF

Total: 19 | Online: 19 | Offline: 0
```

---

## Troubleshooting

### Port 5000 already in use

```bash
# Windows PowerShell — kill the process holding port 5000
Get-NetTCPConnection -LocalPort 5000 | Select-Object OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# macOS / Linux
lsof -ti:5000 | xargs kill -9
```

Then run `python app.py` again.

### MQTT connection fails

- Verify internet access: `ping egycad.com`
- Check credentials are correct
- The broker uses port **1883** (plain MQTT). If that's blocked by your firewall, try port **8883** (TLS) — the app auto-retries on 8883 if 1883 fails

### Dashboard shows "Not connected"

- The app auto-connects on startup. Wait a few seconds for MQTT handshake
- Check the terminal for error messages like `Bad credentials` or `Not authorised`

### Entities not appearing

- Entities are auto-discovered via MQTT retained messages. They should appear within 2–3 seconds
- Hard refresh the browser: **Ctrl+Shift+R** (bypasses cache)

### Stale page after code changes

The app runs with `debug=False`. After editing files, you must **restart the app**:

```bash
# Stop with Ctrl+C, then:
python app.py
```

And hard refresh the browser (**Ctrl+Shift+R**).

---

## Dependencies

| Package        | Version   | Purpose                           |
|----------------|-----------|-----------------------------------|
| Flask          | ≥ 3.0     | Web framework                     |
| Flask-SocketIO | ≥ 5.3     | Real-time WebSocket communication |
| paho-mqtt      | ≥ 1.6, <2 | MQTT client (v3.1.1 protocol)     |
| requests       | ≥ 2.28    | HTTP client for Nivixsa login API   |
