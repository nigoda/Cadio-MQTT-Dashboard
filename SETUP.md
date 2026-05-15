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

---

## Ngrok - public host

```bash
#Step 1: Install ngrok (if not already installed)
npm install -g ngrok
# or if using apt:
# curl https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.zip -o ngrok.zip
# unzip ngrok.zip && sudo mv ngrok /usr/local/bin/


#Step 2: Start your Flask app
cd /workspaces/Cadio-MQTT-Dashboard
python app.py


#Create a free ngrok account → https://dashboard.ngrok.com/signup

#Get your authtoken → https://dashboard.ngrok.com/get-started/your-authtoken

#Configure ngrok with your token:
#Step 3: Open another terminal and expose it with ngrok
ngrok config add-authtoken YOUR_TOKEN_HERE
ngrok http 5000
```
---

## What Happens on Startup

1. The app initializes the local SQLite database (`cadio.db`) and runs schema migrations
2. It checks for a saved user session (auto-login) or reads credentials from `.env`
3. The app calls the Nivixsa login API (`https://egycad.com/apis/cadio/login`) to get MQTT broker details
4. Connects to the MQTT broker (`egycad.com:1883`) and subscribes to all entity discovery topics
5. Entities are auto-discovered and their state/availability topics are subscribed
6. Flask serves the dashboard on port 5000 with real-time WebSocket updates

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
$env:GEMINI_API_KEY = "AIzaSy..." # Needed for AI Irrigation Scheduling
python app.py

# macOS / Linux
MQTT_USERNAME="your@email.com" MQTT_PASSWORD="your_password" GEMINI_API_KEY="AIzaSy..." python app.py
```

### AI Scheduling Setup (Optional)
To use the AI-driven irrigation scheduling, you must configure a free Gemini API key:
1. Get a key from [Google AI Studio](https://aistudio.google.com/).
2. Create a `.env` file in the main folder (you can copy `.env.example`).
3. Add `GEMINI_API_KEY=your_key_here` to the `.env` file.
4. Restart the app.

---

## Project Structure

```
Nivixsa-dashboard/
├── app.py                  # Flask backend — MQTT bridge + SocketIO
├── db.py                   # Database layer — SQLite, Encryption, User/Auto management
├── ai_agent.py             # AI Agent — Gemini integration + Scheduling logic
├── requirements.txt        # Python dependencies
├── cadio.db                # SQLite database (Auto-created)
├── .env                    # Environment variables (Credentials)
├── .encryption_key         # Fernet encryption key (Auto-created)
├── README.md               # API reference documentation
├── SETUP.md                # This file
├── templates/
│   └── index.html          # Dashboard HTML template
└── static/
    ├── css/
    │   └── style.css       # Dashboard styling (HA dark theme)
    └── js/
        ├── dashboard.js    # Core frontend logic
        └── settings.js     # AI Settings & API management logic
```

---

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
| google-genai   | ≥ 0.1     | Gemini API integration (AI scheduling) |
| python-dotenv  | ≥ 1.0     | Environment variable management   |
| cryptography   | ≥ 42.0    | Fernet encryption for credentials |
| bcrypt         | ≥ 4.1     | Password hashing for security     |
