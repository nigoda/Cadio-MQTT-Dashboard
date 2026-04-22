"""
Standalone script to discover and list all Nivixsa IoT entities.
Usage: python list_entities.py
"""

import json
import ssl
import time
import requests
import paho.mqtt.client as mqtt

CADIO_LOGIN_URL = "https://egycad.com/apis/cadio/login"
DISCOVERY_TIMEOUT = 10  # seconds to wait for all config messages

SUPPORTED_COMPONENTS = [
    "alarm_control_panel", "binary_sensor", "button", "camera",
    "climate", "cover", "fan", "humidifier", "light", "lock",
    "number", "scene", "select", "sensor", "siren", "switch",
    "text", "vacuum", "valve", "water_heater",
]

entities = {}  # unique_id -> entity info
done = False


def on_connect(client, userdata, flags, rc):
    if rc != 0:
        print(f"MQTT connection failed: rc={rc}")
        return
    print("Connected to MQTT broker. Discovering entities...\n")
    prefix = userdata["prefix"]
    for comp in SUPPORTED_COMPONENTS:
        client.subscribe(f"{prefix}/{comp}/+/+/config", 0)


def on_message(client, userdata, msg):
    if not msg.topic.endswith("/config"):
        return
    try:
        config = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    uid = config.get("unique_id", msg.topic)
    name = config.get("name", "Unknown")
    # Extract type from topic: prefix/TYPE/account/serial/config
    parts = msg.topic.split("/")
    etype = parts[1] if len(parts) >= 2 else "unknown"

    device = config.get("device", {})
    serial = device.get("serial_number", "")
    model = device.get("model", "")
    device_name = device.get("name", "")

    color_modes = config.get("supported_color_modes", [])
    has_brightness = config.get("brightness", False)

    entities[uid] = {
        "name": name,
        "unique_id": uid,
        "type": etype,
        "serial": serial,
        "model": model,
        "device_name": device_name,
        "has_brightness": has_brightness,
        "color_modes": color_modes,
        "state_topic": config.get("state_topic", ""),
        "command_topic": config.get("command_topic", ""),
        "availability_topic": config.get("availability_topic", ""),
    }


def main():
    email = input("Email: ").strip()
    password = input("Password: ").strip()

    # Step 1: Login
    print("\nLogging in to Nivixsa API...")
    try:
        resp = requests.post(CADIO_LOGIN_URL, json={"email": email, "password": password}, timeout=15)
        if resp.status_code != 200:
            print(f"Login failed: {resp.status_code} {resp.text}")
            return
        data = resp.json()
        if not data.get("success") and not data.get("mqtt_host"):
            print(f"Login failed: {data}")
            return
    except Exception as e:
        print(f"Login error: {e}")
        return

    broker = data.get("mqtt_host", "egycad.com")
    port = int(data.get("mqtt_port", 1883))
    prefix = data.get("discovery_prefix", "homeassistant")
    print(f"Broker: {broker}:{port}  Prefix: {prefix}")

    # Step 2: Connect MQTT
    client = mqtt.Client(client_id="entity-lister", protocol=mqtt.MQTTv311)
    client.user_data_set({"prefix": prefix})
    client.username_pw_set(email, password)
    client.on_connect = on_connect
    client.on_message = on_message

    if port == 8883:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)

    try:
        client.connect(broker, port, keepalive=60)
    except Exception as e:
        print(f"MQTT connect failed: {e}")
        if port == 1883:
            print("Retrying with TLS on 8883...")
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
            try:
                client.connect(broker, 8883, keepalive=60)
            except Exception as e2:
                print(f"TLS connect also failed: {e2}")
                return
        else:
            return

    client.loop_start()

    # Step 3: Wait for discovery
    prev_count = -1
    stable_ticks = 0
    for _ in range(DISCOVERY_TIMEOUT * 2):
        time.sleep(0.5)
        if len(entities) == prev_count and len(entities) > 0:
            stable_ticks += 1
            if stable_ticks >= 4:  # 2 seconds of no new entities
                break
        else:
            stable_ticks = 0
        prev_count = len(entities)

    client.loop_stop()
    client.disconnect()

    # Step 4: Print results
    if not entities:
        print("\nNo entities discovered.")
        return

    # Group by device serial
    by_device = {}
    for e in entities.values():
        key = e["serial"] or "no-device"
        by_device.setdefault(key, []).append(e)

    print(f"\n{'='*70}")
    print(f"  Found {len(entities)} entities across {len(by_device)} devices")
    print(f"{'='*70}\n")

    for serial, ents in sorted(by_device.items()):
        first = ents[0]
        header = f"Device: {first['device_name'] or serial}  (Serial: {serial}, Model: {first['model']})"
        print(header)
        print("-" * len(header))
        for e in sorted(ents, key=lambda x: x["name"]):
            caps = []
            if e["has_brightness"]:
                caps.append("brightness")
            if "rgb" in e["color_modes"]:
                caps.append("rgb")
            if e["command_topic"]:
                caps.append("controllable")
            cap_str = f"  [{', '.join(caps)}]" if caps else ""
            print(f"  {e['type']:20s}  {e['name']:30s}  id={e['unique_id']}{cap_str}")
        print()


if __name__ == "__main__":
    main()
