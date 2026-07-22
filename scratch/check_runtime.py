import sqlite3
import json

conn = sqlite3.connect("cadio.db")
cursor = conn.cursor()
cursor.execute("SELECT id, name, config_json FROM automations WHERE user_email = 'nikhilgowda4164@gmail.com'")
rows = cursor.fetchall()
print(f"Automations for nikhilgowda4164@gmail.com: {len(rows)}")
for r in rows:
    print("=" * 60)
    print(f"ID: {r[0]}")
    print(f"Name: {r[1]}")
    try:
        cfg = json.loads(r[2])
        runtime = cfg.get("runtime", {})
        print(f"\nRuntime state: {runtime.get('state')}")
        print(f"cycles_today: {runtime.get('cycles_today')}")
        print(f"cycles_history: {json.dumps(runtime.get('cycles_history', {}), indent=2)}")
        print(f"duration_history: {json.dumps(runtime.get('duration_history', {}), indent=2)}")
        print(f"last_irrigated: {runtime.get('last_irrigated')}")
        print(f"\nFull runtime keys: {list(runtime.keys())}")
    except Exception as e:
        print(f"Error parsing config: {e}")
conn.close()
