"""
Nivixsa IoT Dashboard — Backend
Bridges Nivixsa cloud MQTT to the browser via Flask-SocketIO.
Uses the Nivixsa login API to obtain the real MQTT broker details.
"""

import copy
import json
import logging
import os
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt
import requests

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# In-memory stores
device_states: dict = {}        # topic -> last payload
sensor_history: dict = {}       # topic -> list of {ts, value}
MAX_HISTORY = 200               # datapoints kept per sensor topic

mqtt_client = None
mqtt_connected = False
pending_subs = 0          # track outstanding SUBSCRIBE calls

# ---------------------------------------------------------------------------
# Irrigation Automation stores
# ---------------------------------------------------------------------------
automations: dict = {}          # auto_id -> automation dict
automation_logs: dict = {}      # auto_id -> list of log entries
MAX_AUTO_LOG = 200
VERIFY_TIMEOUT = 10             # seconds to wait for switch verification
MAX_RETRIES = 3
BUFFER_SECONDS = 5              # default buffer between actions
ENGINE_INTERVAL = 1.0           # state machine tick interval (seconds)
_engine_thread = None
_engine_running = False

# ---------------------------------------------------------------------------
# MQTT helpers
# ---------------------------------------------------------------------------

def on_connect(client, userdata, flags, rc):
    global mqtt_connected, pending_subs
    codes = {
        0: "Connected",
        1: "Incorrect protocol",
        2: "Invalid client ID",
        3: "Server unavailable",
        4: "Bad credentials",
        5: "Not authorised",
    }
    mqtt_connected = rc == 0
    status = codes.get(rc, f"Unknown ({rc})")
    logging.info(f"MQTT on_connect: rc={rc} -> {status}")
    socketio.emit("mqtt_status", {"connected": mqtt_connected, "message": status})
    if mqtt_connected:
        # Use the discovery prefix from the Nivixsa login API
        prefix = userdata.get("discovery_prefix", DISCOVERY_PREFIX)
        SUPPORTED_COMPONENTS = [
            "alarm_control_panel", "binary_sensor", "button", "camera",
            "climate", "cover", "device_automation", "device_tracker",
            "event", "fan", "humidifier", "image", "lawn_mower", "light",
            "lock", "notify", "number", "scene", "siren", "select",
            "sensor", "switch", "tag", "text", "update", "vacuum",
            "valve", "water_heater",
        ]
        topics = []
        # Component discovery: {prefix}/{component}/+/+/config (broker requires 2-level wildcards)
        for comp in SUPPORTED_COMPONENTS:
            topics.append((f"{prefix}/{comp}/+/+/config", 0))
            topics.append((f"{prefix}/{comp}/+/+/state", 0))
            topics.append((f"{prefix}/{comp}/+/+/set", 0))
        # Device discovery
        topics.append((f"{prefix}/device/+/+/config", 0))
        # Birth/will status
        topics.append((f"{prefix}/status", 0))

        userdata["sub_topics"] = [t for t, _ in topics]
        pending_subs = len(topics)
        for t, qos in topics:
            client.subscribe(t, qos)
        logging.info(f"Subscribing to {len(topics)} topics (prefix={prefix})")


def on_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    logging.warning(f"MQTT disconnected: rc={rc}")
    socketio.emit("mqtt_status", {"connected": False, "message": f"Disconnected (rc={rc})"})


def on_subscribe(client, userdata, mid, granted_qos):
    global pending_subs
    if pending_subs > 0:
        pending_subs -= 1
    rejected = all(q == 128 for q in granted_qos)
    if rejected:
        logging.warning(f"Subscription REJECTED by broker: mid={mid} (granted_qos={granted_qos})")
    else:
        logging.info(f"Subscription OK: mid={mid} (granted_qos={granted_qos})")
        userdata["any_sub_ok"] = True

    # Only run post-subscribe logic once, when initial subscriptions are all done
    if pending_subs == 0 and not userdata.get("initial_subs_done"):
        userdata["initial_subs_done"] = True
        any_ok = userdata.get("any_sub_ok", False)
        if not any_ok:
            logging.warning("ALL subscriptions were rejected by the broker ACL")
            socketio.emit("mqtt_status", {
                "connected": True,
                "message": "Connected but broker rejected all subscriptions"
            })
        else:
            socketio.emit("mqtt_status", {"connected": True, "message": "Connected"})


def on_log(client, userdata, level, buf):
    logging.debug(f"MQTT log: {buf}")


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = msg.payload.decode("utf-8")
    except UnicodeDecodeError:
        payload = msg.payload.hex()

    logging.info(f"MSG RECEIVED: {topic} -> {payload[:200]}")

    # Try to parse as JSON
    parsed = payload
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        pass

    now = datetime.now().isoformat()
    device_states[topic] = {"payload": parsed, "raw": payload, "ts": now}

    # If this is a discovery config message, auto-subscribe to state/availability topics
    if isinstance(parsed, dict) and topic.endswith("/config"):
        _auto_subscribe_from_config(client, userdata, parsed)

    # Keep numeric history for sensor graphs
    numeric_val = None
    if isinstance(parsed, (int, float)):
        numeric_val = parsed
    elif isinstance(parsed, dict):
        for key in ("temperature", "humidity", "temp", "hum", "value", "state", "power",
                     "brightness", "color_temp", "battery", "rssi", "voltage", "current"):
            if key in parsed and isinstance(parsed[key], (int, float)):
                sub_topic = f"{topic}/{key}"
                _append_history(sub_topic, parsed[key], now)
    if numeric_val is not None:
        _append_history(topic, numeric_val, now)

    socketio.emit("device_update", {
        "topic": topic,
        "payload": parsed,
        "raw": payload,
        "ts": now,
    })


# Track topics we've already subscribed to (avoid duplicate subscriptions)
_subscribed_topics: set = set()


def _auto_subscribe_from_config(client, userdata, config):
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

    new_topics = topics_to_sub - _subscribed_topics
    for t in new_topics:
        client.subscribe(t, 0)
        _subscribed_topics.add(t)
        logging.info(f"Auto-subscribed to: {t}")



def _append_history(topic, value, ts):
    if topic not in sensor_history:
        sensor_history[topic] = []
    sensor_history[topic].append({"ts": ts, "value": value})
    if len(sensor_history[topic]) > MAX_HISTORY:
        sensor_history[topic] = sensor_history[topic][-MAX_HISTORY:]


def cadio_login(email, password):
    """Call Nivixsa login API to get MQTT broker details. Returns True on success."""
    global MQTT_BROKER, MQTT_PORT, DISCOVERY_PREFIX
    try:
        resp = requests.post(
            CADIO_LOGIN_URL,
            json={"email": email, "password": password},
            timeout=15,
        )
        logging.info(f"Nivixsa login API status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            logging.info(f"Nivixsa login response: {json.dumps(data, indent=2)}")
            mqtt_host = data.get("mqtt_host")
            mqtt_port = data.get("mqtt_port")
            discovery_prefix = data.get("discovery_prefix")
            if mqtt_host:
                MQTT_BROKER = mqtt_host
            if mqtt_port:
                MQTT_PORT = int(mqtt_port)
            if discovery_prefix:
                DISCOVERY_PREFIX = discovery_prefix
            logging.info(f"Nivixsa config: broker={MQTT_BROKER}, port={MQTT_PORT}, prefix={DISCOVERY_PREFIX}")
            return True
        else:
            logging.warning(f"Nivixsa login failed: {resp.status_code} {resp.text}")
            return False
    except Exception as exc:
        logging.error(f"Nivixsa login API error: {exc}")
        return False


def start_mqtt(email=None, password=None):
    global mqtt_client, MQTT_USERNAME, MQTT_PASSWORD
    if email:
        MQTT_USERNAME = email
    if password:
        MQTT_PASSWORD = password
    if mqtt_client is not None:
        try:
            mqtt_client.disconnect()
        except Exception:
            pass

    # Call Nivixsa login API to get real MQTT broker details
    if not cadio_login(MQTT_USERNAME, MQTT_PASSWORD):
        socketio.emit("mqtt_status", {"connected": False, "message": "Bad credentials"})
        return

    client_id = f"cadio-dashboard-{os.getpid()}"
    mqtt_client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
    mqtt_client.user_data_set({"sub_topics": [], "any_sub_ok": False, "discovery_prefix": DISCOVERY_PREFIX})
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message
    mqtt_client.on_subscribe = on_subscribe
    mqtt_client.on_log = on_log

    port = MQTT_PORT
    if port == 8883:
        mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)

    logging.info(f"Connecting to {MQTT_BROKER}:{port} as {MQTT_USERNAME} (prefix={DISCOVERY_PREFIX})")

    try:
        mqtt_client.connect(MQTT_BROKER, port, keepalive=60)
    except Exception as exc:
        logging.error(f"MQTT connect failed on port {port}: {exc}")
        if port == 1883:
            try:
                logging.info("Retrying with TLS on port 8883...")
                mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
                mqtt_client.connect(MQTT_BROKER, 8883, keepalive=60)
            except Exception as exc2:
                logging.error(f"MQTT TLS connect also failed: {exc2}")
                socketio.emit("mqtt_status", {"connected": False, "message": f"Port 1883: {exc} | Port 8883: {exc2}"})
                return
        else:
            socketio.emit("mqtt_status", {"connected": False, "message": str(exc)})
            return

    mqtt_client.loop_start()


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", mqtt_user=MQTT_USERNAME, cache_bust=str(int(time.time())))


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def handle_ws_connect():
    emit("mqtt_status", {"connected": mqtt_connected,
                          "message": "Connected" if mqtt_connected else "Not connected"})
    # Send current state snapshot
    for topic, data in device_states.items():
        emit("device_update", {"topic": topic, **data})


@socketio.on("login")
def handle_login(data):
    email = data.get("email", "")
    password = data.get("password", "")
    start_mqtt(email, password)


@socketio.on("logout")
def handle_logout():
    global mqtt_client, mqtt_connected
    if mqtt_client is not None:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass
        mqtt_client = None
    mqtt_connected = False
    device_states.clear()
    emit("mqtt_status", {"connected": False, "message": "Not connected"})


@socketio.on("publish")
def handle_publish(data):
    topic = data.get("topic", "")
    payload = data.get("payload", "")
    if mqtt_client and mqtt_connected and topic:
        mqtt_client.publish(topic, payload)
        emit("publish_ack", {"topic": topic, "payload": payload, "ok": True})
    else:
        emit("publish_ack", {"ok": False, "error": "MQTT not connected"})


@socketio.on("subscribe")
def handle_subscribe(data):
    topic = data.get("topic", "#")
    if mqtt_client and mqtt_connected:
        mqtt_client.subscribe(topic)


@socketio.on("get_history")
def handle_get_history(data):
    topic = data.get("topic", "")
    history = sensor_history.get(topic, [])
    emit("sensor_history", {"topic": topic, "history": history})


# ---------------------------------------------------------------------------
# Irrigation Automation Engine
# ---------------------------------------------------------------------------

def _new_runtime():
    """Return a fresh runtime block."""
    return {
        "state": "IDLE",
        "currentActionIndex": 0,
        "timerStart": None,
        "remainingTime": None,
        "retryCount": 0,
        "pauseReason": None,
        "verifyStart": None,
        "bufferStart": None,
    }


def _auto_log(auto_id, message, level="info"):
    """Append a timestamped log entry for an automation."""
    if auto_id not in automation_logs:
        automation_logs[auto_id] = []
    entry = {"ts": datetime.now().isoformat(), "msg": message, "level": level}
    automation_logs[auto_id].insert(0, entry)
    if len(automation_logs[auto_id]) > MAX_AUTO_LOG:
        automation_logs[auto_id] = automation_logs[auto_id][:MAX_AUTO_LOG]
    logging.info(f"[AUTO {auto_id}] {message}")


def _get_switch_state(switch_topic):
    """Get the latest known state for a switch from device_states."""
    data = device_states.get(switch_topic)
    if not data:
        return None
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload.get("state", "").upper()
    return str(payload).upper() if payload else None


def _mqtt_set_switch(cmd_topic, state):
    """Publish a command to set a switch state."""
    if mqtt_client and mqtt_connected and cmd_topic:
        payload = json.dumps({"state": state.upper()})
        mqtt_client.publish(cmd_topic, payload)
        logging.info(f"[ENGINE] Published {payload} to {cmd_topic}")


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
        sensor_topic = cond.get("sensorStateTopic", "")
        expected = str(cond.get("value", "")).upper()
        actual = _get_switch_state(sensor_topic)
        matched = actual == expected if actual is not None else False
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
    """Check if current day+time falls within the schedule window.
    Returns True if no schedule is defined."""
    sched = auto.get("schedule")
    if not sched:
        return True
        
    days = sched.get("days", [])
    if not days:
        return False  # No days selected -> Deactivated
        
    start_str = sched.get("startTime", "")
    end_str = sched.get("endTime", "")

    # Use utcOffset if provided, else fallback to server local time
    utc_offset_mins = sched.get("utcOffset")
    if utc_offset_mins is not None:
        try:
            offset = int(utc_offset_mins)
            now = datetime.utcnow() - timedelta(minutes=offset)
        except ValueError:
            now = datetime.now()
    else:
        now = datetime.now()

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    current_day = day_names[now.weekday()]
    if current_day not in days:
        return False

    if not start_str or not end_str:
        return True  # No time selected -> Active 24/7 on the selected days

    try:
        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))
    except (ValueError, AttributeError):
        return True

    now_mins = now.hour * 60 + now.minute
    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m

    if start_mins <= end_mins:
        # Normal range (e.g. 06:00 -> 09:00)
        return start_mins <= now_mins < end_mins
    else:
        # Overnight range (e.g. 22:00 -> 06:00)
        return now_mins >= start_mins or now_mins < end_mins


def _verify_switches(switch_list, auto):
    """Check if all switches in list match their expected state.
    switch_list: list of {switchCmdTopic, switchStateTopic, state}"""
    for item in switch_list:
        state_topic = item.get("switchStateTopic", "")
        expected = item.get("state", "").upper()
        actual = _get_switch_state(state_topic)
        if actual != expected:
            return False
    return True


def _emit_auto_update(auto):
    """Broadcast automation state update to all connected clients."""
    safe = copy.deepcopy(auto)
    auto_id = safe.get("id", "")
    logs = automation_logs.get(auto_id, [])[:20]
    socketio.emit("automation_update", {"automation": safe, "logs": logs})


def engine_tick(auto):
    """Execute one tick of the state machine for an automation."""
    rt = auto["runtime"]
    state = rt["state"]
    auto_id = auto["id"]
    now = time.time()

    # Priority 1: If status is OFF, go IDLE immediately
    if auto.get("status") != "ON":
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

    # Priority 2: User Pause
    if auto.get("isPaused"):
        if state != "PAUSED_USER" and state != "IDLE" and state != "ERROR":
            rt["prePauseState"] = state
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
        rt["state"] = rt.get("prePauseState", "IDLE")
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
        # Status just turned ON
        rt["state"] = "WAIT_CONDITION"
        _auto_log(auto_id, "Status ON → WAIT_CONDITION")
        _emit_auto_update(auto)
        return

    if state == "WAIT_CONDITION":
        cond = evaluate_condition(auto)
        sched = check_schedule(auto)
        if cond and sched:
            rt["state"] = "INIT_SET"
            rt["retryCount"] = 0
            _auto_log(auto_id, "Condition satisfied + Schedule active → INIT_SET")
            _emit_auto_update(auto)
        return

    if state == "INIT_SET":
        inits = auto.get("initialization", [])
        for item in inits:
            _mqtt_set_switch(item.get("switchCmdTopic", ""), item.get("state", "OFF"))
        rt["verifyStart"] = now
        rt["state"] = "INIT_VERIFY"
        _auto_log(auto_id, "Initialization commands sent → INIT_VERIFY")
        _emit_auto_update(auto)
        return

    if state == "INIT_VERIFY":
        inits = auto.get("initialization", [])
        if not inits or _verify_switches(inits, auto):
            rt["state"] = "ACTION_SET"
            rt["retryCount"] = 0
            rt["currentActionIndex"] = 0
            _auto_log(auto_id, "Initialization verified → ACTION_SET")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                rt["state"] = "ERROR_SET"
                _auto_log(auto_id, "Init verification timeout → ERROR_SET", "error")
                _emit_auto_update(auto)
            else:
                rt["state"] = "INIT_SET"
                _auto_log(auto_id, f"Init verify retry {rt['retryCount']}/{MAX_RETRIES}")
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
        _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", "ON"))
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
            duration = action.get("duration", 0)
            rt["timerStart"] = now
            rt["remainingTime"] = duration
            rt["state"] = "ACTION_RUN"
            _auto_log(auto_id, f"Action {idx+1} verified → ACTION_RUN ({duration}s)")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                rt["state"] = "ERROR_SET"
                _auto_log(auto_id, f"Action {idx+1} verify timeout → ERROR_SET", "error")
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
                # We reached the end. Can we loop?
                cond = evaluate_condition(auto)
                sched = check_schedule(auto)
                if cond and sched and len(actions) > 1:
                    rt["loopingToFirst"] = True
                    rt["state"] = "OVERLAP_NEXT_SET"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Action {idx+1} timer done → Looping to Action 1")
                else:
                    rt["loopingToFirst"] = False
                    rt["state"] = "ACTION_REVERT"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Action {idx+1} timer done → ACTION_REVERT")
            _emit_auto_update(auto)
            return
        # State enforcement: ensure switch is still in expected state
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        if idx < len(actions):
            action = actions[idx]
            if not _verify_switches([action], auto):
                _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", ""))
                _auto_log(auto_id, "State enforcement: correcting switch drift", "warning")
        return

    if state == "OVERLAP_NEXT_SET":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        next_idx = (idx + 1) % len(actions)
        
        if next_idx == 0 and rt.get("loopingToFirst"):
            inits = auto.get("initialization", [])
            for item in inits:
                _mqtt_set_switch(item.get("switchCmdTopic", ""), item.get("state", "OFF"))
            _auto_log(auto_id, "Looping: Initialization commands sent")

        action = actions[next_idx]
        _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", "ON"))
        rt["verifyStart"] = now
        rt["state"] = "OVERLAP_NEXT_VERIFY"
        _auto_log(auto_id, f"Overlap Action {next_idx+1}: Set {action.get('switchName','')} → {action.get('state','')}")
        _emit_auto_update(auto)
        return

    if state == "OVERLAP_NEXT_VERIFY":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        next_idx = (idx + 1) % len(actions)
        action = actions[next_idx]
        
        switches_to_verify = [action]
        if next_idx == 0 and rt.get("loopingToFirst"):
            inits = auto.get("initialization", [])
            switches_to_verify.extend(inits)
            
        if _verify_switches(switches_to_verify, auto):
            rt["bufferStart"] = now
            rt["state"] = "BUFFER"
            _auto_log(auto_id, f"Overlap Action {next_idx+1} verified → BUFFER")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                rt["state"] = "ERROR_SET"
                _auto_log(auto_id, f"Overlap Action {next_idx+1} verify timeout → ERROR_SET", "error")
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

    if state == "ACTION_REVERT":
        # Send the OPPOSITE state to revert the switch
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        if idx < len(actions):
            action = actions[idx]
            revert_state = "OFF" if action.get("state", "").upper() == "ON" else "ON"
            _mqtt_set_switch(action.get("switchCmdTopic", ""), revert_state)
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
            if rt.get("loopingToFirst"):
                rt["loopingToFirst"] = False
                next_idx = 0
                rt["currentActionIndex"] = next_idx
                duration = actions[next_idx].get("duration", 0)
                rt["timerStart"] = now
                rt["remainingTime"] = duration
                rt["state"] = "ACTION_RUN"
                rt["retryCount"] = 0
                _auto_log(auto_id, f"Looped to Action 1 → ACTION_RUN ({duration}s)")
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

    if state == "COMPLETED":
        rt["currentActionIndex"] = 0
        rt["timerStart"] = None
        rt["remainingTime"] = None
        rt["state"] = "WAIT_CONDITION"
        _auto_log(auto_id, "Completed → WAIT_CONDITION (awaiting next trigger)")
        _emit_auto_update(auto)
        return

    if state == "ERROR_SET":
        err_states = auto.get("errorState", [])
        for item in err_states:
            _mqtt_set_switch(item.get("switchCmdTopic", ""), item.get("state", "OFF"))
        rt["verifyStart"] = now
        rt["state"] = "ERROR_VERIFY"
        _auto_log(auto_id, "Error state commands sent → ERROR_VERIFY", "error")
        _emit_auto_update(auto)
        return

    if state == "ERROR_VERIFY":
        err_states = auto.get("errorState", [])
        if not err_states or _verify_switches(err_states, auto):
            rt["state"] = "ERROR"
            _auto_log(auto_id, "Error state verified → ERROR (locked)", "error")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["state"] = "ERROR"
            _auto_log(auto_id, "Error verify timeout → ERROR (locked)", "error")
            _emit_auto_update(auto)
        return


def _engine_loop():
    """Background loop that ticks every automation."""
    global _engine_running
    _engine_running = True
    logging.info("[ENGINE] Automation engine started")
    while _engine_running:
        for auto_id, auto in list(automations.items()):
            try:
                engine_tick(auto)
            except Exception as e:
                logging.error(f"[ENGINE] Error in {auto_id}: {e}")
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


def stop_engine():
    """Stop the automation engine."""
    global _engine_running
    _engine_running = False


# ---------------------------------------------------------------------------
# AI Scheduler — background thread
# ---------------------------------------------------------------------------
_ai_last_run_date = None  # track last AI run date to avoid re-running

def _ai_scheduler_loop():
    """Background thread: checks once per minute if it's time to run the AI."""
    global _ai_last_run_date
    AI_RUN_HOUR = 2  # Run at 2:00 AM local time
    logging.info("[AI-SCHEDULER] AI scheduler thread started")
    while _engine_running:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            if now.hour == AI_RUN_HOUR and _ai_last_run_date != today_str:
                _ai_last_run_date = today_str
                _run_ai_for_all_automations()
        except Exception as e:
            logging.error(f"[AI-SCHEDULER] Error: {e}")
        time.sleep(60)  # check every minute


def _run_ai_for_all_automations():
    """Run the AI agent for every automation that has ai_enabled=True."""
    try:
        from ai_agent import get_7_day_forecast, build_automation_context, get_ai_schedule_decision
    except ImportError:
        logging.error("[AI-SCHEDULER] Could not import ai_agent module")
        return

    for auto_id, auto in automations.items():
        sched = auto.get("schedule", {})
        if not sched.get("ai_enabled"):
            continue

        lat = sched.get("lat")
        lon = sched.get("lon")
        if not lat or not lon:
            _auto_log(auto_id, "AI skipped: no Lat/Lon configured", level="warn")
            continue

        logging.info(f"[AI-SCHEDULER] Running AI for '{auto.get('name', auto_id)}'")
        _auto_log(auto_id, "🤖 AI Agent running...")

        forecast = get_7_day_forecast(lat=lat, lon=lon)
        if not forecast:
            _auto_log(auto_id, "AI failed: could not fetch weather", level="error")
            continue

        ctx = build_automation_context(auto_id, auto)
        decision = get_ai_schedule_decision(forecast, ctx)
        if not decision:
            _auto_log(auto_id, "AI failed: model returned no decision", level="error")
            continue

        new_days = decision.get("selected_days", [])
        reasoning = decision.get("reasoning", "")
        old_days = sched.get("days", [])

        sched["days"] = new_days
        _auto_log(auto_id, f"🤖 AI updated days: {old_days} → {new_days}")
        _auto_log(auto_id, f"🤖 Reasoning: {reasoning}")
        logging.info(f"[AI-SCHEDULER] '{auto.get('name')}': {old_days} → {new_days} | {reasoning}")

        _emit_auto_update(auto)


# ---------------------------------------------------------------------------
# Automation SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("get_automations")
def handle_get_automations():
    """Send all automations to the client."""
    result = []
    for auto_id, auto in automations.items():
        safe = copy.deepcopy(auto)
        safe["logs"] = automation_logs.get(auto_id, [])[:20]
        result.append(safe)
    emit("automations_list", result)


@socketio.on("create_automation")
def handle_create_automation(data):
    """Create a new automation."""
    auto_id = str(uuid.uuid4())[:8]
    auto = {
        "id": auto_id,
        "name": data.get("name", "New Automation"),
        "description": data.get("description", ""),
        "status": "OFF",
        "schedule": data.get("schedule", {"days": [], "startTime": "", "endTime": ""}),
        "condition": data.get("condition", []),
        "initialization": data.get("initialization", []),
        "actions": data.get("actions", []),
        "errorState": data.get("errorState", []),
        "bufferTime": data.get("bufferTime", BUFFER_SECONDS),
        "runtime": _new_runtime(),
    }
    automations[auto_id] = auto
    automation_logs[auto_id] = []
    _auto_log(auto_id, f"Automation '{auto['name']}' created")
    _emit_auto_update(auto)
    emit("automation_created", {"id": auto_id})
    start_engine()


@socketio.on("update_automation")
def handle_update_automation(data):
    """Update an existing automation's configuration."""
    auto_id = data.get("id")
    if auto_id not in automations:
        emit("automation_error", {"error": "Not found"})
        return
    auto = automations[auto_id]
    # Only update config fields, not runtime
    for key in ("name", "description", "schedule", "condition",
                "initialization", "actions", "errorState", "bufferTime"):
        if key in data:
            auto[key] = data[key]
    _auto_log(auto_id, f"Automation '{auto['name']}' updated")
    _emit_auto_update(auto)


@socketio.on("pause_automation")
def handle_pause_automation(data):
    auto_id = data.get("id")
    is_paused = data.get("isPaused", False)
    if auto_id in automations:
        automations[auto_id]["isPaused"] = is_paused
        _emit_auto_update(automations[auto_id])
        start_engine()


@socketio.on("toggle_automation")
def handle_toggle_automation(data):
    """Toggle automation ON/OFF."""
    auto_id = data.get("id")
    status = data.get("status", "OFF")
    if auto_id not in automations:
        return
    auto = automations[auto_id]
    auto["status"] = status
    if status == "ON":
        auto["runtime"] = _new_runtime()
        auto["runtime"]["state"] = "WAIT_CONDITION"
        _auto_log(auto_id, "Turned ON → WAIT_CONDITION")
    else:
        auto["runtime"] = _new_runtime()
        _auto_log(auto_id, "Turned OFF → IDLE")
    _emit_auto_update(auto)
    start_engine()


@socketio.on("reset_automation")
def handle_reset_automation(data):
    """Reset automation execution."""
    auto_id = data.get("id")
    if auto_id not in automations:
        return
    auto = automations[auto_id]
    status = auto.get("status", "OFF")
    auto["runtime"] = _new_runtime()
    if status == "ON":
        auto["runtime"]["state"] = "WAIT_CONDITION"
        _auto_log(auto_id, "RESET → WAIT_CONDITION")
    else:
        _auto_log(auto_id, "RESET → IDLE")
    _emit_auto_update(auto)


@socketio.on("delete_automation")
def handle_delete_automation(data):
    """Delete an automation."""
    auto_id = data.get("id")
    if auto_id in automations:
        name = automations[auto_id].get("name", auto_id)
        del automations[auto_id]
        automation_logs.pop(auto_id, None)
        socketio.emit("automation_deleted", {"id": auto_id})
        logging.info(f"Automation '{name}' deleted")


@socketio.on("get_automation_logs")
def handle_get_automation_logs(data):
    """Get logs for a specific automation."""
    auto_id = data.get("id")
    logs = automation_logs.get(auto_id, [])
    emit("automation_logs", {"id": auto_id, "logs": logs})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n  Nivixsa Smart Irrigation Dashboard")
    print("  Open http://localhost:5000 in your browser\n")
    # Auto-connect MQTT on startup if credentials are set via environment variables
    if MQTT_USERNAME and MQTT_PASSWORD:
        start_mqtt()
    start_engine()
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
