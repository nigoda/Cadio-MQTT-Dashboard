# Nivixsa Smart Irrigation Dashboard — Architecture & Security

This document outlines the complete working mechanics, security layers, and AI integration for the Nivixsa Smart Irrigation system.

---

## 1. High-Level Overview

Nivixsa is a multi-user IoT dashboard designed for the CADIO ecosystem. It provides a deterministic state machine for irrigation control, a persistent background automation engine, and a weather-aware AI scheduler.

The system is built on **Python (Flask/SocketIO)** with an **SQLite** persistence layer, using **MQTT** for low-latency communication with hardware switches.

---

## 2. Multi-User & Security Architecture

### Secure Identity Management
*   **Database (SQLite)**: Stores users, automations, and logs.
*   **Encrypted Credentials**: 
    *   Passwords are stored as **Bcrypt** hashes (for authentication).
    *   Original CADIO passwords and API Keys are stored using **Fernet (AES-128)** encryption, ensuring that even if the database is leaked, raw credentials remain protected.
*   **Single Active User Logic**: To maintain stability on low-power hardware (like Raspberry Pi), the backend maintains one active "User Session" at a time. Logging in as User B will safely disconnect User A's background tasks and spin up User B's environment.

### Account Block & Recovery (Anti-Ban)
To prevent CADIO from banning your account due to repeated login attempts (e.g., during network issues):
1.  **Failure Detection**: If the MQTT broker rejects credentials (`rc=4`), the system increments a failure counter.
2.  **Auto-Block**: After **3 consecutive failures**, the account is marked as `Blocked` in the database.
3.  **Spam Prevention**: All reconnect attempts are halted immediately once an account is blocked.
4.  **Self-Healing**: The block is automatically lifted once the user successfully logs in again through the dashboard UI.

---

## 3. Automation Engine (The State Machine)

The engine transitions between predefined states to ensure complete safety.

### Verification-Driven Logic
Unlike simple "Send and Forget" systems, Nivixsa uses a **Set -> Verify -> Proceed** loop:
*   **Sequential Initialization**: Every valve/pump is verified to be in a safe "OFF" state before any watering cycle begins.
*   **Real-time Monitoring**: The engine waits for the hardware to confirm its state over MQTT before moving to the next step.
*   **Failsafe Recovery**: If a switch fails to respond after 3 retries, the system enters `ERROR_SET` and attempts to shut down all valves to prevent flooding.

### 24/7 Persistent Operation
The automation engine runs in a separate Python background thread. 
*   ✅ Closing your browser does **NOT** stop the irrigation.
*   ✅ The server continues to monitor time, weather, and MQTT signals 24/7.
*   ✅ Background tasks automatically resume after a power outage or server reboot.

---

## 4. AI-Driven Scheduling (Google Gemini)

Nivixsa uses **Google Gemini (1.5 Flash/Pro)** to decide *when* to water, replacing static schedules with intelligence.

### Shared vs. Personal AI Modes
1.  **Shared AI (Default)**: Uses the server's built-in Gemini API key. This is the easiest "Plug and Play" option.
2.  **Personal AI**: Users can provide their own Gemini API Key from [Google AI Studio](https://aistudio.google.com/app/apikey).
    *   This provides higher rate limits and avoids sharing quota with other users.
    *   The system dynamically swaps the API client in memory whenever it runs an automation for a "Personal AI" user.

### Decision Intelligence
*   **Weather Fetching**: Queries Open-Meteo for a 10-day window (3 days history + 7 days forecast).
*   **Rain Avoidance**: Automatically skips watering if rainfall is detected in the recent past or predicted for the near future.
*   **Smart Selection**: Analyzes temperatures and humidity to choose the optimal days for hydration.

---

## 5. Hardware Compatibility

The system is optimized for **CADIO-enabled switches** (ESP8266/ESP32) but supports any MQTT-compliant relay that follows the Home Assistant Discovery protocol or standard State/Set topic patterns.

### Recommended Server Hardware
*   **Raspberry Pi 4 (4GB)**: Recommended for the best balance of stability and performance.
*   **Old Laptop/Mini PC**: Running Linux (Ubuntu Server) provides the fastest response times and highest reliability.
