"""
Nivixsa IoT Dashboard — Backend
Bridges Nivixsa cloud MQTT to the browser via Flask-SocketIO.
Uses the Nivixsa login API to obtain the real MQTT broker details.
"""

# Load environment first so the async mode can be chosen before importing any
# module that eventlet needs to monkey-patch.
import os
from dotenv import load_dotenv
load_dotenv()

# Async mode is configurable. Default "eventlet" (used in Docker/production).
# On Windows local dev, eventlet's cooperative networking can stall the whole
# server during blocking I/O (login API calls, MQTT connect, DNS). Set
# ASYNC_MODE=threading in your .env there to avoid it.
ASYNC_MODE = os.getenv("ASYNC_MODE", "eventlet").strip().lower()
if ASYNC_MODE == "eventlet":
    # eventlet must be monkey-patched BEFORE importing socket/threading users.
    import eventlet
    eventlet.monkey_patch()

import copy
import json
import logging
import collections
import ssl
import threading
import time
import uuid
import re
import atexit
import math
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt
import requests
import psutil

# Log level is configurable; default INFO. DEBUG floods the console on every MQTT
# message which, under eventlet, can block the hub and slow the whole server.
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO), format="%(asctime)s [%(levelname)s] %(message)s")
from flask import Flask, render_template, request, session, redirect, send_from_directory, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CADIO_LOGIN_URL = "https://egycad.com/apis/cadio/login"
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

# These are populated after calling the Nivixsa login API
MQTT_BROKER = os.getenv("MQTT_BROKER", "egycad.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
DISCOVERY_PREFIX = "homeassistant"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=ASYNC_MODE)

# In-memory stores
device_states: dict = {}        # topic -> last payload
sensor_history: dict = {}       # topic -> list of {ts, value}
MAX_HISTORY = 200               # datapoints kept per sensor topic

mqtt_client = None
mqtt_connected = False
pending_subs = 0          # track outstanding SUBSCRIBE calls
cadio_login_cached = False # track if we already fetched broker details

# Irrigation Automation stores
# ---------------------------------------------------------------------------
automations: dict = {}          # auto_id -> automation dict
automation_logs: dict = {}      # auto_id -> list of log entries

# ---------------------------------------------------------------------------
# Multi-Tenant Session Management (The Engine)
# ---------------------------------------------------------------------------

class UserSession:
    def __init__(self, email, password, broker=None, port=None, discovery_prefix=None):
        self.email = email
        self.password = password
        self.room = f"user_{email.replace('@', '_').replace('.', '_')}"
        self.mqtt_broker = broker or MQTT_BROKER
        self.mqtt_port = port or MQTT_PORT
        self.discovery_prefix = discovery_prefix or DISCOVERY_PREFIX
        
        self.mqtt_client = None
        self.mqtt_connected = False
        self._mqtt_last_connected_time = time.time()
        
        # In-memory stores for this specific user
        self.device_states = {}
        self.sensor_history = {}
        self.automations = {}
        self.automation_logs = {}
        # Availability tracking (device offline/online detection)
        self.avail_map = {}        # control topic (cmd/state) -> set of availability topics
        self.avail_payloads = {}   # availability topic -> {"pl_avail": str, "pl_not_avail": str}
        self.known_control_topics = set()  # all cmd/state topics seen in discovery configs
        self._sockets = set() # Track active browser tabs
        self.otp = None # Secure code for admin access

    @property
    def has_sockets(self):
        return len(self._sockets) > 0

    def start_mqtt(self, socketio_ref):
        """Initialize and start this user's private MQTT connection."""
        client_id = f"nivixsa-{self.email}-{os.getpid()}"
        self.mqtt_client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311, userdata={"owner_email": self.email})
        self.mqtt_client.username_pw_set(self.email, self.password)
        self.mqtt_client.on_connect = on_connect
        self.mqtt_client.on_disconnect = on_disconnect
        self.mqtt_client.on_message = on_message
        self.mqtt_client.on_subscribe = on_subscribe
        
        if self.mqtt_port == 8883:
            self.mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
            
        try:
            self.mqtt_client.connect_async(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            logging.info(f"[SESSION:{self.email}] MQTT Client Started")
        except Exception as e:
            logging.error(f"[SESSION:{self.email}] MQTT Start Failed: {e}")

class SessionManager:
    def __init__(self):
        self._sessions = {}       # email -> UserSession
        self._socket_to_email = {} # sid -> email

    def create_session(self, email, password, **kwargs):
        email = email.lower()
        if email not in self._sessions:
            self._sessions[email] = UserSession(email, password, **kwargs)
        return self._sessions[email]

    def get_session(self, email):
        if not email: return None
        return self._sessions.get(email.lower())

    def get_session_by_sid(self, sid):
        email = self._socket_to_email.get(sid)
        return self.get_session(email) if email else None

    def register_socket(self, sid, email):
        if not email: return
        session = self.get_session(email)
        if session:
            session._sockets.add(sid)
            self._socket_to_email[sid] = email

    def unregister_socket(self, sid):
        email = self._socket_to_email.pop(sid, None)
        if email:
            sess = self.get_session(email)
            if sess:
                sess._sockets.discard(sid)
                return sess
        return None

    def is_online(self, email):
        """Checks if a user has an active MQTT session."""
        return email.lower() in self._sessions

    def active_count(self):
        """Returns the number of unique, connected users."""
        # Only count sessions that have an email and are actively connected
        count = 0
        unique_emails = set()
        for email, sess in list(self._sessions.items()):
            if email and sess.mqtt_connected:
                unique_emails.add(email.lower())
        return len(unique_emails)

    def get_all_automations(self):
        """Generator to yield all automations across all active users."""
        for email, sess in list(self._sessions.items()):
            for auto_id, auto in list(sess.automations.items()):
                yield (sess, auto_id, auto)

    def remove_session(self, email):
        sess = self._sessions.pop(email, None)
        if sess and sess.mqtt_client:
            sess.mqtt_client.loop_stop()
            sess.mqtt_client.disconnect()
        logging.info(f"[SESSION-MGR] Removed session for {email}")

    def active_count(self):
        return len(self._sessions)

# Global session manager instance
session_mgr = SessionManager()

# Per-socket user session tracking (for multi-user isolation)
_user_sessions: dict = {}       # socket_sid -> email
_sid_token: dict = {}           # socket_sid -> DB session_token (for per-device logout)
_impersonation_tokens: dict = {} # token -> email (Temporary access tokens)

def _get_user_email():
    """Get the email of the currently connected user from their socket session."""
    from flask import request as ws_request
    sid = getattr(ws_request, 'sid', None)
    if sid and sid in _user_sessions:
        return _user_sessions[sid]
    return MQTT_USERNAME  # fallback to global for engine/watchdog threads


def cadio_login(email, password):
    """Call Nivixsa login API to get MQTT broker details. Returns True on success."""
    global MQTT_BROKER, MQTT_PORT, DISCOVERY_PREFIX
    try:
        headers = {
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 5 Build/SQ3A.220705.004)",
            "Content-Type": "application/json"
        }
        resp = requests.post(
            CADIO_LOGIN_URL,
            json={"email": email, "password": password},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            mqtt_host = data.get("mqtt_host")
            if not mqtt_host:
                return False
            
            MQTT_BROKER = mqtt_host
            MQTT_PORT = int(data.get("mqtt_port", 1883))
            DISCOVERY_PREFIX = data.get("discovery_prefix", "homeassistant")
            return True
        return False
    except Exception:
        return False


def _find_automation(auto_id):
    """Find an automation by ID across all sessions."""
    for sess, aid, auto in session_mgr.get_all_automations():
        if aid == auto_id:
            return (sess, auto)
    return (None, None)


MAX_AUTO_LOG = 200
VERIFY_TIMEOUT = 10             # seconds to wait for switch verification
DRIFT_VERIFY_TIMEOUT = 3        # seconds for drift correction (shorter — device was already responding)
NETWORK_RETRY_DELAY = 120       # seconds (2 min) to wait before retrying a device that won't obey
WATCHDOG_STABLE_PINGS = 2       # consecutive good pings before a recovered unit is declared online (rejects flapping)
DISCOVERY_GRACE = 45            # seconds after MQTT connect before a device is judged "missing"
# How often the watchdog pings each enabled unit. Change here (or set WATCHDOG_PING_INTERVAL in .env) to use something other than 60s.
WATCHDOG_PING_INTERVAL = int(os.getenv("WATCHDOG_PING_INTERVAL", 60))  # seconds between watchdog pings

# MQTT Watchdog globals
_mqtt_last_connected_time = time.time()

def _get_enabled_auto_units(sess):
    """Return set of unit serials used by automations that are ON or currently deinitializing/paused."""
    enabled_units = set()
    for auto_id, auto in getattr(sess, "automations", {}).items():
        state = auto.get("runtime", {}).get("state", "IDLE")
        if auto.get("status") != "ON" and not state.startswith("DEINIT") and state != "PAUSED_NETWORK":
            continue
        # Gather all topics from actions, initialization, deinitialization, schedule sets
        all_items = (
            auto.get("actions", []) +
            auto.get("initialization", []) +
            auto.get("deinitialization", []) +
            auto.get("schedule", {}).get("setIfTrue", []) +
            auto.get("schedule", {}).get("setIfFalse", [])
        )
        for item in all_items:
            topic = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
            if topic:
                parts = topic.split("/")
                if len(parts) >= 4:
                    unit = parts[3].split("_")[0]
                    enabled_units.add(unit)
    return enabled_units

def _mqtt_watchdog():
    """Background thread to ensure per-session MQTT reconnects after internet loss."""
    logging.info("[WATCHDOG] MQTT monitor thread started")
    _reconnect_backoff = 0
    _last_loop_time = time.time()
    
    while _engine_running:
        try:
            now = time.time()
            # Detect system sleep/wake (clock jump > 12s when sleep is 5s)
            sleep_detected = (now - _last_loop_time) > 12
            if sleep_detected:
                logging.warning(f"[WATCHDOG] System sleep/wake detected (Gap: {int(now - _last_loop_time)}s)")
            _last_loop_time = now

            # Iterate all active sessions and reconnect any disconnected ones
            all_connected = True
            for email, sess in list(session_mgr._sessions.items()):
                if sess.mqtt_connected:
                    continue
                all_connected = False
                # Only reconnect if sess has stored credentials
                if not sess.password:
                    continue
                elapsed = now - getattr(sess, '_mqtt_last_connected_time', 0)
                
                delays = [5, 15, 30]
                delay = delays[min(_reconnect_backoff, len(delays) - 1)]
                
                # Wait until 'delay' seconds have passed since the last disconnect event
                # We use _mqtt_last_disconnect_time to track this.
                last_disconnect = getattr(sess, '_mqtt_last_disconnect_time', 0)
                
                if (now - last_disconnect) > delay or sleep_detected:
                    sess._mqtt_last_disconnect_time = now # reset for next backoff
                    logging.warning(
                        f"[WATCHDOG:{email}] MQTT disconnected for {int(elapsed)}s. "
                        f"Reconnect attempt (next retry in {delay}s)..."
                    )
                    try:
                        if sess.mqtt_client is not None:
                            try:
                                sess.mqtt_client.loop_stop(force=True)
                                sess.mqtt_client.disconnect()
                            except Exception:
                                pass
                        sess.start_mqtt(socketio)
                    except Exception as e:
                        logging.error(f"[WATCHDOG:{email}] Reconnect failed: {e}")
            
            if all_connected:
                _reconnect_backoff = 0
            else:
                _reconnect_backoff += 1
                
        except Exception as e:
            logging.error(f"[WATCHDOG] Error: {e}")
        time.sleep(5)

MAX_RETRIES = 3
BUFFER_SECONDS = 5              # default buffer between actions
ENGINE_INTERVAL = 1.0           # state machine tick interval (seconds)
_engine_thread = None
_engine_running = False
_ai_running_set: set = set()    # track which automations currently have AI running (prevent duplicates)

# ---------------------------------------------------------------------------
# MQTT handlers
# ---------------------------------------------------------------------------

# Track topics we've already subscribed to (avoid duplicate subscriptions per session)
_subscribed_topics: dict = {}   # email -> set of subscribed topics

SUPPORTED_COMPONENTS = [
    "alarm_control_panel", "binary_sensor", "button", "camera",
    "climate", "cover", "device_automation", "device_tracker",
    "event", "fan", "humidifier", "image", "lawn_mower", "light",
    "lock", "notify", "number", "scene", "siren", "select",
    "sensor", "switch", "tag", "text", "update", "vacuum",
    "valve", "water_heater",
]

def on_connect(client, userdata, flags, rc):
    owner_email = userdata.get("owner_email")
    sess = session_mgr.get_session(owner_email)
    if rc == 0:
        if sess:
            sess.mqtt_connected = True
            sess._mqtt_last_connected_time = time.time()
        logging.info(f"[MQTT:{owner_email}] Connected successfully")
        if sess:
            prefix = sess.discovery_prefix
            # Subscribe to specific component patterns (matching working branch)
            topics = []
            for comp in SUPPORTED_COMPONENTS:
                topics.append((f"{prefix}/{comp}/+/+/config", 0))
                topics.append((f"{prefix}/{comp}/+/+/state", 0))
                topics.append((f"{prefix}/{comp}/+/+/set", 0))
            topics.append((f"{prefix}/device/+/+/config", 0))
            topics.append((f"{prefix}/status", 0))
            
            _subscribed_topics[owner_email] = set(t for t, _ in topics)
            for t, qos in topics:
                client.subscribe(t, qos)
            logging.info(f"[MQTT:{owner_email}] Subscribing to {len(topics)} topics (prefix={prefix})")
            socketio.emit("mqtt_status", {"connected": True, "message": "Connected"}, room=sess.room)
            send_sys_notification(owner_email, "Broker Connected", "Connection established with CADIO MQTT Broker.", type="success")
    else:
        logging.error(f"[MQTT:{owner_email}] Connection failed with code {rc}")
        if sess:
            socketio.emit("mqtt_status", {"connected": False, "message": f"Connection Failed ({rc})"}, room=sess.room)
            send_sys_notification(owner_email, "Broker Connection Failed", f"Could not connect to broker (Code {rc}).", type="error")

def on_disconnect(client, userdata, rc):
    owner_email = userdata.get("owner_email")
    sess = session_mgr.get_session(owner_email)
    if sess:
        sess.mqtt_connected = False
        socketio.emit("mqtt_status", {"connected": False, "message": "Disconnected"}, room=sess.room)
        send_sys_notification(owner_email, "Broker Disconnected", "Dashboard disconnected from the MQTT broker. Checking connection...", type="error")
    logging.warning(f"[MQTT:{owner_email}] Disconnected")

def _auto_subscribe_from_config(client, owner_email, config):
    """Parse HA discovery config and subscribe to state/availability topics."""
    topics_to_sub = set()
    for key in ("state_topic", "command_topic", "availability_topic",
                "brightness_state_topic", "color_temp_state_topic",
                "rgb_state_topic", "json_attributes_topic"):
        if key in config and isinstance(config[key], str):
            topics_to_sub.add(config[key])

    # Also handle availability list
    if "availability" in config and isinstance(config["availability"], list):
        for avail in config["availability"]:
            if isinstance(avail, dict) and "topic" in avail:
                topics_to_sub.add(avail["topic"])

    already = _subscribed_topics.get(owner_email, set())
    new_topics = topics_to_sub - already
    for t in new_topics:
        client.subscribe(t, 0)
        already.add(t)
        logging.info(f"[MQTT:{owner_email}] Auto-subscribed to: {t}")
    _subscribed_topics[owner_email] = already


def _index_availability_from_config(sess, config):
    """Build maps of control topics -> availability topics from a discovery config,
    so the automation engine can detect when a device goes offline."""
    if not sess or not isinstance(config, dict):
        return

    # Record this entity's control topics so the engine can tell whether a device
    # referenced by an automation still exists in discovery (renamed/removed → ERROR).
    for key in ("state_topic", "stat_t", "command_topic", "cmd_t"):
        val = config.get(key)
        if isinstance(val, str) and val:
            sess.known_control_topics.add(val)

    # Collect this entity's availability topics + their payloads
    avail_topics = []

    def _register_avail(topic, pl_avail, pl_not_avail):
        if not topic:
            return
        avail_topics.append(topic)
        sess.avail_payloads[topic] = {
            "pl_avail": str(pl_avail) if pl_avail is not None else "online",
            "pl_not_avail": str(pl_not_avail) if pl_not_avail is not None else "offline",
        }

    default_avail = config.get("payload_available") or config.get("pl_avail")
    default_not_avail = config.get("payload_not_available") or config.get("pl_not_avail")

    single_avail = config.get("availability_topic") or config.get("avty_t")
    if single_avail:
        _register_avail(single_avail, default_avail, default_not_avail)

    if isinstance(config.get("availability"), list):
        for a in config["availability"]:
            if isinstance(a, dict) and a.get("topic"):
                _register_avail(
                    a["topic"],
                    a.get("payload_available") or a.get("pl_avail") or default_avail,
                    a.get("payload_not_available") or a.get("pl_not_avail") or default_not_avail,
                )

    if not avail_topics:
        return

    # Map this entity's control topics to its availability topics
    control_topics = [
        config.get("state_topic") or config.get("stat_t"),
        config.get("command_topic") or config.get("cmd_t"),
    ]
    for ct in control_topics:
        if not ct:
            continue
        existing = sess.avail_map.setdefault(ct, set())
        existing.update(avail_topics)

def on_message(client, userdata, msg):
    owner_email = userdata.get("owner_email")
    sess = session_mgr.get_session(owner_email)
    if not sess: return

    try:
        topic = msg.topic
        try:
            payload_raw = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            payload_raw = msg.payload.hex()

        try:
            payload = json.loads(payload_raw)
        except (json.JSONDecodeError, ValueError):
            payload = payload_raw

        now = datetime.utcnow().isoformat()

        # 1. Store state
        sess.device_states[topic] = {"payload": payload, "raw": payload_raw, "ts": now, "ts_float": time.time()}
        
        # 2. If discovery config, auto-subscribe to state/availability topics
        if isinstance(payload, dict) and topic.endswith("/config"):
            _auto_subscribe_from_config(client, owner_email, payload)
            _index_availability_from_config(sess, payload)
            
            # Auto-assign default watchdog ONLY if the user has NEVER set one for this unit.
            # We check the DB directly to avoid race conditions during MQTT reconnect
            # where sess.watchdogs might be temporarily empty/reloading,
            # which was causing user's manual watchdog selection to be silently overwritten.
            # obj_id/unit are parsed further down (section 3), which is AFTER this
            # block -- referencing them here raised UnboundLocalError on every
            # discovery /config message, aborting the handler before liveness was
            # updated and meaning this auto-assignment never actually ran.
            # Derive them locally from the config topic instead.
            _cfg_parts = topic.split("/")
            _cfg_obj_id = _cfg_parts[3] if len(_cfg_parts) >= 4 else ""
            _cfg_unit = _cfg_obj_id.split("_")[0]

            if _cfg_obj_id.endswith("_20"):
                import db as _db
                saved_watchdogs = _db.get_watchdogs(owner_email)
                # Only auto-assign if neither the in-memory session nor the DB has a saved choice
                if _cfg_unit not in sess.watchdogs and _cfg_unit not in saved_watchdogs:
                    cmd_topic = payload.get("command_topic") or payload.get("state_topic")
                    if cmd_topic:
                        sess.watchdogs[_cfg_unit] = cmd_topic
                        _db.set_watchdogs(owner_email, sess.watchdogs)
                        next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in getattr(sess, "watchdog_state", {}).items() if sess.watchdogs.get(u) != 'none'}
                        socketio.emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(_get_enabled_auto_units(sess))}, room=sess.room)
                elif _cfg_unit not in sess.watchdogs and _cfg_unit in saved_watchdogs:
                    # Session memory is empty but DB has a saved choice — restore it silently
                    sess.watchdogs[_cfg_unit] = saved_watchdogs[_cfg_unit]


            
        # 3. Unit-Level Liveness: Any message marks the unit as online
        if not getattr(sess, "unit_liveness", None):
            sess.unit_liveness = {}
            
        # Extract unit serial from topic (e.g. homeassistant/switch/node/unit_20/...)
        parts = topic.split("/")
        if len(parts) >= 4 and not topic.endswith("/set"):
            obj_id = parts[3]
            unit = obj_id.split("_")[0]
            
            # Mark unit as online whenever ANY device (including watchdog devices) sends a message
            has_watchdog = (unit in sess.watchdogs and sess.watchdogs[unit]
                            and sess.watchdogs[unit] != 'none')

            if has_watchdog:
                # Liveness (offline→online) for a watchdog-monitored unit is owned SOLELY by the
                # active ping/verify cycle (send /set → matching /state). Passive traffic
                # (/state, /availability, birth msgs) must NOT flip it online or resume
                # automations, otherwise a flapping unit's stray messages resume between pings.
                if unit not in sess.unit_liveness:
                    sess.unit_liveness[unit] = True  # first sighting defaults online

                wd_cmd_topic = sess.watchdogs[unit]
                wd_parts = wd_cmd_topic.split("/")
                wd_obj_id = wd_parts[3] if len(wd_parts) >= 4 else ""

                # Idle optimisation: defer the next active ping while the device is already
                # chatting — but only when it's currently online. While offline, let the ping
                # fire promptly so the stability debounce can confirm real recovery.
                if (obj_id != wd_obj_id and topic.endswith("/state")
                        and sess.unit_liveness.get(unit, True) is not False):
                    if hasattr(sess, "watchdog_state") and unit in sess.watchdog_state:
                        wd_state = sess.watchdog_state[unit]
                        if not wd_state.get("pending_ping", False):
                            wd_state["last_ping"] = time.time()
                            if sess.room:
                                next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in sess.watchdog_state.items() if sess.watchdogs.get(u) != 'none'}
                                socketio.emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(_get_enabled_auto_units(sess))}, room=sess.room)
            else:
                # No watchdog configured: passive traffic is the only liveness signal we have.
                was_offline = sess.unit_liveness.get(unit, True) is False
                sess.unit_liveness[unit] = True
                if was_offline:
                    # Unit just came back online! Resume paused automations
                    # BUT only if ALL units used by the automation are alive
                    now_ts = time.time()
                    for auto_id, auto in sess.automations.items():
                        if auto.get("status") == "ON" and auto.get("runtime", {}).get("state") == "PAUSED_NETWORK":
                            # Check if automation uses this unit
                            uses_unit = False
                            all_items = auto.get("actions", []) + auto.get("initialization", []) + auto.get("deinitialization", []) + auto.get("schedule", {}).get("setIfTrue", []) + auto.get("schedule", {}).get("setIfFalse", [])
                            for item in all_items:
                                ctrl = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                                if ctrl and unit in ctrl:
                                    uses_unit = True
                                    break
                            if not uses_unit:
                                continue
                            # Check ALL units used by this automation are alive
                            all_units_alive = True
                            for item in all_items:
                                ctrl = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                                if ctrl:
                                    ctrl_parts = ctrl.split("/")
                                    if len(ctrl_parts) >= 4:
                                        other_unit = ctrl_parts[3].split("_")[0]
                                        if sess.unit_liveness.get(other_unit, True) is False:
                                            all_units_alive = False
                                            break
                            if all_units_alive:
                                _resume_network_pause(auto, auto["runtime"], now_ts)
                                _auto_log(auto_id, f"Unit {unit} recovered. Resuming automation \u2192 {auto['runtime']['state']}", "info")
                                _emit_auto_update(auto)

        # 3. Handle Sensor History (if payload is numeric)
        if isinstance(payload, (int, float)):
            _append_sensor_history(sess, topic, payload, now)
        elif isinstance(payload, dict):
            for key in ("temperature", "humidity", "temp", "hum", "value", "state",
                        "power", "brightness", "color_temp", "battery", "rssi",
                        "voltage", "current"):
                if key in payload and isinstance(payload[key], (int, float)):
                    sub_topic = f"{topic}/{key}"
                    _append_sensor_history(sess, sub_topic, payload[key], now)

        # 4. Broadcast to user's private room
        socketio.emit("device_update", {
            "topic": topic,
            "payload": payload,
            "raw": payload_raw,
            "ts": now,
        }, room=sess.room)
        
    except Exception as e:
        logging.error(f"[MQTT:{owner_email}] Error: {e}")

def _append_sensor_history(sess, topic, value, ts):
    """Append a numeric value to session sensor history."""
    if topic not in sess.sensor_history:
        sess.sensor_history[topic] = []
    sess.sensor_history[topic].append({"ts": ts, "value": value})
    if len(sess.sensor_history[topic]) > MAX_HISTORY:
        sess.sensor_history[topic] = sess.sensor_history[topic][-MAX_HISTORY:]

def on_subscribe(client, userdata, mid, granted_qos):
    owner_email = userdata.get("owner_email")
    rejected = all(q == 128 for q in granted_qos)
    if rejected:
        logging.warning(f"[MQTT:{owner_email}] Subscription REJECTED: mid={mid}")
    else:
        logging.debug(f"[MQTT:{owner_email}] Subscription OK: mid={mid}")


ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@nivixsa.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "nivixsa-admin-2024")

def _sync_master_admin():
    """Ensure the master admin from .env exists in the DB with Level 1 (super).
    Support (Level 2) and observer (Level 3) admins are created by the super admin
    from the admin panel (Admin Team → Add Admin), not seeded from .env."""
    try:
        import db
        db.save_admin(ADMIN_EMAIL, ADMIN_PASSWORD, level=1)
        logging.info(f"[AUTH] Master Admin synced: {ADMIN_EMAIL} (Level 1)")
    except Exception as e:
        logging.error(f"[AUTH] Master Admin sync failed: {e}")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        
        import db
        admin = db.get_admin(email)
        if admin and admin["password"] == password:
            session["admin_email"] = admin["email"]
            session["admin_level"] = admin["level"]
            return redirect("/admin")
        return render_template("admin_login.html", error="Invalid admin credentials")
    return render_template("admin_login.html")

@app.route("/admin")
def admin_dashboard():
    # Verify sess (Any admin level can see overview)
    if not session.get("admin_email"):
        return redirect("/admin/login")
    
    import db
    users = db.get_all_users_for_admin()
    admins = db.get_all_admins()
    return render_template("admin.html", 
                          users=users, 
                          admins=admins,
                          admin_email=session.get("admin_email"),
                          admin_level=session.get("admin_level"),
                          active_sessions=session_mgr.active_count())

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_email", None)
    session.pop("admin_password", None)
    return redirect("/")

@app.route("/logout")
def web_logout():
    """Clear all user session data (including impersonation) and redirect to login."""
    session.pop("email", None)
    session.pop("password", None)
    session.pop("user_session_token", None)
    msg = request.args.get("msg", "")
    if msg:
        import urllib.parse
        return redirect(f"/?msg={urllib.parse.quote(msg)}")
    return redirect("/")

def _admin_telemetry_loop():
    """Background task to send system health and user stats to admin dashboard."""
    while True:
        try:
            # CPU/RAM
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            
            # DB Storage usage
            db_size_mb = 0
            try:
                if os.path.exists("cadio.db"):
                    db_size_mb = round(os.path.getsize("cadio.db") / (1024 * 1024), 2)
            except (OSError, PermissionError): pass
            
            # Broadcast Health
            socketio.emit("admin_health", {
                "cpu": cpu,
                "ram_gb": round(ram.used / (1024**3), 2),
                "ram_percent": ram.percent,
                "db_size": db_size_mb,
                "latency": 42
            }, room="admin_room")

            # Broadcast Live User Stats
            _emit_admin_stats()

        except Exception: pass
        time.sleep(5) # Push every 5 seconds for live feel

def _emit_admin_stats():
    """Helper to broadcast latest stats and user list to all connected admin sessions."""
    import db
    try:
        all_users = db.get_all_users_for_admin()
        
        # Enrich users with LIVE online status
        for u in all_users:
            u['online'] = session_mgr.is_online(u['email'])

        blocked = len([u for u in all_users if u['blocked']])
        socketio.emit("admin_update", {
            "active_sessions": session_mgr.active_count(),
            "blocked_count": blocked,
            "total_users": len(all_users),
            "users": all_users # Send full list for live updates
        }, room="admin_room")
    except Exception as e:
        logging.error(f"[ADMIN] Failed to emit stats: {e}")

@socketio.on("admin_user_block")
def handle_admin_user_block(data):
    """Admin action: Block/Unblock a user."""
    # Level 1 and 2 can block
    if session.get("admin_level", 3) > 2: return
    
    email = data.get("email")
    status = data.get("status") # 1 to block, 0 to unblock
    if not email: return

    import db
    if status == 1:
        db.block_user(email)
        # 1. Kill MQTT Session
        session_mgr.remove_session(email)
        # 2. Purge push subscriptions so a blocked user stops getting notifications
        db.delete_all_push_subscriptions(email)
        # 3. Nuclear Kick from all devices/browsers
        sids_to_kick = [sid for sid, e in list(_user_sessions.items()) if e.lower() == email.lower()]
        for sid in sids_to_kick:
            socketio.emit("force_logout", {
                "email": email,
                "message": "Account blocked by admin. Security policy enforced."
            }, room=sid)
            _user_sessions.pop(sid, None)
        logging.info(f"[ADMIN] User {email} BLOCKED and global force_logout dispatched.")
    else:
        db.unblock_user(email)
    _emit_admin_stats()

@socketio.on("admin_user_delete")
def handle_admin_user_delete(data):
    """Admin action: Permanently delete a user."""
    if session.get("admin_level", 3) > 1: return
    
    email = data.get("email")
    if not email: return
    
    # 1. Kill and remove active session
    import db
    session_mgr.remove_session(email)
    
    # 2. Kick any active sockets (Force Logout)
    sids_to_kick = [sid for sid, e in list(_user_sessions.items()) if e.lower() == email.lower()]
    for sid in sids_to_kick:
        socketio.emit("force_logout", {
            "email": email,
            "message": "Account deleted by admin."
        }, room=sid)
        _user_sessions.pop(sid, None)
        
    # 3. Wipe from DB
    db.delete_user(email)
    logging.info(f"[ADMIN] User {email} PERMANENTLY DELETED and session killed.")
    _emit_admin_stats()

@socketio.on("join_admin")
def handle_join_admin():
    if not session.get("admin_email"): return
    join_room("admin_room")
    logging.info(f"[ADMIN] {session.get('admin_email')} joined admin telemetry room")
    _emit_admin_stats()

@socketio.on("admin_team_add")
def handle_admin_team_add(data=None):
    if not data: return
    # ONLY Level 1 can manage team
    if session.get("admin_level", 3) > 1: return
    
    email = data.get("email")
    password = data.get("password")
    level = int(data.get("level", 3))
    import db
    db.save_admin(email, password, level)
    _emit_admin_stats()

@socketio.on("admin_team_delete")
def handle_admin_team_delete(data):
    # ONLY Level 1 can manage team
    if session.get("admin_level", 3) > 1: return
    
    email = data.get("email")
    # Prevent self-deletion
    if email == session.get("admin_email"): return
    
    import db
    db.delete_admin(email)
    _emit_admin_stats()

@app.route("/admin/impersonate/<email>")
def admin_impersonate(email):
    """Admin only: Securely impersonate a user using a verified token."""
    email = email.lower()
    if not session.get("admin_email") or session.get("admin_level", 3) > 2:
        return "Unauthorized", 403
        
    token = request.args.get("token")
    if not token or _impersonation_tokens.get(token) != email:
        return "Invalid or expired security token. Access denied.", 403
    
    # Token used once, remove it
    _impersonation_tokens.pop(token, None)

    import db
    user = db.get_user(email)
    if not user: return "User not found", 404
    
    session["email"] = user["email"]
    session["password"] = user["password"]
    logging.info(f"[ADMIN] Impersonating user via OTP: {email}")
    return redirect("/")

@socketio.on("admin_request_otp")
def handle_admin_request_otp(data):
    """Admin requests access to a user account.
    Level 1 (super admin): direct access — no OTP, and no popup on the user's screen.
    Level 2 (support): OTP code is shown on the user's live dashboard.
    Level 3 (observer): not allowed (unchanged)."""
    level = session.get("admin_level", 3)
    if level > 2: return
    email = data.get("email", "").strip().lower()

    # Super admin: skip the OTP entirely, mint the impersonation token immediately.
    # No security_code_request is emitted, so the user sees no popup.
    if level == 1:
        import db
        if not db.get_user(email):
            emit("admin_otp_error", {"message": "User not found."})
            return
        token = str(uuid.uuid4())
        _impersonation_tokens[token] = email
        logging.info(f"[ADMIN] Super admin direct access to {email} (no OTP)")
        emit("admin_otp_success", {"email": email, "token": token})
        return

    # Level 2 (support): OTP flow — requires the user online to display the code.
    sess = session_mgr.get_session(email)
    if not sess:
        emit("admin_otp_error", {"message": "User is not currently online. Admin can only login if user dashboard is active."})
        return

    import random
    code = str(random.randint(100000, 999999))
    sess.otp = code
    # Show code on USER dashboard
    socketio.emit("security_code_request", {"code": code}, room=sess.room)
    emit("admin_otp_sent", {"email": email})

@socketio.on("admin_verify_otp")
def handle_admin_verify_otp(data):
    """Admin submits the code provided by the user."""
    if session.get("admin_level", 3) > 2: return
    email = data.get("email", "").strip().lower()
    code = data.get("code")
    sess = session_mgr.get_session(email)
    
    if sess and sess.otp == code:
        token = str(uuid.uuid4())
        _impersonation_tokens[token] = email
        sess.otp = None # Clear it
        emit("admin_otp_success", {"email": email, "token": token})
    else:
        emit("admin_otp_error", {"message": "Invalid security code."})


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    # If an admin is impersonating, we might have email/password in session
    mqtt_user = session.get("email", MQTT_USERNAME)
    mqtt_pass = session.get("password", "")
    return render_template("index.html", 
                          mqtt_user=mqtt_user, 
                          mqtt_pass=mqtt_pass,
                          cache_bust=str(int(time.time())))

@app.route("/sw.js")
def service_worker():
    """Serve the PWA service worker from root scope."""
    response = send_from_directory("static", "sw.js")
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/api/push/public-key", methods=["GET"])
def get_push_public_key():
    pub_key = os.environ.get("VAPID_PUBLIC_KEY")
    return jsonify({"publicKey": pub_key})


@app.route("/api/watchdogs", methods=["GET"])
def api_get_watchdogs():
    email = None
    token = session.get("user_session_token")
    if token:
        import db
        email = db.validate_user_session(token)
    if not email:
        email = session.get("email")
    if not email:
        return jsonify({"error": "Unauthorized"}), 401
    
    sess = session_mgr.get_session(email)
    if not sess:
        return jsonify({"success": False, "error": "No session"}), 401
    return jsonify({"success": True, "watchdogs": sess.watchdogs})

@app.route("/api/watchdogs", methods=["POST"])
def api_save_watchdogs():
    email = None
    token = session.get("user_session_token")
    if token:
        import db
        email = db.validate_user_session(token)
    if not email:
        email = session.get("email")
    if not email:
        return jsonify({"error": "Unauthorized"}), 401

    sess = session_mgr.get_session(email)
    if not sess:
        return jsonify({"success": False, "error": "No session"}), 401
    
    data = request.json or {}
    unit = data.get("unit")
    topic = data.get("topic")
    if not unit:
        return jsonify({"success": False, "error": "Missing unit"}), 400
    
    if topic and topic != "none":
        sess.watchdogs[unit] = topic
    else:
        sess.watchdogs[unit] = "none"
        
    import db
    db.set_watchdogs(email, sess.watchdogs)
    
    socketio.emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "enabled_units": list(_get_enabled_auto_units(sess))}, room=sess.room)
    return jsonify({"success": True, "watchdogs": sess.watchdogs})


@app.route("/api/push/subscribe", methods=["POST"])
def subscribe_push():
    email = None
    token = session.get("user_session_token")
    if token:
        import db
        email = db.validate_user_session(token)
    if not email:
        email = session.get("email")

    if not email:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Invalid subscription data"}), 400

    import db
    db.save_push_subscription(email, endpoint, p256dh, auth)
    return jsonify({"success": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
def unsubscribe_push():
    data = request.get_json() or {}
    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify({"error": "Missing endpoint"}), 400

    import db
    db.delete_push_subscription(endpoint)
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def handle_ws_connect():
    # On initial connect, send "not connected" — user must login first
    emit("mqtt_status", {"connected": False, "message": "Not connected"})


@socketio.on("disconnect")
def handle_ws_disconnect():
    sid = request.sid
    # Standard disconnect (browser close) doesn't necessarily kill the MQTT session 
    # unless it was the last socket.
    _sid_token.pop(sid, None)
    email = _user_sessions.pop(sid, None)
    if email:
        session_mgr.unregister_socket(sid)
        # If no more sockets for this user, we could stop MQTT, 
        # but usually we keep it alive for automations.
    _emit_admin_stats()


def _resolve_session_token(email, data):
    """Return the DB session token to use for this device.

    Reuses the client-provided token (from localStorage) or the cookie token when
    it is still valid for this user — this keeps a single session row per device
    across page refreshes/reconnects. Only creates a new token when none is valid.
    """
    import db
    from flask import request as ws_request
    client_token = data.get("token") or session.get("user_session_token")
    if client_token and db.validate_user_session(client_token) == email:
        return client_token
    ua = ""
    try:
        ua = ws_request.headers.get("User-Agent", "")
    except Exception:
        pass
    return db.create_user_session(email, ua)


@socketio.on("login")
def handle_login(data):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    # 1. Check if blocked locally BEFORE hitting external API
    import db
    if db.is_user_blocked(email):
        emit("mqtt_status", {"connected": False, "message": "Account blocked: security strike policy. Contact admin."})
        return

    # 1.5 Token-gated auto-login: if this is a SILENT auto-login (from saved
    # credentials / reconnect) and this device holds a session token that has
    # been revoked (e.g. "Log out this device" was used from elsewhere), refuse
    # the re-login and force the user to sign in manually again. This makes
    # per-device logout effective even for devices that were offline at the time.
    # The device's token is provided by the client (localStorage) and/or the cookie.
    client_token = data.get("token") or session.get("user_session_token")
    if data.get("auto") and client_token:
        try:
            revoked = db.validate_user_session(client_token) != email
        except Exception:
            revoked = False  # fail open: never block a login on a DB hiccup
        if revoked:
            emit("force_logout", {
                "email": email,
                "message": "This device was logged out. Please sign in again."
            })
            return

    # 2. Fast path: if session already exists and is connected, just re-join the room
    existing_sess = session_mgr.get_session(email)
    if existing_sess and existing_sess.mqtt_connected and existing_sess.password == password:
        # Reuse this device's existing valid token (avoids creating a duplicate
        # session row on every refresh); create one only if none is valid yet.
        tok = _resolve_session_token(email, data)
        session["user_session_token"] = tok
        _user_sessions[request.sid] = email
        _sid_token[request.sid] = tok
        session_mgr.register_socket(request.sid, email)
        join_room(existing_sess.room)
        emit("session_token", {"token": tok})
        emit("mqtt_status", {"connected": True, "message": "Connected"})
        for topic, d in existing_sess.device_states.items():
            emit("device_update", {"topic": topic, **d})
        for auto in db.load_automations(email):
            logs = getattr(existing_sess, "auto_logs", {}).get(auto["id"], [])
            emit("automation_update", {"automation": auto, "logs": logs})
        next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in getattr(existing_sess, "watchdog_state", {}).items() if existing_sess.watchdogs.get(u) != 'none'}
        emit("watchdogs_update", {"watchdogs": existing_sess.watchdogs, "liveness": getattr(existing_sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(_get_enabled_auto_units(existing_sess))})
        _emit_admin_stats()
        return

    # 3. Validate credentials with CADIO
    success = cadio_login(email, password)
    if not success:
        emit("mqtt_status", {"connected": False, "message": "Cadio Login Failed (Check email/password)"})
        return
    
    # 4. Save valid user to DB FIRST (needed for foreign key on sessions table)
    db.save_user(email, password)
    db.unblock_user(email)
    
    # 5. Reuse this device's existing valid token or create a new one
    session_token = _resolve_session_token(email, data)
    session["user_session_token"] = session_token
    emit("session_token", {"token": session_token})
        
    # 6. Register socket and join user's private room
    _user_sessions[request.sid] = email
    _sid_token[request.sid] = session_token
    sess = session_mgr.create_session(
        email, password,
        broker=MQTT_BROKER, port=MQTT_PORT, discovery_prefix=DISCOVERY_PREFIX
    )
    session_mgr.register_socket(request.sid, email)
    join_room(sess.room)
    
    # 7. Load automations into the session
    _load_session_automations(sess, email)
    
    # 8. Connect session's MQTT client (if not already connected)
    if not sess.mqtt_connected:
        sess.start_mqtt(socketio)
    else:
        # Already connected (e.g. second tab) — send current state
        emit("mqtt_status", {"connected": True, "message": "Connected"})
        for topic, data in sess.device_states.items():
            emit("device_update", {"topic": topic, **data})
    
    # 9. Send automation state to this client
    for auto in db.load_automations(email):
        logs = getattr(sess, "auto_logs", {}).get(auto["id"], [])
        emit("automation_update", {"automation": auto, "logs": logs})
    next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in getattr(sess, "watchdog_state", {}).items() if sess.watchdogs.get(u) != 'none'}
    emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(_get_enabled_auto_units(sess))})

    _emit_admin_stats()

@socketio.on("set_watchdog")
def handle_set_watchdog(data):
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess: return
    unit = data.get("unit")
    topic = data.get("topic")
    if not unit: return
    
    if topic and topic != "none":
        sess.watchdogs[unit] = topic
    else:
        sess.watchdogs[unit] = "none"
        
    import db
    db.set_watchdogs(sess.email, sess.watchdogs)
    next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in getattr(sess, "watchdog_state", {}).items() if sess.watchdogs.get(u) != 'none'}
    socketio.emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(_get_enabled_auto_units(sess))}, room=sess.room)



@socketio.on("save_push_subscription")
def handle_save_push_subscription(data):
    """Save a Web Push subscription via Socket.IO."""
    email = _user_sessions.get(request.sid)
    if not email:
        return
    
    endpoint = data.get("endpoint")
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return

    import db
    db.save_push_subscription(email, endpoint, p256dh, auth)
    logging.info(f"[WebPush:{email}] Saved push subscription via Socket.IO")


@socketio.on("unsubscribe_push")
def handle_unsubscribe_push(data):
    """Remove a Web Push subscription via Socket.IO."""
    endpoint = data.get("endpoint")
    if not endpoint:
        return
    import db
    db.delete_push_subscription(endpoint)
    logging.info("[WebPush] Removed push subscription via Socket.IO")


@socketio.on("logout")
def handle_logout():
    user_email = _get_user_email()
    user_session = session_mgr.get_session(user_email)
    
    # Save automations to DB
    if user_session:
        try:
            import db
            for auto_id, auto in user_session.automations.items():
                db.save_automation(user_email, auto)
            db.clear_last_login(user_email)
            logging.info(f"[SESSION:{user_email}] Saved {len(user_session.automations)} automations on logout")
        except Exception as e:
            logging.error(f"[SESSION:{user_email}] Logout save failed: {e}")
    
    # Global session invalidation: delete ALL DB sessions for this user
    import db
    db.delete_all_user_sessions(user_email)
    # Purge every device's push subscription so notifications stop everywhere
    db.delete_all_push_subscriptions(user_email)
    session.pop("user_session_token", None)
    
    # Broadcast force_logout to ALL sockets of this user (other tabs/devices)
    sids = [s for s, e in list(_user_sessions.items()) if e and e.lower() == (user_email or "").lower()]
    for s in sids:
        if s != request.sid:
            socketio.emit("force_logout", {
                "email": user_email,
                "message": "Your session has been logged out from another device."
            }, room=s)
    
    # Unregister ALL sockets for this user
    for s in sids:
        _user_sessions.pop(s, None)
        _sid_token.pop(s, None)
    if user_session:
        session_mgr.unregister_socket(request.sid)
        leave_room(user_session.room)
        session_mgr.remove_session(user_email)
    
    _emit_admin_stats()
    emit("mqtt_status", {"connected": False, "message": "Not connected"})


def _parse_user_agent(ua):
    """Produce a short, human-friendly device/browser label from a User-Agent string."""
    ua = ua or ""
    ua_l = ua.lower()
    # Operating system / device
    if "android" in ua_l:
        os_name = "Android"
    elif "iphone" in ua_l or "ipad" in ua_l or "ipod" in ua_l:
        os_name = "iOS"
    elif "windows" in ua_l:
        os_name = "Windows"
    elif "mac os" in ua_l or "macintosh" in ua_l:
        os_name = "macOS"
    elif "linux" in ua_l:
        os_name = "Linux"
    else:
        os_name = "Unknown OS"
    # Browser
    if "edg/" in ua_l or "edge" in ua_l:
        browser = "Edge"
    elif "chrome" in ua_l and "chromium" not in ua_l:
        browser = "Chrome"
    elif "firefox" in ua_l:
        browser = "Firefox"
    elif "safari" in ua_l:
        browser = "Safari"
    else:
        browser = "Browser"
    return f"{browser} on {os_name}"


@socketio.on("list_user_sessions")
def handle_list_user_sessions():
    """Send the current user their active login sessions (devices)."""
    email = _user_sessions.get(request.sid)
    if not email:
        emit("user_sessions", {"sessions": []})
        return
    import db
    current_token = session.get("user_session_token")
    online_tokens = set(t for t in _sid_token.values() if t)
    rows = db.get_user_sessions(email)
    out = []
    for r in rows:
        token = r.get("session_token", "")
        out.append({
            "id": token[:12],  # short, non-credential identifier used for revocation
            "device": _parse_user_agent(r.get("user_agent")),
            "user_agent": r.get("user_agent") or "",
            "created_at": r.get("created_at") or "",
            "expires_at": r.get("expires_at") or "",
            "current": bool(current_token) and token == current_token,
            "online": token in online_tokens,
        })
    emit("user_sessions", {"sessions": out})


@socketio.on("logout_device")
def handle_logout_device(data):
    """Revoke a single login session (device) belonging to the current user."""
    email = _user_sessions.get(request.sid)
    if not email:
        return
    session_id = (data or {}).get("id", "")
    if not session_id:
        return
    import db
    # Resolve the short id back to the full token, scoped to this user only.
    match = next((r["session_token"] for r in db.get_user_sessions(email)
                  if r.get("session_token", "").startswith(session_id)), None)
    if not match:
        emit("user_sessions_error", {"message": "Session not found."})
        return

    # Delete the token from the DB (device is logged out on its next request).
    db.delete_user_session(match)

    # Immediately force-disconnect any live sockets bound to that token.
    sids = [s for s, t in list(_sid_token.items()) if t == match]
    for s in sids:
        socketio.emit("force_logout", {
            "email": email,
            "message": "This device was logged out from another device."
        }, room=s)
        _sid_token.pop(s, None)
        _user_sessions.pop(s, None)
        try:
            session_mgr.unregister_socket(s)
        except Exception:
            pass

    # Refresh the requester's device list (unless they logged themselves out).
    if match != session.get("user_session_token"):
        handle_list_user_sessions()


@socketio.on("logout_this_device")
def handle_logout_this_device():
    """Log out ONLY the current device. Other devices stay signed in and the
    user's MQTT session / automations keep running."""
    email = _user_sessions.get(request.sid)
    token = session.get("user_session_token")

    # Revoke just this device's session token (if it has one).
    if token:
        import db
        db.delete_user_session(token)
    session.pop("user_session_token", None)

    # Unregister only this socket; leave the shared user session intact so
    # other devices and running automations are unaffected.
    _user_sessions.pop(request.sid, None)
    _sid_token.pop(request.sid, None)
    user_session = session_mgr.get_session(email) if email else None
    if user_session:
        try:
            leave_room(user_session.room)
        except Exception:
            pass
    try:
        session_mgr.unregister_socket(request.sid)
    except Exception:
        pass

    _emit_admin_stats()
    emit("mqtt_status", {"connected": False, "message": "Not connected"})


@socketio.on("publish")
def handle_publish(data):
    topic = data.get("topic", "")
    payload = data.get("payload", "")
    session = session_mgr.get_session_by_sid(request.sid)
    if session and session.mqtt_client and session.mqtt_connected and topic:
        session.mqtt_client.publish(topic, payload)
        emit("publish_ack", {"topic": topic, "payload": payload, "ok": True})
    else:
        emit("publish_ack", {"ok": False, "error": "MQTT not connected"})


@socketio.on("subscribe")
def handle_subscribe(data):
    topic = data.get("topic", "#")
    session = session_mgr.get_session_by_sid(request.sid)
    if session and session.mqtt_client and session.mqtt_connected:
        session.mqtt_client.subscribe(topic)


@socketio.on("get_history")
def handle_get_history(data):
    topic = data.get("topic", "")
    session = session_mgr.get_session_by_sid(request.sid)
    if session:
        history = session.sensor_history.get(topic, [])
    else:
        history = sensor_history.get(topic, [])
    emit("sensor_history", {"topic": topic, "history": history})


# ---------------------------------------------------------------------------
# Irrigation Automation Engine
# ---------------------------------------------------------------------------

def _get_auto_now(auto):
    """Return the current datetime adjusted for the automation's utcOffset.
    JS getTimezoneOffset() returns positive values for west of UTC (e.g. UTC-5 = 300).
    We store the same convention: utcOffset in minutes, where UTC+5:30 = -330.
    Formula: local_time = utc_time - offset_in_minutes.
    Falls back to server local time if no offset is configured."""
    sched = auto.get("schedule", {}) if auto else {}
    utc_offset_mins = sched.get("utcOffset")
    if utc_offset_mins is not None:
        try:
            offset = int(utc_offset_mins)
            return datetime.utcnow() - timedelta(minutes=offset)
        except (ValueError, TypeError):
            pass
    return datetime.now()


def _send_web_push_async(owner_email, data):
    """Sends Web Push notifications via pywebpush asynchronously in a background thread."""
    def run():
        try:
            import db
            # Safety: never fan out to every user's subscriptions
            if not owner_email:
                logging.warning("[WebPush] Skipping push with no owner_email")
                return
            # Get active subscriptions
            subscriptions = db.get_push_subscriptions(owner_email)
            if not subscriptions:
                return

            private_key = os.environ.get("VAPID_PRIVATE_KEY")
            claims_email = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:admin@nivixsa.com")
            
            if not private_key:
                logging.warning("[WebPush] VAPID_PRIVATE_KEY is missing, skipping push notification.")
                return

            vapid_claims = {"sub": claims_email}
            
            # Prepare payload JSON
            import json
            payload = json.dumps({
                "title": data["title"],
                "body": data["message"],
                "message": data["message"],
                "type": data["type"],
                "timestamp": data["timestamp"]
            })

            for sub in subscriptions:
                try:
                    subscription_info = {
                        "endpoint": sub["endpoint"],
                        "keys": {
                            "p256dh": sub["p256dh"],
                            "auth": sub["auth"]
                        }
                    }
                    from pywebpush import webpush, WebPushException
                    webpush(
                        subscription_info=subscription_info,
                        data=payload,
                        vapid_private_key=private_key,
                        vapid_claims=vapid_claims
                    )
                    logging.info(f"[WebPush] Successfully sent push to {sub['endpoint'][:30]}...")
                except Exception as ex:
                    # Catch WebPushException specifically to prune expired subscriptions
                    from pywebpush import WebPushException
                    if isinstance(ex, WebPushException):
                        logging.warning(f"[WebPush] Failed for endpoint {sub['endpoint'][:30]}: {ex}")
                        if ex.response is not None and ex.response.status_code in (400, 410, 404):
                            logging.info(f"[WebPush] Removing bad/expired subscription for endpoint")
                            db.delete_push_subscription(sub["endpoint"])
                    else:
                        logging.error(f"[WebPush] Error sending push: {ex}")
        except Exception as e:
            logging.error(f"[WebPush] Thread runtime error: {e}")

    import threading
    threading.Thread(target=run, daemon=True).start()


def send_sys_notification(owner_email, title, message, type="info"):
    """Send a system/PWA notification to all connected browser sessions of the owner."""
    data = {
        "title": title,
        "message": message,
        "type": type,
        "timestamp": time.time()
    }
    
    if not owner_email:
        # Never broadcast to everyone: without an owner we cannot scope the
        # notification, so drop it rather than leaking it to all users.
        logging.warning(f"[NOTIFY] Dropped notification with no owner_email: {title}")
        return

    room = f"user_{owner_email.replace('@', '_').replace('.', '_')}"
    logging.info(f"[NOTIFY:{owner_email}] {title}: {message} ({type})")
    socketio.emit("sys_notification", data, room=room)
    
    # Push Web Push notification natively to mobile/desktop lock screens
    _send_web_push_async(owner_email, data)


def _auto_log(auto_id, message, level="info", notify=True):
    """Append a timestamped log entry for an automation (timezone-aware).
    Routes to sess logs if the automation belongs to a sess user."""
    # Determine log target (sess or global)
    auto = None
    sess = None
    # Check sessions first
    for email, s in list(session_mgr._sessions.items()):
        if auto_id in s.automations:
            auto = s.automations[auto_id]
            sess = s
            break
    # Fallback to global
    if not auto:
        auto = automations.get(auto_id)

    # We log absolute UTC time so the frontend can dynamically shift it 
    # to the automation's currently configured timezone, even if it changes later.
    now_utc = datetime.utcnow()
    entry = {"ts": now_utc.isoformat() + "Z", "msg": message, "level": level}

    if sess:
        if auto_id not in sess.automation_logs:
            sess.automation_logs[auto_id] = []
        sess.automation_logs[auto_id].insert(0, entry)
        if len(sess.automation_logs[auto_id]) > MAX_AUTO_LOG:
            sess.automation_logs[auto_id] = sess.automation_logs[auto_id][:MAX_AUTO_LOG]
    else:
        if auto_id not in automation_logs:
            automation_logs[auto_id] = []
        automation_logs[auto_id].insert(0, entry)
        if len(automation_logs[auto_id]) > MAX_AUTO_LOG:
            automation_logs[auto_id] = automation_logs[auto_id][:MAX_AUTO_LOG]
    logging.info(f"[AUTO {auto_id}] {message}")

    # --- PWA & Dashboard Alerts Routing ---
    if auto and sess and notify:
        email = sess.email
        notify_title = None
        notify_type = "info"
        auto_name = auto.get("name", "Smart Sprinkler")

        if level == "error":
            notify_title = f"🚨 {auto_name} - Error"
            notify_type = "error"
        elif level == "warning":
            notify_title = f"⚠️ {auto_name} - Warning"
            notify_type = "warning"
        elif "ACTION_RUN" in message:
            notify_title = f"💧 Irrigation Cycle Started"
            notify_type = "success"
        elif "COMPLETED" in message:
            notify_title = f"✅ Irrigation Cycle Completed"
            notify_type = "success"
        elif "AI updated days" in message:
            notify_title = f"🤖 AI Agronomist Schedule Update"
            notify_type = "success"
        elif "User paused" in message:
            notify_title = f"⏸️ Automation Paused"
            notify_type = "info"
        elif "User resumed" in message:
            notify_title = f"▶️ Automation Resumed"
            notify_type = "info"
        elif "drift detected" in message:
            notify_title = f"⚠️ Controller Drift Warning"
            notify_type = "warning"

        if notify_title:
            send_sys_notification(email, notify_title, message, type=notify_type)



def _new_runtime(old_rt=None):
    """Return a fresh runtime block, preserving history if old_rt is provided."""
    rt = {
        "state": "IDLE",
        "currentActionIndex": 0,
        "timerStart": None,
        "remainingTime": None,
        "retryCount": 0,
        "pauseReason": None,
        "verifyStart": None,
        "bufferStart": None,
        "currentInitIndex": 0,
        "errorReason": None,
    }
    if old_rt:
        for k in ["cycles_today", "cycles_date", "cycles_history", "duration_history", "last_irrigated"]:
            if k in old_rt:
                rt[k] = old_rt[k]
    return rt


def _get_switch_state(switch_topic, auto=None):
    """Get the latest known state for a switch.
    Routes to the owning sess's device_states, falls back to global."""
    source = device_states  # default fallback
    if auto:
        owner = auto.get("_owner_email", "")
        sess = session_mgr.get_session(owner)
        if sess:
            source = sess.device_states
    data = source.get(switch_topic)
    if not data:
        return None
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload.get("state", "").upper()
    return str(payload).upper() if payload else None


def _topic_is_available(sess, ctrl_topic):
    """Check whether the device behind a control topic (cmd/state) is online.
    Returns True if available or unknown (no availability info), False if offline."""
    if not sess or not ctrl_topic:
        return True
    avail_topics = sess.avail_map.get(ctrl_topic)
    if not avail_topics:
        return True  # No availability topic known → assume available
    for avail_topic in avail_topics:
        data = sess.device_states.get(avail_topic)
        if not data:
            continue  # No availability message received yet → assume available
        payload = data.get("payload")
        raw = payload if isinstance(payload, str) else data.get("raw", str(payload))
        cfg = sess.avail_payloads.get(avail_topic, {})
        pl_avail = cfg.get("pl_avail", "online")
        pl_not_avail = cfg.get("pl_not_avail", "offline")
        if str(raw) == str(pl_not_avail):
            return False
        # If it explicitly matches available payload, or Nivixsa YES fallback, it's online
        if str(raw) == str(pl_avail) or str(raw).upper() in ("YES", "ONLINE"):
            continue
        # Unrecognized payload → treat "OFFLINE"/"NO"/"UNAVAILABLE" as offline
        if str(raw).upper() in ("NO", "OFFLINE", "UNAVAILABLE"):
            return False
    return True


def _automation_device_health(auto):
    """Classify the devices used by an automation.
    Returns {"missing": [names], "offline": [names]}.
      - missing: control topic is no longer present in discovery (device removed/renamed) → ERROR
      - offline: device is known but its availability reports offline → network pause
    """
    result = {"missing": [], "offline": []}
    owner = auto.get("_owner_email", "")
    sess = session_mgr.get_session(owner)
    if not sess:
        return result

    # Only judge a device "missing" once discovery has had time to populate,
    # to avoid false positives right after (re)connecting to the broker.
    discovery_ready = (
        bool(sess.known_control_topics)
        and (time.time() - getattr(sess, "_mqtt_last_connected_time", 0) > DISCOVERY_GRACE)
    )

    seen = set()

    def _check(item):
        ctrl = item.get("switchCmdTopic") or item.get("switchStateTopic") or item.get("sensorStateTopic")
        if not ctrl or ctrl in seen:
            return
        seen.add(ctrl)
        name = item.get("switchName") or item.get("sensorName") or ctrl
        if discovery_ready and ctrl not in sess.known_control_topics:
            result["missing"].append(name)
            return
            
        parts = ctrl.split("/")
        if len(parts) >= 4:
            obj_id = parts[3]
            unit = obj_id.split("_")[0]
            if getattr(sess, "unit_liveness", {}).get(unit, True) is False:
                result["offline"].append(name)
                return
                
        if not _topic_is_available(sess, ctrl):
            result["offline"].append(name)

    sched = auto.get("schedule", {})
    for group in (
        auto.get("initialization", []),
        auto.get("actions", []),
        auto.get("errorState", []),
        auto.get("condition", []),
        sched.get("setIfTrue", []),
        sched.get("setIfFalse", []),
    ):
        for item in group or []:
            _check(item)

    return result


def _enter_network_pause(auto, rt, now, resume_state, reason="not_obeying", retry=True):
    """Freeze the sequence timers and enter PAUSED_NETWORK.
    Resumes to `resume_state`. If retry=True, resumes automatically after
    NETWORK_RETRY_DELAY (used when a reachable device won't obey)."""
    if rt.get("timerStart"):
        elapsed = now - rt["timerStart"]
        rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
        rt["timerStart"] = None
    if rt.get("bufferStart"):
        elapsed = now - rt["bufferStart"]
        rt["remainingBuffer"] = max(0, auto.get("bufferTime", BUFFER_SECONDS) - elapsed)
        rt["bufferStart"] = None
    rt["verifyStart"] = None
    rt["prePauseNetwork"] = resume_state
    rt["errorReason"] = reason
    rt["retryCount"] = 0
    rt["pauseUntil"] = (now + NETWORK_RETRY_DELAY) if retry else None
    rt["state"] = "PAUSED_NETWORK"


def _resume_network_pause(auto, rt, now):
    """Restore frozen timers and resume from the pre-pause state."""
    resume_state = rt.get("prePauseNetwork", "IDLE")
    rt["state"] = resume_state
    
    # Only mask the UI state as retrying if we are actively retrying a command/verification
    if resume_state.endswith("_SET") or "VERIFY" in resume_state:
        rt["isNetworkRetry"] = True
    else:
        rt.pop("isNetworkRetry", None)
        
    # _enter_network_pause() cleared verifyStart. Every *_VERIFY handler gates its
    # timeout on `now - (rt.get("verifyStart") or now) > TIMEOUT`, which evaluates
    # to 0 forever while verifyStart is None -- so a verify state resumed from a
    # network pause could never time out, never retry and never re-pause. The
    # automation sat silently in that state until the user toggled it off and on.
    # Restart the verify window from the moment we resume.
    if "VERIFY" in resume_state:
        rt["verifyStart"] = now
        rt["retryCount"] = 0

    if rt.get("remainingTime") is not None:
        rt["timerStart"] = now
    if rt.get("remainingBuffer") is not None:
        rt["bufferStart"] = now - (auto.get("bufferTime", BUFFER_SECONDS) - rt["remainingBuffer"])
        del rt["remainingBuffer"]
    rt["errorReason"] = None
    rt["pauseUntil"] = None



def _emit_mqtt_tx(sess, topic, payload, source="server"):
    """Mirror a server-originated MQTT publish (watchdog / automation engine) to the
    user's Developer live feed and Logbook, so outgoing server traffic is visible too
    (the browser only self-logs commands it sends itself)."""
    if not sess or not getattr(sess, "room", None):
        return
    socketio.emit("mqtt_tx", {
        "topic": topic,
        "payload": payload,
        "source": source,
        "ts": datetime.utcnow().isoformat() + "Z",
    }, room=sess.room)


def _mqtt_set_switch(cmd_topic, state, auto=None):
    """Publish a command to set a switch state.
    Routes to the owning sess's MQTT client, falls back to global."""
    if not cmd_topic:
        return
    payload = json.dumps({"state": state.upper()})
    if auto:
        owner = auto.get("_owner_email", "")
        sess = session_mgr.get_session(owner)
        if sess and sess.mqtt_client and sess.mqtt_connected:
            sess.mqtt_client.publish(cmd_topic, payload)
            logging.info(f"[ENGINE:{owner}] Published {payload} to {cmd_topic}")
            _emit_mqtt_tx(sess, cmd_topic, payload, source="automation")
            return
    # Legacy fallback
    if mqtt_client and mqtt_connected:
        mqtt_client.publish(cmd_topic, payload)
        logging.info(f"[ENGINE] Published {payload} to {cmd_topic}")


def _match_condition(cond, auto):
    """Evaluate a single sensor condition against its live value.

    Supports operators: ==, != (equality — numeric when both sides parse as numbers,
    otherwise case-insensitive string compare) and >, >=, <, <= (numeric only).
    Missing/legacy conditions (no "op") default to equality. Returns False when the
    live value is unavailable or a numeric operator gets a non-numeric value."""
    sensor_topic = cond.get("sensorStateTopic", "")
    actual = _get_switch_state(sensor_topic, auto)
    if actual is None:
        return False

    op = cond.get("op") or "=="
    expected = cond.get("value", "")

    if op in ("<", "<=", ">", ">="):
        try:
            a = float(actual)
            b = float(expected)
        except (ValueError, TypeError):
            return False
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        return a >= b

    # Equality / inequality: prefer numeric compare, fall back to string compare.
    try:
        eq = float(actual) == float(expected)
    except (ValueError, TypeError):
        eq = str(actual).strip().upper() == str(expected).strip().upper()
    return (not eq) if op == "!=" else eq


def evaluate_condition(auto):
    """Evaluate the condition expression using AND/OR logic.
    AND has higher precedence than OR (groups are formed by AND, then OR'd).
    Returns True if condition list is empty."""
    conditions = auto.get("condition", [])
    if not conditions:
        return True

    # Build results list with logic operators
    results = []
    for cond in conditions:
        matched = _match_condition(cond, auto)
        results.append({"matched": matched, "logic": cond.get("logic")})

    # Evaluate: AND groups first, then OR between groups
    # Split into OR-separated groups of AND-connected conditions
    or_groups = []
    current_group = [results[0]["matched"]]
    for i in range(1, len(results)):
        prev_logic = results[i - 1].get("logic", "AND")
        if prev_logic == "OR":
            or_groups.append(current_group)
            current_group = [results[i]["matched"]]
        else:  # AND
            current_group.append(results[i]["matched"])
    or_groups.append(current_group)

    # Each group must have ALL true (AND), then any group true (OR)
    return any(all(g) for g in or_groups)


def check_schedule(auto):
    """Check if current day+time+conditions falls within the schedule window.
    Returns True if no schedule is defined."""
    # Check if max cycles per day limit is reached
    max_cycles = auto.get("maxCyclesPerDay", 0)
    if max_cycles > 0:
        rt = auto.get("runtime", {})
        state = rt.get("state", "IDLE")
        if state not in ("INIT_SET", "INIT_VERIFY_INDIVIDUAL", "INIT_VERIFY_ALL", "ACTION_SET", "ACTION_RUN", "ACTION_DRIFT_VERIFY", "OVERLAP_NEXT_SET", "OVERLAP_NEXT_VERIFY", "BUFFER", "BUFFER_DRIFT_VERIFY", "ACTION_REVERT", "ACTION_VERIFY_REVERT", "ERROR_SET", "ERROR_VERIFY", "ERROR"):
            today_str = _get_auto_now(auto).strftime("%Y-%m-%d")
            cycles_today = rt.get("cycles_today", 0) if rt.get("cycles_date") == today_str else 0
            if cycles_today >= max_cycles:
                return False

    sched = auto.get("schedule")
    if not sched:
        return True
        
    days = sched.get("days", [])
    if not days:
        return False  # No days selected -> Deactivated
        
    start_str = sched.get("startTime", "")
    end_str = sched.get("endTime", "")

    # Use the automation's timezone
    now = _get_auto_now(auto)

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    current_day = day_names[now.weekday()]
    if current_day not in days:
        return False

    # --- Check time window ---
    time_ok = False

    if sched.get("is24hr"):
        time_ok = True
    else:
        ranges = sched.get("timeRanges", [])
        if not ranges:
            # Fallback for old single range format
            s_start = sched.get("startTime", "")
            s_end = sched.get("endTime", "")
            if not s_start or not s_end:
                time_ok = True
            else:
                ranges = [{"start": s_start, "end": s_end}]

        if not time_ok:
            now_mins = now.hour * 60 + now.minute
            for r in ranges:
                r_start = r.get("start", "")
                r_end = r.get("end", "")
                if not r_start or not r_end:
                    continue
                try:
                    start_h, start_m = map(int, r_start.split(":"))
                    end_h, end_m = map(int, r_end.split(":"))
                    start_mins = start_h * 60 + start_m
                    end_mins = end_h * 60 + end_m

                    if start_mins <= end_mins:
                        if start_mins <= now_mins < end_mins:
                            time_ok = True
                            break
                    else:
                        if now_mins >= start_mins or now_mins < end_mins:
                            time_ok = True
                            break
                except (ValueError, AttributeError):
                    continue

    if not time_ok:
        return False

    # --- Check scheduler sensor conditions ---
    sched_conditions = sched.get("conditions", [])
    if sched_conditions:
        if not _evaluate_sched_conditions(sched_conditions, auto):
            return False

    return True


def _evaluate_sched_conditions(conditions, auto):
    """Evaluate scheduler sensor conditions with AND/OR logic.
    Same logic as evaluate_condition() but reads from schedule.conditions."""
    if not conditions:
        return True

    results = []
    for cond in conditions:
        matched = _match_condition(cond, auto)
        results.append({"matched": matched, "logic": cond.get("logic")})

    # Evaluate: AND groups first, then OR between groups
    or_groups = []
    current_group = [results[0]["matched"]]
    for i in range(1, len(results)):
        prev_logic = results[i - 1].get("logic", "AND")
        if prev_logic == "OR":
            or_groups.append(current_group)
            current_group = [results[i]["matched"]]
        else:
            current_group.append(results[i]["matched"])
    or_groups.append(current_group)

    return any(all(g) for g in or_groups)


def _verify_switches(switch_list, auto):
    """Check if all switches in list match their expected state.
    switch_list: list of {switchCmdTopic, switchStateTopic, state}"""
    for item in switch_list:
        state_topic = item.get("switchStateTopic", "")
        expected = item.get("state", "").upper()
        actual = _get_switch_state(state_topic, auto)
        if actual != expected:
            return False
    return True


def _emit_auto_update(auto):
    """Broadcast automation state update to the owning user's room."""
    safe = copy.deepcopy(auto)
    auto_id = safe.get("id", "")
    owner_email = auto.get("_owner_email", "")
    
    # Inject current AI running status
    safe["ai_running"] = (auto_id in _ai_running_set)
    
    # Try sess logs first, fallback to global
    sess = session_mgr.get_session(owner_email) if owner_email else None
    if sess and auto_id in sess.automation_logs:
        logs = sess.automation_logs.get(auto_id, [])[:50]
    else:
        logs = automation_logs.get(auto_id, [])[:50]
    # Emit to user's room if available, otherwise broadcast (legacy)
    if sess:
        socketio.emit("automation_update", {"automation": safe, "logs": logs}, room=sess.room)
    else:
        socketio.emit("automation_update", {"automation": safe, "logs": logs})


def engine_tick(auto, sequence_overrides=None, schedule_overrides=None, session_automations=None):
    """Execute one tick of the state machine for an automation."""
    rt = auto["runtime"]
    state = rt["state"]
    auto_id = auto["id"]
    now = time.time()

    # Daily rollover: reset today's cycle counter shortly after midnight (at 00:01),
    # so the displayed count returns to 0 at the start of a new day. The 1-minute guard
    # avoids resetting exactly at 00:00 while a cycle may still be wrapping up.
    now_local = _get_auto_now(auto)
    today_str = now_local.strftime("%Y-%m-%d")
    minute_of_day = now_local.hour * 60 + now_local.minute
    if rt.get("cycles_today", 0) and rt.get("cycles_date") != today_str and minute_of_day >= 1:
        rt["cycles_date"] = today_str
        rt["cycles_today"] = 0
        rt.pop("_cycle_paused", None)
        _auto_log(auto_id, "New day (00:01) → daily cycle counter reset to 0")
        _emit_auto_update(auto)

    # Priority 1: If status is OFF
    if auto.get("status") != "ON":
        deinits = auto.get("deinitialization", [])

        # Deinitialization runs once when the automation is switched OFF: set each
        # switch, verify it individually, before settling to IDLE.
        if state == "DEINIT_SET":
            idx = rt.get("currentDeinitIndex", 0)
            if not deinits:
                rt["state"] = "IDLE"
                rt["currentDeinitIndex"] = 0
                rt["currentActionIndex"] = 0
                rt["timerStart"] = None
                rt["remainingTime"] = None
                rt["retryCount"] = 0
                rt["pauseReason"] = None
                _emit_auto_update(auto)
                return

            # --- Serialized deinit: wait if another automation sharing our switches
            # started deinit earlier and hasn't finished yet. ---
            my_queued_at = rt.get("deinit_queued_at", 0)
            my_topics = set(
                item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                for item in deinits
                if item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
            )
            # Look across ALL automations in the same session for a conflicting earlier deinit
            should_wait = False
            for other_id, other_auto in (session_automations or {}).items():
                if other_id == auto_id:
                    continue
                other_rt = other_auto.get("runtime", {})
                other_state = other_rt.get("state", "IDLE")
                if other_state not in ("DEINIT_SET", "DEINIT_VERIFY_INDIVIDUAL"):
                    continue
                other_queued_at = other_rt.get("deinit_queued_at", 0)
                # Tiebreak by auto_id (lexicographic) so both sides agree on who goes first
                # when timestamps are identical (e.g. toggled simultaneously by a script).
                if other_queued_at > my_queued_at:
                    continue  # Other started later — we go first
                if other_queued_at == my_queued_at and other_id > auto_id:
                    continue  # Same timestamp — smaller ID wins; if other_id is larger, we go first
                # Other started earlier — check if it shares any switch with us
                other_topics = set(
                    item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                    for item in other_auto.get("deinitialization", [])
                    if item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                )
                if my_topics & other_topics:  # Intersection — shared switches
                    should_wait = True
                    break
            if should_wait:
                # Don't log every tick — only log when we first start waiting
                if not rt.get("_deinit_waiting"):
                    rt["_deinit_waiting"] = True
                    _auto_log(auto_id, "DEINIT waiting for earlier automation to finish first", "warning")
                    _emit_auto_update(auto)
                return  # Try again next engine tick
            else:
                if rt.pop("_deinit_waiting", None):
                    _auto_log(auto_id, "DEINIT proceeding — earlier automation finished")
                    _emit_auto_update(auto)

            if idx < len(deinits):
                item = deinits[idx]
                topic = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                
                # Check if ANY OTHER automation has claimed this switch
                is_claimed_by_other = False
                for other_id, claims in (sequence_overrides or {}).items():
                    if other_id != auto_id and topic in claims:
                        is_claimed_by_other = True
                        break
                for other_id, claims in (schedule_overrides or {}).items():
                    if other_id != auto_id and topic in claims:
                        is_claimed_by_other = True
                        break

                if is_claimed_by_other:
                    _auto_log(auto_id, f"Yielding DEINIT on {item.get('switchName', 'switch')} to active sequence/schedule", "warning")
                    if "yielded_switches" not in rt:
                        rt["yielded_switches"] = []
                    if topic not in rt["yielded_switches"]:
                        rt["yielded_switches"].append(topic)
                    
                    rt["currentDeinitIndex"] = idx + 1
                    rt["state"] = "DEINIT_SET"
                    rt["retryCount"] = 0
                    _emit_auto_update(auto)
                else:
                    if topic in rt.get("yielded_switches", []):
                        rt["yielded_switches"].remove(topic)
                    _mqtt_set_switch(topic, item.get("state", "OFF"), auto)
                    rt["verifyStart"] = now
                    rt["state"] = "DEINIT_VERIFY_INDIVIDUAL"
                    _auto_log(auto_id, f"Deinitialization {idx+1}/{len(deinits)} sent → DEINIT_VERIFY_INDIVIDUAL")
                    _emit_auto_update(auto)
            else:
                rt["state"] = "IDLE"
                rt["currentDeinitIndex"] = 0
                rt["currentActionIndex"] = 0
                rt["timerStart"] = None
                rt["remainingTime"] = None
                rt["retryCount"] = 0
                rt["pauseReason"] = None
                _auto_log(auto_id, "All individual deinitialization commands sent → IDLE")
                _emit_auto_update(auto)
            return

        if state == "DEINIT_VERIFY_INDIVIDUAL":
            idx = rt.get("currentDeinitIndex", 0)
            if idx < len(deinits):
                item = deinits[idx]
                topic = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                yielded = rt.get("yielded_switches", [])
                
                if topic in yielded or _verify_switches([item], auto):
                    rt["currentDeinitIndex"] = idx + 1
                    rt["state"] = "DEINIT_SET"
                    rt["retryCount"] = 0
                    rt.pop("isNetworkRetry", None)
                    if topic not in yielded:
                        _auto_log(auto_id, f"Deinitialization {idx+1}/{len(deinits)} verified")
                    _emit_auto_update(auto)
                elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
                    rt["retryCount"] = rt.get("retryCount", 0) + 1
                    if rt["retryCount"] >= MAX_RETRIES:
                        # Network issue: pause until it comes online or retry delay elapses
                        _enter_network_pause(auto, rt, now, "DEINIT_SET", reason="not_obeying", retry=True)
                        rt.pop("retryCount", None)
                        _auto_log(auto_id, f"Deinit {idx+1}/{len(deinits)} not responding → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                        _emit_auto_update(auto)
                    else:
                        rt["state"] = "DEINIT_SET"
                        _auto_log(auto_id, f"Deinit {idx+1}/{len(deinits)} verify retry {rt['retryCount']}/{MAX_RETRIES}")
                        _emit_auto_update(auto)
            else:
                rt["state"] = "DEINIT_SET"
            return



        # If we are in a DEINIT_SET or DEINIT_VERIFY_INDIVIDUAL, we already processed and returned.
        # If we are in PAUSED_NETWORK for deinit, we want to let it fall through to standard network pause logic.
        if state == "PAUSED_NETWORK" and rt.get("prePauseNetwork") in ("DEINIT_SET", "DEINIT_VERIFY_INDIVIDUAL"):
            pass # Fall through to Priority 1.05 and Priority 2
        else:
            # No deinitialization in progress → settle to IDLE
            if state != "IDLE":
                rt["state"] = "IDLE"
                rt["currentActionIndex"] = 0
                rt["timerStart"] = None
                rt["remainingTime"] = None
                rt["retryCount"] = 0
                rt["pauseReason"] = None
                _auto_log(auto_id, "Status OFF → IDLE")
                _emit_auto_update(auto)
            return

    # Priority 1.05: Broker connectivity. Without a live MQTT link to the broker we
    # can neither command nor verify switches, so a dropped connection must pause the
    # sequence immediately — including mid-action (ACTION_RUN) — instead of only
    # surfacing when the next command is sent and its verify times out.
    owner_sess = session_mgr.get_session(auto.get("_owner_email", ""))
    if owner_sess and not owner_sess.mqtt_connected:
        if state not in ("IDLE", "PAUSED_NETWORK", "PAUSED_USER", "ERROR", "ERROR_SET", "ERROR_VERIFY"):
            _enter_network_pause(auto, rt, now, state, reason="offline", retry=False)
            if state == "ACTION_RUN":
                # Re-confirm the switch state the moment the broker link returns.
                rt["_recheckOnResume"] = True
            _auto_log(auto_id, "Broker connection lost → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
            _emit_auto_update(auto)
        elif state == "PAUSED_NETWORK":
            # Stay paused until the broker link returns.
            rt["errorReason"] = "offline"
            rt["pauseUntil"] = None
        return

    # Priority 1.1: Device health (missing / offline / not-obeying)
    #   - missing (removed/renamed in discovery) → genuine ERROR (locked)
    #   - offline / not-obeying → PAUSED_NETWORK (freeze + auto-resume)
    health = _automation_device_health(auto)
    missing_devices = health["missing"]
    offline_devices = health["offline"]

    # A referenced device no longer exists in discovery → real configuration error
    if missing_devices and state not in ("ERROR", "ERROR_SET", "ERROR_VERIFY"):
        names = ", ".join(missing_devices)
        rt["errorReason"] = "missing"
        rt["state"] = "ERROR_SET"
        _auto_log(auto_id, f"Device not found (removed/renamed): {names} → ERROR", "error")
        _emit_auto_update(auto)
        return

    if state == "PAUSED_NETWORK":
        # Already network-paused — decide hold / escalate / resume
        if missing_devices:
            names = ", ".join(missing_devices)
            rt["errorReason"] = "missing"
            rt["state"] = "ERROR_SET"
            _auto_log(auto_id, f"Device not found (removed/renamed): {names} → ERROR", "error")
            _emit_auto_update(auto)
            return

        # An action paused by its own liveness check: retry on a ~1-minute cadence,
        # but attempt EARLY the moment the device spontaneously reports back in (it
        # auto-publishes its state/availability on reconnect, no request needed).
        if rt.get("prePauseNetwork") == "ACTION_RUN" and rt.get("errorReason") == "offline":
            paused_ts = rt.get("_offlinePausedTs", "")
            reported_in = False
            for t in rt.get("_offlineTopics", []):
                d = owner_sess.device_states.get(t) if owner_sess else None
                if d and d.get("ts", "") > paused_ts:
                    reported_in = True
                    break
            due = now >= (rt.get("pauseUntil") or 0)
            if not reported_in and not due:
                return  # keep holding until the device reports in or the minute elapses
            

            if offline_devices:
                # Availability still reports offline → arm the next 1-minute retry.
                rt["pauseUntil"] = now + 60
                rt["_offlinePausedTs"] = datetime.utcnow().isoformat()
                return

            # Reachable again (according to 1 min timeout)
            _resume_network_pause(auto, rt, now)
            _auto_log(auto_id, "Network paused 1-minute timeout reached. Resuming.", "info")
            _emit_auto_update(auto)
            return

        if offline_devices:
            # Still offline → keep waiting
            if rt.get("errorReason") != "offline":
                rt["errorReason"] = "offline"
            rt["pauseUntil"] = None
            return
        # Devices reachable. If we paused because a device would not obey,
        # wait for the retry backoff before trying again.
        if rt.get("errorReason") == "not_obeying" and now < (rt.get("pauseUntil") or 0):
            return
        _resume_network_pause(auto, rt, now)
        _auto_log(auto_id, f"Device(s) reachable → resuming {rt['state']}", "info")
        _emit_auto_update(auto)
        return
    elif offline_devices and state not in ("ACTION_RUN", "PAUSED_USER"):
        # Enter network pause due to an offline device (resume when it returns).
        # PAUSED_USER is excluded so a user's explicit pause is never overridden.
        # ACTION_RUN is intentionally excluded: it runs its own interval-based
        # liveness check (5/30/60) so it can roll the timer back to the exact check point
        # instead of freezing at the current (later) tick, avoiding lost irrigation time due to MQTT Keep-Alive delay.
        names = ", ".join(offline_devices)
        _enter_network_pause(auto, rt, now, state, reason="offline", retry=False)
        _auto_log(auto_id, f"Device offline: {names} → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
        _emit_auto_update(auto)
        return

    bg_unverified = False
    # Guard: do NOT enforce setIfTrue until initialization has completed at least once.
    # This prevents the scheduler from setting switches the moment the automation turns ON,
    # before the initialization sequence has had a chance to run.
    init_done = rt.get("init_completed", False)
    if state not in ("ERROR", "ERROR_SET", "ERROR_VERIFY", "IDLE", "INIT_SET", "INIT_VERIFY_INDIVIDUAL", "INIT_VERIFY_ALL", "OVERLAP_NEXT_SET", "OVERLAP_NEXT_VERIFY") and not auto.get("isPaused") and init_done:
        # --- Background Enforce (Set if True / Set if False) ---
        sched_is_true = check_schedule(auto)
        sched_cfg = auto.get("schedule", {})
        
        enforce_list = sched_cfg.get("setIfTrue", []) if sched_is_true else sched_cfg.get("setIfFalse", [])
        
        yielded_this_tick = []
        for item in enforce_list:
            topic = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
            if not topic: continue
            
            # Check if ANY OTHER automation claims this switch in its sequence or schedule
            is_claimed_by_other = False
            for other_id, claims in (sequence_overrides or {}).items():
                if other_id != auto_id and topic in claims:
                    is_claimed_by_other = True
                    break
            for other_id, claims in (schedule_overrides or {}).items():
                if other_id != auto_id and topic in claims:
                    is_claimed_by_other = True
                    break

            if not sched_is_true:
                if is_claimed_by_other:
                    yielded_this_tick.append(topic)
                    continue
                
            last_sent_key = f"_last_sent_{topic}"
            retry_key = f"_retry_{topic}"

            if not _verify_switches([item], auto):
                bg_unverified = True
                # VERIFY_TIMEOUT is used to prevent spamming
                if now - rt.get(last_sent_key, 0) > VERIFY_TIMEOUT:
                    retries = rt.get(retry_key, 0)
                    if retries >= MAX_RETRIES:
                        _enter_network_pause(auto, rt, now, state, reason="not_obeying")
                        _auto_log(auto_id, f"Scheduler enforce failed for {item.get('switchName')} (device not responding) → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                        _emit_auto_update(auto)
                        return

                    _mqtt_set_switch(topic, item.get("state", ""), auto)
                    rt[last_sent_key] = now
                    rt[retry_key] = retries + 1
                    if retries == 0:
                        _auto_log(auto_id, f"Scheduler drift detected on {item.get('switchName', topic)} — correcting to {item.get('state')}", "warning")
                    else:
                        _auto_log(auto_id, f"Scheduler drift correction retry {retries + 1}/{MAX_RETRIES} on {item.get('switchName', topic)} → {item.get('state')}", "warning")
                    _emit_auto_update(auto)
            else:
                if retry_key in rt:
                    rt.pop(retry_key, None)
                if last_sent_key in rt:
                    rt.pop(last_sent_key, None)
                

        if rt.get("sched_yielded_switches", []) != yielded_this_tick:
            rt["sched_yielded_switches"] = yielded_this_tick
            _emit_auto_update(auto)

    # Priority 1.5: Enforce Pause
    if bg_unverified:
        if state != "PAUSED_ENFORCE" and state != "IDLE" and state != "ERROR" and state != "PAUSED_USER":
            rt["prePauseEnforce"] = state
            # freeze timers
            if "timerStart" in rt and rt["timerStart"]:
                elapsed = now - rt["timerStart"]
                rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
                rt["timerStart"] = None
            if "bufferStart" in rt and rt["bufferStart"]:
                elapsed = now - rt["bufferStart"]
                rt["remainingBuffer"] = max(0, auto.get("bufferTime", BUFFER_SECONDS) - elapsed)
                rt["bufferStart"] = None
            if "verifyStart" in rt and rt["verifyStart"]:
                elapsed = now - rt["verifyStart"]
                rt["remainingVerify"] = max(0, VERIFY_TIMEOUT - elapsed)
                rt["verifyStart"] = None
                
            rt["state"] = "PAUSED_ENFORCE"
            _auto_log(auto_id, "Pausing main sequence to enforce schedule")
            _emit_auto_update(auto)
        return

    # Priority 1.6: Enforce Resume
    if not bg_unverified and state == "PAUSED_ENFORCE":
        rt["state"] = rt.get("prePauseEnforce", "IDLE")
        # resume timers
        if "remainingTime" in rt and rt["remainingTime"] is not None:
            rt["timerStart"] = now
        if "remainingBuffer" in rt and rt["remainingBuffer"] is not None:
            rt["bufferStart"] = now - (auto.get("bufferTime", BUFFER_SECONDS) - rt["remainingBuffer"])
            del rt["remainingBuffer"]
        if "remainingVerify" in rt and rt["remainingVerify"] is not None:
            rt["verifyStart"] = now - (VERIFY_TIMEOUT - rt["remainingVerify"])
            del rt["remainingVerify"]
        _auto_log(auto_id, f"Schedule verified → Resuming {rt['state']}")
        _emit_auto_update(auto)

    # Priority 2: User Pause
    if auto.get("isPaused"):
        if state != "PAUSED_USER" and state != "IDLE" and state != "ERROR":
            rt["prePauseUser"] = state
            # freeze timers
            if "timerStart" in rt and rt["timerStart"]:
                elapsed = now - rt["timerStart"]
                rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
                rt["timerStart"] = None
            if "bufferStart" in rt and rt["bufferStart"]:
                elapsed = now - rt["bufferStart"]
                rt["remainingBuffer"] = max(0, auto.get("bufferTime", BUFFER_SECONDS) - elapsed)
                rt["bufferStart"] = None
            if "verifyStart" in rt and rt["verifyStart"]:
                elapsed = now - rt["verifyStart"]
                rt["remainingVerify"] = max(0, VERIFY_TIMEOUT - elapsed)
                rt["verifyStart"] = None
                
            rt["state"] = "PAUSED_USER"
            _auto_log(auto_id, "User paused the automation")
            _emit_auto_update(auto)
        return

    # Priority 3: User Resume
    if not auto.get("isPaused") and state == "PAUSED_USER":
        rt["state"] = rt.get("prePauseUser", "IDLE")
        # resume timers
        if "remainingTime" in rt and rt["remainingTime"] is not None:
            rt["timerStart"] = now
        if "remainingBuffer" in rt and rt["remainingBuffer"] is not None:
            rt["bufferStart"] = now - (auto.get("bufferTime", BUFFER_SECONDS) - rt["remainingBuffer"])
            del rt["remainingBuffer"]
        if "remainingVerify" in rt and rt["remainingVerify"] is not None:
            rt["verifyStart"] = now - (VERIFY_TIMEOUT - rt["remainingVerify"])
            del rt["remainingVerify"]
        _auto_log(auto_id, f"User resumed → {rt['state']}")
        _emit_auto_update(auto)
        return

    # Priority 4: ERROR state — only RESET or OFF can exit
    if state == "ERROR":
        return

    # --- State transitions ---

    if state == "IDLE":
        # Status just turned ON → wait for condition and schedule
        rt["state"] = "WAIT_CONDITION"
        rt["init_completed"] = False
        rt["retryCount"] = 0
        _auto_log(auto_id, "Status ON → WAIT_CONDITION")
        _emit_auto_update(auto)
        return

    if state == "WAIT_CONDITION":
        cond = evaluate_condition(auto)
        sched = check_schedule(auto)
        
        # Check cycle limit before starting a new cycle
        max_cycles = auto.get("maxCyclesPerDay", 0)
        today_str = _get_auto_now(auto).strftime("%Y-%m-%d")
        cycles_today = rt.get("cycles_today", 0) if rt.get("cycles_date") == today_str else 0
        
        if not rt.get("init_completed"):
            if sched:
                if max_cycles > 0 and cycles_today >= max_cycles:
                    if rt.get("_cycle_paused") != today_str:
                        rt["_cycle_paused"] = today_str
                        _auto_log(auto_id, f"Max cycles reached ({cycles_today}/{max_cycles}) — pausing until tomorrow")
                        _emit_auto_update(auto)
                    return
                
                rt["state"] = "INIT_SET"
                rt["retryCount"] = 0
                _auto_log(auto_id, "Schedule active → INIT_SET (First run only)")
                _emit_auto_update(auto)
        else:
            if cond and sched:
                if max_cycles > 0 and cycles_today >= max_cycles:
                    if rt.get("_cycle_paused") != today_str:
                        rt["_cycle_paused"] = today_str
                        _auto_log(auto_id, f"Max cycles reached ({cycles_today}/{max_cycles}) — pausing until tomorrow")
                        _emit_auto_update(auto)
                    return
                
                if not auto.get("actions"):
                    rt["state"] = "SCHEDULER_RUN"
                    _auto_log(auto_id, "Condition satisfied + Schedule active → SCHEDULER_RUN (No actions mode)")
                else:
                    rt["state"] = "ACTION_SET"
                    rt["currentActionIndex"] = 0
                    _auto_log(auto_id, "Condition satisfied + Schedule active → ACTION_SET")
                _emit_auto_update(auto)
        return

    if state == "INIT_SET":
        inits = auto.get("initialization", [])
        idx = rt.get("currentInitIndex", 0)
        
        if not inits:
            rt["state"] = "INIT_VERIFY_ALL"
            _emit_auto_update(auto)
            return
            
        if idx < len(inits):
            item = inits[idx]
            topic = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
            
            _mqtt_set_switch(topic, item.get("state", "ON"), auto)
            rt["verifyStart"] = now
            rt["state"] = "INIT_VERIFY_INDIVIDUAL"
            _auto_log(auto_id, f"Initialization {idx+1}/{len(inits)} sent → INIT_VERIFY_INDIVIDUAL")
            _emit_auto_update(auto)
        else:
            rt["verifyStart"] = now
            rt["state"] = "INIT_VERIFY_ALL"
            _auto_log(auto_id, "All individual initialization commands sent. Final bulk check → INIT_VERIFY_ALL")
            _emit_auto_update(auto)
        return

    if state == "INIT_VERIFY_INDIVIDUAL":
        inits = auto.get("initialization", [])
        idx = rt.get("currentInitIndex", 0)
        if idx < len(inits):
            item = inits[idx]
            topic = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
            yielded = rt.get("yielded_switches", [])
            
            if topic in yielded or _verify_switches([item], auto):
                rt["currentInitIndex"] = idx + 1
                rt["state"] = "INIT_SET"
                rt["retryCount"] = 0
                rt.pop("isNetworkRetry", None)
                if topic not in yielded:
                    _auto_log(auto_id, f"Initialization {idx+1}/{len(inits)} verified")
                _emit_auto_update(auto)
            elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
                rt["retryCount"] = rt.get("retryCount", 0) + 1
                if rt["retryCount"] >= MAX_RETRIES:
                    _enter_network_pause(auto, rt, now, "INIT_SET", reason="not_obeying")
                    _auto_log(auto_id, f"Init {idx+1}/{len(inits)} not responding → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                    _emit_auto_update(auto)
                else:
                    rt["state"] = "INIT_SET"
                    _auto_log(auto_id, f"Init {idx+1}/{len(inits)} verify retry {rt['retryCount']}/{MAX_RETRIES}")
                    _emit_auto_update(auto)
        return

    if state == "INIT_VERIFY_ALL":
        inits = auto.get("initialization", [])
        yielded = rt.get("yielded_switches", [])
        active_inits = [sw for sw in inits if (sw.get("switchCmdTopic", "") or sw.get("switchStateTopic", "")) not in yielded]
        
        if not active_inits or _verify_switches(active_inits, auto):
            rt["init_completed"] = True
            rt.pop("isNetworkRetry", None)
            if rt.get("loopingToFirst"):
                rt["bufferStart"] = now
                rt["state"] = "BUFFER"
                _auto_log(auto_id, "All initialization verified → BUFFER")
            else:
                rt["state"] = "WAIT_CONDITION"
                rt["retryCount"] = 0
                _auto_log(auto_id, "Initialization verified → WAIT_CONDITION")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                rt["currentInitIndex"] = 0
                _enter_network_pause(auto, rt, now, "INIT_SET", reason="not_obeying")
                _auto_log(auto_id, "Bulk init not responding → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                _emit_auto_update(auto)
            else:
                # Restart the sequential flow
                rt["currentInitIndex"] = 0
                rt["state"] = "INIT_SET"
                _auto_log(auto_id, f"Bulk init verify failed, restarting sequence! Retry {rt['retryCount']}/{MAX_RETRIES}", "warning")
                _emit_auto_update(auto)
        return

    if state == "ACTION_SET":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        if idx >= len(actions):
            rt["state"] = "COMPLETED"
            _auto_log(auto_id, "No more actions → COMPLETED")
            _emit_auto_update(auto)
            return
        action = actions[idx]
        topic = action.get("switchCmdTopic", "") or action.get("switchStateTopic", "")

        # Check if ANY OTHER automation has claimed this switch
        is_claimed_by_other = False
        for other_id, claims in (sequence_overrides or {}).items():
            if other_id != auto_id and topic in claims:
                is_claimed_by_other = True
                break
        for other_id, claims in (schedule_overrides or {}).items():
            if other_id != auto_id and topic in claims:
                is_claimed_by_other = True
                break

        if is_claimed_by_other:
            if "yielded_switches" not in rt:
                rt["yielded_switches"] = []
            if topic not in rt["yielded_switches"]:
                _auto_log(auto_id, f"Yielding ACTION on {action.get('switchName', 'switch')} to active sequence/schedule", "warning")
                rt["yielded_switches"].append(topic)
            
            rt["state"] = "ACTION_SET" # Wait and try again next tick
            _emit_auto_update(auto)
            return
        elif topic in rt.get("yielded_switches", []):
            rt["yielded_switches"].remove(topic)

        _mqtt_set_switch(topic, action.get("state", "ON"), auto)
        rt["verifyStart"] = now
        rt["state"] = "ACTION_VERIFY"
        _auto_log(auto_id, f"Action {idx+1}: Set {action.get('switchName','')} → {action.get('state','')}")
        _emit_auto_update(auto)
        return

    if state == "ACTION_VERIFY":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        action = actions[idx] if idx < len(actions) else {}
        if _verify_switches([action], auto):
            rt.pop("isNetworkRetry", None)
            duration = action.get("duration", 0)
            rt["timerStart"] = now
            rt["remainingTime"] = duration
            rt["state"] = "ACTION_RUN"
            rt.pop("isNetworkRetry", None)
            _auto_log(auto_id, f"Action {idx+1} verified → ACTION_RUN ({duration}s)")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                _enter_network_pause(auto, rt, now, "ACTION_SET", reason="not_obeying")
                _auto_log(auto_id, f"Action {idx+1} not responding → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                _emit_auto_update(auto)
            else:
                rt["state"] = "ACTION_SET"
                _auto_log(auto_id, f"Action {idx+1} verify retry {rt['retryCount']}")
                _emit_auto_update(auto)
        return

    if state == "ACTION_RUN":
        # Check for condition pause
        if not evaluate_condition(auto):
            elapsed = now - (rt.get("timerStart") or now)
            rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
            rt["pauseReason"] = "condition"
            rt["state"] = "PAUSED_CONDITION"
            _auto_log(auto_id, "Condition FALSE → PAUSED_CONDITION")
            _emit_auto_update(auto)
            return
        # Check for schedule pause
        if not check_schedule(auto):
            elapsed = now - (rt.get("timerStart") or now)
            rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
            rt["pauseReason"] = "schedule"
            rt["state"] = "PAUSED_SCHEDULE"
            _auto_log(auto_id, "Schedule ended → PAUSED_SCHEDULE")
            _emit_auto_update(auto)
            return
        
        # Action Sequence Drift Detection
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        switches_to_verify = []
        if idx < len(actions):
            switches_to_verify.append(actions[idx])
        
        if not _verify_switches(switches_to_verify, auto):
            # Freeze timer and correct drift
            elapsed_run = now - (rt.get("timerStart") or now)
            rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed_run)
            rt["timerStart"] = None
            _mqtt_set_switch(actions[idx].get("switchCmdTopic", ""), actions[idx].get("state", "ON"), auto)
            rt["driftRetryCount"] = 0
            rt["verifyStart"] = now
            rt["state"] = "ACTION_DRIFT_VERIFY"
            _auto_log(auto_id, f"Drift detected on {actions[idx].get('switchName','')} — correcting", "warning")
            _emit_auto_update(auto)
            return

        # Check timer
        elapsed = now - (rt.get("timerStart") or now)
        remaining = (rt.get("remainingTime") or 0) - elapsed
        if remaining <= 0:
            actions = auto.get("actions", [])
            idx = rt.get("currentActionIndex", 0)
            if idx + 1 < len(actions):
                rt["state"] = "OVERLAP_NEXT_SET"
                rt["retryCount"] = 0
                _auto_log(auto_id, f"Action {idx+1} timer done → OVERLAP_NEXT_SET")
            else:
                # We reached the end of all actions. Increment cycle count first.
                max_cycles = auto.get("maxCyclesPerDay", 0)
                today_str = _get_auto_now(auto).strftime("%Y-%m-%d")
                if rt.get("cycles_date") != today_str:
                    rt["cycles_date"] = today_str
                    rt["cycles_today"] = 0
                rt["cycles_today"] = rt.get("cycles_today", 0) + 1
                
                # Track history independently of the resettable cycles_today
                if "cycles_history" not in rt:
                    rt["cycles_history"] = {}
                if "duration_history" not in rt:
                    rt["duration_history"] = {}
                    
                rt["cycles_history"][today_str] = rt["cycles_history"].get(today_str, 0) + 1
                
                # Add total cycle duration to today's history
                cycle_duration = sum(act.get("duration", 0) for act in auto.get("actions", []))
                rt["duration_history"][today_str] = rt["duration_history"].get(today_str, 0) + cycle_duration

                # Prune old history
                for h_key in ["cycles_history", "duration_history"]:
                    sorted_dates = sorted(rt[h_key].keys())
                    while len(sorted_dates) > 7:
                        del rt[h_key][sorted_dates.pop(0)]
                rt["last_irrigated"] = _get_auto_now(auto).strftime("%Y-%m-%d %H:%M")

                cycles_today_val = rt.get("cycles_today", 0)
                cycle_limit_reached = max_cycles > 0 and cycles_today_val >= max_cycles

                # Can we loop?
                sched = check_schedule(auto)
                if sched and len(actions) > 1 and not cycle_limit_reached:
                    rt["loopingToFirst"] = True
                    rt["stopAfterRevert"] = False
                    rt["state"] = "OVERLAP_NEXT_SET"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Cycle #{cycles_today_val} done → Loop to Action 1")
                elif cycle_limit_reached:
                    rt["loopingToFirst"] = True
                    rt["stopAfterRevert"] = True
                    rt["state"] = "OVERLAP_NEXT_SET"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Cycle #{cycles_today_val} done → Max cycles ({max_cycles}/day) reached → revert → stop")
                else:
                    rt["loopingToFirst"] = False
                    rt["state"] = "ACTION_REVERT"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Cycle #{cycles_today_val} done → ACTION_REVERT")
            # Persist cycle history to DB so it survives restarts
            try:
                import db as _db
                owner = auto.get("_owner_email", "")
                if owner:
                    _db.save_automation(owner, auto)
            except Exception as e:
                logging.error(f"[DB] Failed to persist cycle history: {e}")
            _emit_auto_update(auto)
            return
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        return

    if state == "ACTION_DRIFT_VERIFY":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        action = actions[idx] if idx < len(actions) else {}
        if _verify_switches([action], auto):
            # Switch corrected — resume ACTION_RUN with remaining time
            rt["timerStart"] = now
            rt["state"] = "ACTION_RUN"
            rt["driftRetryCount"] = 0
            _auto_log(auto_id, f"Drift corrected on {action.get('switchName','')} — resuming")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > DRIFT_VERIFY_TIMEOUT:
            rt["driftRetryCount"] = rt.get("driftRetryCount", 0) + 1
            if rt["driftRetryCount"] >= MAX_RETRIES:
                _enter_network_pause(auto, rt, now, "ACTION_RUN", reason="not_obeying")
                _auto_log(auto_id, f"Action drift correction failed after {MAX_RETRIES} retries → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                _emit_auto_update(auto)
            else:
                # Re-send and try again
                _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", "ON"), auto)
                rt["verifyStart"] = now
                _auto_log(auto_id, f"Drift correction retry {rt['driftRetryCount']}/{MAX_RETRIES} on {action.get('switchName','')}", "warning")
                _emit_auto_update(auto)
        return

    if state == "OVERLAP_NEXT_SET":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        next_idx = (idx + 1) % len(actions)
        
        if next_idx == 0 and rt.get("stopAfterRevert"):
            # Max cycles reached. Do not overlap with the next cycle.
            # Skip straight to the buffer before reverting the last action and stopping.
            rt["bufferStart"] = now
            rt["state"] = "BUFFER"
            rt["retryCount"] = 0
            _auto_log(auto_id, "Max cycles reached → Skipping overlap → BUFFER")
            _emit_auto_update(auto)
            return
        else:
            action = actions[next_idx]
            _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", "ON"), auto)
            _auto_log(auto_id, f"Overlap Action {next_idx+1}: Set {action.get('switchName','')} → {action.get('state','')}")
            
        rt["verifyStart"] = now
        rt["state"] = "OVERLAP_NEXT_VERIFY"
        _emit_auto_update(auto)
        return

    if state == "OVERLAP_NEXT_VERIFY":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        next_idx = (idx + 1) % len(actions)
        
        switches_to_verify = [actions[next_idx]]
            
        if _verify_switches(switches_to_verify, auto):
            rt.pop("isNetworkRetry", None)
            rt["bufferStart"] = now
            rt["state"] = "BUFFER"
            _auto_log(auto_id, f"Overlap transition verified → BUFFER")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                _enter_network_pause(auto, rt, now, "OVERLAP_NEXT_SET", reason="not_obeying")
                _auto_log(auto_id, f"Overlap Action {next_idx+1} not responding → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                _emit_auto_update(auto)
            else:
                rt["state"] = "OVERLAP_NEXT_SET"
                _auto_log(auto_id, f"Overlap Action {next_idx+1} verify retry {rt['retryCount']}")
                _emit_auto_update(auto)
        return

    if state == "BUFFER":
        buffer_time = auto.get("bufferTime", BUFFER_SECONDS)
        if now - (rt.get("bufferStart") or now) >= buffer_time:
            rt["state"] = "ACTION_REVERT"
            rt["retryCount"] = 0
            _auto_log(auto_id, f"Buffer ({buffer_time}s) done → ACTION_REVERT")
            _emit_auto_update(auto)
            return
        # Enforce expected switch states during buffer
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        switches_to_enforce = []
        # Current action (still ON, not yet reverted)
        if idx < len(actions):
            switches_to_enforce.append(actions[idx])
        # Next action (overlap — already set ON)
        next_idx = (idx + 1) % len(actions)
        if next_idx != idx and next_idx < len(actions) and not rt.get("stopAfterRevert"):
            switches_to_enforce.append(actions[next_idx])
        for sw in switches_to_enforce:
            if not _verify_switches([sw], auto):
                # Freeze buffer timer and correct
                elapsed_buf = now - (rt.get("bufferStart") or now)
                rt["remainingBuffer"] = max(0, buffer_time - elapsed_buf)
                rt["bufferStart"] = None
                _mqtt_set_switch(sw.get("switchCmdTopic", ""), sw.get("state", ""), auto)
                rt["driftRetryCount"] = 0
                rt["verifyStart"] = now
                rt["driftAction"] = sw
                rt["state"] = "BUFFER_DRIFT_VERIFY"
                _auto_log(auto_id, f"Buffer drift detected on {sw.get('switchName', '')} — correcting", "warning")
                _emit_auto_update(auto)
                return
        return

    if state == "BUFFER_DRIFT_VERIFY":
        sw = rt.get("driftAction", {})
        if _verify_switches([sw], auto):
            # Corrected — resume buffer with remaining time
            rt["bufferStart"] = now - (auto.get("bufferTime", BUFFER_SECONDS) - (rt.get("remainingBuffer") or 0))
            rt.pop("remainingBuffer", None)
            rt.pop("driftAction", None)
            rt["driftRetryCount"] = 0
            rt["state"] = "BUFFER"
            _auto_log(auto_id, f"Buffer drift corrected on {sw.get('switchName', '')} — resuming")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > DRIFT_VERIFY_TIMEOUT:
            rt["driftRetryCount"] = rt.get("driftRetryCount", 0) + 1
            if rt["driftRetryCount"] >= MAX_RETRIES:
                rt.pop("driftAction", None)
                _enter_network_pause(auto, rt, now, "BUFFER", reason="not_obeying")
                _auto_log(auto_id, "Buffer drift — device not responding → NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                _emit_auto_update(auto)
            else:
                _mqtt_set_switch(sw.get("switchCmdTopic", ""), sw.get("state", ""), auto)
                rt["verifyStart"] = now
                _auto_log(auto_id, f"Buffer drift retry {rt['driftRetryCount']}/{MAX_RETRIES} on {sw.get('switchName', '')}", "warning")
                _emit_auto_update(auto)
        return

    if state == "ACTION_REVERT":
        # Send the OPPOSITE state to revert the switch
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        if idx < len(actions):
            action = actions[idx]
            revert_state = "OFF" if action.get("state", "").upper() == "ON" else "ON"
            _mqtt_set_switch(action.get("switchCmdTopic", ""), revert_state, auto)
            _auto_log(auto_id, f"Action {idx+1}: Reverting {action.get('switchName','')} → {revert_state}")
        rt["verifyStart"] = now
        rt["state"] = "ACTION_VERIFY_REVERT"
        _emit_auto_update(auto)
        return

    if state == "ACTION_VERIFY_REVERT":
        # Verify the switch reverted to opposite state
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        
        def _finish_revert():
            rt.pop("isNetworkRetry", None)
            if rt.get("loopingToFirst"):
                rt["loopingToFirst"] = False
                if rt.pop("stopAfterRevert", False):
                    # Max cycles reached — stop here
                    rt["currentActionIndex"] = 0
                    rt["state"] = "COMPLETED"
                    _auto_log(auto_id, f"Max cycles done → COMPLETED (stopping)")
                else:
                    # Loop back for another cycle (Make-Before-Break overlap finished). Advance to Action 1 RUN.
                    next_idx = 0
                    rt["currentActionIndex"] = next_idx
                    duration = actions[next_idx].get("duration", 0)
                    rt["timerStart"] = now
                    rt["remainingTime"] = duration
                    rt["state"] = "ACTION_RUN"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Revert complete → Advanced to next cycle Action 1 → ACTION_RUN ({duration}s)")
            elif idx + 1 < len(actions):
                # Make-Before-Break finished. Advance to next action and start its timer.
                next_idx = idx + 1
                rt["currentActionIndex"] = next_idx
                duration = actions[next_idx].get("duration", 0)
                rt["timerStart"] = now
                rt["remainingTime"] = duration
                rt["state"] = "ACTION_RUN"
                rt["retryCount"] = 0
                _auto_log(auto_id, f"Advanced to Action {next_idx+1} → ACTION_RUN ({duration}s)")
            else:
                rt["state"] = "COMPLETED"
                _auto_log(auto_id, "All actions completed → COMPLETED")
            _emit_auto_update(auto)

        if idx < len(actions):
            action = actions[idx]
            revert_state = "OFF" if action.get("state", "").upper() == "ON" else "ON"
            revert_check = {"switchStateTopic": action.get("switchStateTopic", ""), "state": revert_state}
            if _verify_switches([revert_check], auto):
                _auto_log(auto_id, f"Action {idx+1} revert verified")
                _finish_revert()
            elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
                _auto_log(auto_id, f"Action {idx+1} revert verify timeout", "warning")
                _finish_revert()
        else:
            _finish_revert()
        return

    if state == "PAUSED_CONDITION":
        if evaluate_condition(auto):
            rt["timerStart"] = now
            rt["pauseReason"] = None
            rt["state"] = "ACTION_RUN"
            _auto_log(auto_id, "Condition TRUE → resume ACTION_RUN")
            _emit_auto_update(auto)
        return

    if state == "PAUSED_SCHEDULE":
        if check_schedule(auto):
            rt["timerStart"] = now
            rt["pauseReason"] = None
            rt["state"] = "ACTION_RUN"
            _auto_log(auto_id, "Schedule active → resume ACTION_RUN")
            _emit_auto_update(auto)
        return

    if state == "SCHEDULER_RUN":
        cond = evaluate_condition(auto)
        sched = check_schedule(auto)
        if not cond or not sched:
            # Scheduler time ended, just return to wait condition without counting cycles
            rt["state"] = "WAIT_CONDITION"
            _auto_log(auto_id, "Scheduler block ended (condition/schedule false) → WAIT_CONDITION")
            _emit_auto_update(auto)
        return

    if state == "COMPLETED":
        rt["currentActionIndex"] = 0
        rt["timerStart"] = None
        rt["remainingTime"] = None
        rt["state"] = "WAIT_CONDITION"
        _auto_log(auto_id, f"Completed → WAIT_CONDITION (cycle #{rt.get('cycles_today', 0)} today)")
        _emit_auto_update(auto)
        return

    if state == "ERROR_SET":
        err_states = auto.get("errorState", [])
        for item in err_states:
            _mqtt_set_switch(item.get("switchCmdTopic", ""), item.get("state", "OFF"), auto)
        rt["verifyStart"] = now
        rt["state"] = "ERROR_VERIFY"
        _auto_log(auto_id, "Error state commands sent → ERROR_VERIFY", "error")
        _emit_auto_update(auto)
        return

    if state == "ERROR_VERIFY":
        err_states = auto.get("errorState", [])
        if not err_states or _verify_switches(err_states, auto):
            rt.pop("isNetworkRetry", None)
            rt["state"] = "ERROR"
            _auto_log(auto_id, "Error state verified → ERROR (locked)", "error")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["state"] = "ERROR"
            _auto_log(auto_id, "Error verify timeout → ERROR (locked)", "error")
            _emit_auto_update(auto)
        return


def _engine_loop():
    """Background loop that ticks every automation across all sessions."""
    global _engine_running
    _engine_running = True
    logging.info("[ENGINE] Automation engine started")
    _last_db_save = time.time()
    while _engine_running:
        # Build global active overrides per session
        sequence_overrides = collections.defaultdict(dict)
        schedule_overrides = collections.defaultdict(dict)
        try:
            for session in session_mgr._sessions.values():
                sequence_overrides[session.email] = {}
                schedule_overrides[session.email] = {}
                for auto_id, auto in session.automations.items():
                    sequence_overrides[session.email][auto_id] = set()
                    schedule_overrides[session.email][auto_id] = set()
                    
                    rt = auto.get("runtime", {})
                    state = rt.get("state", "IDLE")

                    # 1. Claim switches based on active phase
                    if state not in ("IDLE", "ERROR", "ERROR_SET", "ERROR_VERIFY", "PAUSED_NETWORK"):
                        # Only claim INIT switches if ACTIVELY running INIT ("until init complete")
                        if state.startswith("INIT_"):
                            for item in auto.get("initialization", []):
                                ctrl = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                                if ctrl: sequence_overrides[session.email][auto_id].add(ctrl)
                            
                        # Only claim DEINIT switches if ACTIVELY running DEINIT
                        if state.startswith("DEINIT_"):
                            for item in auto.get("deinitialization", []):
                                ctrl = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                                if ctrl: sequence_overrides[session.email][auto_id].add(ctrl)
                            
                        # Only claim the CURRENT ACTION switch(es), not all of them
                        if state.startswith("ACTION_") or state.startswith("OVERLAP_"):
                            idx = rt.get("currentActionIndex", 0)
                            actions = auto.get("actions", [])
                            if 0 <= idx < len(actions):
                                ctrl = actions[idx].get("switchCmdTopic", "") or actions[idx].get("switchStateTopic", "")
                                if ctrl: sequence_overrides[session.email][auto_id].add(ctrl)
                    
                    # 2. Any active schedule's setIfTrue claims the switch as a schedule override
                    if auto.get("status") == "ON" and check_schedule(auto):
                        for item in auto.get("schedule", {}).get("setIfTrue", []):
                            ctrl = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                            if ctrl: schedule_overrides[session.email][auto_id].add(ctrl)
        except Exception as e:
            logging.error(f"[ENGINE] Error building active overrides: {e}")

        # Tick all session-based automations (multi-tenant)
        for session, auto_id, auto in session_mgr.get_all_automations():
            try:
                # Inject a snapshot of sibling automations so engine_tick can serialize
                # concurrent deinits. Snapshot avoids concurrent-modification issues.
                engine_tick(auto, sequence_overrides.get(session.email, {}), schedule_overrides.get(session.email, {}), dict(session.automations))
            except Exception as e:
                logging.error(f"[ENGINE] Error in {auto_id} (user={session.email}): {e}")
                
        # --- Unit-Level Watchdog Verification ---
        now = time.time()
        for email, sess in list(session_mgr._sessions.items()):
            if not getattr(sess, "watchdog_state", None):
                sess.watchdog_state = {}
            
            # Only activate watchdog for units used in ON automations
            enabled_units = _get_enabled_auto_units(sess)
            
            # If enabled_units changed (e.g. an automation finished deinit and went IDLE), notify frontend
            last_enabled = getattr(sess, "last_enabled_units", None)
            if last_enabled is not None and enabled_units != last_enabled and sess.room:
                next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in sess.watchdog_state.items() if sess.watchdogs.get(u) != 'none'}
                socketio.emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(enabled_units)}, room=sess.room)
            sess.last_enabled_units = set(enabled_units)
                
            for unit, topic in list(sess.watchdogs.items()):
                if not topic or topic == 'none': continue # Empty means no watchdog for this unit
                
                # Skip watchdog ping if unit is not in any ON automation
                if unit not in enabled_units:
                    continue

                
                state = sess.watchdog_state.get(unit)
                if state is None:
                    state = {}
                    sess.watchdog_state[unit] = state
                last_ping = state.get("last_ping", 0)
                
                if now - last_ping >= WATCHDOG_PING_INTERVAL and not state.get("pending_ping", False):
                    # Time to ping this unit
                    # Toggle state ON -> OFF -> ON based on last known payload
                    current_payload = "off"
                    state_topic = topic.replace("/set", "/state").replace("/availability", "/state")
                    if state_topic in sess.device_states:
                         p = sess.device_states[state_topic].get("payload", "")
                         if isinstance(p, dict):
                             payload_raw = str(p.get("state", "")).lower()
                         else:
                             payload_raw = str(p).lower()
                         
                         logging.info(f"[WATCHDOG-DEBUG] p: {p}, type: {type(p)}, payload_raw: {payload_raw}")
                         
                         if payload_raw == "off": current_payload = "off"
                         else: current_payload = "on"
                    toggle_to = "OFF" if current_payload == "on" else "ON"
                    
                    cmd_topic = topic.replace("/state", "/set").replace("/availability", "/set")
                    # It's usually the set topic we want. If they selected the state topic, we replace it.
                    if "/set" not in topic:
                        cmd_topic = topic.rsplit("/", 1)[0] + "/set"
                    else:
                        cmd_topic = topic
                    
                    state["pending_ping"] = True
                    state["last_ping"] = now
                    state["expected_state"] = toggle_to
                    state["restore_state"] = "ON" if current_payload == "on" else "OFF"

                    if sess.mqtt_client and sess.mqtt_connected:
                        sess.mqtt_client.publish(cmd_topic, json.dumps({"state": toggle_to}))
                        logging.info(f"[WATCHDOG:{email}] Published {toggle_to} to {cmd_topic}")
                        _emit_mqtt_tx(sess, cmd_topic, json.dumps({"state": toggle_to}), source="watchdog")
                        if sess.room:
                            next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in sess.watchdog_state.items() if sess.watchdogs.get(u) != 'none'}
                            socketio.emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(enabled_units)}, room=sess.room)
                            logging.info(f"[WATCHDOG:{email}] Emitted next_pings to room {sess.room}")
                        else:
                            logging.info(f"[WATCHDOG:{email}] Skipped emitting next_pings because room is {sess.room}")
                        
                elif state.get("pending_ping") and now - last_ping > 5:
                    # 5 seconds passed since ping, check if expected state arrived
                    is_alive = False
                    state_topic = topic.replace("/set", "/state").replace("/availability", "/state")
                    expected = state.get("expected_state")
                    
                    if state_topic in sess.device_states:
                        p = sess.device_states[state_topic].get("payload", "")
                        ts_f = sess.device_states[state_topic].get("ts_float", 0)
                        if isinstance(p, dict):
                            payload_raw = str(p.get("state", p.get("value", ""))).lower()
                        elif isinstance(p, str) and p.startswith("{"):
                            try:
                                d = json.loads(p)
                                payload_raw = str(d.get("state", d.get("value", p))).lower()
                            except:
                                payload_raw = p.lower()
                        else:
                            payload_raw = str(p).lower()
                            
                        logging.info(f"[WATCHDOG-VERIFY] {unit}: expected={expected}, payload_raw={payload_raw}, ts_f={ts_f}, last_ping={last_ping}, diff={ts_f - last_ping}")
                        
                        if ts_f > last_ping and expected and expected.lower() == payload_raw:
                            is_alive = True
                    else:
                        logging.info(f"[WATCHDOG-VERIFY] {unit}: state_topic {state_topic} NOT IN device_states!")
                            
                    state["pending_ping"] = False
                    
                    if getattr(sess, "unit_liveness", None) is None:
                        sess.unit_liveness = {}
                    
                    old_liveness = sess.unit_liveness.get(unit, True)
                    
                    if is_alive:
                        state["retry_count"] = 0
                        
                        # Restore original state after ping test so switch is not left in toggled state
                        restore_to = state.get("restore_state")
                        if restore_to and sess.mqtt_client and sess.mqtt_connected:
                            cmd_topic = topic.replace("/state", "/set").replace("/availability", "/set")
                            if "/set" not in topic:
                                cmd_topic = topic.rsplit("/", 1)[0] + "/set"
                            sess.mqtt_client.publish(cmd_topic, json.dumps({"state": restore_to}))
                            logging.info(f"[WATCHDOG:{email}] Restored original state {restore_to} to {cmd_topic}")
                            _emit_mqtt_tx(sess, cmd_topic, json.dumps({"state": restore_to}), source="watchdog")
                        state["restore_state"] = None
                        
                        # A recovered unit must pass several consecutive pings before it counts as truly
                        # online. A flapping unit keeps verifying in the background (stays offline /
                        # PAUSED_NETWORK) instead of resume→re-pause churn and repeated notifications.
                        if not old_liveness:
                            state["alive_streak"] = state.get("alive_streak", 0) + 1
                            if state["alive_streak"] < WATCHDOG_STABLE_PINGS:
                                sess.watchdog_state[unit] = state
                                continue
                        state["alive_streak"] = 0
                        sess.unit_liveness[unit] = True
                        
                        if not old_liveness:
                            # Unit just came back online (confirmed stable)! Resume paused automations
                            # BUT only if ALL units used by the automation are alive
                            for auto_id, auto in list(sess.automations.items()):
                                if auto.get("status") == "ON" and auto.get("runtime", {}).get("state") == "PAUSED_NETWORK":
                                    # Check if automation uses this unit
                                    uses_unit = False
                                    all_items = auto.get("actions", []) + auto.get("initialization", []) + auto.get("deinitialization", []) + auto.get("schedule", {}).get("setIfTrue", []) + auto.get("schedule", {}).get("setIfFalse", [])
                                    for item in all_items:
                                        ctrl = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                                        if ctrl and unit in ctrl:
                                            uses_unit = True
                                            break
                                    if not uses_unit:
                                        continue
                                    # Check ALL units used by this automation are alive
                                    all_units_alive = True
                                    for item in all_items:
                                        ctrl = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                                        if ctrl:
                                            ctrl_parts = ctrl.split("/")
                                            if len(ctrl_parts) >= 4:
                                                other_unit = ctrl_parts[3].split("_")[0]
                                                if sess.unit_liveness.get(other_unit, True) is False:
                                                    all_units_alive = False
                                                    break
                                    if all_units_alive:
                                        _resume_network_pause(auto, auto["runtime"], time.time())
                                        _auto_log(auto_id, f"Unit {unit} verified online via watchdog. Resuming automation \u2192 {auto['runtime']['state']}", "info")
                                        _emit_auto_update(auto)
                    else:
                        retry_count = state.get("retry_count", 0)
                        if retry_count < 2:
                            state["retry_count"] = retry_count + 1
                            state["last_ping"] = 0  # Force immediate retry
                            continue  # Skip marking offline
                        else:
                            # Unit is DEAD!
                            sess.unit_liveness[unit] = False
                            state["retry_count"] = 0
                            state["alive_streak"] = 0
                            
                    if old_liveness != sess.unit_liveness[unit] and sess.room:
                        # Liveness changed, broadcast update
                        next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in sess.watchdog_state.items() if sess.watchdogs.get(u) != 'none'}
                        socketio.emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(enabled_units)}, room=sess.room)
                        
                    if not sess.unit_liveness[unit]:
                        # Pause all automations using devices from this unit!
                        for auto_id, auto in list(sess.automations.items()):
                            if auto.get("status") != "ON": continue
                            rt = auto.get("runtime", {})
                            auto_state = rt.get("state", "IDLE")
                            # PAUSED_USER excluded: a user pause outranks watchdog auto-pause.
                            if auto_state in ("IDLE", "PAUSED_NETWORK", "PAUSED_USER", "ERROR", "ERROR_SET", "ERROR_VERIFY"): continue
                            
                            # Check if automation uses this unit
                            uses_unit = False
                            for item in auto.get("actions", []) + auto.get("initialization", []) + auto.get("deinitialization", []) + auto.get("schedule", {}).get("setIfTrue", []) + auto.get("schedule", {}).get("setIfFalse", []):
                                ctrl = item.get("switchCmdTopic", "") or item.get("switchStateTopic", "")
                                if ctrl and unit in ctrl:
                                    uses_unit = True
                                    break
                            
                            if uses_unit:
                                _enter_network_pause(auto, rt, now, auto_state, reason="offline", retry=False)
                                _auto_log(auto_id, f"Unit {unit} watchdog failed. Entire unit offline \u2192 NETWORK ERROR PAUSED", "error", notify=not rt.get("isNetworkRetry", False))
                                rt["isNetworkRetry"] = True
                                _emit_auto_update(auto)
                
                sess.watchdog_state[unit] = state
        
        # Periodic DB save every 60 seconds
        if time.time() - _last_db_save > 60:
            try:
                import db
                for email, sess in list(session_mgr._sessions.items()):
                    db.save_all_runtimes(sess.automations)
            except Exception as e:
                logging.error(f"[ENGINE] Periodic DB save failed: {e}")
            _last_db_save = time.time()
        time.sleep(ENGINE_INTERVAL)
    logging.info("[ENGINE] Automation engine stopped")


def start_engine():
    """Start the automation engine background thread."""
    global _engine_thread, _engine_running
    if _engine_thread and _engine_thread.is_alive():
        return
    _engine_running = True
    _engine_thread = threading.Thread(target=_engine_loop, daemon=True)
    _engine_thread.start()
    # Start AI scheduler thread alongside the engine
    _ai_thread = threading.Thread(target=_ai_scheduler_loop, daemon=True)
    _ai_thread.start()
    # Pre-load AI model in background so it's ready when needed
    _preload_ai_model()

def _preload_ai_model():
    """Pre-load the AI model in a background thread so it's ready when needed."""
    def _load():
        try:
            from ai_agent import refresh_client, is_model_loaded
            logging.info("[AI-PRELOAD] Initializing Gemini API client...")
            refresh_client()
            if is_model_loaded():
                logging.info("[AI-PRELOAD] Gemini API client ready!")
            else:
                logging.warning("[AI-PRELOAD] Gemini API client not ready - will retry on first AI call")
        except Exception as e:
            logging.warning(f"[AI-PRELOAD] Could not pre-load AI client: {e}")
    
    # Start in background thread so it doesn't block engine startup
    threading.Thread(target=_load, daemon=True, name="AI-Preload").start()


def stop_engine():
    """Stop the automation engine."""
    global _engine_running
    _engine_running = False


# ---------------------------------------------------------------------------
# AI Scheduler — background thread
# ---------------------------------------------------------------------------

def _ai_scheduler_loop():
    """Background thread: checks once per minute if it's time to run the AI."""
    AI_RUN_HOUR = 2  # Run at 2:00 AM in each automation's timezone
    logging.info("[AI-SCHEDULER] AI scheduler thread started")
    while _engine_running:
        try:
            # 1. Daily scheduled run at 2:00 AM (per-automation timezone)
            for session, auto_id, auto in session_mgr.get_all_automations():
                sched = auto.get("schedule", {})
                # Only trigger 2 AM run if AI is enabled AND automation is ON
                if not sched.get("ai_enabled") or auto.get("status") != "ON":
                    continue
                auto_now = _get_auto_now(auto)
                auto_today = auto_now.strftime("%Y-%m-%d")
                last_run = auto.get("_ai_last_run_date")
                if auto_now.hour == AI_RUN_HOUR and last_run != auto_today:
                    auto["_ai_last_run_date"] = auto_today
                    
                    import random
                    jitter_delay = random.uniform(0, 120)
                    logging.info(f"[AI-SCHEDULER] 2AM triggered for '{auto_id}' (tz-aware) with {jitter_delay:.1f}s jitter")
                    socketio.start_background_task(_run_ai_for_automation, auto_id, jitter_delay)
                
            # 2. Dynamic automatic retry for failed runs
            now_ts = time.time()
            for session, auto_id, auto in session_mgr.get_all_automations():
                sched = auto.get("schedule", {})
                
                # If turned OFF or AI disabled, clear any pending retries
                if auto.get("status") == "OFF" or not sched.get("ai_enabled"):
                    auto.pop("ai_last_fail", None)
                    auto.pop("ai_retry_delay", None)
                    auto.pop("ai_fail_count", None)
                    continue
                
                # If manually Paused, don't trigger retries yet (wait for resume)
                if auto.get("isPaused"):
                    continue

                fail_ts = auto.get("ai_last_fail")
                retry_delay = auto.get("ai_retry_delay", 1800)
                if fail_ts and (now_ts - fail_ts) >= retry_delay:
                    retry_mins = math.ceil(retry_delay / 60)
                    suffix = "th"
                    if retry_mins % 10 == 1 and retry_mins % 100 != 11: suffix = "st"
                    elif retry_mins % 10 == 2 and retry_mins % 100 != 12: suffix = "nd"
                    elif retry_mins % 10 == 3 and retry_mins % 100 != 13: suffix = "rd"
                    
                    logging.info(f"[AI-SCHEDULER] {retry_mins}{suffix}-minute retry triggered for '{auto_id}'")
                    
                    # Temporarily clear the flags so we don't trigger it again immediately
                    auto.pop("ai_last_fail", None) 
                    auto.pop("ai_retry_delay", None)
                    
                    # Run it (this will re-set the flag if it fails again)
                    socketio.start_background_task(_run_ai_for_automation, auto_id)

        except Exception as e:
            logging.error(f"[AI-SCHEDULER] Error: {e}")
        time.sleep(60)  # check every minute


def _schedules_overlap(a, b):
    """Check if two automations overlap in time ranges AND share at least one switch."""
    def get_switches(auto):
        switches = set()
        # Exclude deinitialization as requested by user
        for x in auto.get("initialization", []) + auto.get("actions", []) + auto.get("schedule", {}).get("setIfTrue", []) + auto.get("schedule", {}).get("setIfFalse", []):
            if x.get("switchCmdTopic"): switches.add(x["switchCmdTopic"])
        return switches

    if not get_switches(a).intersection(get_switches(b)):
        return False
        
    def _weekly_utc_intervals(sched):
        days = sched.get("days", [])
        if not days: return []
        offset = sched.get("utcOffset", 0)
        try: offset = int(offset)
        except: offset = 0
        
        is24hr = sched.get("is24hr", False)
        ranges = []
        if is24hr:
            ranges = [[0, 1440]]
        else:
            for r in sched.get("timeRanges", []):
                try:
                    sh, sm = map(int, r.get("start", "").split(":"))
                    eh, em = map(int, r.get("end", "").split(":"))
                    s = sh * 60 + sm
                    e = eh * 60 + em
                    if s < e: ranges.append([s, e])
                    elif s > e: ranges.extend([[s, 1440], [0, e]])
                    else: ranges.append([0, 1440])
                except:
                    pass
            if not ranges: ranges = [[0, 1440]]
            
        out = []
        day_idx = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        for d in days:
            di = day_idx.get(d)
            if di is None: continue
            for ps, pe in ranges:
                s = ((di * 1440 + ps + offset) % 10080 + 10080) % 10080
                e = s + (pe - ps)
                if e <= 10080: out.append([s, e])
                else: out.extend([[s, 10080], [0, e - 10080]])
        return out
        
    ia = _weekly_utc_intervals(a.get("schedule", {}))
    ib = _weekly_utc_intervals(b.get("schedule", {}))
    
    if not ia or not ib: return False
    for s1, e1 in ia:
        for s2, e2 in ib:
            if s1 < e2 and s2 < e1:
                return True
    return False

def _run_ai_for_automation(auto_id, delay=0):
    """Helper to run the AI engine for a single automation."""
    global _ai_running_set
    
    if delay > 0:
        time.sleep(delay)
        
    # Prevent duplicate concurrent runs
    if auto_id in _ai_running_set:
        logging.info(f"[AI-SCHEDULER] AI already running for '{auto_id}', skipping duplicate request")
        return
    
    _ai_running_set.add(auto_id)
    
    try:
        from ai_agent import get_weather_data, build_automation_context, get_ai_schedule_decision, is_model_loaded, is_model_loading, refresh_client
    except ImportError as e:
        logging.error(f"[AI-SCHEDULER] Could not import ai_agent module: {e}")
        session, auto = _find_automation(auto_id)
        _ai_running_set.discard(auto_id)
        if auto:
            _auto_log(auto_id, f"AI error: module import failed - {e}", level="error")
            _emit_auto_update(auto)
        return

    session, auto = _find_automation(auto_id)
    if not auto:
        _ai_running_set.discard(auto_id)
        return

    sched = auto.get("schedule", {})
    if not sched.get("ai_enabled"):
        _ai_running_set.discard(auto_id)
        return

    # Ensure the AI client is loaded with THIS automation's owner's API key
    owner_email = auto.get("_owner_email", MQTT_USERNAME)
    refresh_client(owner_email)
    
    if not is_model_loaded():
        _auto_log(auto_id, "AI skipped: AI Model not loaded or API key invalid.", level="error")
        _ai_running_set.discard(auto_id)
        _emit_auto_update(auto)
        return

    lat = sched.get("lat")
    lon = sched.get("lon")
    if not lat or not lon:
        _auto_log(auto_id, "AI skipped: no Lat/Lon configured", level="warn")
        _ai_running_set.discard(auto_id)
        _emit_auto_update(auto)
        return

    logging.info(f"[AI-SCHEDULER] Running AI for '{auto.get('name', auto_id)}'")
    try:
        import db as _db
        _api_settings = _db.get_api_settings(owner_email)
        _key_label = "personal" if _api_settings.get("api_mode") == "custom" else "shared"
    except Exception:
        _key_label = "shared"
    _auto_log(auto_id, f"🤖 ({_key_label}) AI Agent starting...")
    _emit_auto_update(auto) # force UI update to show log

    try:
        farm_area = sched.get("farmArea", 5)
        precision = 1 if farm_area > 300 else (2 if farm_area > 3 else 3)
        weather_data = get_weather_data(lat=lat, lon=lon, decimal_places=precision)
        if not weather_data:
            _auto_log(auto_id, "AI failed: could not fetch weather", level="error")
            auto["ai_last_fail"] = datetime.now().timestamp()
            _ai_running_set.discard(auto_id)
            _emit_auto_update(auto)
            return

        occupied_days = set()
        all_autos = list(session_mgr.get_all_automations())
        for other_sess, other_id, other_auto in all_autos:
            if other_id == auto_id: continue
            if other_auto.get("status") != "ON": continue
            if _schedules_overlap(auto, other_auto):
                days = other_auto.get("schedule", {}).get("days", [])
                occupied_days.update(days)

        ctx = build_automation_context(auto_id, auto, list(occupied_days))
        decision = get_ai_schedule_decision(weather_data, ctx)
        if not decision:
            _auto_log(auto_id, "AI failed: model returned no decision", level="error")
            auto["ai_last_fail"] = datetime.now().timestamp()
            _ai_running_set.discard(auto_id)
            _emit_auto_update(auto)
            return

        new_days = decision.get("selected_days", [])
        reasoning = decision.get("reasoning", "")
        old_days = sched.get("days", [])
        
        # HARD FILTER: Ensure no occupied days are included, even if AI hallucinates
        filtered_days = []
        removed_days = []
        for d in new_days:
            if d in occupied_days:
                removed_days.append(d)
            else:
                filtered_days.append(d)
                
        if removed_days:
            new_days = filtered_days
            reasoning += f" (System Override: Automatically removed {', '.join(removed_days)} due to hard schedule conflicts with other automations.)"

        # SAFETY: If we are currently RUNNING or WORKING on an action, 
        # ensure today stays in the schedule so we don't stop mid-cycle.
        rt = auto.get("runtime", {})
        if rt.get("state") not in ("IDLE", "ERROR"):
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            today_name = day_names[_get_auto_now(auto).weekday()]
            if today_name not in new_days:
                new_days.append(today_name)
                reasoning += f" (Note: Today was kept in schedule because a cycle is currently active.)"

        # Assign the days back to the global reference to guarantee persistence
        if "schedule" not in auto:
            auto["schedule"] = {}
        auto["schedule"]["days"] = new_days
        auto["ai_last_reasoning"] = reasoning
        
        # Clear any previous failure flags on success
        auto.pop("ai_last_fail", None)
        auto.pop("ai_fail_count", None)
        
        _auto_log(auto_id, f"🤖 AI updated days: {old_days} → {new_days}")
        _auto_log(auto_id, f"🤖 Reasoning: {reasoning}")
        logging.info(f"[AI-SCHEDULER] '{auto.get('name')}': {old_days} → {new_days} | {reasoning}")
        
        # Deep Sync: Send specific log message AND full update
        owner_email = auto.get("_owner_email", MQTT_USERNAME)
        owner_session = session_mgr.get_session(owner_email)
        if owner_session:
            socketio.emit("log_message", {"entity": auto.get("name"), "state": "AI Schedule Updated"}, room=owner_session.room)
        else:
            socketio.emit("log_message", {"entity": auto.get("name"), "state": "AI Schedule Updated"})
        
        # Send a premium detailed system notification
        send_sys_notification(
            owner_email,
            "🤖 AI Agronomist Schedule Update",
            f"'{auto.get('name', 'Sprinkler')}' schedule updated: {old_days} → {new_days}.\nReasoning: {reasoning}",
            type="success"
        )
        _emit_auto_update(auto) 

    except Exception as e:
        error_msg = str(e)
        logging.error(f"[AI-SCHEDULER] Error running AI for '{auto.get('name', auto_id)}': {error_msg}")
        
        # Track consecutive failures
        fail_count = auto.get("ai_fail_count", 0) + 1
        auto["ai_fail_count"] = fail_count

        # Determine retry delay based on attempt number
        retry_delay = 1800  # default for 3rd+ failure
        
        if fail_count >= 3:
            # Strike 3: wait 30 mins
            retry_delay = 1800
            _auto_log(auto_id, f"AI error (Attempt {fail_count}/3): Multiple failures. Falling back to 30-min retry.", level="error")
            auto["ai_fail_count"] = 0 # Reset count for the next cycle after 30 mins
        else:
            match = re.search(r'Please retry in ([\d\.]+)s', error_msg)
            if match:
                try:
                    seconds = float(match.group(1))
                    # Staggered buffers: +1m for first fail, +5m for second fail
                    buffer = 60 if fail_count == 1 else 300
                    retry_delay = seconds + buffer
                    _auto_log(auto_id, f"AI error (Attempt {fail_count}/3): Quota exceeded. Retrying in ~{math.ceil(retry_delay/60)} mins.", level="error")
                except ValueError:
                    _auto_log(auto_id, f"AI error (Attempt {fail_count}/3): {error_msg[:100]}", level="error")
            else:
                # If no specific time requested, use standard 30m
                _auto_log(auto_id, f"AI error (Attempt {fail_count}/3): {error_msg[:100]}", level="error")
            
        auto["ai_last_fail"] = time.time()
        auto["ai_retry_delay"] = retry_delay
    finally:
        _ai_running_set.discard(auto_id)
        _emit_auto_update(auto)


def _run_ai_for_all_automations():
    """Run the AI agent for every automation that has ai_enabled=True, ordered by ai_priority."""
    all_autos = list(session_mgr.get_all_automations())
    # Sort by ai_priority (default 99999), lower number = higher priority (executes first)
    all_autos.sort(key=lambda x: x[2].get("ai_priority", 99999))
    
    for session, auto_id, auto in all_autos:
        _run_ai_for_automation(auto_id)


# ---------------------------------------------------------------------------
# Automation SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("run_ai_now")
def handle_run_ai_now(data):
    """Immediately trigger the AI for a specific automation (called from UI)."""
    auto_id = data.get("id")
    if _find_automation(auto_id):
        socketio.start_background_task(_run_ai_for_automation, auto_id)

@socketio.on("get_weather_insights")
def handle_get_weather_insights(data):
    """Fetch 14-day weather array for UI charts."""
    lat = data.get("lat")
    lon = data.get("lon")
    auto_id = data.get("auto_id")
    if not lat or not lon:
        return
        
    client_sid = request.sid
        
    def _fetch():
        from ai_agent import get_weather_data
        try:
            session, auto = _find_automation(auto_id)
            farm_area = auto.get("schedule", {}).get("farmArea", 5) if auto else 5
            precision = 1 if farm_area > 300 else (2 if farm_area > 3 else 3)
            weather = get_weather_data(lat=lat, lon=lon, past_days=7, decimal_places=precision)
            socketio.emit("weather_insights_data", {"auto_id": auto_id, "weather": weather}, to=client_sid)
        except Exception as e:
            logging.error(f"Failed to fetch weather insights: {e}")
            
    socketio.start_background_task(_fetch)

@socketio.on("get_automations")
def handle_get_automations():
    """Send all automations to the client."""
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess:
        emit("automations_list", [])
        return
    result = []
    for auto_id, auto in sess.automations.items():
        safe = copy.deepcopy(auto)
        safe["logs"] = sess.automation_logs.get(auto_id, [])[:20]
        result.append(safe)
    emit("automations_list", result)


# ---------------------------------------------------------------------------
# API Settings events (per-user, stored encrypted in DB)
# ---------------------------------------------------------------------------
from ai_agent import refresh_client

@socketio.on("get_api_settings")
def handle_get_api_settings():
    import db
    from ai_agent import GEMINI_API_KEY
    settings = db.get_api_settings(_get_user_email())
    # Mask key for safety
    safe_settings = copy.deepcopy(settings)
    if safe_settings.get("custom_api_key"):
        key = safe_settings["custom_api_key"]
        safe_settings["custom_api_key"] = key[:4] + "*" * (len(key)-8) + key[-4:] if len(key) > 8 else "****"
    
    # Add info about whether the system has a shared key
    safe_settings["has_shared_key"] = bool(GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE")
    emit("api_settings", safe_settings)

@socketio.on("update_api_settings")
def handle_update_api_settings(data):
    import db
    import copy
    user_email = _get_user_email()
    logging.info(f"[API_SETTINGS] Saving API key for resolved user_email: {user_email}")
    current = db.get_api_settings(user_email)
    
    # If key is masked (contains *), keep the old one
    new_key = data.get("custom_api_key", "")
    if "*" in new_key:
        new_key = current.get("custom_api_key", "")
    
    api_mode = data.get("api_mode", "default")
    db.save_api_settings(user_email, api_mode, new_key)
    
    refresh_client(user_email)
    
    # Emit the updated settings directly back to the client
    updated_settings = db.get_api_settings(user_email)
    safe_settings = copy.deepcopy(updated_settings)
    if safe_settings.get("custom_api_key"):
        key = safe_settings["custom_api_key"]
        safe_settings["custom_api_key"] = key[:4] + "*" * (len(key)-8) + key[-4:] if len(key) > 8 else "****"
    emit("api_settings", safe_settings)
    
    emit("log_message", {"entity": "System", "state": "API Settings Updated"})

@socketio.on("update_ai_priority")
def handle_update_ai_priority(data):
    import db
    user_email = _get_user_email()
    id_priority_map = data.get("priorities", {})
    if id_priority_map:
        db.update_automation_priorities(user_email, id_priority_map)
        
        # Also update in-memory session
        sess = session_mgr.get_session_by_sid(request.sid)
        if sess:
            for auto_id, prio in id_priority_map.items():
                if auto_id in sess.automations:
                    sess.automations[auto_id]["ai_priority"] = prio
        
        emit("log_message", {"entity": "System", "state": "AI Priorities Updated"})


@socketio.on("create_automation")
def handle_create_automation(data):
    """Create a new automation."""
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess:
        emit("automation_error", {"error": "Not logged in"})
        return
    auto_id = str(uuid.uuid4())[:8]
    auto = {
        "id": auto_id,
        "name": data.get("name", "New Automation"),
        "description": data.get("description", ""),
        "status": "OFF",
        "schedule": data.get("schedule", {"days": [], "startTime": "", "endTime": ""}),
        "condition": data.get("condition", []),
        "initialization": data.get("initialization", []),
        "deinitialization": data.get("deinitialization", []),
        "actions": data.get("actions", []),
        "errorState": data.get("errorState", []),
        "bufferTime": data.get("bufferTime", BUFFER_SECONDS),
        "maxCyclesPerDay": data.get("maxCyclesPerDay", 0),
        "runtime": _new_runtime(),
    }
    auto["_owner_email"] = sess.email
    sess.automations[auto_id] = auto
    sess.automation_logs[auto_id] = []
    _auto_log(auto_id, f"Automation '{auto['name']}' created")
    _emit_auto_update(auto)
    emit("automation_created", {"id": auto_id})
    # Persist to DB
    try:
        import db
        db.save_automation(sess.email, auto)
    except Exception as e:
        logging.error(f"[DB] Failed to save new automation: {e}")
    start_engine()


@socketio.on("update_automation")
def handle_update_automation(data):
    """Update an existing automation's configuration."""
    auto_id = data.get("id")
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess or auto_id not in sess.automations:
        emit("automation_error", {"error": "Not found"})
        return
    auto = sess.automations[auto_id]
    # Update config fields
    actions_changed = "actions" in data
    old_actions = auto.get("actions", [])
    
    for key in ("name", "description", "schedule", "condition",
                "initialization", "deinitialization", "actions", "errorState", "bufferTime", "maxCyclesPerDay"):
        if key in data:
            auto[key] = data[key]

    # Handle Live Sequence Updates (Adding/Removing actions)
    rt = auto.get("runtime", {})
    if rt.get("state") in ("ACTION_SET", "ACTION_VERIFY", "ACTION_RUN", "BUFFER_WAIT") and actions_changed:
        idx = rt.get("currentActionIndex", 0)
        
        if idx < len(old_actions):
            current_action_topic = old_actions[idx].get("switchCmdTopic")
            
            # Try to find where our current action moved to in the new list
            new_actions = auto.get("actions", [])
            new_idx = -1
            for i, a in enumerate(new_actions):
                if a.get("switchCmdTopic") == current_action_topic:
                    new_idx = i
                    break
            
            if new_idx != -1:
                if new_idx != idx:
                    _auto_log(auto_id, f"Sequence changed: Current action moved from #{idx+1} to #{new_idx+1}. Tracking automatically.")
                    rt["currentActionIndex"] = new_idx
                
                # Also handle duration update if we are currently running
                if rt["state"] == "ACTION_RUN":
                    new_dur = new_actions[new_idx].get("duration", 0)
                    old_dur = old_actions[idx].get("duration", 0)
                    if new_dur != old_dur:
                        rt["remainingTime"] = new_dur
                        _auto_log(auto_id, f"Live duration updated: {old_dur}s -> {new_dur}s")
            else:
                # The action we were running is gone!
                _auto_log(auto_id, "The active action was deleted from the sequence. Resetting to IDLE.", level="warn")
                rt["state"] = "IDLE"
                rt["currentActionIndex"] = 0
        else:
            # Index was already out of bounds for some reason
            rt["state"] = "IDLE"
            rt["currentActionIndex"] = 0
            
    elif rt.get("state") == "SCHEDULER_RUN" and actions_changed and auto.get("actions"):
        # User added actions to a running scheduler-only automation
        _auto_log(auto_id, "Actions added to running scheduler! Transitioning to action sequence.")
        rt["state"] = "ACTION_SET"
        rt["currentActionIndex"] = 0

    # Conflict check: if it is ON, ensure the newly saved changes don't conflict with other running automations
    if auto.get("status") == "ON":
        for other_id, other_auto in sess.automations.items():
            if other_id == auto_id: continue
            if other_auto.get("status") == "ON":
                if _schedules_overlap(auto, other_auto):
                    _auto_log(auto_id, f"Edit introduced a conflict with running automation '{other_auto.get('name')}'. Turning OFF.", level="warning")
                    auto["status"] = "OFF"
                    deinits = auto.get("deinitialization", [])
                    if deinits:
                        rt["state"] = "DEINIT_SET"
                        rt["currentDeinitIndex"] = 0
                        rt["retryCount"] = 0
                        rt["deinit_queued_at"] = time.time()
                    else:
                        rt["state"] = "IDLE"
                    socketio.emit("automation_error", {"error": f"Automation '{auto.get('name')}' turned OFF due to a conflict with '{other_auto.get('name')}'."}, room=sess.room)
                    break

    _auto_log(auto_id, f"Automation '{auto['name']}' updated")
    _emit_auto_update(auto)

    # Persist to DB
    try:
        import db
        db.save_automation(sess.email, auto)
    except Exception as e:
        logging.error(f"[DB] Failed to persist automation update: {e}")
    
    # Run AI immediately if enabled upon save
    # (The AI runner itself will now handle safety if a run is already in progress)
    sched = auto.get("schedule", {})
    if sched.get("ai_enabled"):
        socketio.start_background_task(_run_ai_for_automation, auto_id)


@socketio.on("pause_automation")
def handle_pause_automation(data):
    auto_id = data.get("id")
    is_paused = data.get("isPaused", False)
    sess = session_mgr.get_session_by_sid(request.sid)
    if sess and auto_id in sess.automations:
        sess.automations[auto_id]["isPaused"] = is_paused
        _emit_auto_update(sess.automations[auto_id])
        start_engine()


@socketio.on("toggle_automation")
def handle_toggle_automation(data):
    """Toggle automation ON/OFF."""
    auto_id = data.get("id")
    status = data.get("status", "OFF")
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess or auto_id not in sess.automations:
        return
    auto = sess.automations[auto_id]
    auto["status"] = status
    if status == "ON":
        auto["runtime"] = _new_runtime(auto.get("runtime", {}))
        auto["runtime"]["state"] = "WAIT_CONDITION"
        auto["runtime"]["init_completed"] = False
        auto["runtime"]["retryCount"] = 0
        _auto_log(auto_id, "Turned ON → WAIT_CONDITION")
        
        # Run AI immediately if enabled
        sched = auto.get("schedule", {})
        if sched.get("ai_enabled"):
            socketio.start_background_task(_run_ai_for_automation, auto_id)
    else:
        deinits = auto.get("deinitialization", [])
        new_rt = _new_runtime(auto.get("runtime", {}))
        if deinits:
            # Run the deinitialization set→verify sequence before settling to IDLE.
            new_rt["state"] = "DEINIT_SET"
            new_rt["currentDeinitIndex"] = 0
            new_rt["retryCount"] = 0
            new_rt["deinit_queued_at"] = time.time()  # Used to sequence concurrent deinits
            auto["runtime"] = new_rt
            _auto_log(auto_id, "Turned OFF → DEINIT_SET (deinitialization)")
        else:
            auto["runtime"] = new_rt
            _auto_log(auto_id, "Turned OFF → IDLE")
    _emit_auto_update(auto)
    start_engine()
    # Persist to DB
    try:
        import db
        db.save_automation(_get_user_email(), auto)
    except Exception as e:
        logging.error(f"[DB] Failed to save toggle state: {e}")
    
    # Notify frontend about changed enabled_units so watchdog UI updates immediately
    if sess.room:
        next_pings = {u: s.get("last_ping", 0) + WATCHDOG_PING_INTERVAL for u, s in getattr(sess, "watchdog_state", {}).items() if sess.watchdogs.get(u) != 'none'}
        socketio.emit("watchdogs_update", {"watchdogs": sess.watchdogs, "liveness": getattr(sess, "unit_liveness", {}), "next_pings": next_pings, "enabled_units": list(_get_enabled_auto_units(sess))}, room=sess.room)


@socketio.on("reset_automation")
def handle_reset_automation(data):
    """Reset automation execution."""
    auto_id = data.get("id")
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess or auto_id not in sess.automations:
        return
    auto = sess.automations[auto_id]
    rt = auto.get("runtime", {})
    now = time.time()
    today_str = datetime.now().strftime("%Y-%m-%d")

    # If currently running, capture the partial duration for insights before resetting
    if rt.get("state") == "ACTION_RUN" and rt.get("timerStart"):
        elapsed = now - rt["timerStart"]
        if "duration_history" not in rt:
            rt["duration_history"] = {}
        rt["duration_history"][today_str] = rt["duration_history"].get(today_str, 0) + elapsed
        _auto_log(auto_id, f"Reset: Added partial duration ({int(elapsed)}s) to history")

    status = auto.get("status", "OFF")
    auto["runtime"] = _new_runtime(auto.get("runtime", {}))
    # Explicitly set cycles_today to 0 on reset so the row display restarts
    auto["runtime"]["cycles_today"] = 0
    if status == "ON":
        auto["runtime"]["state"] = "WAIT_CONDITION"
        auto["runtime"]["retryCount"] = 0
        auto["runtime"]["init_completed"] = False
        _auto_log(auto_id, "RESET → WAIT_CONDITION (Restarting schedule checks)")
    else:
        _auto_log(auto_id, "RESET → IDLE")
    _emit_auto_update(auto)
    # Persist to DB
    try:
        import db
        db.save_automation(_get_user_email(), auto)
    except Exception as e:
        logging.error(f"[DB] Failed to save reset state: {e}")


@socketio.on("delete_automation")
def handle_delete_automation(data):
    """Delete an automation."""
    auto_id = data.get("id")
    sess = session_mgr.get_session_by_sid(request.sid)
    if sess and auto_id in sess.automations:
        name = sess.automations[auto_id].get("name", auto_id)
        del sess.automations[auto_id]
        sess.automation_logs.pop(auto_id, None)
        socketio.emit("automation_deleted", {"id": auto_id}, room=sess.room)
        logging.info(f"[SESSION:{sess.email}] Automation '{name}' deleted")
        # Remove from DB
        try:
            import db
            db.delete_automation(auto_id)
        except Exception as e:
            logging.error(f"[DB] Failed to delete automation: {e}")


@socketio.on("suggest_ai_settings")
def handle_suggest_ai_settings(data):
    """Generate suggested AI guidelines and thresholds based on name & description."""
    name = data.get("name", "")
    desc = data.get("description", "")
    client_sid = request.sid

    def _run():
        try:
            from ai_agent import generate_ai_settings_suggestion, refresh_client
            sess = session_mgr.get_session_by_sid(client_sid)
            owner_email = sess.email if sess else MQTT_USERNAME
            refresh_client(owner_email)
            
            suggestion = generate_ai_settings_suggestion(name, desc)
            if suggestion:
                socketio.emit("suggested_ai_settings_response", {"status": "success", "data": suggestion}, to=client_sid)
            else:
                socketio.emit("suggested_ai_settings_response", {"status": "error", "message": "AI failed to generate suggestion. Please try again."}, to=client_sid)
        except Exception as e:
            logging.error(f"[AI-SUGGESTION] Failed to generate AI rules: {e}")
            socketio.emit("suggested_ai_settings_response", {"status": "error", "message": str(e)}, to=client_sid)

    socketio.start_background_task(_run)


@socketio.on("get_automation_logs")
def handle_get_automation_logs(data):
    """Get logs for a specific automation."""
    auto_id = data.get("id")
    sess = session_mgr.get_session_by_sid(request.sid)
    if sess:
        logs = sess.automation_logs.get(auto_id, [])
    else:
        logs = automation_logs.get(auto_id, [])
    emit("automation_logs", {"id": auto_id, "logs": logs})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _load_user_automations(email):
    """Load a user's automations from DB into the in-memory dict."""
    global automations, automation_logs
    try:
        import db
        # Save current user's state before wiping memory for the new user
        if automations:
            _save_all_to_db()
            
        saved = db.load_automations(email)
        automations.clear()
        automation_logs.clear()
        for auto in saved:
            auto_id = auto["id"]
            auto["_owner_email"] = email
            automations[auto_id] = auto
            # Load logs from DB
            logs = db.get_logs(auto_id, 200)
            automation_logs[auto_id] = [{"ts": l["ts"], "msg": l["message"], "level": "info"} for l in logs]
        logging.info(f"[DB] Loaded {len(saved)} automations for {email}")
    except Exception as e:
        logging.error(f"[DB] Failed to load automations for {email}: {e}")


def _load_session_automations(sess, email):
    """Load a user's automations from DB into a UserSession object."""
    try:
        import db
        saved = db.load_automations(email)
        sess.watchdogs = db.get_watchdogs(email)
        sess.automations.clear()
        sess.automation_logs.clear()
        for auto in saved:
            auto_id = auto["id"]
            auto["_owner_email"] = email
            sess.automations[auto_id] = auto
            logs = db.get_logs(auto_id, 200)
            sess.automation_logs[auto_id] = [{"ts": l["ts"], "msg": l["message"], "level": "info"} for l in logs]
        logging.info(f"[SESSION-MGR] Loaded {len(saved)} automations for {email}")
    except Exception as e:
        logging.error(f"[SESSION-MGR] Failed to load automations for {email}: {e}")


def _save_all_to_db():
    """Save all current automations to DB (used on shutdown)."""
    try:
        import db
        for email, sess in list(session_mgr._sessions.items()):
            for auto_id, auto in sess.automations.items():
                db.save_automation(email, auto)
        logging.info(f"[DB] Saved all sess automations on shutdown (sessions: {session_mgr.active_count()})")
    except Exception as e:
        logging.error(f"[DB] Shutdown save failed: {e}")


def _auto_resume_sessions():
    """On server restart, reconnect all users who have active (ON) automations."""
    try:
        import db
        active_users = db.get_users_with_active_automations()
        if not active_users:
            logging.info("[AUTO-RESUME] No users with active automations to resume.")
            return
        for user in active_users:
            email = user["email"]
            password = user["password"]
            # Skip the legacy global user (already connected above)
            if email == MQTT_USERNAME:
                continue
            # Validate credentials with CADIO
            if not cadio_login(email, password):
                logging.warning(f"[AUTO-RESUME] CADIO login failed for {email}, skipping")
                continue
            # Create sess, load automations, connect MQTT
            sess = session_mgr.create_session(
                email, password,
                broker=MQTT_BROKER, port=MQTT_PORT, discovery_prefix=DISCOVERY_PREFIX
            )
            _load_session_automations(sess, email)
            sess.start_mqtt(socketio)
            logging.info(f"[AUTO-RESUME] Resumed sess for {email} ({len(sess.automations)} automations)")
        logging.info(f"[AUTO-RESUME] Resumed {len(active_users)} user sessions total")
    except Exception as e:
        logging.error(f"[AUTO-RESUME] Failed: {e}")


if __name__ == "__main__":
    print("\n  Nivixsa Smart Irrigation Dashboard")
    print("  Open http://localhost:5000 in your browser\n")
    # Initialize database
    import db
    db.init_db()
    _sync_master_admin()
    # Register shutdown hook to save state
    atexit.register(_save_all_to_db)
    # Auto-resume all users with active automations from DB
    _auto_resume_sessions()
    # Legacy: if environment variables are set, also create a session for that user
    if MQTT_USERNAME and MQTT_PASSWORD:
        sess = session_mgr.create_session(
            MQTT_USERNAME, MQTT_PASSWORD,
            broker=MQTT_BROKER, port=MQTT_PORT, discovery_prefix=DISCOVERY_PREFIX
        )
        _load_session_automations(sess, MQTT_USERNAME)
        sess.start_mqtt(socketio)
    elif not session_mgr._sessions:
        # Try to auto-login from last saved user (only if no sessions were resumed)
        last_user = db.get_last_user()
        if last_user and last_user.get("password"):
            logging.info(f"[DB] Auto-login from saved user: {last_user['email']}")
            sess = session_mgr.create_session(
                last_user["email"], last_user["password"],
                broker=MQTT_BROKER, port=MQTT_PORT, discovery_prefix=DISCOVERY_PREFIX
            )
            _load_session_automations(sess, last_user["email"])
            sess.start_mqtt(socketio)
        elif last_user:
            logging.info(f"[DB] Found user {last_user['email']} but password not recoverable. Manual login required.")
    start_engine()
    # Start Admin Telemetry
    socketio.start_background_task(_admin_telemetry_loop)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
