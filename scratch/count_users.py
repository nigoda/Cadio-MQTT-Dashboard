import sqlite3

try:
    conn = sqlite3.connect("cadio.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    
    cursor.execute("SELECT email FROM users")
    users = cursor.fetchall()
    
    print(f"Total Users: {count}")
    for u in users:
        print(f" - {u[0]}")
except Exception as e:
    print(f"Error reading database: {e}")
