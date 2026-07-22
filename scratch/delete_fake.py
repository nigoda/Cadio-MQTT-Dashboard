import sqlite3
conn = sqlite3.connect("cadio.db")
conn.execute("DELETE FROM users WHERE email = 'nishud1202fsdfd@gmail.com'")
conn.commit()
print("Deleted fake user")
