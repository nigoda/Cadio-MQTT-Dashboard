import sqlite3
import json

conn = sqlite3.connect("cadio.db")
cursor = conn.cursor()
cursor.execute("SELECT id, name, description, config_json FROM automations")
rows = cursor.fetchall()
print(f"Total automations in DB: {len(rows)}")
for r in rows:
    print("-" * 50)
    print(f"ID: {r[0]}")
    print(f"Name: {r[1]}")
    print(f"Description: {r[2]}")
    try:
        cfg = json.loads(r[3])
        print(f"AI Enabled: {cfg.get('schedule', {}).get('ai_enabled')}")
        print(f"AI Thresholds: {cfg.get('schedule', {}).get('ai_thresholds')}")
        print(f"AI Custom Rules: {cfg.get('schedule', {}).get('ai_custom_rules')}")
    except Exception as e:
        print(f"Error parsing config: {e}")
conn.close()
