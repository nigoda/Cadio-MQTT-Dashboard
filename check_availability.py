"""Check availability of all Nivixsa entities."""
import json
import os
import paho.mqtt.client as mqtt
import requests
import time

LOGIN_URL = "https://egycad.com/apis/cadio/login"
EMAIL = os.getenv("MQTT_USERNAME", "")
PASSWORD = os.getenv("MQTT_PASSWORD", "")

r = requests.post(LOGIN_URL, json={"email": EMAIL, "password": PASSWORD}, timeout=15).json()
print(f"Broker: {r['mqtt_host']}:{r['mqtt_port']}\n")

entities = {}       # entity_id -> {name, type, avail_topic, state_topic, ...}
results = {}        # entity_id -> availability status
states = {}         # entity_id -> state payload
avail_map = {}      # avail_topic -> entity_id
state_map = {}      # state_topic -> entity_id

def on_connect(c, u, f, rc):
    print(f"Connected (rc={rc}), discovering entities via config...\n")
    for comp in ["switch", "light", "binary_sensor", "sensor", "climate", "fan", "cover"]:
        c.subscribe(f"homeassistant/{comp}/+/+/config", 0)

def on_msg(c, u, msg):
    topic = msg.topic
    payload = msg.payload.decode()

    if topic.endswith("/config"):
        try:
            cfg = json.loads(payload)
        except json.JSONDecodeError:
            return
        parts = topic.split("/")
        comp = parts[1]
        entity_id = parts[3]
        name = cfg.get("name", entity_id)
        avail_topic = cfg.get("availability_topic") or cfg.get("avail_t")
        state_topic = cfg.get("state_topic") or cfg.get("stat_t")
        if avail_topic:
            entities[entity_id] = {"name": name, "type": comp, "avail_topic": avail_topic}
            avail_map[avail_topic] = entity_id
            c.subscribe(avail_topic, 0)
        if state_topic:
            state_map[state_topic] = entity_id
            c.subscribe(state_topic, 0)
            print(f"  [config] {comp}/{entity_id} ({name})")
    else:
        # Check if this is an availability message
        eid = avail_map.get(topic)
        if eid:
            status = "ONLINE" if payload == "YES" else "OFFLINE"
            results[eid] = status
            print(f"  [avail]  {eid} -> {payload} ({status})")
        # Check if this is a state message
        eid_s = state_map.get(topic)
        if eid_s:
            states[eid_s] = payload

client = mqtt.Client("Nivixsa-avail-checker")
client.username_pw_set(EMAIL, PASSWORD)
client.on_connect = on_connect
client.on_message = on_msg
client.connect(r["mqtt_host"], r["mqtt_port"], 60)
client.loop_start()
time.sleep(10)
client.loop_stop()
client.disconnect()

# Print results
print(f"\n{'Entity':<28} {'Name':<16} {'Type':<16} {'Status':<12} {'State'}")
print("-" * 100)
for eid in sorted(entities):
    e = entities[eid]
    status = results.get(eid, "UNKNOWN")
    marker = "+" if status == "ONLINE" else ("X" if status == "OFFLINE" else "?")
    state_raw = states.get(eid, "")
    # Parse state JSON if possible
    try:
        sj = json.loads(state_raw)
        parts = []
        if "state" in sj:
            parts.append(sj["state"])
        if "brightness" in sj:
            parts.append(f"bri={sj['brightness']}")
        if "color" in sj:
            c = sj["color"]
            parts.append(f"rgb({c.get('r',0)},{c.get('g',0)},{c.get('b',0)})")
        state_str = " | ".join(parts) if parts else state_raw
    except (json.JSONDecodeError, TypeError):
        state_str = state_raw
    print(f"  {eid:<26} {e['name']:<16} {e['type']:<16} [{marker}] {status:<8} {state_str}")

online = sum(1 for s in results.values() if s == "ONLINE")
offline = sum(1 for s in results.values() if s == "OFFLINE")
print(f"\nTotal: {len(entities)} | Online: {online} | Offline: {offline}")
