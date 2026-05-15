"""
Nivixsa IoT Dashboard — Backend
Bridges Nivixsa cloud MQTT to the browser via Flask-SocketIO.
Uses the Nivixsa login API to obtain the real MQTT broker details.
"""

import copy
import json
import logging
import os
import ssl
import threading
import time
import uuid
import re
import atexit
import math
from datetime import datetime, timedelta
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

import paho.mqtt.client as mqtt
import requests
import psutil

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
from flask import Flask, render_template, request, session, redirect, g
from flask_socketio import SocketIO, emit, join_room, leave_room

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CADIO_LOGIN_URL = "https://egycad.com/apis/cadio/login"
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

# These are populated after calling the Nivixsa login API
MQTT_BROKER = os.getenv("MQTT_BROKER", "egycad.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
DISCOVERY_PREFIX = "homeassistant"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# In-memory stores
device_states: dict = {}        # topic -> last payload
sensor_history: dict = {}       # topic -> list of {ts, value}
MAX_HISTORY = 200               # datapoints kept per sensor topic

mqtt_client = None
mqtt_connected = False
pending_subs = 0          # track outstanding SUBSCRIBE calls
cadio_login_cached = False # track if we already fetched broker details

# Irrigation Automation stores
# ---------------------------------------------------------------------------
automations: dict = {}          # auto_id -> automation dict
automation_logs: dict = {}      # auto_id -> list of log entries

# ---------------------------------------------------------------------------
# Multi-Tenant Session Management (The Engine)
# ---------------------------------------------------------------------------

class UserSession:
    def __init__(self, email, password, broker=None, port=None, discovery_prefix=None):
        self.email = email
        self.password = password
        self.room = f"user_{email.replace('@', '_').replace('.', '_')}"
        self.mqtt_broker = broker or MQTT_BROKER
        self.mqtt_port = port or MQTT_PORT
        self.discovery_prefix = discovery_prefix or DISCOVERY_PREFIX
        
        self.mqtt_client = None
        self.mqtt_connected = False
        self._mqtt_last_connected_time = time.time()
        
        # In-memory stores for this specific user
        self.device_states = {}
        self.sensor_history = {}
        self.automations = {}
        self.automation_logs = {}
        self._sockets = set() # Track active browser tabs
        self.otp = None # Secure code for admin access

    @property
    def has_sockets(self):
        return len(self._sockets) > 0

    def start_mqtt(self, socketio_ref):
        """Initialize and start this user's private MQTT connection."""
        client_id = f"nivixsa-{self.email}-{os.getpid()}"
        self.mqtt_client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311, userdata={"owner_email": self.email})
        self.mqtt_client.username_pw_set(self.email, self.password)
        self.mqtt_client.on_connect = on_connect
        self.mqtt_client.on_disconnect = on_disconnect
        self.mqtt_client.on_message = on_message
        self.mqtt_client.on_subscribe = on_subscribe
        
        if self.mqtt_port == 8883:
            self.mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
            
        try:
            self.mqtt_client.connect_async(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            logging.info(f"[SESSION:{self.email}] MQTT Client Started")
        except Exception as e:
            logging.error(f"[SESSION:{self.email}] MQTT Start Failed: {e}")

class SessionManager:
    def __init__(self):
        self._sessions = {}       # email -> UserSession
        self._socket_to_email = {} # sid -> email

    def create_session(self, email, password, **kwargs):
        email = email.lower()
        if email not in self._sessions:
            self._sessions[email] = UserSession(email, password, **kwargs)
        return self._sessions[email]

    def get_session(self, email):
        if not email: return None
        return self._sessions.get(email.lower())

    def get_session_by_sid(self, sid):
        email = self._socket_to_email.get(sid)
        return self.get_session(email) if email else None

    def register_socket(self, sid, email):
        if not email: return
        session = self.get_session(email)
        if session:
            session._sockets.add(sid)
            self._socket_to_email[sid] = email

    def unregister_socket(self, sid):
        email = self._socket_to_email.pop(sid, None)
        if email:
            sess = self.get_session(email)
            if sess:
                sess._sockets.discard(sid)
                return sess
        return None

    def is_online(self, email):
        """Checks if a user has an active MQTT session."""
        return email.lower() in self._sessions

    def active_count(self):
        """Returns the number of unique, connected users."""
        # Only count sessions that have an email and are actively connected
        count = 0
        unique_emails = set()
        for email, sess in list(self._sessions.items()):
            if email and sess.mqtt_connected:
                unique_emails.add(email.lower())
        return len(unique_emails)

    def get_all_automations(self):
        """Generator to yield all automations across all active users."""
        for email, sess in list(self._sessions.items()):
            for auto_id, auto in list(sess.automations.items()):
                yield (sess, auto_id, auto)

    def remove_session(self, email):
        sess = self._sessions.pop(email, None)
        if sess and sess.mqtt_client:
            sess.mqtt_client.loop_stop()
            sess.mqtt_client.disconnect()
        logging.info(f"[SESSION-MGR] Removed session for {email}")

    def active_count(self):
        return len(self._sessions)

# Global session manager instance
session_mgr = SessionManager()

# Per-socket user session tracking (for multi-user isolation)
_user_sessions: dict = {}       # socket_sid -> email
_impersonation_tokens: dict = {} # token -> email (Temporary access tokens)

def _get_user_email():
    """Get the email of the currently connected user from their socket session."""
    from flask import request as ws_request
    sid = getattr(ws_request, 'sid', None)
    if sid and sid in _user_sessions:
        return _user_sessions[sid]
    return MQTT_USERNAME  # fallback to global for engine/watchdog threads


def cadio_login(email, password):
    """Call Nivixsa login API to get MQTT broker details. Returns True on success."""
    global MQTT_BROKER, MQTT_PORT, DISCOVERY_PREFIX
    try:
        headers = {
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 5 Build/SQ3A.220705.004)",
            "Content-Type": "application/json"
        }
        resp = requests.post(
            CADIO_LOGIN_URL,
            json={"email": email, "password": password},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            mqtt_host = data.get("mqtt_host")
            if not mqtt_host:
                return False
            
            MQTT_BROKER = mqtt_host
            MQTT_PORT = int(data.get("mqtt_port", 1883))
            DISCOVERY_PREFIX = data.get("discovery_prefix", "homeassistant")
            return True
        return False
    except Exception:
        return False


def _find_automation(auto_id):
    """Find an automation by ID across all sessions."""
    for sess, aid, auto in session_mgr.get_all_automations():
        if aid == auto_id:
            return (sess, auto)
    return (None, None)


MAX_AUTO_LOG = 200
VERIFY_TIMEOUT = 10             # seconds to wait for switch verification
DRIFT_VERIFY_TIMEOUT = 3        # seconds for drift correction (shorter — device was already responding)

# MQTT Watchdog globals
_mqtt_last_connected_time = time.time()

def _mqtt_watchdog():
    """Background thread to ensure per-session MQTT reconnects after internet loss."""
    logging.info("[WATCHDOG] MQTT monitor thread started")
    _reconnect_backoff = 0
    _last_loop_time = time.time()
    
    while _engine_running:
        try:
            now = time.time()
            # Detect system sleep/wake (clock jump > 12s when sleep is 5s)
            sleep_detected = (now - _last_loop_time) > 12
            if sleep_detected:
                logging.warning(f"[WATCHDOG] System sleep/wake detected (Gap: {int(now - _last_loop_time)}s)")
            _last_loop_time = now

            # Iterate all active sessions and reconnect any disconnected ones
            all_connected = True
            for email, sess in list(session_mgr._sessions.items()):
                if sess.mqtt_connected:
                    continue
                all_connected = False
                # Only reconnect if sess has stored credentials
                if not sess.password:
                    continue
                elapsed = now - getattr(sess, '_mqtt_last_connected_time', 0)
                if elapsed > 15 or sleep_detected:
                    delays = [5, 15, 30]
                    delay = delays[min(_reconnect_backoff, len(delays) - 1)]
                    logging.warning(
                        f"[WATCHDOG:{email}] MQTT disconnected for {int(elapsed)}s. "
                        f"Reconnect attempt (next retry in {delay}s)..."
                    )
                    try:
                        if sess.mqtt_client is not None:
                            try:
                                sess.mqtt_client.loop_stop(force=True)
                                sess.mqtt_client.disconnect()
                            except Exception:
                                pass
                        sess.start_mqtt(socketio)
                    except Exception as e:
                        logging.error(f"[WATCHDOG:{email}] Reconnect failed: {e}")
            
            if all_connected:
                _reconnect_backoff = 0
            else:
                _reconnect_backoff += 1
                
        except Exception as e:
            logging.error(f"[WATCHDOG] Error: {e}")
        time.sleep(5)

MAX_RETRIES = 3
BUFFER_SECONDS = 5              # default buffer between actions
ENGINE_INTERVAL = 1.0           # state machine tick interval (seconds)
_engine_thread = None
_engine_running = False
_ai_running_set: set = set()    # track which automations currently have AI running (prevent duplicates)

# ---------------------------------------------------------------------------
# MQTT handlers
# ---------------------------------------------------------------------------

def on_connect(client, userdata, flags, rc):
    owner_email = userdata.get("owner_email")
    sess = session_mgr.get_session(owner_email)
    if rc == 0:
        if sess:
            sess.mqtt_connected = True
            sess._mqtt_last_connected_time = time.time()
        logging.info(f"[MQTT:{owner_email}] Connected successfully")
        # Subscribe to discovery
        if sess:
            client.subscribe(f"{sess.discovery_prefix}/#")
            socketio.emit("mqtt_status", {"connected": True, "message": "Connected"}, room=sess.room)
    else:
        logging.error(f"[MQTT:{owner_email}] Connection failed with code {rc}")
        if sess:
            socketio.emit("mqtt_status", {"connected": False, "message": f"Connection Failed ({rc})"}, room=sess.room)

def on_disconnect(client, userdata, rc):
    owner_email = userdata.get("owner_email")
    sess = session_mgr.get_session(owner_email)
    if sess:
        sess.mqtt_connected = False
        socketio.emit("mqtt_status", {"connected": False, "message": "Disconnected"}, room=sess.room)
    logging.warning(f"[MQTT:{owner_email}] Disconnected")

def on_message(client, userdata, msg):
    owner_email = userdata.get("owner_email")
    sess = session_mgr.get_session(owner_email)
    if not sess: return

    try:
        topic = msg.topic
        payload_raw = msg.payload.decode()
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = payload_raw

        # 1. Store state
        sess.device_states[topic] = {"payload": payload, "ts": time.time()}
        
        # 2. Handle Discovery (Home Assistant Style)
        if "/config" in topic:
            # We don't need to do much here since the frontend handles discovery 
            # by listening to the broadcast, but we can log it.
            pass

        # 3. Handle Sensor History (if payload is numeric)
        try:
            val = float(payload)
            if topic not in sess.sensor_history:
                sess.sensor_history[topic] = []
            sess.sensor_history[topic].append({"ts": time.time(), "value": val})
            if len(sess.sensor_history[topic]) > MAX_HISTORY:
                sess.sensor_history[topic].pop(0)
        except (ValueError, TypeError):
            pass

        # 4. Broadcast to user's private room
        socketio.emit("device_update", {"topic": topic, "payload": payload}, room=sess.room)
        
    except Exception as e:
        logging.error(f"[MQTT:{owner_email}] Error: {e}")

def on_subscribe(client, userdata, mid, granted_qos):
    pass


ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@nivixsa.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "nivixsa-admin-2024")

def _sync_master_admin():
    """Ensure the master admin from .env exists in DB with Level 1 permissions."""
    try:
        import db
        db.save_admin(ADMIN_EMAIL, ADMIN_PASSWORD, level=1)
        logging.info(f"[AUTH] Master Admin synced: {ADMIN_EMAIL} (Level 1)")
    except Exception as e:
        logging.error(f"[AUTH] Master Admin sync failed: {e}")

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_token = session.get("admin_session_token")
        if not session_token:
            return redirect("/admin/login")
        
        import db
        admin_email = db.get_admin_by_session(session_token)
        if not admin_email:
            return redirect("/admin/login")
        
        admin = db.get_admin(admin_email)
        if not admin:
            return redirect("/admin/login")
        
        # Attach admin to request context for use in the view function
        g.admin = admin
        return f(*args, **kwargs)
    return decorated_function

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        
        import db
        admin = db.get_admin(email)
        if admin and db.verify_password(password, admin["password_hash"]):
            session_token = db.create_admin_session(admin["email"], request.headers.get("User-Agent"))
            session["admin_session_token"] = session_token
            return redirect("/admin")
        return render_template("admin_login.html", error="Invalid admin credentials")
    return render_template("admin_login.html")

@app.route("/admin")
@admin_required
def admin_dashboard():
    import db
    users = db.get_all_users_for_admin()
    admins = db.get_all_admins()
    return render_template("admin.html", 
                          users=users, 
                          admins=admins,
                          admin_email=g.admin["email"],
                          admin_level=g.admin["level"],
                          active_sessions=session_mgr.active_count())

@app.route("/admin/logout")
def admin_logout():
    session_token = session.pop("admin_session_token", None)
    if session_token:
        import db
        db.delete_admin_session(session_token)
    return redirect("/")

def _admin_telemetry_loop():
    """Background task to send system health and user stats to admin dashboard."""
    while True:
        try:
            # CPU/RAM
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            
            # DB Storage usage
            db_size_mb = 0
            try:
                if os.path.exists("cadio.db"):
                    db_size_mb = round(os.path.getsize("cadio.db") / (1024 * 1024), 2)
            except (OSError, PermissionError): pass
            
            # Broadcast Health
            socketio.emit("admin_health", {
                "cpu": cpu,
                "ram_gb": round(ram.used / (1024**3), 2),
                "ram_percent": ram.percent,
                "db_size": db_size_mb,
                "latency": 42
            }, room="admin_room")

            # Broadcast Live User Stats
            _emit_admin_stats()

        except Exception: pass
        time.sleep(5) # Push every 5 seconds for live feel

def _emit_admin_stats():
    """Helper to broadcast latest stats and user list to all connected admin sessions."""
    import db
    try:
        all_users = db.get_all_users_for_admin()
        
        # Enrich users with LIVE online status
        for u in all_users:
            u['online'] = session_mgr.is_online(u['email'])

        blocked = len([u for u in all_users if u['blocked']])
        socketio.emit("admin_update", {
            "active_sessions": session_mgr.active_count(),
            "blocked_count": blocked,
            "total_users": len(all_users),
            "users": all_users # Send full list for live updates
        }, room="admin_room")
    except Exception as e:
        logging.error(f"[ADMIN] Failed to emit stats: {e}")

@socketio.on("admin_user_block")
@socket_admin_required
def handle_admin_user_block(data):
    """Admin action: Block/Unblock a user."""
    if g.admin["level"] > 2: return
    
    email = data.get("email")
    status = data.get("status") # 1 to block, 0 to unblock
    if not email: return

    import db
    if status == 1:
        db.block_user(email)
        # 1. Kill MQTT Session
        session_mgr.remove_session(email)
        # 2. Kick from Sockets
        sids_to_kick = [sid for sid, e in list(_user_sessions.items()) if e.lower() == email.lower()]
        for sid in sids_to_kick:
            emit("mqtt_status", {"connected": False, "message": "Account blocked by admin."}, room=sid)
            _user_sessions.pop(sid, None)
            disconnect(sid=sid)
        logging.info(f"[ADMIN] User {email} BLOCKED and session terminated.")
    else:
        db.unblock_user(email)
    _emit_admin_stats()

@socketio.on("admin_user_delete")
@socket_admin_required
def handle_admin_user_delete(data):
    """Admin action: Permanently delete a user."""
    if g.admin["level"] > 1: return
    
    email = data.get("email")
    if not email: return
    
    # 1. Kill and remove active session
    import db
    session_mgr.remove_session(email)
    
    # 2. Kick any active sockets
    sids_to_kick = [sid for sid, e in list(_user_sessions.items()) if e.lower() == email.lower()]
    for sid in sids_to_kick:
        emit("mqtt_status", {"connected": False, "message": "Account deleted by admin."}, room=sid)
        _user_sessions.pop(sid, None)
        disconnect(sid=sid)
        
    # 3. Wipe from DB
    db.delete_user(email)
    logging.info(f"[ADMIN] User {email} PERMANENTLY DELETED and session killed.")
    _emit_admin_stats()

@socketio.on("join_admin")
@socket_admin_required
def handle_join_admin():
    join_room("admin_room")
    logging.info(f"[ADMIN] {g.admin['email']} joined admin telemetry room")
    _emit_admin_stats()

@socketio.on("admin_team_add")
@socket_admin_required
def handle_admin_team_add(data=None):
    if not data: return
    if g.admin["level"] > 1: return
    
    email = data.get("email")
    password = data.get("password")
    level = int(data.get("level", 3))
    import db
    db.save_admin(email, password, level)
    _emit_admin_stats()

@socketio.on("admin_team_delete")
@socket_admin_required
def handle_admin_team_delete(data):
    if g.admin["level"] > 1: return
    
    email = data.get("email")
    # Prevent self-deletion
    if email == g.admin["email"]: return
    
    import db
    db.delete_admin(email)
    _emit_admin_stats()
def handle_join_admin():
    if session.get("admin_email"):
        join_room("admin_room")
        logging.info(f"[WS] Admin {session.get('admin_email')} joined monitoring room (Level {session.get('admin_level')})")
        _emit_admin_stats()

@app.route("/admin/impersonate/<email>")
@admin_required
def admin_impersonate(email):
    """Admin only: Securely impersonate a user using a verified token."""
    email = email.lower()
    if request.admin["level"] > 2:
        return "Unauthorized", 403
        
    token = request.args.get("token")
    if not token or _impersonation_tokens.get(token) != email:
        return "Invalid or expired security token. Access denied.", 403
    
    # Token used once, remove it
    _impersonation_tokens.pop(token, None)

    import db
    user = db.get_user(email)
    if not user: return "User not found", 404
    
    session["email"] = user["email"]
    session["password"] = user["password"]
    logging.info(f"[ADMIN] Impersonating user via OTP: {email}")
    return redirect("/")

@socketio.on("admin_request_otp")
@socket_admin_required
def handle_admin_request_otp(data):
    """Admin requests an OTP to login as a user."""
    if g.admin["level"] > 2: return
    email = data.get("email", "").strip().lower()
    sess = session_mgr.get_session(email)
    if not sess:
        emit("admin_otp_error", {"message": "User is not currently online. Admin can only login if user dashboard is active."})
        return
    
    import random
    code = str(random.randint(100000, 999999))
    sess.otp = code
    # Show code on USER dashboard
    socketio.emit("security_code_request", {"code": code}, room=sess.room)
    emit("admin_otp_sent", {"email": email})

@socketio.on("admin_verify_otp")
@socket_admin_required
def handle_admin_verify_otp(data):
    """Admin submits the code provided by the user."""
    if g.admin["level"] > 2: return
    email = data.get("email", "").strip().lower()
    code = data.get("code")
    sess = session_mgr.get_session(email)
    
    if sess and sess.otp == code:
        token = str(uuid.uuid4())
        _impersonation_tokens[token] = email
        sess.otp = None # Clear it
        emit("admin_otp_success", {"email": email, "token": token})
    else:
        emit("admin_otp_error", {"message": "Invalid security code."})

def _emit_auto_update(auto):
    """Broadcast automation state update to the owning user's room."""
    safe = copy.deepcopy(auto)
    auto_id = safe.get("id", "")
    owner_email = auto.get("_owner_email", MQTT_USERNAME)
    
    sess = session_mgr.get_session(owner_email)
    if sess and auto_id in sess.automation_logs:
        logs = sess.automation_logs.get(auto_id, [])[:20]
    else:
        logs = automation_logs.get(auto_id, [])[:20]
        
    data = {"automation": safe, "logs": logs}
    
    if sess:
        socketio.emit("automation_update", data, room=sess.room)
    else:
        socketio.emit("automation_update", data)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    # If an admin is impersonating, we might have email/password in session
    mqtt_user = session.get("email", MQTT_USERNAME)
    mqtt_pass = session.get("password", "")
    return render_template("index.html", 
                          mqtt_user=mqtt_user, 
                          mqtt_pass=mqtt_pass,
                          cache_bust=str(int(time.time())))


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def handle_ws_connect():
    session_token = session.get('user_session_token')
    if session_token:
        import db
        user_email = db.get_user_by_session(session_token)
        if user_email:
            user = db.get_user(user_email)
            if user:
                # We have a valid session, log the user in automatically
                handle_login({"email": user["email"], "password": db.decrypt_password(user["password_enc"])})
                return

    # On initial connect, send "not connected" — user must login first
    emit("mqtt_status", {"connected": False, "message": "Not connected"})


@socketio.on("logout")
def handle_logout():
    """Handles global logout by deleting the session token and notifying other clients."""
    sid = request.sid
    email = _user_sessions.get(sid)
    if not email: return
    
    email = email.lower()
    logging.info(f"[SESSION] Logout for {email}")

    # 1. Invalidate the database session
    session_token = session.pop('user_session_token', None)
    if session_token:
        import db
        db.delete_session(session_token)
    
    # 2. Broadcast to other clients of this user to force logout
    socketio.emit("force_logout", {
        "email": email,
        "message": "Session terminated."
    })
    
    # 3. Kill the MQTT session
    session_mgr.remove_session(email)

    # 4. Clean up the SID mapping
    sids = [s for s, e in list(_user_sessions.items()) if e.lower() == email]
    for s in sids:
        _user_sessions.pop(s, None)
    
    _emit_admin_stats()

@app.route("/logout")
def global_logout_route():
    """Route to clear Flask session and redirect home."""
    session.pop("email", None)
    session.pop("password", None)
    session.pop("user_session_token", None)
    return redirect("/")

@socketio.on("disconnect")
def handle_ws_disconnect():
    sid = request.sid
    # Standard disconnect (browser close) doesn't necessarily kill the MQTT session 
    # unless it was the last socket.
    email = _user_sessions.pop(sid, None)
    if email:
        session_mgr.unregister_socket(sid)
        # If no more sockets for this user, we could stop MQTT, 
        # but usually we keep it alive for automations.
    _emit_admin_stats()


@socketio.on("login")
def handle_login(data):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    
    # 1. Check if blocked locally BEFORE hitting external API
    import db
    if db.is_user_blocked(email):
        emit("mqtt_status", {"connected": False, "message": "Account blocked: security strike policy. Contact admin."})
        return

    # 2. Fast path: if session already exists and is connected, just re-join the room
    existing_sess = session_mgr.get_session(email)
    if existing_sess and existing_sess.mqtt_connected and existing_sess.password == password:
        _user_sessions[request.sid] = email
        session_mgr.register_socket(request.sid, email)
        join_room(existing_sess.room)
        emit("mqtt_status", {"connected": True, "message": "Connected"})
        for topic, d in existing_sess.device_states.items():
            emit("device_update", {"topic": topic, **d})
        for auto_id, auto in existing_sess.automations.items():
            safe = copy.deepcopy(auto)
            logs = existing_sess.automation_logs.get(auto_id, [])[:20]
            emit("automation_update", {"automation": safe, "logs": logs})
        _emit_admin_stats()
        return

    # 3. Validate credentials with CADIO
    success = cadio_login(email, password)
    if not success:
        emit("mqtt_status", {"connected": False, "message": "Cadio Login Failed (Check email/password)"})
        return
    
    # Create a database-backed session
    import db
    session_token = db.create_session(email, request.headers.get("User-Agent"))
    session['user_session_token'] = session_token
        
    # 4. Register socket and join user's private room
    _user_sessions[request.sid] = email
    sess = session_mgr.create_session(
        email, password,
        broker=MQTT_BROKER, port=MQTT_PORT, discovery_prefix=DISCOVERY_PREFIX
    )
    session_mgr.register_socket(request.sid, email)
    join_room(sess.room)
    
    # 5. Save valid user to DB
    db.save_user(email, password)
    db.unblock_user(email)
    
    # 5. Load automations into the session
    _load_session_automations(sess, email)
    
    # 6. Connect session's MQTT client (if not already connected)
    if not sess.mqtt_connected:
        sess.start_mqtt(socketio)
    else:
        # Already connected (e.g. second tab) — send current state
        emit("mqtt_status", {"connected": True, "message": "Connected"})
        for topic, data in sess.device_states.items():
            emit("device_update", {"topic": topic, **data})
    
    # 7. Send automation state to this client
    for auto_id, auto in sess.automations.items():
        safe = copy.deepcopy(auto)
        logs = sess.automation_logs.get(auto_id, [])[:20]
        emit("automation_update", {"automation": safe, "logs": logs})

    _emit_admin_stats()





@socketio.on("publish")
def handle_publish(data):
    topic = data.get("topic", "")
    payload = data.get("payload", "")
    session = session_mgr.get_session_by_sid(request.sid)
    if session and session.mqtt_client and session.mqtt_connected and topic:
        session.mqtt_client.publish(topic, payload)
        emit("publish_ack", {"topic": topic, "payload": payload, "ok": True})
    else:
        emit("publish_ack", {"ok": False, "error": "MQTT not connected"})


@socketio.on("subscribe")
def handle_subscribe(data):
    topic = data.get("topic", "#")
    session = session_mgr.get_session_by_sid(request.sid)
    if session and session.mqtt_client and session.mqtt_connected:
        session.mqtt_client.subscribe(topic)


@socketio.on("get_history")
def handle_get_history(data):
    topic = data.get("topic", "")
    session = session_mgr.get_session_by_sid(request.sid)
    if session:
        history = session.sensor_history.get(topic, [])
    else:
        history = sensor_history.get(topic, [])
    emit("sensor_history", {"topic": topic, "history": history})


# ---------------------------------------------------------------------------
# Irrigation Automation Engine
# ---------------------------------------------------------------------------

def _get_auto_now(auto):
    """Return the current datetime adjusted for the automation's utcOffset.
    JS getTimezoneOffset() returns positive values for west of UTC (e.g. UTC-5 = 300).
    We store the same convention: utcOffset in minutes, where UTC+5:30 = -330.
    Formula: local_time = utc_time - offset_in_minutes.
    Falls back to server local time if no offset is configured."""
    sched = auto.get("schedule", {}) if auto else {}
    utc_offset_mins = sched.get("utcOffset")
    if utc_offset_mins is not None:
        try:
            offset = int(utc_offset_mins)
            return datetime.utcnow() - timedelta(minutes=offset)
        except (ValueError, TypeError):
            pass
    return datetime.now()


def _auto_log(auto_id, message, level="info"):
    """Append a timestamped log entry for an automation (timezone-aware).
    Routes to sess logs if the automation belongs to a sess user."""
    # Determine log target (sess or global)
    auto = None
    sess = None
    # Check sessions first
    for email, s in list(session_mgr._sessions.items()):
        if auto_id in s.automations:
            auto = s.automations[auto_id]
            sess = s
            break
    # Fallback to global
    if not auto:
        auto = automations.get(auto_id)

    now = _get_auto_now(auto)
    entry = {"ts": now.isoformat(), "msg": message, "level": level}

    if sess:
        if auto_id not in sess.automation_logs:
            sess.automation_logs[auto_id] = []
        sess.automation_logs[auto_id].insert(0, entry)
        if len(sess.automation_logs[auto_id]) > MAX_AUTO_LOG:
            sess.automation_logs[auto_id] = sess.automation_logs[auto_id][:MAX_AUTO_LOG]
    else:
        if auto_id not in automation_logs:
            automation_logs[auto_id] = []
        automation_logs[auto_id].insert(0, entry)
        if len(automation_logs[auto_id]) > MAX_AUTO_LOG:
            automation_logs[auto_id] = automation_logs[auto_id][:MAX_AUTO_LOG]
    logging.info(f"[AUTO {auto_id}] {message}")


def _new_runtime(old_rt=None):
    """Return a fresh runtime block, preserving history if old_rt is provided."""
    rt = {
        "state": "IDLE",
        "currentActionIndex": 0,
        "timerStart": None,
        "remainingTime": None,
        "retryCount": 0,
        "pauseReason": None,
        "verifyStart": None,
        "bufferStart": None,
        "currentInitIndex": 0,
    }
    if old_rt:
        for k in ["cycles_today", "cycles_date", "cycles_history", "duration_history", "last_irrigated"]:
            if k in old_rt:
                rt[k] = old_rt[k]
    return rt


def _get_switch_state(switch_topic, auto=None):
    """Get the latest known state for a switch.
    Routes to the owning sess's device_states, falls back to global."""
    source = device_states  # default fallback
    if auto:
        owner = auto.get("_owner_email", "")
        sess = session_mgr.get_session(owner)
        if sess:
            source = sess.device_states
    data = source.get(switch_topic)
    if not data:
        return None
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload.get("state", "").upper()
    return str(payload).upper() if payload else None


def _mqtt_set_switch(cmd_topic, state, auto=None):
    """Publish a command to set a switch state.
    Routes to the owning sess's MQTT client, falls back to global."""
    if not cmd_topic:
        return
    payload = json.dumps({"state": state.upper()})
    if auto:
        owner = auto.get("_owner_email", "")
        sess = session_mgr.get_session(owner)
        if sess and sess.mqtt_client and sess.mqtt_connected:
            sess.mqtt_client.publish(cmd_topic, payload)
            logging.info(f"[ENGINE:{owner}] Published {payload} to {cmd_topic}")
            return
    # Legacy fallback
    if mqtt_client and mqtt_connected:
        mqtt_client.publish(cmd_topic, payload)
        logging.info(f"[ENGINE] Published {payload} to {cmd_topic}")


def evaluate_condition(auto):
    """Evaluate the condition expression using AND/OR logic.
    AND has higher precedence than OR (groups are formed by AND, then OR'd).
    Returns True if condition list is empty."""
    conditions = auto.get("condition", [])
    if not conditions:
        return True

    # Build results list with logic operators
    results = []
    for cond in conditions:
        sensor_topic = cond.get("sensorStateTopic", "")
        expected = str(cond.get("value", "")).upper()
        actual = _get_switch_state(sensor_topic, auto)
        matched = actual == expected if actual is not None else False
        results.append({"matched": matched, "logic": cond.get("logic")})

    # Evaluate: AND groups first, then OR between groups
    # Split into OR-separated groups of AND-connected conditions
    or_groups = []
    current_group = [results[0]["matched"]]
    for i in range(1, len(results)):
        prev_logic = results[i - 1].get("logic", "AND")
        if prev_logic == "OR":
            or_groups.append(current_group)
            current_group = [results[i]["matched"]]
        else:  # AND
            current_group.append(results[i]["matched"])
    or_groups.append(current_group)

    # Each group must have ALL true (AND), then any group true (OR)
    return any(all(g) for g in or_groups)


def check_schedule(auto):
    """Check if current day+time falls within the schedule window.
    Returns True if no schedule is defined."""
    sched = auto.get("schedule")
    if not sched:
        return True
        
    days = sched.get("days", [])
    if not days:
        return False  # No days selected -> Deactivated
        
    start_str = sched.get("startTime", "")
    end_str = sched.get("endTime", "")

    # Use the automation's timezone
    now = _get_auto_now(auto)

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    current_day = day_names[now.weekday()]
    if current_day not in days:
        return False

    if sched.get("is24hr"):
        return True

    ranges = sched.get("timeRanges", [])
    if not ranges:
        # Fallback for old single range format
        s_start = sched.get("startTime", "")
        s_end = sched.get("endTime", "")
        if not s_start or not s_end:
            return True
        ranges = [{"start": s_start, "end": s_end}]

    now_mins = now.hour * 60 + now.minute

    for r in ranges:
        start_str = r.get("start", "")
        end_str = r.get("end", "")
        if not start_str or not end_str:
            continue
        try:
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            start_mins = start_h * 60 + start_m
            end_mins = end_h * 60 + end_m

            if start_mins <= end_mins:
                # Normal range
                if start_mins <= now_mins < end_mins:
                    return True
            else:
                # Overnight range
                if now_mins >= start_mins or now_mins < end_mins:
                    return True
        except (ValueError, AttributeError):
            continue

    return False


def _verify_switches(switch_list, auto):
    """Check if all switches in list match their expected state.
    switch_list: list of {switchCmdTopic, switchStateTopic, state}"""
    for item in switch_list:
        state_topic = item.get("switchStateTopic", "")
        expected = item.get("state", "").upper()
        actual = _get_switch_state(state_topic, auto)
        if actual != expected:
            return False
    return True


def _emit_auto_update(auto):
    """Broadcast automation state update to the owning user's room."""
    safe = copy.deepcopy(auto)
    auto_id = safe.get("id", "")
    owner_email = auto.get("_owner_email", "")
    # Try sess logs first, fallback to global
    sess = session_mgr.get_session(owner_email) if owner_email else None
    if sess and auto_id in sess.automation_logs:
        logs = sess.automation_logs.get(auto_id, [])[:20]
    else:
        logs = automation_logs.get(auto_id, [])[:20]
    # Emit to user's room if available, otherwise broadcast (legacy)
    if sess:
        socketio.emit("automation_update", {"automation": safe, "logs": logs}, room=sess.room)
    else:
        socketio.emit("automation_update", {"automation": safe, "logs": logs})


def engine_tick(auto):
    """Execute one tick of the state machine for an automation."""
    rt = auto["runtime"]
    state = rt["state"]
    auto_id = auto["id"]
    now = time.time()

    # Priority 1: If status is OFF, go IDLE immediately
    if auto.get("status") != "ON":
        if state != "IDLE":
            rt["state"] = "IDLE"
            rt["currentActionIndex"] = 0
            rt["timerStart"] = None
            rt["remainingTime"] = None
            rt["retryCount"] = 0
            rt["pauseReason"] = None
            _auto_log(auto_id, "Status OFF → IDLE")
            _emit_auto_update(auto)
        return

    bg_unverified = False
    if state not in ("ERROR", "ERROR_SET", "ERROR_VERIFY", "IDLE", "INIT_SET", "INIT_VERIFY_INDIVIDUAL", "INIT_VERIFY_ALL", "OVERLAP_NEXT_SET", "OVERLAP_NEXT_VERIFY") and not auto.get("isPaused"):
        # --- Background Enforce (Set if True / Set if False) ---
        sched_is_true = check_schedule(auto)
        sched_cfg = auto.get("schedule", {})
        
        enforce_list = sched_cfg.get("setIfTrue", []) if sched_is_true else sched_cfg.get("setIfFalse", [])
        
        for item in enforce_list:
            topic = item.get("switchCmdTopic", "")
            last_sent_key = f"_last_sent_{topic}"
            retry_key = f"_retry_{topic}"

            if not _verify_switches([item], auto):
                bg_unverified = True
                # VERIFY_TIMEOUT is used to prevent spamming
                if now - rt.get(last_sent_key, 0) > VERIFY_TIMEOUT:
                    retries = rt.get(retry_key, 0)
                    if retries >= MAX_RETRIES:
                        rt["state"] = "ERROR_SET"
                        _auto_log(auto_id, f"Scheduler enforce failed for {item.get('switchName')} after {MAX_RETRIES} retries → ERROR_SET", "error")
                        _emit_auto_update(auto)
                        return

                    _mqtt_set_switch(topic, item.get("state", ""), auto)
                    rt[last_sent_key] = now
                    rt[retry_key] = retries + 1
                    level = "info" if retries == 0 else "warning"
                    _auto_log(auto_id, f"Scheduler background enforce: {item.get('switchName')} → {item.get('state')} (Attempt {retries + 1}/{MAX_RETRIES})", level)
                    _emit_auto_update(auto)
            else:
                if retry_key in rt:
                    rt.pop(retry_key, None)
                if last_sent_key in rt:
                    rt.pop(last_sent_key, None)

    # Priority 1.5: Enforce Pause
    if bg_unverified:
        if state != "PAUSED_ENFORCE" and state != "IDLE" and state != "ERROR" and state != "PAUSED_USER":
            rt["prePauseEnforce"] = state
            # freeze timers
            if "timerStart" in rt and rt["timerStart"]:
                elapsed = now - rt["timerStart"]
                rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
                rt["timerStart"] = None
            if "bufferStart" in rt and rt["bufferStart"]:
                elapsed = now - rt["bufferStart"]
                rt["remainingBuffer"] = max(0, auto.get("bufferTime", BUFFER_SECONDS) - elapsed)
                rt["bufferStart"] = None
            if "verifyStart" in rt and rt["verifyStart"]:
                elapsed = now - rt["verifyStart"]
                rt["remainingVerify"] = max(0, VERIFY_TIMEOUT - elapsed)
                rt["verifyStart"] = None
                
            rt["state"] = "PAUSED_ENFORCE"
            _auto_log(auto_id, "Pausing main sequence to enforce schedule")
            _emit_auto_update(auto)
        return

    # Priority 1.6: Enforce Resume
    if not bg_unverified and state == "PAUSED_ENFORCE":
        rt["state"] = rt.get("prePauseEnforce", "IDLE")
        # resume timers
        if "remainingTime" in rt and rt["remainingTime"] is not None:
            rt["timerStart"] = now
        if "remainingBuffer" in rt and rt["remainingBuffer"] is not None:
            rt["bufferStart"] = now - (auto.get("bufferTime", BUFFER_SECONDS) - rt["remainingBuffer"])
            del rt["remainingBuffer"]
        if "remainingVerify" in rt and rt["remainingVerify"] is not None:
            rt["verifyStart"] = now - (VERIFY_TIMEOUT - rt["remainingVerify"])
            del rt["remainingVerify"]
        _auto_log(auto_id, f"Schedule verified → Resuming {rt['state']}")
        _emit_auto_update(auto)

    # Priority 2: User Pause
    if auto.get("isPaused"):
        if state != "PAUSED_USER" and state != "IDLE" and state != "ERROR":
            rt["prePauseUser"] = state
            # freeze timers
            if "timerStart" in rt and rt["timerStart"]:
                elapsed = now - rt["timerStart"]
                rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
                rt["timerStart"] = None
            if "bufferStart" in rt and rt["bufferStart"]:
                elapsed = now - rt["bufferStart"]
                rt["remainingBuffer"] = max(0, auto.get("bufferTime", BUFFER_SECONDS) - elapsed)
                rt["bufferStart"] = None
            if "verifyStart" in rt and rt["verifyStart"]:
                elapsed = now - rt["verifyStart"]
                rt["remainingVerify"] = max(0, VERIFY_TIMEOUT - elapsed)
                rt["verifyStart"] = None
                
            rt["state"] = "PAUSED_USER"
            _auto_log(auto_id, "User paused the automation")
            _emit_auto_update(auto)
        return

    # Priority 3: User Resume
    if not auto.get("isPaused") and state == "PAUSED_USER":
        rt["state"] = rt.get("prePauseUser", "IDLE")
        # resume timers
        if "remainingTime" in rt and rt["remainingTime"] is not None:
            rt["timerStart"] = now
        if "remainingBuffer" in rt and rt["remainingBuffer"] is not None:
            rt["bufferStart"] = now - (auto.get("bufferTime", BUFFER_SECONDS) - rt["remainingBuffer"])
            del rt["remainingBuffer"]
        if "remainingVerify" in rt and rt["remainingVerify"] is not None:
            rt["verifyStart"] = now - (VERIFY_TIMEOUT - rt["remainingVerify"])
            del rt["remainingVerify"]
        _auto_log(auto_id, f"User resumed → {rt['state']}")
        _emit_auto_update(auto)
        return

    # Priority 4: ERROR state — only RESET or OFF can exit
    if state == "ERROR":
        return

    # --- State transitions ---

    if state == "IDLE":
        # Status just turned ON → run initialization immediately
        rt["state"] = "INIT_SET"
        rt["retryCount"] = 0
        _auto_log(auto_id, "Status ON → INIT_SET (initialization runs unconditionally)")
        _emit_auto_update(auto)
        return

    if state == "WAIT_CONDITION":
        cond = evaluate_condition(auto)
        sched = check_schedule(auto)
        if cond and sched:
            # Check cycle limit before starting a new cycle
            max_cycles = auto.get("maxCyclesPerDay", 0)
            if max_cycles > 0:
                today_str = _get_auto_now(auto).strftime("%Y-%m-%d")
                cycles_today = rt.get("cycles_today", 0) if rt.get("cycles_date") == today_str else 0
                if cycles_today >= max_cycles:
                    if rt.get("_cycle_paused") != today_str:
                        rt["_cycle_paused"] = today_str
                        _auto_log(auto_id, f"Max cycles reached ({cycles_today}/{max_cycles}) — pausing until tomorrow")
                        _emit_auto_update(auto)
                    return
            rt["state"] = "ACTION_SET"
            rt["currentActionIndex"] = 0
            _auto_log(auto_id, "Condition satisfied + Schedule active → ACTION_SET")
            _emit_auto_update(auto)
        return

    if state == "INIT_SET":
        inits = auto.get("initialization", [])
        idx = rt.get("currentInitIndex", 0)
        
        if not inits:
            rt["state"] = "INIT_VERIFY_ALL"
            _emit_auto_update(auto)
            return
            
        if idx < len(inits):
            item = inits[idx]
            _mqtt_set_switch(item.get("switchCmdTopic", ""), item.get("state", "OFF"), auto)
            rt["verifyStart"] = now
            rt["state"] = "INIT_VERIFY_INDIVIDUAL"
            _auto_log(auto_id, f"Initialization {idx+1}/{len(inits)} sent → INIT_VERIFY_INDIVIDUAL")
            _emit_auto_update(auto)
        else:
            rt["verifyStart"] = now
            rt["state"] = "INIT_VERIFY_ALL"
            _auto_log(auto_id, "All individual initialization commands sent. Final bulk check → INIT_VERIFY_ALL")
            _emit_auto_update(auto)
        return

    if state == "INIT_VERIFY_INDIVIDUAL":
        inits = auto.get("initialization", [])
        idx = rt.get("currentInitIndex", 0)
        if idx < len(inits):
            item = inits[idx]
            if _verify_switches([item], auto):
                rt["currentInitIndex"] = idx + 1
                rt["state"] = "INIT_SET"
                rt["retryCount"] = 0
                _auto_log(auto_id, f"Initialization {idx+1}/{len(inits)} verified")
                _emit_auto_update(auto)
            elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
                rt["retryCount"] = rt.get("retryCount", 0) + 1
                if rt["retryCount"] >= MAX_RETRIES:
                    rt["state"] = "ERROR_SET"
                    _auto_log(auto_id, f"Init {idx+1}/{len(inits)} verification timeout → ERROR_SET", "error")
                    _emit_auto_update(auto)
                else:
                    rt["state"] = "INIT_SET"
                    _auto_log(auto_id, f"Init {idx+1}/{len(inits)} verify retry {rt['retryCount']}/{MAX_RETRIES}")
                    _emit_auto_update(auto)
        return

    if state == "INIT_VERIFY_ALL":
        inits = auto.get("initialization", [])
        if not inits or _verify_switches(inits, auto):
            if rt.get("loopingToFirst"):
                rt["bufferStart"] = now
                rt["state"] = "BUFFER"
                _auto_log(auto_id, "All initialization verified → BUFFER")
            else:
                rt["state"] = "WAIT_CONDITION"
                rt["retryCount"] = 0
                _auto_log(auto_id, "Initialization verified → WAIT_CONDITION (awaiting condition + schedule)")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                rt["state"] = "ERROR_SET"
                _auto_log(auto_id, "Bulk init verification timeout → ERROR_SET", "error")
                _emit_auto_update(auto)
            else:
                # Restart the sequential flow
                rt["currentInitIndex"] = 0
                rt["state"] = "INIT_SET"
                _auto_log(auto_id, f"Bulk init verify failed, restarting sequence! Retry {rt['retryCount']}/{MAX_RETRIES}", "warning")
                _emit_auto_update(auto)
        return

    if state == "ACTION_SET":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        if idx >= len(actions):
            rt["state"] = "COMPLETED"
            _auto_log(auto_id, "No more actions → COMPLETED")
            _emit_auto_update(auto)
            return
        action = actions[idx]
        _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", "ON"), auto)
        rt["verifyStart"] = now
        rt["state"] = "ACTION_VERIFY"
        _auto_log(auto_id, f"Action {idx+1}: Set {action.get('switchName','')} → {action.get('state','')}")
        _emit_auto_update(auto)
        return

    if state == "ACTION_VERIFY":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        action = actions[idx] if idx < len(actions) else {}
        if _verify_switches([action], auto):
            duration = action.get("duration", 0)
            rt["timerStart"] = now
            rt["remainingTime"] = duration
            rt["state"] = "ACTION_RUN"
            _auto_log(auto_id, f"Action {idx+1} verified → ACTION_RUN ({duration}s)")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                rt["state"] = "ERROR_SET"
                _auto_log(auto_id, f"Action {idx+1} verify timeout → ERROR_SET", "error")
                _emit_auto_update(auto)
            else:
                rt["state"] = "ACTION_SET"
                _auto_log(auto_id, f"Action {idx+1} verify retry {rt['retryCount']}/{MAX_RETRIES}")
                _emit_auto_update(auto)
        return

    if state == "ACTION_RUN":
        # Check for condition pause
        if not evaluate_condition(auto):
            elapsed = now - (rt.get("timerStart") or now)
            rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
            rt["pauseReason"] = "condition"
            rt["state"] = "PAUSED_CONDITION"
            _auto_log(auto_id, "Condition FALSE → PAUSED_CONDITION")
            _emit_auto_update(auto)
            return
        # Check for schedule pause
        if not check_schedule(auto):
            elapsed = now - (rt.get("timerStart") or now)
            rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed)
            rt["pauseReason"] = "schedule"
            rt["state"] = "PAUSED_SCHEDULE"
            _auto_log(auto_id, "Schedule ended → PAUSED_SCHEDULE")
            _emit_auto_update(auto)
            return
        # Check timer
        elapsed = now - (rt.get("timerStart") or now)
        remaining = (rt.get("remainingTime") or 0) - elapsed
        if remaining <= 0:
            actions = auto.get("actions", [])
            idx = rt.get("currentActionIndex", 0)
            if idx + 1 < len(actions):
                rt["state"] = "OVERLAP_NEXT_SET"
                rt["retryCount"] = 0
                _auto_log(auto_id, f"Action {idx+1} timer done → OVERLAP_NEXT_SET")
            else:
                # We reached the end of all actions. Increment cycle count first.
                max_cycles = auto.get("maxCyclesPerDay", 0)
                today_str = _get_auto_now(auto).strftime("%Y-%m-%d")
                if rt.get("cycles_date") != today_str:
                    rt["cycles_date"] = today_str
                    rt["cycles_today"] = 0
                rt["cycles_today"] = rt.get("cycles_today", 0) + 1
                
                # Track history independently of the resettable cycles_today
                if "cycles_history" not in rt:
                    rt["cycles_history"] = {}
                if "duration_history" not in rt:
                    rt["duration_history"] = {}
                    
                rt["cycles_history"][today_str] = rt["cycles_history"].get(today_str, 0) + 1
                
                # Add total cycle duration to today's history
                cycle_duration = sum(act.get("duration", 0) for act in auto.get("actions", []))
                rt["duration_history"][today_str] = rt["duration_history"].get(today_str, 0) + cycle_duration

                # Prune old history
                for h_key in ["cycles_history", "duration_history"]:
                    sorted_dates = sorted(rt[h_key].keys())
                    while len(sorted_dates) > 7:
                        del rt[h_key][sorted_dates.pop(0)]
                rt["last_irrigated"] = _get_auto_now(auto).strftime("%Y-%m-%d %H:%M")

                cycles_today_val = rt.get("cycles_today", 0)
                cycle_limit_reached = max_cycles > 0 and cycles_today_val >= max_cycles

                # Can we loop?
                sched = check_schedule(auto)
                if sched and len(actions) > 1 and not cycle_limit_reached:
                    rt["loopingToFirst"] = True
                    rt["stopAfterRevert"] = False
                    rt["state"] = "OVERLAP_NEXT_SET"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Cycle #{cycles_today_val} done → Init → Loop to Action 1")
                elif cycle_limit_reached:
                    rt["loopingToFirst"] = True
                    rt["stopAfterRevert"] = True
                    rt["state"] = "OVERLAP_NEXT_SET"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Cycle #{cycles_today_val} done → Max cycles ({max_cycles}/day) reached, init → revert → stop")
                else:
                    rt["loopingToFirst"] = False
                    rt["state"] = "ACTION_REVERT"
                    rt["retryCount"] = 0
                    _auto_log(auto_id, f"Cycle #{cycles_today_val} done → ACTION_REVERT")
            _emit_auto_update(auto)
            return
        # State enforcement: ensure switch is still in expected state
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        if idx < len(actions):
            action = actions[idx]
            if not _verify_switches([action], auto):
                # Freeze the action timer
                elapsed_so_far = now - (rt.get("timerStart") or now)
                rt["remainingTime"] = max(0, (rt.get("remainingTime") or 0) - elapsed_so_far)
                rt["timerStart"] = None
                # Send correction command and enter verify state
                _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", ""), auto)
                rt["driftRetryCount"] = 0
                rt["verifyStart"] = now
                rt["state"] = "ACTION_DRIFT_VERIFY"
                _auto_log(auto_id, f"Switch drift detected on Action {idx+1} — correcting", "warning")
                _emit_auto_update(auto)
        return

    if state == "ACTION_DRIFT_VERIFY":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        action = actions[idx] if idx < len(actions) else {}
        if _verify_switches([action], auto):
            # Switch corrected — resume ACTION_RUN with remaining time
            rt["timerStart"] = now
            rt["state"] = "ACTION_RUN"
            rt["driftRetryCount"] = 0
            _auto_log(auto_id, f"Drift corrected on Action {idx+1} — resuming")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > DRIFT_VERIFY_TIMEOUT:
            rt["driftRetryCount"] = rt.get("driftRetryCount", 0) + 1
            if rt["driftRetryCount"] >= MAX_RETRIES:
                rt["state"] = "ERROR_SET"
                _auto_log(auto_id, f"Action {idx+1} drift correction failed after {MAX_RETRIES} retries → ERROR_SET", "error")
                _emit_auto_update(auto)
            else:
                # Re-send and try again
                _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", ""), auto)
                rt["verifyStart"] = now
                _auto_log(auto_id, f"Drift correction retry {rt['driftRetryCount']}/{MAX_RETRIES} on Action {idx+1}", "warning")
                _emit_auto_update(auto)
        return

    if state == "OVERLAP_NEXT_SET":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        next_idx = (idx + 1) % len(actions)
        
        if next_idx == 0 and rt.get("loopingToFirst"):
            rt["currentInitIndex"] = 0
            rt["state"] = "INIT_SET"
            rt["retryCount"] = 0
            _auto_log(auto_id, "Looping: Starting sequential initialization")
            _emit_auto_update(auto)
            return
        else:
            action = actions[next_idx]
            _mqtt_set_switch(action.get("switchCmdTopic", ""), action.get("state", "ON"), auto)
            _auto_log(auto_id, f"Overlap Action {next_idx+1}: Set {action.get('switchName','')} → {action.get('state','')}")
            
        rt["verifyStart"] = now
        rt["state"] = "OVERLAP_NEXT_VERIFY"
        _emit_auto_update(auto)
        return

    if state == "OVERLAP_NEXT_VERIFY":
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        next_idx = (idx + 1) % len(actions)
        
        switches_to_verify = [actions[next_idx]]
            
        if _verify_switches(switches_to_verify, auto):
            rt["bufferStart"] = now
            rt["state"] = "BUFFER"
            _auto_log(auto_id, f"Overlap transition verified → BUFFER")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["retryCount"] = rt.get("retryCount", 0) + 1
            if rt["retryCount"] >= MAX_RETRIES:
                rt["state"] = "ERROR_SET"
                _auto_log(auto_id, f"Overlap Action {next_idx+1} verify timeout → ERROR_SET", "error")
                _emit_auto_update(auto)
            else:
                rt["state"] = "OVERLAP_NEXT_SET"
                _auto_log(auto_id, f"Overlap Action {next_idx+1} verify retry {rt['retryCount']}")
                _emit_auto_update(auto)
        return

    if state == "BUFFER":
        buffer_time = auto.get("bufferTime", BUFFER_SECONDS)
        if now - (rt.get("bufferStart") or now) >= buffer_time:
            rt["state"] = "ACTION_REVERT"
            rt["retryCount"] = 0
            _auto_log(auto_id, f"Buffer ({buffer_time}s) done → ACTION_REVERT")
            _emit_auto_update(auto)
            return
        # Enforce expected switch states during buffer
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        switches_to_enforce = []
        # Current action (still ON, not yet reverted)
        if idx < len(actions):
            switches_to_enforce.append(actions[idx])
        # Next action (overlap — already set ON)
        next_idx = (idx + 1) % len(actions)
        if next_idx != idx and next_idx < len(actions) and not rt.get("loopingToFirst"):
            switches_to_enforce.append(actions[next_idx])
        for sw in switches_to_enforce:
            if not _verify_switches([sw], auto):
                # Freeze buffer timer and correct
                elapsed_buf = now - (rt.get("bufferStart") or now)
                rt["remainingBuffer"] = max(0, buffer_time - elapsed_buf)
                rt["bufferStart"] = None
                _mqtt_set_switch(sw.get("switchCmdTopic", ""), sw.get("state", ""), auto)
                rt["driftRetryCount"] = 0
                rt["verifyStart"] = now
                rt["driftAction"] = sw
                rt["state"] = "BUFFER_DRIFT_VERIFY"
                _auto_log(auto_id, f"Buffer drift detected on {sw.get('switchName', '')} — correcting", "warning")
                _emit_auto_update(auto)
                return
        return

    if state == "BUFFER_DRIFT_VERIFY":
        sw = rt.get("driftAction", {})
        if _verify_switches([sw], auto):
            # Corrected — resume buffer with remaining time
            rt["bufferStart"] = now - (auto.get("bufferTime", BUFFER_SECONDS) - (rt.get("remainingBuffer") or 0))
            rt.pop("remainingBuffer", None)
            rt.pop("driftAction", None)
            rt["driftRetryCount"] = 0
            rt["state"] = "BUFFER"
            _auto_log(auto_id, f"Buffer drift corrected on {sw.get('switchName', '')} — resuming")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > DRIFT_VERIFY_TIMEOUT:
            rt["driftRetryCount"] = rt.get("driftRetryCount", 0) + 1
            if rt["driftRetryCount"] >= MAX_RETRIES:
                rt.pop("driftAction", None)
                rt["state"] = "ERROR_SET"
                _auto_log(auto_id, f"Buffer drift correction failed after {MAX_RETRIES} retries → ERROR_SET", "error")
                _emit_auto_update(auto)
            else:
                _mqtt_set_switch(sw.get("switchCmdTopic", ""), sw.get("state", ""), auto)
                rt["verifyStart"] = now
                _auto_log(auto_id, f"Buffer drift retry {rt['driftRetryCount']}/{MAX_RETRIES} on {sw.get('switchName', '')}", "warning")
                _emit_auto_update(auto)
        return

    if state == "ACTION_REVERT":
        # Send the OPPOSITE state to revert the switch
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        if idx < len(actions):
            action = actions[idx]
            revert_state = "OFF" if action.get("state", "").upper() == "ON" else "ON"
            _mqtt_set_switch(action.get("switchCmdTopic", ""), revert_state, auto)
            _auto_log(auto_id, f"Action {idx+1}: Reverting {action.get('switchName','')} → {revert_state}")
        rt["verifyStart"] = now
        rt["state"] = "ACTION_VERIFY_REVERT"
        _emit_auto_update(auto)
        return

    if state == "ACTION_VERIFY_REVERT":
        # Verify the switch reverted to opposite state
        actions = auto.get("actions", [])
        idx = rt.get("currentActionIndex", 0)
        
        def _finish_revert():
            if rt.get("loopingToFirst"):
                rt["loopingToFirst"] = False
                if rt.pop("stopAfterRevert", False):
                    # Max cycles reached — stop here
                    rt["currentActionIndex"] = 0
                    rt["state"] = "COMPLETED"
                    _auto_log(auto_id, f"Max cycles done → COMPLETED (stopping)")
                else:
                    # Loop back for another cycle
                    rt["currentActionIndex"] = 0
                    rt["state"] = "ACTION_SET"
                    _auto_log(auto_id, "Init + Revert complete → Starting next cycle (Action 1)")
            elif idx + 1 < len(actions):
                # Make-Before-Break finished. Advance to next action and start its timer.
                next_idx = idx + 1
                rt["currentActionIndex"] = next_idx
                duration = actions[next_idx].get("duration", 0)
                rt["timerStart"] = now
                rt["remainingTime"] = duration
                rt["state"] = "ACTION_RUN"
                rt["retryCount"] = 0
                _auto_log(auto_id, f"Advanced to Action {next_idx+1} → ACTION_RUN ({duration}s)")
            else:
                rt["state"] = "COMPLETED"
                _auto_log(auto_id, "All actions completed → COMPLETED")
            _emit_auto_update(auto)

        if idx < len(actions):
            action = actions[idx]
            revert_state = "OFF" if action.get("state", "").upper() == "ON" else "ON"
            revert_check = {"switchStateTopic": action.get("switchStateTopic", ""), "state": revert_state}
            if _verify_switches([revert_check], auto):
                _auto_log(auto_id, f"Action {idx+1} revert verified")
                _finish_revert()
            elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
                _auto_log(auto_id, f"Action {idx+1} revert verify timeout", "warning")
                _finish_revert()
        else:
            _finish_revert()
        return

    if state == "PAUSED_CONDITION":
        if evaluate_condition(auto):
            rt["timerStart"] = now
            rt["pauseReason"] = None
            rt["state"] = "ACTION_RUN"
            _auto_log(auto_id, "Condition TRUE → resume ACTION_RUN")
            _emit_auto_update(auto)
        return

    if state == "PAUSED_SCHEDULE":
        if check_schedule(auto):
            rt["timerStart"] = now
            rt["pauseReason"] = None
            rt["state"] = "ACTION_RUN"
            _auto_log(auto_id, "Schedule active → resume ACTION_RUN")
            _emit_auto_update(auto)
        return

    if state == "COMPLETED":
        rt["currentActionIndex"] = 0
        rt["timerStart"] = None
        rt["remainingTime"] = None
        rt["state"] = "WAIT_CONDITION"
        _auto_log(auto_id, f"Completed → WAIT_CONDITION (cycle #{rt.get('cycles_today', 0)} today)")
        _emit_auto_update(auto)
        return

    if state == "ERROR_SET":
        err_states = auto.get("errorState", [])
        for item in err_states:
            _mqtt_set_switch(item.get("switchCmdTopic", ""), item.get("state", "OFF"), auto)
        rt["verifyStart"] = now
        rt["state"] = "ERROR_VERIFY"
        _auto_log(auto_id, "Error state commands sent → ERROR_VERIFY", "error")
        _emit_auto_update(auto)
        return

    if state == "ERROR_VERIFY":
        err_states = auto.get("errorState", [])
        if not err_states or _verify_switches(err_states, auto):
            rt["state"] = "ERROR"
            _auto_log(auto_id, "Error state verified → ERROR (locked)", "error")
            _emit_auto_update(auto)
        elif now - (rt.get("verifyStart") or now) > VERIFY_TIMEOUT:
            rt["state"] = "ERROR"
            _auto_log(auto_id, "Error verify timeout → ERROR (locked)", "error")
            _emit_auto_update(auto)
        return


def _engine_loop():
    """Background loop that ticks every automation across all sessions."""
    global _engine_running
    _engine_running = True
    logging.info("[ENGINE] Automation engine started")
    _last_db_save = time.time()
    while _engine_running:
        # Tick all session-based automations (multi-tenant)
        for session, auto_id, auto in session_mgr.get_all_automations():
            try:
                engine_tick(auto)
            except Exception as e:
                logging.error(f"[ENGINE] Error in {auto_id} (user={session.email}): {e}")
        # Periodic DB save every 60 seconds
        if time.time() - _last_db_save > 60:
            try:
                import db
                for email, sess in list(session_mgr._sessions.items()):
                    db.save_all_runtimes(sess.automations)
            except Exception as e:
                logging.error(f"[ENGINE] Periodic DB save failed: {e}")
            _last_db_save = time.time()
        time.sleep(ENGINE_INTERVAL)
    logging.info("[ENGINE] Automation engine stopped")


def start_engine():
    """Start the automation engine background thread."""
    global _engine_thread, _engine_running
    if _engine_thread and _engine_thread.is_alive():
        return
    _engine_running = True
    _engine_thread = threading.Thread(target=_engine_loop, daemon=True)
    _engine_thread.start()
    # Start AI scheduler thread alongside the engine
    _ai_thread = threading.Thread(target=_ai_scheduler_loop, daemon=True)
    _ai_thread.start()
    # Pre-load AI model in background so it's ready when needed
    _preload_ai_model()

def _preload_ai_model():
    """Pre-load the AI model in a background thread so it's ready when needed."""
    def _load():
        try:
            from ai_agent import refresh_client, is_model_loaded
            logging.info("[AI-PRELOAD] Initializing Gemini API client...")
            refresh_client()
            if is_model_loaded():
                logging.info("[AI-PRELOAD] Gemini API client ready!")
            else:
                logging.warning("[AI-PRELOAD] Gemini API client not ready - will retry on first AI call")
        except Exception as e:
            logging.warning(f"[AI-PRELOAD] Could not pre-load AI client: {e}")
    
    # Start in background thread so it doesn't block engine startup
    threading.Thread(target=_load, daemon=True, name="AI-Preload").start()


def stop_engine():
    """Stop the automation engine."""
    global _engine_running
    _engine_running = False


# ---------------------------------------------------------------------------
# AI Scheduler — background thread
# ---------------------------------------------------------------------------

def _ai_scheduler_loop():
    """Background thread: checks once per minute if it's time to run the AI."""
    AI_RUN_HOUR = 2  # Run at 2:00 AM in each automation's timezone
    logging.info("[AI-SCHEDULER] AI scheduler thread started")
    while _engine_running:
        try:
            # 1. Daily scheduled run at 2:00 AM (per-automation timezone)
            for session, auto_id, auto in session_mgr.get_all_automations():
                sched = auto.get("schedule", {})
                # Only trigger 2 AM run if AI is enabled AND automation is ON
                if not sched.get("ai_enabled") or auto.get("status") != "ON":
                    continue
                auto_now = _get_auto_now(auto)
                auto_today = auto_now.strftime("%Y-%m-%d")
                last_run = auto.get("_ai_last_run_date")
                if auto_now.hour == AI_RUN_HOUR and last_run != auto_today:
                    auto["_ai_last_run_date"] = auto_today
                    logging.info(f"[AI-SCHEDULER] 2AM triggered for '{auto_id}' (tz-aware)")
                    socketio.start_background_task(_run_ai_for_automation, auto_id)
                
            # 2. Dynamic automatic retry for failed runs
            now_ts = time.time()
            for session, auto_id, auto in session_mgr.get_all_automations():
                sched = auto.get("schedule", {})
                
                # If turned OFF or AI disabled, clear any pending retries
                if auto.get("status") == "OFF" or not sched.get("ai_enabled"):
                    auto.pop("ai_last_fail", None)
                    auto.pop("ai_retry_delay", None)
                    auto.pop("ai_fail_count", None)
                    continue
                
                # If manually Paused, don't trigger retries yet (wait for resume)
                if auto.get("isPaused"):
                    continue

                fail_ts = auto.get("ai_last_fail")
                retry_delay = auto.get("ai_retry_delay", 1800)
                if fail_ts and (now_ts - fail_ts) >= retry_delay:
                    retry_mins = math.ceil(retry_delay / 60)
                    suffix = "th"
                    if retry_mins % 10 == 1 and retry_mins % 100 != 11: suffix = "st"
                    elif retry_mins % 10 == 2 and retry_mins % 100 != 12: suffix = "nd"
                    elif retry_mins % 10 == 3 and retry_mins % 100 != 13: suffix = "rd"
                    
                    logging.info(f"[AI-SCHEDULER] {retry_mins}{suffix}-minute retry triggered for '{auto_id}'")
                    
                    # Temporarily clear the flags so we don't trigger it again immediately
                    auto.pop("ai_last_fail", None) 
                    auto.pop("ai_retry_delay", None)
                    
                    # Run it (this will re-set the flag if it fails again)
                    socketio.start_background_task(_run_ai_for_automation, auto_id)

        except Exception as e:
            logging.error(f"[AI-SCHEDULER] Error: {e}")
        time.sleep(60)  # check every minute


def _run_ai_for_automation(auto_id):
    """Helper to run the AI engine for a single automation."""
    global _ai_running_set
    
    # Prevent duplicate concurrent runs
    if auto_id in _ai_running_set:
        logging.info(f"[AI-SCHEDULER] AI already running for '{auto_id}', skipping duplicate request")
        return
    
    _ai_running_set.add(auto_id)
    
    try:
        from ai_agent import get_weather_data, build_automation_context, get_ai_schedule_decision, is_model_loaded, is_model_loading, refresh_client
    except ImportError as e:
        logging.error(f"[AI-SCHEDULER] Could not import ai_agent module: {e}")
        session, auto = _find_automation(auto_id)
        if auto:
            _auto_log(auto_id, f"AI error: module import failed - {e}", level="error")
            _emit_auto_update(auto)
        _ai_running_set.discard(auto_id)
        return

    session, auto = _find_automation(auto_id)
    if not auto:
        _ai_running_set.discard(auto_id)
        return

    sched = auto.get("schedule", {})
    if not sched.get("ai_enabled"):
        _ai_running_set.discard(auto_id)
        return

    # Ensure the AI client is loaded with THIS automation's owner's API key
    owner_email = auto.get("_owner_email", MQTT_USERNAME)
    refresh_client(owner_email)
    
    if not is_model_loaded():
        _auto_log(auto_id, "AI skipped: AI Model not loaded or API key invalid.", level="error")
        _emit_auto_update(auto)
        _ai_running_set.discard(auto_id)
        return

    lat = sched.get("lat")
    lon = sched.get("lon")
    if not lat or not lon:
        _auto_log(auto_id, "AI skipped: no Lat/Lon configured", level="warn")
        _emit_auto_update(auto)
        _ai_running_set.discard(auto_id)
        return

    logging.info(f"[AI-SCHEDULER] Running AI for '{auto.get('name', auto_id)}'")
    try:
        import db as _db
        _api_settings = _db.get_api_settings(owner_email)
        _key_label = "personal" if _api_settings.get("api_mode") == "custom" else "shared"
    except Exception:
        _key_label = "shared"
    _auto_log(auto_id, f"🤖 ({_key_label}) AI Agent starting...")
    _emit_auto_update(auto) # force UI update to show log

    try:
        weather_data = get_weather_data(lat=lat, lon=lon)
        if not weather_data:
            _auto_log(auto_id, "AI failed: could not fetch weather", level="error")
            auto["ai_last_fail"] = datetime.now().timestamp()
            _emit_auto_update(auto)
            _ai_running_set.discard(auto_id)
            return

        ctx = build_automation_context(auto_id, auto)
        decision = get_ai_schedule_decision(weather_data, ctx)
        if not decision:
            _auto_log(auto_id, "AI failed: model returned no decision", level="error")
            auto["ai_last_fail"] = datetime.now().timestamp()
            _emit_auto_update(auto)
            _ai_running_set.discard(auto_id)
            return

        new_days = decision.get("selected_days", [])
        reasoning = decision.get("reasoning", "")
        old_days = sched.get("days", [])

        # SAFETY: If we are currently RUNNING or WORKING on an action, 
        # ensure today stays in the schedule so we don't stop mid-cycle.
        rt = auto.get("runtime", {})
        if rt.get("state") not in ("IDLE", "ERROR"):
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            today_name = day_names[_get_auto_now(auto).weekday()]
            if today_name not in new_days:
                new_days.append(today_name)
                reasoning += f" (Note: Today was kept in schedule because a cycle is currently active.)"

        # Assign the days back to the global reference to guarantee persistence
        if "schedule" not in auto:
            auto["schedule"] = {}
        auto["schedule"]["days"] = new_days
        
        # Clear any previous failure flags on success
        auto.pop("ai_last_fail", None)
        auto.pop("ai_fail_count", None)
        
        _auto_log(auto_id, f"🤖 AI updated days: {old_days} → {new_days}")
        _auto_log(auto_id, f"🤖 Reasoning: {reasoning}")
        logging.info(f"[AI-SCHEDULER] '{auto.get('name')}': {old_days} → {new_days} | {reasoning}")
        
        # Deep Sync: Send specific log message AND full update
        owner_email = auto.get("_owner_email", MQTT_USERNAME)
        owner_session = session_mgr.get_session(owner_email)
        if owner_session:
            socketio.emit("log_message", {"entity": auto.get("name"), "state": "AI Schedule Updated"}, room=owner_session.room)
        else:
            socketio.emit("log_message", {"entity": auto.get("name"), "state": "AI Schedule Updated"})
        _emit_auto_update(auto) 

    except Exception as e:
        error_msg = str(e)
        logging.error(f"[AI-SCHEDULER] Error running AI for '{auto.get('name', auto_id)}': {error_msg}")
        
        # Track consecutive failures
        fail_count = auto.get("ai_fail_count", 0) + 1
        auto["ai_fail_count"] = fail_count

        # Determine retry delay based on attempt number
        retry_delay = 1800  # default for 3rd+ failure
        
        if fail_count >= 3:
            # Strike 3: wait 30 mins
            retry_delay = 1800
            _auto_log(auto_id, f"AI error (Attempt {fail_count}/3): Multiple failures. Falling back to 30-min retry.", level="error")
            auto["ai_fail_count"] = 0 # Reset count for the next cycle after 30 mins
        else:
            match = re.search(r'Please retry in ([\d\.]+)s', error_msg)
            if match:
                try:
                    seconds = float(match.group(1))
                    # Staggered buffers: +1m for first fail, +5m for second fail
                    buffer = 60 if fail_count == 1 else 300
                    retry_delay = seconds + buffer
                    _auto_log(auto_id, f"AI error (Attempt {fail_count}/3): Quota exceeded. Retrying in ~{math.ceil(retry_delay/60)} mins.", level="error")
                except ValueError:
                    _auto_log(auto_id, f"AI error (Attempt {fail_count}/3): {error_msg[:100]}", level="error")
            else:
                # If no specific time requested, use standard 30m
                _auto_log(auto_id, f"AI error (Attempt {fail_count}/3): {error_msg[:100]}", level="error")
            
        auto["ai_last_fail"] = time.time()
        auto["ai_retry_delay"] = retry_delay
    finally:
        _emit_auto_update(auto)
        _ai_running_set.discard(auto_id)


def _run_ai_for_all_automations():
    """Run the AI agent for every automation that has ai_enabled=True."""
    for session, auto_id, auto in session_mgr.get_all_automations():
        _run_ai_for_automation(auto_id)


# ---------------------------------------------------------------------------
# Automation SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("run_ai_now")
def handle_run_ai_now(data):
    """Immediately trigger the AI for a specific automation (called from UI)."""
    auto_id = data.get("id")
    if _find_automation(auto_id):
        socketio.start_background_task(_run_ai_for_automation, auto_id)

@socketio.on("get_automations")
def handle_get_automations():
    """Send all automations to the client."""
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess:
        emit("automations_list", [])
        return
    result = []
    for auto_id, auto in sess.automations.items():
        safe = copy.deepcopy(auto)
        safe["logs"] = sess.automation_logs.get(auto_id, [])[:20]
        result.append(safe)
    emit("automations_list", result)


# ---------------------------------------------------------------------------
# API Settings events (per-user, stored encrypted in DB)
# ---------------------------------------------------------------------------
from ai_agent import refresh_client

@socketio.on("get_api_settings")
def handle_get_api_settings():
    import db
    from ai_agent import GEMINI_API_KEY
    settings = db.get_api_settings(_get_user_email())
    # Mask key for safety
    safe_settings = copy.deepcopy(settings)
    if safe_settings.get("custom_api_key"):
        key = safe_settings["custom_api_key"]
        safe_settings["custom_api_key"] = key[:4] + "*" * (len(key)-8) + key[-4:] if len(key) > 8 else "****"
    
    # Add info about whether the system has a shared key
    safe_settings["has_shared_key"] = bool(GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE")
    emit("api_settings", safe_settings)

@socketio.on("update_api_settings")
def handle_update_api_settings(data):
    import db
    import copy
    user_email = _get_user_email()
    logging.info(f"[API_SETTINGS] Saving API key for resolved user_email: {user_email}")
    current = db.get_api_settings(user_email)
    
    # If key is masked (contains *), keep the old one
    new_key = data.get("custom_api_key", "")
    if "*" in new_key:
        new_key = current.get("custom_api_key", "")
    
    api_mode = data.get("api_mode", "default")
    db.save_api_settings(user_email, api_mode, new_key)
    
    refresh_client(user_email)
    
    # Emit the updated settings directly back to the client
    updated_settings = db.get_api_settings(user_email)
    safe_settings = copy.deepcopy(updated_settings)
    if safe_settings.get("custom_api_key"):
        key = safe_settings["custom_api_key"]
        safe_settings["custom_api_key"] = key[:4] + "*" * (len(key)-8) + key[-4:] if len(key) > 8 else "****"
    emit("api_settings", safe_settings)
    
    emit("log_message", {"entity": "System", "state": "API Settings Updated"})


@socketio.on("create_automation")
def handle_create_automation(data):
    """Create a new automation."""
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess:
        emit("automation_error", {"error": "Not logged in"})
        return
    auto_id = str(uuid.uuid4())[:8]
    auto = {
        "id": auto_id,
        "name": data.get("name", "New Automation"),
        "description": data.get("description", ""),
        "status": "OFF",
        "schedule": data.get("schedule", {"days": [], "startTime": "", "endTime": ""}),
        "condition": data.get("condition", []),
        "initialization": data.get("initialization", []),
        "actions": data.get("actions", []),
        "errorState": data.get("errorState", []),
        "bufferTime": data.get("bufferTime", BUFFER_SECONDS),
        "maxCyclesPerDay": data.get("maxCyclesPerDay", 0),
        "runtime": _new_runtime(),
    }
    auto["_owner_email"] = sess.email
    sess.automations[auto_id] = auto
    sess.automation_logs[auto_id] = []
    _auto_log(auto_id, f"Automation '{auto['name']}' created")
    _emit_auto_update(auto)
    emit("automation_created", {"id": auto_id})
    # Persist to DB
    try:
        import db
        db.save_automation(sess.email, auto)
    except Exception as e:
        logging.error(f"[DB] Failed to save new automation: {e}")
    start_engine()


@socketio.on("update_automation")
def handle_update_automation(data):
    """Update an existing automation's configuration."""
    auto_id = data.get("id")
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess or auto_id not in sess.automations:
        emit("automation_error", {"error": "Not found"})
        return
    auto = sess.automations[auto_id]
    # Update config fields
    actions_changed = "actions" in data
    old_actions = auto.get("actions", [])
    
    for key in ("name", "description", "schedule", "condition",
                "initialization", "actions", "errorState", "bufferTime", "maxCyclesPerDay"):
        if key in data:
            auto[key] = data[key]

    # Handle Live Sequence Updates (Adding/Removing actions)
    rt = auto.get("runtime", {})
    if rt.get("state") in ("ACTION_SET", "ACTION_VERIFY", "ACTION_RUN", "BUFFER_WAIT") and actions_changed:
        idx = rt.get("currentActionIndex", 0)
        
        if idx < len(old_actions):
            current_action_topic = old_actions[idx].get("switchCmdTopic")
            
            # Try to find where our current action moved to in the new list
            new_actions = auto.get("actions", [])
            new_idx = -1
            for i, a in enumerate(new_actions):
                if a.get("switchCmdTopic") == current_action_topic:
                    new_idx = i
                    break
            
            if new_idx != -1:
                if new_idx != idx:
                    _auto_log(auto_id, f"Sequence changed: Current action moved from #{idx+1} to #{new_idx+1}. Tracking automatically.")
                    rt["currentActionIndex"] = new_idx
                
                # Also handle duration update if we are currently running
                if rt["state"] == "ACTION_RUN":
                    new_dur = new_actions[new_idx].get("duration", 0)
                    old_dur = old_actions[idx].get("duration", 0)
                    if new_dur != old_dur:
                        rt["remainingTime"] = new_dur
                        _auto_log(auto_id, f"Live duration updated: {old_dur}s -> {new_dur}s")
            else:
                # The action we were running is gone!
                _auto_log(auto_id, "The active action was deleted from the sequence. Resetting to IDLE.", level="warn")
                rt["state"] = "IDLE"
                rt["currentActionIndex"] = 0
        else:
            # Index was already out of bounds for some reason
            rt["state"] = "IDLE"
            rt["currentActionIndex"] = 0

    _auto_log(auto_id, f"Automation '{auto['name']}' updated")
    _emit_auto_update(auto)

    # Persist to DB
    try:
        import db
        db.save_automation(sess.email, auto)
    except Exception as e:
        logging.error(f"[DB] Failed to persist automation update: {e}")
    
    # Run AI immediately if enabled upon save
    # (The AI runner itself will now handle safety if a run is already in progress)
    sched = auto.get("schedule", {})
    if sched.get("ai_enabled"):
        socketio.start_background_task(_run_ai_for_automation, auto_id)


@socketio.on("pause_automation")
def handle_pause_automation(data):
    auto_id = data.get("id")
    is_paused = data.get("isPaused", False)
    sess = session_mgr.get_session_by_sid(request.sid)
    if sess and auto_id in sess.automations:
        sess.automations[auto_id]["isPaused"] = is_paused
        _emit_auto_update(sess.automations[auto_id])
        start_engine()


@socketio.on("toggle_automation")
def handle_toggle_automation(data):
    """Toggle automation ON/OFF."""
    auto_id = data.get("id")
    status = data.get("status", "OFF")
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess or auto_id not in sess.automations:
        return
    auto = sess.automations[auto_id]
    auto["status"] = status
    if status == "ON":
        auto["runtime"] = _new_runtime(auto.get("runtime", {}))
        auto["runtime"]["state"] = "INIT_SET"
        auto["runtime"]["retryCount"] = 0
        _auto_log(auto_id, "Turned ON → INIT_SET")
        
        # Run AI immediately if enabled
        sched = auto.get("schedule", {})
        if sched.get("ai_enabled"):
            socketio.start_background_task(_run_ai_for_automation, auto_id)
    else:
        auto["runtime"] = _new_runtime(auto.get("runtime", {}))
        _auto_log(auto_id, "Turned OFF → IDLE")
    _emit_auto_update(auto)
    start_engine()
    # Persist to DB
    try:
        import db
        db.save_automation(_get_user_email(), auto)
    except Exception as e:
        logging.error(f"[DB] Failed to save toggle state: {e}")


@socketio.on("reset_automation")
def handle_reset_automation(data):
    """Reset automation execution."""
    auto_id = data.get("id")
    sess = session_mgr.get_session_by_sid(request.sid)
    if not sess or auto_id not in sess.automations:
        return
    auto = sess.automations[auto_id]
    rt = auto.get("runtime", {})
    now = time.time()
    today_str = datetime.now().strftime("%Y-%m-%d")

    # If currently running, capture the partial duration for insights before resetting
    if rt.get("state") == "ACTION_RUN" and rt.get("timerStart"):
        elapsed = now - rt["timerStart"]
        if "duration_history" not in rt:
            rt["duration_history"] = {}
        rt["duration_history"][today_str] = rt["duration_history"].get(today_str, 0) + elapsed
        _auto_log(auto_id, f"Reset: Added partial duration ({int(elapsed)}s) to history")

    status = auto.get("status", "OFF")
    auto["runtime"] = _new_runtime(auto.get("runtime", {}))
    # Explicitly set cycles_today to 0 on reset so the row display restarts
    auto["runtime"]["cycles_today"] = 0
    if status == "ON":
        auto["runtime"]["state"] = "INIT_SET"
        auto["runtime"]["retryCount"] = 0
        auto["runtime"]["currentInitIndex"] = 0
        _auto_log(auto_id, "RESET → INIT_SET (Restarting from initialization)")
    else:
        _auto_log(auto_id, "RESET → IDLE")
    _emit_auto_update(auto)
    # Persist to DB
    try:
        import db
        db.save_automation(_get_user_email(), auto)
    except Exception as e:
        logging.error(f"[DB] Failed to save reset state: {e}")


@socketio.on("delete_automation")
def handle_delete_automation(data):
    """Delete an automation."""
    auto_id = data.get("id")
    sess = session_mgr.get_session_by_sid(request.sid)
    if sess and auto_id in sess.automations:
        name = sess.automations[auto_id].get("name", auto_id)
        del sess.automations[auto_id]
        sess.automation_logs.pop(auto_id, None)
        socketio.emit("automation_deleted", {"id": auto_id}, room=sess.room)
        logging.info(f"[SESSION:{sess.email}] Automation '{name}' deleted")
        # Remove from DB
        try:
            import db
            db.delete_automation(auto_id)
        except Exception as e:
            logging.error(f"[DB] Failed to delete automation: {e}")


@socketio.on("get_automation_logs")
def handle_get_automation_logs(data):
    """Get logs for a specific automation."""
    auto_id = data.get("id")
    sess = session_mgr.get_session_by_sid(request.sid)
    if sess:
        logs = sess.automation_logs.get(auto_id, [])
    else:
        logs = automation_logs.get(auto_id, [])
    emit("automation_logs", {"id": auto_id, "logs": logs})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _load_user_automations(email):
    """Load a user's automations from DB into the in-memory dict."""
    global automations, automation_logs
    try:
        import db
        # Save current user's state before wiping memory for the new user
        if automations:
            _save_all_to_db()
            
        saved = db.load_automations(email)
        automations.clear()
        automation_logs.clear()
        for auto in saved:
            auto_id = auto["id"]
            auto["_owner_email"] = email
            automations[auto_id] = auto
            # Load logs from DB
            logs = db.get_logs(auto_id, 200)
            automation_logs[auto_id] = [{"ts": l["ts"], "msg": l["message"], "level": "info"} for l in logs]
        logging.info(f"[DB] Loaded {len(saved)} automations for {email}")
    except Exception as e:
        logging.error(f"[DB] Failed to load automations for {email}: {e}")


def _load_session_automations(sess, email):
    """Load a user's automations from DB into a UserSession object."""
    try:
        import db
        saved = db.load_automations(email)
        sess.automations.clear()
        sess.automation_logs.clear()
        for auto in saved:
            auto_id = auto["id"]
            auto["_owner_email"] = email
            sess.automations[auto_id] = auto
            logs = db.get_logs(auto_id, 200)
            sess.automation_logs[auto_id] = [{"ts": l["ts"], "msg": l["message"], "level": "info"} for l in logs]
        logging.info(f"[SESSION-MGR] Loaded {len(saved)} automations for {email}")
    except Exception as e:
        logging.error(f"[SESSION-MGR] Failed to load automations for {email}: {e}")


def _save_all_to_db():
    """Save all current automations to DB (used on shutdown)."""
    try:
        import db
        for email, sess in list(session_mgr._sessions.items()):
            for auto_id, auto in sess.automations.items():
                db.save_automation(email, auto)
        logging.info(f"[DB] Saved all sess automations on shutdown (sessions: {session_mgr.active_count()})")
    except Exception as e:
        logging.error(f"[DB] Shutdown save failed: {e}")


def _auto_resume_sessions():
    """On server restart, reconnect all users who have active (ON) automations."""
    try:
        import db
        active_users = db.get_users_with_active_automations()
        if not active_users:
            logging.info("[AUTO-RESUME] No users with active automations to resume.")
            return
        for user in active_users:
            email = user["email"]
            password = user["password"]
            # Skip the legacy global user (already connected above)
            if email == MQTT_USERNAME:
                continue
            # Validate credentials with CADIO
            if not cadio_login(email, password):
                logging.warning(f"[AUTO-RESUME] CADIO login failed for {email}, skipping")
                continue
            # Create sess, load automations, connect MQTT
            sess = session_mgr.create_session(
                email, password,
                broker=MQTT_BROKER, port=MQTT_PORT, discovery_prefix=DISCOVERY_PREFIX
            )
            _load_session_automations(sess, email)
            sess.start_mqtt(socketio)
            logging.info(f"[AUTO-RESUME] Resumed sess for {email} ({len(sess.automations)} automations)")
        logging.info(f"[AUTO-RESUME] Resumed {len(active_users)} user sessions total")
    except Exception as e:
        logging.error(f"[AUTO-RESUME] Failed: {e}")


if __name__ == "__main__":
    print("\n  Nivixsa Smart Irrigation Dashboard")
    print("  Open http://localhost:5000 in your browser\n")
    # Initialize database
    import db
    db.init_db()
    _sync_master_admin()
    # Register shutdown hook to save state
    atexit.register(_save_all_to_db)
    # Auto-resume all users with active automations from DB
    _auto_resume_sessions()
    # Legacy: if environment variables are set, also create a session for that user
    if MQTT_USERNAME and MQTT_PASSWORD:
        sess = session_mgr.create_session(
            MQTT_USERNAME, MQTT_PASSWORD,
            broker=MQTT_BROKER, port=MQTT_PORT, discovery_prefix=DISCOVERY_PREFIX
        )
        _load_session_automations(sess, MQTT_USERNAME)
        sess.start_mqtt(socketio)
    elif not session_mgr._sessions:
        # Try to auto-login from last saved user (only if no sessions were resumed)
        last_user = db.get_last_user()
        if last_user and last_user.get("password"):
            logging.info(f"[DB] Auto-login from saved user: {last_user['email']}")
            sess = session_mgr.create_session(
                last_user["email"], last_user["password"],
                broker=MQTT_BROKER, port=MQTT_PORT, discovery_prefix=DISCOVERY_PREFIX
            )
            _load_session_automations(sess, last_user["email"])
            sess.start_mqtt(socketio)
        elif last_user:
            logging.info(f"[DB] Found user {last_user['email']} but password not recoverable. Manual login required.")
    start_engine()
    # Start Admin Telemetry
    socketio.start_background_task(_admin_telemetry_loop)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
