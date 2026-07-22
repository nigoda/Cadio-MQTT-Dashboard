"""
Nivixsa Seed Script — HIGH-DETAIL Dummy Data Generator
Populates the database with a full set of realistic users, plans, and 24-hour histories.
"""

import sys
import os
import json
import uuid
import random
from datetime import datetime, timedelta

# Ensure we can import db.py from the parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

def seed():
    print("💎 Generating High-Detail Dummy Data...")
    db.init_db()
    
    # 1. Define Realistic Users with full metadata
    test_users = [
        {
            "email": "sarah.green@landscapes.com", 
            "plan": "yearly", 
            "lat": 12.9716, "lon": 77.5946, 
            "created": "2024-01-15",
            "automations": [
                {"name": "East Garden Bed", "zones": 3, "active": True},
                {"name": "Rooftop Greenhouse", "zones": 2, "active": True}
            ]
        },
        {
            "email": "farmer.rajesh@karnataka.gov.in", 
            "plan": "yearly", 
            "lat": 15.3647, "lon": 75.1240, 
            "created": "2023-11-20",
            "automations": [
                {"name": "Mango Grove (North)", "zones": 5, "active": True},
                {"name": "Rice Paddy A1", "zones": 1, "active": False}
            ]
        },
        {
            "email": "alex.smith@gmail.com", 
            "plan": "monthly", 
            "lat": 40.7128, "lon": -74.0060, 
            "created": "2025-02-10",
            "automations": [
                {"name": "Backyard Lawn", "zones": 2, "active": True}
            ]
        },
        {
            "email": "trial_user_99@hotmail.com", 
            "plan": "free", 
            "lat": 51.5074, "lon": -0.1278, 
            "created": "2025-05-01",
            "automations": [
                {"name": "Indoor Plants", "zones": 1, "active": True}
            ]
        },
        {
            "email": "blocked_account@security.com", 
            "plan": "free", 
            "lat": 0.0, "lon": 0.0, 
            "created": "2025-04-12",
            "blocked": 1,
            "automations": []
        }
    ]
    
    conn = db._get_conn()

    for u in test_users:
        try:
            # Create user and set encrypted password placeholder
            db.register_user(u["email"], "password123")
            
            # Populate Plan, Blocked, and Geo-data
            conn.execute("""
                UPDATE users SET 
                    api_mode = ?, 
                    blocked = ?, 
                    created_at = ?,
                    api_key_enc = 'ENCRYPTED_PLACEHOLDER'
                WHERE email = ?
            """, (u["plan"], u.get("blocked", 0), u["created"], u["email"]))
            
            print(f"👤 User: {u['email']} created.")

            # Add Automations for this user
            for auto_spec in u["automations"]:
                auto_id = str(uuid.uuid4())[:8]
                
                # Build zones
                actions = []
                for i in range(auto_spec["zones"]):
                    actions.append({
                        "name": f"Zone {i+1} Valve",
                        "topic": f"homeassistant/switch/{u['email'].split('@')[0]}_{auto_id}_{i}/set",
                        "duration": random.randint(300, 1800) # 5 to 30 mins
                    })

                auto_config = {
                    "name": auto_spec["name"],
                    "description": f"Professional setup for {auto_spec['name']}",
                    "status": "ON" if auto_spec["active"] else "OFF",
                    "lat": u["lat"],
                    "lon": u["lon"],
                    "schedule": {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "time": "05:00"},
                    "actions": actions,
                    "maxCyclesPerDay": random.randint(1, 3)
                }
                
                db.save_automation(u["email"], auto_config, auto_id=auto_id)

                # Add a massive history of logs (Last 48 hours)
                log_templates = [
                    "🤖 AI Agent starting...",
                    "⛅ Weather: Temp 28°C, Humidity 60%. Proceeding.",
                    "🚀 Cycle {cycle} of {total} started.",
                    "🚿 Opening Valve: {zone}",
                    "✅ Verification: Valve confirmed ON.",
                    "⌛ Waiting for duration...",
                    "🛑 Reverting Valve: {zone}",
                    "🏁 Action sequence completed."
                ]

                for cycle in range(1, 3):
                    for zone_idx in range(auto_spec["zones"]):
                        # Group logs by time to look realistic
                        base_time = datetime.now() - timedelta(hours=random.randint(1, 48))
                        for i, tpl in enumerate(log_templates):
                            msg = tpl.format(cycle=cycle, total=2, zone=f"Zone {zone_idx+1}")
                            log_time = (base_time + timedelta(minutes=i*2)).isoformat()
                            conn.execute("INSERT INTO automation_logs (automation_id, user_email, message, timestamp) VALUES (?,?,?,?)",
                                         (auto_id, u["email"], msg, log_time))
                
                print(f"   ∟ Automation: {auto_spec['name']} ({auto_spec['zones']} zones) + 48hr History added.")

            conn.commit()
        except Exception as e:
            print(f"⚠️ Error with user {u['email']}: {e}")

    print("\n✨ Database is now FULL and ready for presentation!")
    print("Run your dashboard and log in as any of these users to see the result.")

if __name__ == "__main__":
    seed()
