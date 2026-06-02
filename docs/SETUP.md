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
```

Open **http://localhost:5000** in your browser.

---

## 🛡️ Administrative Access (Command Center)

The Nivixsa Dashboard includes a professional "Master Control" interface for the platform owner to manage all users and monitor system health.

### 1. Accessing the Admin Panel
To access the admin dashboard, visit the dedicated login page:
**http://localhost:5000/admin/login**

Log in using your administrative credentials:
*   **Email**: `ADMIN_EMAIL` (Defined in `.env`)
*   **Password**: `ADMIN_PASSWORD` (Defined in `.env`)

### 2. Admin Features
- **Secure Login**: Professional POST-based login page with session persistence.
- **User Registry**: View all registered users, their status, and automation counts.
- **Security Kill-Switch**: Instantly **Block** or **Delete** users from the platform.
- **Impersonation (Login As)**: Troubleshooting customer issues by context-switching to their view (OTP-secured).
- **Live Telemetry**: Real-time monitoring of **CPU**, **RAM**, **MQTT Latency**, and **DB Size**.

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

Default credentials are **not** included in the code. Enter them on the login screen or set environment variables.

### `.env` File Reference

Create a `.env` file in the project root. Below is the complete list of all supported variables:

```env
# ──────────────────────────────────────────────────
# MQTT / CADIO Credentials (Optional)
# ──────────────────────────────────────────────────
# If not set, users must enter credentials on the login screen.
MQTT_USERNAME=your@email.com
MQTT_PASSWORD=your_password
MQTT_BROKER=egycad.com
MQTT_PORT=1883

# ──────────────────────────────────────────────────
# Admin Command Center
# ──────────────────────────────────────────────────
ADMIN_EMAIL=admin@nivixsa.com
ADMIN_PASSWORD=nivixsa-admin-2024

# ──────────────────────────────────────────────────
# Gemini AI Key (Optional — for AI Agronomist)
# ──────────────────────────────────────────────────
# Get a free key from: https://aistudio.google.com/app/apikey
# Without this, AI scheduling is disabled but manual schedules work fine.
GEMINI_API_KEY=AIzaSy...

# ──────────────────────────────────────────────────
# VAPID Web Push Keys (Optional — for background notifications)
# ──────────────────────────────────────────────────
# These enable native lock-screen push notifications on iOS/Android/Desktop.
# Without these, the app works fully — only background push is disabled.
# In-app toast notifications still work without these keys.
#
# To DISABLE push notifications: comment out or delete these 3 lines.
# To RE-ENABLE: uncomment them and restart the server.
#
# Key Specifications:
#   PUBLIC_KEY  → 87 characters, URL-safe Base64, always starts with 'B'
#   PRIVATE_KEY → 43 characters, URL-safe Base64
#   CLAIMS_EMAIL → must start with 'mailto:' followed by a valid email
#
# ⚠️ IMPORTANT:
#   • Public and Private keys are a MATCHED PAIR — generate them together.
#   • Changing keys invalidates ALL existing device subscriptions.
#     (Users auto-resubscribe on next login — no manual action needed.)
#   • You can use ANY valid email for CLAIMS_EMAIL.
#
# How to generate new keys:
#   python -c "from py_vapid import Vapid; v=Vapid(); v.generate_keys(); print('Public:', v.public_key); print('Private:', v.private_key)"
#
VAPID_PUBLIC_KEY=BN5LkFCS-PDB5k7RxYXFqqmOuxuk6som0P_6vN9wKchrmOwE8m5LC_EQ9KqadiR5mNM0pr23yVE25h-iil_nSGw
VAPID_PRIVATE_KEY=G3ARj44SoPOYrHSHVNGUTNvlc2utd_LrPsn2s5FspAw
VAPID_CLAIMS_EMAIL=mailto:admin@nivixsa.com
```

### Variable Summary Table

| Variable | Required | Default | Description |
|:---|:---:|:---|:---|
| `MQTT_USERNAME` | No | *(none)* | CADIO account email |
| `MQTT_PASSWORD` | No | *(none)* | CADIO password |
| `MQTT_BROKER` | No | `egycad.com` | MQTT broker host |
| `MQTT_PORT` | No | `1883` | MQTT broker port |
| `ADMIN_EMAIL` | No | `admin@nivixsa.com` | Admin panel login email |
| `ADMIN_PASSWORD` | No | `nivixsa-admin-2024` | Admin panel login password |
| `GEMINI_API_KEY` | No | *(none)* | Google Gemini API key for AI scheduling |
| `VAPID_PUBLIC_KEY` | No | *(none)* | Web Push public key (87 chars, URL-safe Base64) |
| `VAPID_PRIVATE_KEY` | No | *(none)* | Web Push private key (43 chars, URL-safe Base64) |
| `VAPID_CLAIMS_EMAIL` | No | `mailto:admin@nivixsa.com` | Contact email for push service identification |

### What Works Without `.env`

| Feature | Without `.env` |
|:---|:---:|
| Server starts | ✅ |
| User login & dashboard | ✅ |
| Irrigation engine | ✅ |
| MQTT hardware control | ✅ |
| In-app toast notifications | ✅ |
| Admin panel (default creds) | ✅ |
| Database & encryption | ✅ (auto-generates key) |
| AI Agronomist | ❌ Needs `GEMINI_API_KEY` |
| Background push notifications | ❌ Needs `VAPID_*` keys |

### AI Scheduling Setup (Optional)
To use the AI-driven irrigation scheduling, you must configure a free Gemini API key:
1. Get a key from [Google AI Studio](https://aistudio.google.com/).
2. Create a `.env` file in the main folder.
3. Add `GEMINI_API_KEY=your_key_here` to the `.env` file.
4. Restart the app.

---

## Project Structure

```
Nivixsa-dashboard/
├── app.py                  # Flask backend — MQTT bridge + SocketIO + Push
├── db.py                   # Database layer — SQLite, Encryption, User/Auto/Push management
├── ai_agent.py             # AI Agent — Gemini integration + Scheduling logic
├── session_manager.py      # Multi-user session management
├── requirements.txt        # Python dependencies
├── cadio.db                # SQLite database (Auto-created)
├── .env                    # Environment variables (Credentials + Keys)
├── .encryption_key         # Fernet encryption key (Auto-created)
├── README.md               # API reference documentation
├── SETUP.md                # This file
├── SMART_IRRIGATION_ARCHITECTURE.md  # Full system architecture docs
├── templates/
│   ├── index.html          # Main User Dashboard (PWA)
│   ├── admin.html          # Admin Command Center
│   └── admin_login.html    # Admin Login Page
└── static/
    ├── manifest.json       # PWA manifest (icons, theme, display mode)
    ├── sw.js               # Service Worker (caching, push, offline)
    ├── Logo.png            # Nivixsa logo
    ├── css/
    │   └── style.css       # Dashboard styling (HA dark theme)
    ├── js/
    │   ├── dashboard.js    # Core frontend logic + push subscription
    │   ├── automation.js   # Automation UI + AI toggle lock
    │   └── settings.js     # AI Settings & API management logic
    └── icons/
        ├── icon-72x72.png  # PWA icons (7 sizes: 72→512)
        ├── icon-96x96.png
        ├── icon-128x128.png
        ├── icon-144x144.png
        ├── icon-192x192.png
        ├── icon-384x384.png
        └── icon-512x512.png
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

### Push notifications not working

- Verify `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` are set in `.env`
- Ensure they are a matched pair (generated together)
- Check the browser console for `[PWA]` log messages
- On iOS: requires **Safari 16.4+** (iOS 16.4 or later)
- Try clearing the site data and re-allowing notification permission

### PWA not installable

- The app must be served over **HTTPS** (or `localhost`) for PWA installation
- Use ngrok for HTTPS tunneling during development
- Hard refresh and check browser console for manifest errors

### Stale page after code changes

The app runs with `debug=False`. After editing files, you must **restart the app**:

```bash
# Stop with Ctrl+C, then:
python app.py
```

And hard refresh the browser (**Ctrl+Shift+R**).

---

## Dependencies

| Package | Version | Purpose |
|:---|:---|:---|
| Flask | ≥ 3.0 | Web framework |
| Flask-SocketIO | ≥ 5.3 | Real-time WebSocket communication |
| paho-mqtt | ≥ 1.6, <2 | MQTT client (v3.1.1 protocol) |
| requests | ≥ 2.28 | HTTP client for Nivixsa login API |
| google-genai | ≥ 0.1 | Gemini API integration (AI scheduling) |
| python-dotenv | ≥ 1.0 | Environment variable management |
| cryptography | ≥ 42.0 | Fernet encryption for credentials |
| bcrypt | ≥ 4.1 | Password hashing for security |
| pywebpush | ≥ 2.0 | VAPID Web Push notifications |
| py-vapid | ≥ 1.9 | VAPID key generation & management |
| psutil | ≥ 5.9 | System health monitoring (CPU/RAM) |
