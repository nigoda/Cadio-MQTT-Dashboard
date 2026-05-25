# Cadio-MQTT-Dashboard: AI Agent Developer Guide

This document is designed to provide software agents (AIs) with a complete, in-depth technical understanding of the Cadio-MQTT-Dashboard architecture. Use this guide to safely and effectively customize, extend, or debug the application.

## 1. System Architecture & Data Flow

The application is a real-time IoT dashboard built primarily for Smart Irrigation and Home Automation, communicating via MQTT.

### High-Level Flow
`[IoT Devices/ESP8266]` <—(MQTT)—> `[Flask Backend]` <—(WebSockets/Socket.IO)—> `[Browser Frontend (PWA)]`

### Key Design Principles
*   **Multi-Tenant:** The server acts as a middleman. Every user session spins up a dedicated `paho-mqtt` client connected to the broker using their specific credentials.
*   **Real-time:** State changes from MQTT are immediately pushed to the browser via Socket.IO.
*   **Background Engine:** Automations (like timed irrigation) run continuously on the backend, tracking states, verifying switch toggles, and managing cycle history independent of the frontend.
*   **AI Integration:** An AI Agronomist (Google Gemini) runs periodically in the background to adjust irrigation schedules based on weather data and user-defined thresholds.
*   **Progressive Web App (PWA):** The frontend installs natively on mobile/desktop and supports Web Push notifications.

---

## 2. Backend Architecture (`app.py`, `session_manager.py`)

The backend is built with Python, Flask, and Flask-SocketIO.

### Core Components
*   **`app.py`:** The massive main entry point (~2700 lines). Contains Flask routes, SocketIO event handlers, the automation state machine loop (`_engine_loop`), AI scheduler loop (`_ai_scheduler_loop`), push notification logic, and watchdog threads.
*   **`session_manager.py`:** Manages isolated `UserSession` instances in a thread-safe manner. Each `UserSession` holds its own `mqtt_client`, state cache (`device_states`), sensor history, and active automations.
*   **Socket.IO Events:**
    *   `login` / `logout`: Triggers CADIO API validation, DB session creation, and spawns the MQTT client.
    *   `publish` / `subscribe`: Routes commands to the specific user's MQTT client.
    *   `create_automation`, `update_automation`, `toggle_automation`, etc.: CRUD and control for the automation engine.
    *   `sys_notification`: Pushes alerts to the UI.

### Automation Engine State Machine
The backend engine evaluates automations every second (`engine_tick()`). It handles complex irrigation logic via a state machine:
`IDLE` → `INIT_SET` → `INIT_VERIFY` → `WAIT_CONDITION` → `ACTION_SET` → `ACTION_VERIFY` → `ACTION_RUN` → `BUFFER` → `ACTION_REVERT` → `COMPLETED`
*   Features: Drift detection (correcting manual overrides), make-before-break overlapping, max cycle limits, and background schedule enforcement.

### Web Push Notifications
Handled via `pywebpush` (`_send_web_push_async`). The server pushes alerts (automation start/stop/errors, AI updates) directly to browsers using VAPID keys. Expired subscriptions (HTTP 404, 410, 400) are automatically pruned from the database.

---

## 3. Database Layer (`db.py`)

Uses SQLite with WAL (Write-Ahead Logging) mode for concurrency.

**Schema:**
*   `users`: Stores `email` (PK), bcrypt `password_hash`, Fernet `password_enc` (for reconnects), and API keys.
*   `admins`: Role-Based Access Control (Level 1: Super Admin, 2: Support, 3: Observer).
*   `sessions` / `admin_sessions`: Persistent login tokens.
*   `automations`: Stores JSON configs (`config_json`) and live state (`runtime_json`).
*   `automation_logs`: Execution history (pruned to 200 per automation).
*   `push_subscriptions`: Maps `user_email` to browser Web Push endpoints and keys.

---

## 4. AI & Weather Integration (`ai_agent.py`)

*   **Weather:** Fetches 7-day past & 7-day forecast data from Open-Meteo (ET0, rain, temp, wind).
*   **AI Agronomist:** Uses `google-genai` (Gemini Flash). It is provided with weather data, irrigation history, and user-defined thresholds (e.g., "Don't water if rain > 5mm"). It outputs a JSON decision determining which days in the upcoming week should be skipped or scheduled.
*   **Execution:** Runs nightly at 2:00 AM (local to the automation's timezone) or on-demand via the UI.

---

## 5. Frontend Architecture (`static/`, `templates/`)

The frontend is a vanilla JS/CSS Single Page Application (SPA). No React/Vue/Angular.

### HTML (`templates/index.html`)
A ~1100-line shell. Navigation works by hiding/showing containers based on `.ha-nav-item[data-tab=*]` clicks.
*   **Tabs:** Overview, Automations, Lights, Switches, Sensors, History, Log, Developer, API, Settings.
*   **Modals:** Used extensively for Automation configuration (`#auto-modal-overlay`), AI rules (`#ai-rules-modal`), and entity details (`#detail-overlay`).

### Javascript
*   **`static/js/dashboard.js` (~2000 lines):** The core engine. Handles Socket.IO connection, maintains the `entities{}` state tree, processes incoming MQTT updates, and performs debounced DOM rendering (`renderAll()`). Handles push subscription flows (`subscribeUserToPush`).
*   **`static/js/automation.js`:** Handles the complex UI for the Smart Irrigation builder, AI analytics charts (using Chart.js), and AI settings overlays.
*   **`static/js/settings.js`:** Manages API key preferences.

### CSS (`static/css/style.css`)
*   Uses a strict CSS Variable (`:root`) theme system (Dark mode only).
*   Heavy use of glass-morphism (`backdrop-filter`), CSS transitions, and pure-CSS UI elements (e.g., toggle switches using sibling selectors).
*   Responsive breakpoints (`<=768px` for mobile sidebars).

### Progressive Web App (`static/sw.js`, `manifest.json`)
*   **`sw.js`:** Caches core assets (`CACHE_NAME`, `PRECACHE_ASSETS`) for instant offline loading. Listens for the `push` event to display system notifications globally, even when the browser is closed (platform dependent).

---

## 6. Admin Panel (`templates/admin.html`)

A completely separate, isolated dashboard (`/admin`) for system administrators.
*   Provides real-time system telemetry (CPU, RAM, DB size).
*   Allows user management (Block/Delete).
*   Includes a secure OTP-based impersonation flow allowing Level 2+ admins to temporarily log into a user's dashboard to provide support.

---

## 7. Guidelines for Modifying the Dashboard

When an AI agent is tasked with adding a new feature, follow these patterns:

1.  **Adding a New Tab:**
    *   Add the sidebar button in `index.html` with `data-tab="your_new_tab"`.
    *   Add the content container `<div id="tab-your_new_tab" class="ha-tab-content">...</div>`.
    *   `dashboard.js` automatically handles tab switching based on these classes.
2.  **Adding a New Backend Event:**
    *   Add a `@socketio.on('your_event')` handler in `app.py`.
    *   Ensure the handler identifies the user via `session.get("email")` and interacts with their specific `UserSession` to isolate data.
3.  **UI/Styling:**
    *   Do NOT use inline styles unless absolutely necessary. Rely on variables in `style.css` (e.g., `var(--ha-card)`, `var(--ha-text)`).
4.  **Database Changes:**
    *   If adding new tables or columns to `db.py`, write raw SQLite queries. Ensure you use `with _get_connection() as conn:` to maintain thread safety in WAL mode.
5.  **Notifications:**
    *   Use `send_sys_notification(email, title, message, type)` in `app.py` to simultaneously alert the user in the UI (via Socket.IO toast) and send a native OS push notification (via Web Push).
