import sqlite3
import csv
import json
import os

DB_PATH = "cadio.db"
OUTPUT_DIR = "exports"

os.makedirs(OUTPUT_DIR, exist_ok=True)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Get all table names
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print(f"Found {len(tables)} tables: {tables}")

for table in tables:
    cursor.execute(f"SELECT * FROM {table}")
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    
    filepath = os.path.join(OUTPUT_DIR, f"{table}.csv")
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)
    
    print(f"  [OK] {table}.csv -- {len(rows)} rows, {len(cols)} columns")

# Also export automations with parsed JSON fields (flattened)
cursor.execute("SELECT id, user_email, name, description, config_json FROM automations")
rows = cursor.fetchall()

flat_rows = []
flat_cols = ["id", "user_email", "name", "description", "status", 
             "ai_enabled", "lat", "lon", "timezone",
             "cycles_today", "last_irrigated",
             "total_actions", "max_cycles_per_day"]

for r in rows:
    try:
        cfg = json.loads(r[4]) if r[4] else {}
    except:
        cfg = {}
    sched = cfg.get("schedule", {})
    rt = cfg.get("runtime", {})
    flat_rows.append([
        r[0], r[1], r[2], r[3],
        cfg.get("status", ""),
        sched.get("ai_enabled", False),
        sched.get("lat", ""),
        sched.get("lon", ""),
        sched.get("timezone", ""),
        rt.get("cycles_today", 0),
        rt.get("last_irrigated", ""),
        len(cfg.get("actions", [])),
        cfg.get("maxCyclesPerDay", 0),
    ])

filepath = os.path.join(OUTPUT_DIR, "automations_flat.csv")
with open(filepath, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(flat_cols)
    writer.writerows(flat_rows)

print(f"\n  [OK] automations_flat.csv -- {len(flat_rows)} rows (parsed JSON fields)")
print(f"\nAll exports saved to: {os.path.abspath(OUTPUT_DIR)}")
conn.close()
