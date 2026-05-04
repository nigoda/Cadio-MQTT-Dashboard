"""
Smart Irrigation Dashboard — Flask Application

Main entry point. Handles:
- MQTT connection & device discovery
- Engine tick loop (non-blocking)
- REST API for dashboard
- SocketIO for real-time updates
"""

import json
import logging
import os
import threading
import time
from datetime import datetime

import paho.mqtt.client as mqtt
import requests
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

from config import (
    CADIO_LOGIN_URL, MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
    DISCOVERY_PREFIX, ENGINE_TICK_INTERVAL, SECRET_KEY
)
from engine import Engine, State

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
switch_states: dict = {}    # switch_id (state_topic) → "ON"/"OFF"
sensor_states: dict = {}    # sensor_id (state_topic) → value string
device_registry: dict = {}  # topic → {name, type, state_topic, cmd_topic, ...}

mqtt_client = None
mqtt_connected = False
broker_info = {"broker": MQTT_BROKER, "port": MQTT_PORT}

# ---------------------------------------------------------------------------
# Engine instance
# ---------------------------------------------------------------------------

def publish_fn(topic, payload):
    if mqtt_client and mqtt_client.is_connected():
        mqtt_client.publish(topic, payload)
        log.debug(f"[PUB] {topic} → {payload}")

def get_switch_state_fn(switch_id):
    return switch_states.get(switch_id)

def get_sensor_state_fn(sensor_id):
    return sensor_states.get(sensor_id)

engine = Engine(publish_fn, get_switch_state_fn, get_sensor_state_fn)

# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------

def on_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        log.info("[MQTT] Connected")
        # Subscribe to discovery
        components = ["switch", "light", "sensor", "binary_sensor",
                      "climate", "cover", "fan", "lock"]
        for comp in components:
            base = f"{DISCOVERY_PREFIX}/{comp}/+/+/"
            client.subscribe(f"{base}config")
            client.subscribe(f"{base}state")
        socketio.emit("mqtt_status", {"connected": True})
    else:
        mqtt_connected = False
        log.error(f"[MQTT] Connect failed rc={rc}")

def on_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    log.warning("[MQTT] Disconnected")
    socketio.emit("mqtt_status", {"connected": False})

def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = msg.payload.decode("utf-8")
    except Exception:
        return

    if topic.endswith("/config"):
        _handle_config(topic, payload)
    elif topic.endswith("/state") or "/state" in topic:
        _handle_state(topic, payload)

def _handle_config(topic, payload):
    """Parse HA discovery config and register device."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    # Extract type from topic
    parts = topic.split("/")
    if len(parts) < 5:
        return
    dev_type = parts[1]
    state_topic = data.get("state_topic", "")
    cmd_topic = data.get("command_topic", "")
    name = data.get("name", "Unknown")

    if not state_topic:
        return

    device_registry[state_topic] = {
        "name": name,
        "type": dev_type,
        "state_topic": state_topic,
        "cmd_topic": cmd_topic,
    }

    # Subscribe to state topic
    if mqtt_client:
        mqtt_client.subscribe(state_topic)

    log.debug(f"[REG] {dev_type}: {name} ({state_topic})")

def _handle_state(topic, payload):
    """Update switch/sensor state from MQTT."""
    # Try JSON
    try:
        data = json.loads(payload)
        state_val = data.get("state", payload)
    except (json.JSONDecodeError, AttributeError):
        state_val = payload

    if isinstance(state_val, dict):
        state_val = str(state_val)

    # Determine if switch or sensor from registry
    info = device_registry.get(topic, {})
    dev_type = info.get("type", "")

    if dev_type in ("switch", "light", "lock", "fan", "cover"):
        switch_states[topic] = str(state_val).upper()
    else:
        sensor_states[topic] = str(state_val)

    # Broadcast state update
    socketio.emit("state_update", {
        "topic": topic,
        "value": str(state_val),
        "type": dev_type,
    })

def connect_mqtt():
    global mqtt_client, broker_info

    # Try login API first
    if MQTT_USERNAME and MQTT_PASSWORD:
        try:
            resp = requests.post(CADIO_LOGIN_URL, json={
                "email": MQTT_USERNAME,
                "password": MQTT_PASSWORD
            }, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                broker_info["broker"] = data.get("broker", data.get("mqtt_broker", MQTT_BROKER))
                broker_info["port"] = data.get("port", data.get("mqtt_port", MQTT_PORT))
                log.info(f"[API] Broker: {broker_info['broker']}:{broker_info['port']}")
        except Exception as e:
            log.warning(f"[API] Login failed: {e}")

    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    if MQTT_USERNAME:
        mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    try:
        mqtt_client.connect(broker_info["broker"], broker_info["port"], 60)
        mqtt_client.loop_start()
    except Exception as e:
        log.error(f"[MQTT] Connection error: {e}")

# ---------------------------------------------------------------------------
# Engine tick loop
# ---------------------------------------------------------------------------

def engine_loop():
    """Background thread running the state machine."""
    while True:
        try:
            engine.tick()
        except Exception as e:
            log.error(f"[ENGINE] Tick error: {e}")
        time.sleep(ENGINE_TICK_INTERVAL)

# ---------------------------------------------------------------------------
# Persistence (simple JSON file)
# ---------------------------------------------------------------------------
DATA_FILE = os.path.join(os.path.dirname(__file__), "automations.json")

def save_automations():
    data = {}
    for auto_id, auto in engine.automations.items():
        data[auto_id] = {
            "name": auto.name,
            "status": auto.status,
            "schedule": auto.schedule,
            "conditions": auto.conditions,
            "initialization": auto.initialization,
            "actions": auto.actions,
            "error_state": auto.error_state,
            "buffer_time": auto.buffer_time,
        }
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_automations():
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
        for auto_id, auto_data in data.items():
            engine.add_automation(auto_id, auto_data)
        log.info(f"[LOAD] Loaded {len(data)} automations")
    except Exception as e:
        log.error(f"[LOAD] Error: {e}")

# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    return jsonify({
        "mqtt": mqtt_connected,
        "broker": broker_info,
        "switches": len(switch_states),
        "sensors": len(sensor_states),
        "automations": len(engine.automations),
    })

@app.route("/api/devices")
def api_devices():
    switches = []
    sensors = []
    for topic, info in device_registry.items():
        entry = {**info, "value": switch_states.get(topic) or sensor_states.get(topic, "--")}
        if info["type"] in ("switch", "light", "lock", "fan", "cover"):
            switches.append(entry)
        else:
            sensors.append(entry)
    return jsonify({"switches": switches, "sensors": sensors})

@app.route("/api/automations")
def api_automations():
    result = []
    for auto in engine.automations.values():
        result.append(auto.to_dict())
    return jsonify(result)

@app.route("/api/automations", methods=["POST"])
def api_create_automation():
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"ok": False, "msg": "name required"}), 400

    auto_id = f"auto_{int(time.time() * 1000)}"
    engine.add_automation(auto_id, data)
    save_automations()
    return jsonify({"ok": True, "id": auto_id})

@app.route("/api/automations/<auto_id>", methods=["PUT"])
def api_update_automation(auto_id):
    auto = engine.get_automation(auto_id)
    if not auto:
        return jsonify({"ok": False, "msg": "not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "msg": "no data"}), 400

    # Update fields
    if "name" in data: auto.name = data["name"]
    if "schedule" in data: auto.schedule = data["schedule"]
    if "conditions" in data: auto.conditions = data["conditions"]
    if "initialization" in data: auto.initialization = data["initialization"]
    if "actions" in data: auto.actions = data["actions"]
    if "error_state" in data: auto.error_state = data["error_state"]
    if "buffer_time" in data: auto.buffer_time = data["buffer_time"]

    save_automations()
    return jsonify({"ok": True})

@app.route("/api/automations/<auto_id>", methods=["DELETE"])
def api_delete_automation(auto_id):
    engine.remove_automation(auto_id)
    save_automations()
    return jsonify({"ok": True})

@app.route("/api/automations/<auto_id>/toggle", methods=["POST"])
def api_toggle_automation(auto_id):
    auto = engine.get_automation(auto_id)
    if not auto:
        return jsonify({"ok": False, "msg": "not found"}), 404

    data = request.get_json() or {}
    status = data.get("status")

    if status is True or status == "on":
        auto.turn_on()
    else:
        auto.turn_off()

    save_automations()
    return jsonify({"ok": True, "status": auto.status})

@app.route("/api/automations/<auto_id>/reset", methods=["POST"])
def api_reset_automation(auto_id):
    auto = engine.get_automation(auto_id)
    if not auto:
        return jsonify({"ok": False, "msg": "not found"}), 404

    auto.reset()
    return jsonify({"ok": True})

@app.route("/api/switches/<path:topic>/set", methods=["POST"])
def api_set_switch(topic):
    data = request.get_json() or {}
    state = data.get("state", "OFF")
    publish_fn(topic + "/set" if not topic.endswith("/set") else topic,
               json.dumps({"state": state.upper()}))
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# SocketIO
# ---------------------------------------------------------------------------

@socketio.on("connect")
def handle_connect():
    emit("mqtt_status", {"connected": mqtt_connected})
    # Send current state
    emit("full_state", {
        "switches": switch_states,
        "sensors": sensor_states,
        "automations": [a.to_dict() for a in engine.automations.values()],
    })

# ---------------------------------------------------------------------------
# Periodic state broadcast
# ---------------------------------------------------------------------------

def broadcast_loop():
    """Broadcast automation states every second."""
    while True:
        try:
            autos = [a.to_dict() for a in engine.automations.values()]
            socketio.emit("automations_update", autos)
        except Exception:
            pass
        time.sleep(1)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    load_automations()
    connect_mqtt()

    # Start engine thread
    engine_thread = threading.Thread(target=engine_loop, daemon=True)
    engine_thread.start()

    # Start broadcast thread
    broadcast_thread = threading.Thread(target=broadcast_loop, daemon=True)
    broadcast_thread.start()

    log.info("[APP] Starting Smart Irrigation Dashboard")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
