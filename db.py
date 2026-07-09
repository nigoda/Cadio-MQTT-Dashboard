"""
SQLite persistence layer for the Cadio MQTT Dashboard.
Stores users, automations (per-user), automation logs, and runtime state.
All data survives application restarts.

Security:
  - Passwords: bcrypt hash for verification + Fernet encrypted for auto-login
  - API keys: Fernet encrypted
"""

import sqlite3
import json
import os
import base64
import logging
import secrets
from datetime import datetime, timedelta

import bcrypt
from cryptography.fernet import Fernet

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cadio.db")
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".encryption_key")

_conn = None
_fernet = None


def _get_fernet():
    """Get or create the Fernet encryption instance."""
    global _fernet
    if _fernet is not None:
        return _fernet
    if os.path.exists(KEY_PATH) and os.path.getsize(KEY_PATH) > 0:
        with open(KEY_PATH, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        with open(KEY_PATH, "wb") as f:
            f.write(key)
        logging.info(f"[DB] Generated new encryption key at {KEY_PATH}")
    _fernet = Fernet(key)
    return _fernet


def _get_conn():
    """Get or create a thread-local SQLite connection."""
    global _conn
    if _conn is None:
        # timeout + busy_timeout make writers wait for a lock (up to 5s) instead
        # of failing/hanging immediately — important under threading async mode
        # where the telemetry loop and login handlers share this connection.
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read/write
        _conn.execute("PRAGMA busy_timeout=5000")  # wait up to 5s for locks
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            email         TEXT PRIMARY KEY COLLATE NOCASE,
            password_hash TEXT NOT NULL DEFAULT '',
            password_enc  TEXT NOT NULL DEFAULT '',
            api_key_enc   TEXT DEFAULT '',
            api_mode      TEXT DEFAULT 'default',
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            last_login    TEXT,
            blocked       INTEGER DEFAULT 0,
            failed_reconnects INTEGER DEFAULT 0,
            blocked_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS admins (
            email         TEXT PRIMARY KEY COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            password_enc  TEXT NOT NULL,
            level         INTEGER DEFAULT 3,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_token TEXT PRIMARY KEY,
            user_email    TEXT NOT NULL COLLATE NOCASE,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at    TEXT,
            user_agent    TEXT,
            FOREIGN KEY (user_email) REFERENCES users(email) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS admin_sessions (
            session_token TEXT PRIMARY KEY,
            admin_email   TEXT NOT NULL COLLATE NOCASE,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at    TEXT,
            user_agent    TEXT,
            FOREIGN KEY (admin_email) REFERENCES admins(email) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS automations (
            id          TEXT PRIMARY KEY,
            user_email  TEXT NOT NULL COLLATE NOCASE,
            name        TEXT NOT NULL DEFAULT 'New Automation',
            description TEXT DEFAULT '',
            status      TEXT DEFAULT 'OFF',
            config_json TEXT DEFAULT '{}',
            runtime_json TEXT DEFAULT '{}',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_email) REFERENCES users(email)
        );

        CREATE TABLE IF NOT EXISTS automation_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id TEXT NOT NULL,
            user_email  TEXT NOT NULL COLLATE NOCASE,
            message     TEXT NOT NULL,
            timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (automation_id) REFERENCES automations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_auto_user ON automations(user_email);
        CREATE INDEX IF NOT EXISTS idx_logs_auto ON automation_logs(automation_id);
        CREATE INDEX IF NOT EXISTS idx_logs_ts ON automation_logs(timestamp);

        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email    TEXT NOT NULL COLLATE NOCASE,
            endpoint      TEXT NOT NULL UNIQUE,
            p256dh        TEXT NOT NULL,
            auth          TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_email) REFERENCES users(email) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions(user_email);
    """)
    conn.commit()

    # --- Migrations for existing databases ---
    _migrate_columns(conn)
    _migrate_passwords(conn)

    logging.info(f"[DB] Database initialized at {DB_PATH}")


def _migrate_columns(conn):
    """Add new columns to existing databases if missing."""
    migrations = [
        ("users", "password_hash", "TEXT NOT NULL DEFAULT ''"),
        ("users", "password_enc", "TEXT NOT NULL DEFAULT ''"),
        ("users", "api_key_enc", "TEXT DEFAULT ''"),
        ("users", "api_mode", "TEXT DEFAULT 'default'"),
        # Block tracking columns
        ("users", "blocked", "INTEGER DEFAULT 0"),
        ("users", "failed_reconnects", "INTEGER DEFAULT 0"),
        ("users", "blocked_at", "TEXT"),
        # Legacy columns we need for migration
        ("users", "api_key_b64", "TEXT DEFAULT ''"),
    ]
    for table, column, col_type in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists


def _migrate_passwords(conn):
    """Migrate old Base64 passwords/API keys to bcrypt+Fernet."""
    # Check which columns exist in the users table
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    has_pw_b64 = "password_b64" in cols
    has_key_b64 = "api_key_b64" in cols
    
    if not has_pw_b64 and not has_key_b64:
        return  # No legacy columns to migrate
    
    f = _get_fernet()
    # Build a safe SELECT query with only existing columns
    select_cols = ["email", "password_hash", "password_enc", "api_key_enc"]
    if has_pw_b64:
        select_cols.append("password_b64")
    if has_key_b64:
        select_cols.append("api_key_b64")
    rows = conn.execute(f"SELECT {', '.join(select_cols)} FROM users").fetchall()
    for row in rows:
        email = row["email"]
        needs_update = False
        updates = {}

        # Migrate password from Base64 to bcrypt hash + Fernet encrypted
        old_pw_b64 = row["password_b64"] if "password_b64" in row.keys() else ""
        current_hash = row["password_hash"]
        current_enc = row["password_enc"]

        if old_pw_b64 and not current_hash:
            try:
                plain_pw = base64.b64decode(old_pw_b64.encode("utf-8")).decode("utf-8")
                updates["password_hash"] = bcrypt.hashpw(plain_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                updates["password_enc"] = f.encrypt(plain_pw.encode("utf-8")).decode("utf-8")
                needs_update = True
                logging.info(f"[DB] Migrated password for {email} to bcrypt+Fernet")
            except Exception as e:
                logging.error(f"[DB] Failed to migrate password for {email}: {e}")

        # Migrate API key from Base64 to Fernet
        old_key_b64 = row["api_key_b64"] if "api_key_b64" in row.keys() else ""
        current_key_enc = row["api_key_enc"]

        if old_key_b64 and not current_key_enc:
            try:
                plain_key = base64.b64decode(old_key_b64.encode("utf-8")).decode("utf-8")
                updates["api_key_enc"] = f.encrypt(plain_key.encode("utf-8")).decode("utf-8")
                needs_update = True
                logging.info(f"[DB] Migrated API key for {email} to Fernet")
            except Exception as e:
                logging.error(f"[DB] Failed to migrate API key for {email}: {e}")

        if needs_update:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE users SET {set_clause} WHERE email = ?",
                        (*updates.values(), email))
    conn.commit()


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def _hash_pw(password):
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_pw(password, hashed):
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _encrypt(plaintext):
    """Encrypt a string with Fernet. Returns empty string for empty input."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _decrypt(ciphertext):
    """Decrypt a Fernet-encrypted string. Returns empty string on failure."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

def save_user(email, password):
    """Save or update a user record with bcrypt hash + encrypted password."""
    email = email.lower()
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    pw_hash = _hash_pw(password)
    pw_enc = _encrypt(password)
    conn.execute(
        """INSERT INTO users (email, password_hash, password_enc, last_login)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(email) DO UPDATE SET
               password_hash = excluded.password_hash,
               password_enc = excluded.password_enc,
               last_login = excluded.last_login""",
        (email, pw_hash, pw_enc, now)
    )
    conn.commit()


def clear_last_login(email):
    """Clear last_login so auto-login won't trigger for this user."""
    conn = _get_conn()
    conn.execute("UPDATE users SET last_login = NULL WHERE email = ?", (email,))
    conn.commit()


def get_user(email):
    """Get user record. Returns dict with email, password, or None."""
    email = email.lower()
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        return {
            "email": row["email"],
            "password": _decrypt(row["password_enc"]),
            "created_at": row["created_at"],
            "last_login": row["last_login"],
        }
    return None


def get_last_user():
    """Get the most recently logged in user (for auto-login on restart)."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE last_login IS NOT NULL ORDER BY last_login DESC LIMIT 1"
    ).fetchone()
    if row:
        return {
            "email": row["email"],
            "password": _decrypt(row["password_enc"]),
        }
    return None


def save_api_settings(email, api_mode, api_key):
    """Save API settings for a user (Fernet encrypted)."""
    email = email.lower()
    conn = _get_conn()
    key_enc = _encrypt(api_key)
    conn.execute(
        "UPDATE users SET api_mode = ?, api_key_enc = ? WHERE email = ?",
        (api_mode, key_enc, email)
    )
    conn.commit()


def get_api_settings(email):
    """Get API settings for a user. Returns dict with api_mode and custom_api_key."""
    email = email.lower()
    conn = _get_conn()
    row = conn.execute(
        "SELECT api_mode, api_key_enc FROM users WHERE email = ?",
        (email,)
    ).fetchone()
    if row:
        key = _decrypt(row["api_key_enc"]) if row["api_key_enc"] else ""
        return {
            "api_mode": row["api_mode"] or "default",
            "custom_api_key": key
        }
    return {"api_mode": "default", "custom_api_key": ""}


# ---------------------------------------------------------------------------
# Account Block / Recovery helpers
# ---------------------------------------------------------------------------

MAX_FAILED_RECONNECTS = 3


def increment_failed_reconnects(email):
    """Increment consecutive bad-credential counter. Returns new count."""
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET failed_reconnects = failed_reconnects + 1 WHERE email = ?",
        (email,)
    )
    conn.commit()
    row = conn.execute(
        "SELECT failed_reconnects FROM users WHERE email = ?", (email,)
    ).fetchone()
    return row["failed_reconnects"] if row else 0


def block_user(email):
    """Mark a user as blocked due to repeated CADIO credential failures."""
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET blocked = 1, blocked_at = datetime('now') WHERE email = ?",
        (email,)
    )
    conn.commit()
    logging.warning(f"[DB] Account '{email}' has been auto-blocked.")


def unblock_user(email):
    """Clear the block on a user after a successful CADIO login."""
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET blocked = 0, failed_reconnects = 0, blocked_at = NULL WHERE email = ?",
        (email,)
    )
    conn.commit()
    logging.info(f"[DB] Account '{email}' has been unblocked.")


def is_user_blocked(email):
    """Returns True if the user is currently blocked."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT blocked FROM users WHERE email = ?", (email,)
    ).fetchone()
    return bool(row["blocked"]) if row else False



def get_all_users_for_admin():
    """Fetch all users with automation counts and status for the admin dashboard."""
    conn = _get_conn()
    # Join with automations to get counts
    rows = conn.execute("""
        SELECT u.*, 
               (SELECT COUNT(*) FROM automations a WHERE a.user_email = u.email) as total_autos,
               (SELECT COUNT(*) FROM automations a WHERE a.user_email = u.email AND a.status = 'ON') as active_autos
        FROM users u
        ORDER BY u.created_at DESC
    """).fetchall()
    
    users = []
    for r in rows:
        users.append({
            "email": r["email"],
            "created_at": r["created_at"],
            "last_login": r["last_login"],
            "blocked": bool(r["blocked"]),
            "failed_reconnects": r["failed_reconnects"],
            "total_autos": r["total_autos"],
            "active_autos": r["active_autos"],
            "api_mode": r["api_mode"]
        })
    return users


def delete_user(email):
    """Admin only: Fully wipe a user and all their data."""
    conn = _get_conn()
    try:
        # Delete logs first (foreign key might handle it but let's be explicit)
        conn.execute("DELETE FROM automation_logs WHERE user_email = ?", (email,))
        conn.execute("DELETE FROM automations WHERE user_email = ?", (email,))
        conn.execute("DELETE FROM users WHERE email = ?", (email,))
        conn.commit()
        logging.info(f"[DB-ADMIN] Deleted user {email} and all associated data.")
        return True
    except Exception as e:
        logging.error(f"[DB-ADMIN] Failed to delete user {email}: {e}")
        return False


# ---------------------------------------------------------------------------
# Admin Team Management (RBAC)
# ---------------------------------------------------------------------------

def save_admin(email, password, level=3):
    """Save or update an admin record."""
    email = email.lower()
    conn = _get_conn()
    pw_hash = _hash_pw(password)
    pw_enc = _encrypt(password)
    conn.execute(
        """INSERT INTO admins (email, password_hash, password_enc, level)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(email) DO UPDATE SET
               password_hash = excluded.password_hash,
               password_enc = excluded.password_enc,
               level = excluded.level""",
        (email, pw_hash, pw_enc, level)
    )
    conn.commit()


def get_admin(email):
    """Get admin record for login."""
    email = email.lower()
    conn = _get_conn()
    row = conn.execute("SELECT * FROM admins WHERE email = ?", (email,)).fetchone()
    if row:
        return {
            "email": row["email"],
            "password": _decrypt(row["password_enc"]),
            "level": row["level"]
        }
    return None


def get_all_admins():
    """Fetch all admin team members."""
    conn = _get_conn()
    rows = conn.execute("SELECT email, level, created_at FROM admins ORDER BY level ASC").fetchall()
    return [dict(r) for r in rows]


def delete_admin(email):
    """Remove an admin from the team."""
    email = email.lower()
    conn = _get_conn()
    conn.execute("DELETE FROM admins WHERE email = ?", (email,))
    conn.commit()


# ---------------------------------------------------------------------------
# Automation CRUD
# ---------------------------------------------------------------------------

def _auto_to_row(user_email, auto):
    """Convert an in-memory automation dict to DB row values."""
    # Separate runtime from config
    runtime = auto.get("runtime", {})
    config = {}
    for k, v in auto.items():
        if k not in ("id", "name", "description", "status", "runtime", "logs"):
            config[k] = v
    return (
        auto["id"],
        user_email,
        auto.get("name", "New Automation"),
        auto.get("description", ""),
        auto.get("status", "OFF"),
        json.dumps(config, default=str),
        json.dumps(runtime, default=str),
        datetime.utcnow().isoformat(),
    )


def _row_to_auto(row):
    """Convert a DB row back to an in-memory automation dict."""
    auto = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
    }
    config = json.loads(row["config_json"] or "{}")
    auto.update(config)
    auto["runtime"] = json.loads(row["runtime_json"] or "{}")
    return auto


def save_automation(user_email, auto):
    """Insert or update a single automation."""
    user_email = user_email.lower()
    conn = _get_conn()
    vals = _auto_to_row(user_email, auto)
    conn.execute(
        """INSERT INTO automations (id, user_email, name, description, status, config_json, runtime_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               name = excluded.name,
               description = excluded.description,
               status = excluded.status,
               config_json = excluded.config_json,
               runtime_json = excluded.runtime_json,
               updated_at = excluded.updated_at""",
        vals
    )
    conn.commit()


def save_runtime(auto_id, runtime):
    """Save only the runtime state (for periodic saves without overwriting config)."""
    conn = _get_conn()
    conn.execute(
        """UPDATE automations SET runtime_json = ?, updated_at = ?
           WHERE id = ?""",
        (json.dumps(runtime, default=str), datetime.utcnow().isoformat(), auto_id)
    )
    conn.commit()


def load_automations(user_email):
    """Load all automations for a user. Returns list of automation dicts."""
    user_email = user_email.lower()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM automations WHERE user_email = ? ORDER BY created_at",
        (user_email,)
    ).fetchall()
    return [_row_to_auto(r) for r in rows]


def delete_automation(auto_id):
    """Delete an automation and its logs."""
    conn = _get_conn()
    conn.execute("DELETE FROM automation_logs WHERE automation_id = ?", (auto_id,))
    conn.execute("DELETE FROM automations WHERE id = ?", (auto_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Automation Logs
# ---------------------------------------------------------------------------

def append_log(auto_id, user_email, message):
    """Append a log entry for an automation."""
    user_email = user_email.lower()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO automation_logs (automation_id, user_email, message, timestamp)
           VALUES (?, ?, ?, ?)""",
        (auto_id, user_email, message, datetime.utcnow().isoformat())
    )
    # Prune old logs — keep only last 200 per automation
    conn.execute(
        """DELETE FROM automation_logs WHERE id IN (
            SELECT id FROM automation_logs
            WHERE automation_id = ?
            ORDER BY timestamp DESC
            LIMIT -1 OFFSET 200
        )""",
        (auto_id,)
    )
    conn.commit()


def get_logs(auto_id, limit=200):
    """Get recent logs for an automation."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT message, timestamp FROM automation_logs
           WHERE automation_id = ?
           ORDER BY timestamp DESC LIMIT ?""",
        (auto_id, limit)
    ).fetchall()
    return [{"message": r["message"], "ts": r["timestamp"]} for r in rows]


def save_all_runtimes(automations_dict):
    """Bulk save all runtime states. Used by periodic background save."""
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    for auto_id, auto in automations_dict.items():
        runtime = auto.get("runtime", {})
        conn.execute(
            "UPDATE automations SET runtime_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(runtime, default=str), now, auto_id)
        )
    conn.commit()


def get_users_with_active_automations():
    """Get all users who have at least one automation with status='ON'.
    Returns list of dicts with email and decrypted password (for auto-resume)."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT DISTINCT u.email, u.password_enc
           FROM users u
           JOIN automations a ON a.user_email = u.email
           WHERE a.status = 'ON' AND u.blocked = 0"""
    ).fetchall()
    result = []
    for row in rows:
        pw = _decrypt(row["password_enc"])
        if pw:
            result.append({"email": row["email"], "password": pw})
    return result


# ---------------------------------------------------------------------------
# Session Token Management (Global Session Invalidation)
# ---------------------------------------------------------------------------

def create_user_session(user_email, user_agent=None):
    """Create a new database-backed session token for a user.
    Sessions never expire (expires_at = NULL); they remain valid until an explicit
    logout revokes them."""
    conn = _get_conn()
    token = secrets.token_hex(32)
    conn.execute(
        "INSERT INTO sessions (session_token, user_email, user_agent, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_email.lower(), user_agent, None)
    )
    conn.commit()
    return token


def validate_user_session(session_token):
    """Validate a session token. Returns user email if valid, None if revoked/invalid.
    A NULL expires_at means the session never expires."""
    if not session_token:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT user_email FROM sessions WHERE session_token = ? AND (expires_at IS NULL OR expires_at > ?)",
        (session_token, datetime.utcnow().isoformat())
    ).fetchone()
    return row["user_email"] if row else None


def delete_user_session(session_token):
    """Delete a specific session token (single-device logout)."""
    if not session_token:
        return
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE session_token = ?", (session_token,))
    conn.commit()


def delete_all_user_sessions(user_email):
    """Delete ALL session tokens for a user (global logout across all devices)."""
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE user_email = ?", (user_email.lower(),))
    conn.commit()


def get_user_sessions(user_email):
    """Return all active (non-expired) login sessions for a user, newest first.
    Each item: {session_token, created_at, expires_at, user_agent}."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT session_token, created_at, expires_at, user_agent
           FROM sessions
           WHERE user_email = ? AND (expires_at IS NULL OR expires_at > ?)
           ORDER BY created_at DESC""",
        (user_email.lower(), datetime.utcnow().isoformat())
    ).fetchall()
    return [dict(r) for r in rows]


def create_admin_session(admin_email, user_agent=None):
    """Create a new database-backed session token for an admin."""
    conn = _get_conn()
    token = secrets.token_hex(32)
    expires_at = (datetime.utcnow() + timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO admin_sessions (session_token, admin_email, user_agent, expires_at) VALUES (?, ?, ?, ?)",
        (token, admin_email.lower(), user_agent, expires_at)
    )
    conn.commit()
    return token


def validate_admin_session(session_token):
    """Validate an admin session token. Returns admin email if valid, None otherwise."""
    if not session_token:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT admin_email FROM admin_sessions WHERE session_token = ? AND expires_at > ?",
        (session_token, datetime.utcnow().isoformat())
    ).fetchone()
    return row["admin_email"] if row else None


def delete_admin_session(session_token):
    """Delete an admin session token."""
    if not session_token:
        return
    conn = _get_conn()
    conn.execute("DELETE FROM admin_sessions WHERE session_token = ?", (session_token,))
    conn.commit()


# ---------------------------------------------------------------------------
# Web Push Notifications Subscriptions
# ---------------------------------------------------------------------------

def save_push_subscription(user_email, endpoint, p256dh, auth):
    """Save or update a Web Push subscription for a user."""
    user_email = user_email.lower()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO push_subscriptions (user_email, endpoint, p256dh, auth)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET
               user_email = excluded.user_email,
               p256dh = excluded.p256dh,
               auth = excluded.auth""",
        (user_email, endpoint, p256dh, auth)
    )
    conn.commit()


def get_push_subscriptions(user_email=None):
    """Get all active Web Push subscriptions for a user (or all if None)."""
    conn = _get_conn()
    if user_email:
        user_email = user_email.lower()
        rows = conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_email = ?",
            (user_email,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_push_subscription(endpoint):
    """Remove a bad/expired Web Push subscription."""
    conn = _get_conn()
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
