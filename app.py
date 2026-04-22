"""
Nivixsa IoT Dashboard — Backend
Bridges Nivixsa cloud MQTT to the browser via Flask-SocketIO.
Uses the Nivixsa login API to obtain the real MQTT broker details.
"""

import json
import logging
import os
import ssl
import threading
import time
from datetime import datetime

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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n  Nivixsa IoT Dashboard")
    print("  Open http://localhost:5000 in your browser\n")
    # Auto-connect MQTT on startup if credentials are set via environment variables
    if MQTT_USERNAME and MQTT_PASSWORD:
        start_mqtt()
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
