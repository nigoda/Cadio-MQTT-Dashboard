"""
Multi-Tenant Session Manager for Nivixsa Dashboard.
Each user gets their own UserSession with isolated MQTT, automations, and state.
"""

import copy
import json
import logging
import os
import ssl
import threading
import time
import uuid
from datetime import datetime
from typing import Optional

import paho.mqtt.client as mqtt
import requests

CADIO_LOGIN_URL = "https://egycad.com/apis/cadio/login"
DISCOVERY_PREFIX = "homeassistant"
MAX_HISTORY = 200
MAX_AUTO_LOG = 200
VERIFY_TIMEOUT = 10
DRIFT_VERIFY_TIMEOUT = 3
ENGINE_INTERVAL = 1


class UserSession:
    """Encapsulates all per-user state: MQTT client, automations, device data, and logs."""

    def __init__(self, email, password, broker=None, port=None, discovery_prefix=None):
        self.email = email
        self.password = password
        self.broker = broker or os.getenv("MQTT_BROKER", "egycad.com")
        self.port = port or int(os.getenv("MQTT_PORT", 1883))
        self.discovery_prefix = discovery_prefix or DISCOVERY_PREFIX

        # MQTT state
        self.mqtt_client = None
        self.mqtt_connected = False
        self._mqtt_last_connected_time = time.time()

        # Device state
        self.device_states: dict = {}       # topic -> last payload
        self.sensor_history: dict = {}      # topic -> list of {ts, value}

        # Automation state
        self.automations: dict = {}         # auto_id -> automation dict
        self.automation_logs: dict = {}     # auto_id -> list of log entries

        # Unit-Level Watchdog state
        self.watchdogs: dict = {}           # unit_serial -> topic
        self.unit_liveness: dict = {}       # unit_serial -> True/False (True = online)

        # Socket tracking (all browser tabs for this user)
        self.socket_sids: set = set()

        # Session status
        self.active = False
        self.last_activity = time.time()
        self.created_at = time.time()

        # AI state
        self._genai_client = None
        self._current_api_key = None

    @property
    def room(self):
        """SocketIO room name for this user (all their browser tabs)."""
        return f"user_{self.email}"

    def add_socket(self, sid):
        """Register a browser tab (socket) to this session."""
        self.socket_sids.add(sid)
        self.last_activity = time.time()

    def remove_socket(self, sid):
        """Unregister a browser tab."""
        self.socket_sids.discard(sid)

    @property
    def has_sockets(self):
        """True if at least one browser tab is connected."""
        return len(self.socket_sids) > 0

    # --- MQTT ---

    def start_mqtt(self, socketio):
        """Connect this user's MQTT client to their broker."""
        if self.mqtt_client is not None:
            try:
                self.mqtt_client.loop_stop(force=True)
                self.mqtt_client.disconnect()
            except Exception:
                pass

        client_id = f"cadio-{self.email[:8]}-{os.getpid()}-{int(time.time()) % 10000}"
        self.mqtt_client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self.mqtt_client.user_data_set({
            "sub_topics": [],
            "any_sub_ok": False,
            "discovery_prefix": self.discovery_prefix,
            "session": self,
            "socketio": socketio,
        })
        self.mqtt_client.username_pw_set(self.email, self.password)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_client.on_message = self._on_message

        port = self.port
        if port == 8883:
            self.mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)

        logging.info(f"[SESSION:{self.email}] Connecting MQTT to {self.broker}:{port} (async)")
        try:
            # Non-blocking connect: the TCP/MQTT handshake happens in the
            # loop_start() background thread, so the login request returns
            # immediately instead of blocking 5-30s. The client is told
            # "Connected" from the on_connect callback once it actually connects.
            self.mqtt_client.connect_async(self.broker, port, keepalive=60)
        except Exception as exc:
            logging.error(f"[SESSION:{self.email}] MQTT connect_async failed: {exc}")
            return False

        self.mqtt_client.loop_start()
        self.active = True
        return True

    def stop_mqtt(self):
        """Disconnect MQTT cleanly."""
        if self.mqtt_client is not None:
            try:
                self.mqtt_client.loop_stop(force=True)
                self.mqtt_client.disconnect()
            except Exception:
                pass
            self.mqtt_client = None
        self.mqtt_connected = False
        self.active = False

    def _on_connect(self, client, userdata, flags, rc):
        socketio = userdata["socketio"]
        self.mqtt_connected = (rc == 0)
        
        if self.mqtt_connected:
            self._mqtt_last_connected_time = time.time()
            # Reset strikes on success
            try:
                import db
                db.unblock_user(self.email)
            except Exception: pass
            
            # Subscribe to all user topics
            client.subscribe(f"{self.discovery_prefix}/#")
            client.subscribe("cadio/#")
            socketio.emit("mqtt_status", {"connected": True, "message": "Connected"}, room=self.room)
            logging.info(f"[SESSION:{self.email}] MQTT Connected")
        else:
            # Handle Strikes for bad credentials
            if rc == 4:
                try:
                    import db
                    count = db.increment_failed_reconnects(self.email)
                    if count >= 3:
                        db.block_user(self.email)
                        client.loop_stop()
                except Exception: pass
            
            socketio.emit("mqtt_status", {"connected": False, "message": f"Connection failed (rc={rc})"}, room=self.room)

    def _on_disconnect(self, client, userdata, rc):
        socketio = userdata["socketio"]
        self.mqtt_connected = False
        socketio.emit("mqtt_status", {"connected": False, "message": f"Disconnected (rc={rc})"}, room=self.room)

    def _on_message(self, client, userdata, msg):
        socketio = userdata["socketio"]
        topic = msg.topic
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = str(msg.payload)

        # Parse JSON if possible
        parsed = payload
        try:
            parsed = json.loads(payload)
        except Exception: pass

        now_iso = datetime.now().isoformat()
        self.device_states[topic] = {"payload": parsed, "raw": payload, "ts": now_iso}

        # Track history for graphs
        if isinstance(parsed, (int, float)):
            if topic not in self.sensor_history:
                self.sensor_history[topic] = []
            self.sensor_history[topic].append({"ts": now_iso, "value": parsed})
            if len(self.sensor_history[topic]) > MAX_HISTORY:
                self.sensor_history[topic].pop(0)

        # Private emit
        socketio.emit("device_update", {
            "topic": topic,
            "payload": parsed,
            "raw": payload,
            "ts": now_iso
        }, room=self.room)

    # --- Automation Helpers ---

    def get_switch_state(self, state_topic):
        """Get current switch state from device_states."""
        data = self.device_states.get(state_topic, {})
        payload = data.get("payload", "") if isinstance(data, dict) else ""
        return payload.upper().strip()

    def mqtt_set_switch(self, cmd_topic, state):
        """Publish a switch command via this user's MQTT client."""
        if self.mqtt_client and self.mqtt_connected and cmd_topic:
            self.mqtt_client.publish(cmd_topic, state)

    def emit_to_user(self, socketio, event, data):
        """Emit a SocketIO event to all of this user's connected browsers."""
        socketio.emit(event, data, room=self.room)


class SessionManager:
    """Global registry that manages all active UserSessions."""

    def __init__(self):
        self._sessions: dict = {}       # email -> UserSession
        self._sid_to_email: dict = {}   # socket_sid -> email
        self._lock = threading.Lock()

    @property
    def sessions(self):
        """Access all active sessions (read-only snapshot)."""
        with self._lock:
            return dict(self._sessions)

    def get_session(self, email) -> Optional[UserSession]:
        """Get a session by email."""
        with self._lock:
            return self._sessions.get(email)

    def get_session_by_sid(self, sid) -> Optional[UserSession]:
        """Get a session by socket ID."""
        with self._lock:
            email = self._sid_to_email.get(sid)
            if email:
                return self._sessions.get(email)
        return None

    def create_session(self, email, password, broker=None, port=None, discovery_prefix=None) -> UserSession:
        """Create or reuse a session for a user."""
        with self._lock:
            if email in self._sessions:
                session = self._sessions[email]
                # Update password if changed
                session.password = password
                if broker:
                    session.broker = broker
                if port:
                    session.port = port
                if discovery_prefix:
                    session.discovery_prefix = discovery_prefix
                return session

            session = UserSession(email, password, broker, port, discovery_prefix)
            self._sessions[email] = session
            logging.info(f"[SESSION-MGR] Created session for {email} (total: {len(self._sessions)})")
            return session

    def register_socket(self, sid, email):
        """Associate a socket ID with a user session."""
        with self._lock:
            self._sid_to_email[sid] = email
            session = self._sessions.get(email)
            if session:
                session.add_socket(sid)

    def unregister_socket(self, sid):
        """Remove a socket ID. Returns the session (or None)."""
        with self._lock:
            email = self._sid_to_email.pop(sid, None)
            if email:
                session = self._sessions.get(email)
                if session:
                    session.remove_socket(sid)
                    return session
        return None

    def remove_session(self, email):
        """Fully remove a session (disconnect MQTT, clear state)."""
        with self._lock:
            session = self._sessions.pop(email, None)
            if session:
                session.stop_mqtt()
                # Clean up sid mappings
                sids_to_remove = [sid for sid, e in self._sid_to_email.items() if e == email]
                for sid in sids_to_remove:
                    del self._sid_to_email[sid]
                logging.info(f"[SESSION-MGR] Removed session for {email} (total: {len(self._sessions)})")
            return session

    def get_all_automations(self):
        """Get all automations across all sessions (for the engine loop)."""
        result = []
        with self._lock:
            for email, session in self._sessions.items():
                for auto_id, auto in session.automations.items():
                    result.append((session, auto_id, auto))
        return result

    def active_count(self):
        """Number of active sessions."""
        with self._lock:
            return len(self._sessions)
