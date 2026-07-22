import sqlite3

conn = sqlite3.connect("cadio.db")
conn.row_factory = sqlite3.Row

print("--- USERS ---")
users = conn.execute("SELECT email, api_key_enc FROM users").fetchall()
for u in users:
    print(dict(u))

print("\n--- AUTOMATIONS ---")
autos = conn.execute("SELECT id, name FROM automations").fetchall()
for a in autos:
    print(dict(a))

conn.close()
