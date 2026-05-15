import sqlite3

conn = sqlite3.connect("cadio.db")
# Delete any users whose email doesn't end with known valid domains
# (manually keep only real verified accounts)
cursor = conn.cursor()
cursor.execute("SELECT email FROM users")
all_users = cursor.fetchall()
print("Before cleanup:")
for u in all_users:
    print(f"  {u[0]}")

conn.execute("DELETE FROM users WHERE email = 'nishud12sdcfswdf@gmail.com'")
conn.commit()

cursor.execute("SELECT email FROM users")
all_users = cursor.fetchall()
print(f"\nAfter cleanup ({len(all_users)} users):")
for u in all_users:
    print(f"  {u[0]}")
