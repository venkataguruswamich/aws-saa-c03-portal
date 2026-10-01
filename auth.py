#!/usr/bin/env python3
"""
VG Learner Portal - login gateway, role-based access, sessions and audit log.

Server-side security layer for the portal (the browser only holds an opaque
bearer token; every decision is made here):

  * Passwords are stored as salted scrypt hashes (werkzeug.security).
  * Optional per-user MFA: TOTP (RFC 6238: 6 digits, 30 s step, +/-1 step
    drift) with replay protection. Accounts created with --mfa (or given
    reset-mfa) are asked for an authenticator code after their password.
  * Sessions are random 256-bit tokens. Only their SHA-256 is stored, so a
    leaked database does not leak usable tokens. Sessions expire, can be ended
    by the user and revoked by an administrator; revocation takes effect on the
    client's next heartbeat (every 5 s).
  * Brute-force protection: MAX_FAILED_LOGINS failures per email (or 4x that per
    source IP) inside LOCKOUT_MINUTES locks further attempts.
  * RBAC: "admin" (Admin Profile) and "learner" (User Profile). An account is
    an Admin Profile when its stored role is admin OR its email is listed in
    ADMIN_EMAILS (default admin@vglearner.local). The role is decided here at
    login, never by the browser. /api/admin/* requires the admin role.
  * Audit log of LOGIN_SUCCESS, LOGIN_FAILED, LOGIN_LOCKED, MFA_TRIGGERED,
    MFA_SUCCESS, MFA_FAILED, LOGOUT, SESSION_EXPIRED, SESSION_REVOKED,
    ACCESS_DENIED and client events (EXAM_STARTED, EXAM_SUBMITTED).

Self-service account flows (real-world login page):
  * Sign up: name, email, password (policy: PASSWORD_MIN_LENGTH+, not common,
    not the email) and terms acceptance. Always a User Profile; ADMIN_EMAILS can
    never self-register. SIGNUP_MODE: open (active at once), approval (an admin
    approves in the portal) or closed. Optional SIGNUP_DOMAINS allow-list.
  * Forgot password: always answers the same generic message (no account
    enumeration). With SMTP_* configured it emails a single-use link valid for
    RESET_TOKEN_MINUTES; otherwise the request appears in the admin panel, where
    an admin creates the link and passes it on. Resetting revokes every session.
  * Email verification (EMAIL_VERIFICATION, default on): a self-registered
    account cannot log in until its owner opens the single-use verification
    link (emailed with SMTP, otherwise handed out by an admin, or the admin
    marks the address verified). Accounts created by an admin on the server
    are trusted; completing a password reset link also proves the address.
  * Granting access: admins can promote or demote other users in the portal
    (Make admin / Remove admin); active sessions pick up the change live.
  * Remember me: REMEMBER_ME_DAYS session instead of SESSION_TTL_MINUTES.
  * Change password (signed in): needs the current password, revokes the
    user's other sessions.

User management (run on the server):
  python auth.py create-user --email admin@vglearner.local   (admin via ADMIN_EMAILS)
  python auth.py create-user --email student@example.com     (User Profile)
  python auth.py create-user --email user@example.com --mfa  (with authenticator MFA)
  python auth.py list-users
  python auth.py reset-password --email user@example.com
  python auth.py reset-mfa --email user@example.com      (enrol / re-enrol MFA)
  python auth.py disable-mfa --email user@example.com
  python auth.py set-role --email user@example.com --role admin
  python auth.py delete-user --email user@example.com
  python auth.py verify-user --email user@example.com     (mark the email address verified)
  python auth.py import-users --csv users.csv [--send]    (bulk create; prints set-password links)
  python auth.py export-users --out users.csv
  python auth.py export-audit --out audit.csv [--days 30]

Environment variables
  AUTH_ENABLED (true)            false = no login wall (not recommended)
  AUTH_DB (data/portal.db, or /tmp/auth.db on Vercel/Lambda)
                                  SQLite file for users, sessions and audit log.
                                  /tmp on serverless platforms is EPHEMERAL - see
                                  the AUTH_DB comment below for why that is not
                                  durable production storage.
  SESSION_TTL_MINUTES (720)      absolute session lifetime
  ONLINE_WINDOW_SECONDS (60)     heartbeat age that still counts as "online now"
  MAX_FAILED_LOGINS (5)          failures per email before lockout
  LOCKOUT_MINUTES (15)           lockout / failure-counting window
  AUDIT_RETENTION_DAYS (90)      older audit rows are pruned
  ADMIN_EMAILS (admin@vglearner.local)  comma-separated emails that always get the Admin Profile
  SIGNUP_MODE (open)             open | approval | closed
  SIGNUP_DOMAINS ("")            e.g. "example.com,example.org" to restrict self sign-up
  PASSWORD_MIN_LENGTH (12)       minimum password length for sign-up / reset / change
  REMEMBER_ME_DAYS (30)          session lifetime when "Remember me" is ticked
  RESET_TOKEN_MINUTES (30)       password-reset link lifetime
  EMAIL_VERIFICATION (true)      new sign-ups must verify their email before the first login
  VERIFY_TOKEN_HOURS (24)        verification link lifetime
  INVITE_TOKEN_HOURS (72)        lifetime of set-password links created by import-users
  PUBLIC_URL ("")                portal URL used in reset links (default: this server)
  SMTP_HOST / SMTP_PORT (587) / SMTP_USER / SMTP_PASSWORD / SMTP_FROM
  SMTP_SSL (true when port 465) / SMTP_STARTTLS (true otherwise)
  All settings can also live in a .env file next to auth.py (see .env.example).
  Test email delivery: python auth.py test-email --to you@example.com
"""
from __future__ import annotations

import argparse
import base64
import csv
import logging
import smtplib
import getpass
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import struct
import sys
import threading
import time
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent


def load_dotenv(path: Path = BASE_DIR / ".env") -> list[str]:
    """Read KEY=VALUE lines from .env into the environment (no extra package needed).
    Variables already set in the real environment win. Lines starting with # are
    comments; values may be wrapped in single or double quotes."""
    loaded = []
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


# Settings file next to this script (see .env.example). Loaded before any setting
# below is read, and before app.py reads its own settings (app.py imports auth first).
DOTENV_KEYS = load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Vercel (and most other serverless platforms) set this automatically; nothing
# else in this file depends on it besides picking a writable default DB path.
IS_SERVERLESS = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))

AUTH_ENABLED = _env_bool("AUTH_ENABLED", True)
# AUTH_DB: SQLite file for users, sessions and the audit log.
#   - Local/Docker (default): BASE_DIR/data/portal.db, a real path on a real disk.
#   - Serverless (Vercel/Lambda) default: /tmp/auth.db. /var/task (BASE_DIR) is
#     read-only at runtime there, so writing under it raises
#     OSError: [Errno 30] Read-only file system.
#   /tmp IS NOT PERSISTENT STORAGE on Vercel: it is a per-instance scratch disk
#   that is wiped whenever the function's container is recycled (which can
#   happen between any two requests), and it is never shared across concurrent
#   instances. Every cold start effectively starts with an empty user/session/
#   audit database. This unblocks deployment but does NOT give you durable
#   accounts, sessions or audit history in production - set AUTH_DB to a
#   network database (see below) for that. Set AUTH_DB explicitly to override
#   either default.
AUTH_DB = os.getenv("AUTH_DB", "/tmp/auth.db" if IS_SERVERLESS else str(BASE_DIR / "data" / "portal.db"))
SESSION_TTL = max(5, _env_int("SESSION_TTL_MINUTES", 720)) * 60
ONLINE_WINDOW = max(15, _env_int("ONLINE_WINDOW_SECONDS", 60))
MAX_FAILED = max(1, _env_int("MAX_FAILED_LOGINS", 5))
LOCKOUT = max(1, _env_int("LOCKOUT_MINUTES", 15)) * 60
AUDIT_RETENTION = max(1, _env_int("AUDIT_RETENTION_DAYS", 90)) * 86400
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "admin@vglearner.local").split(",") if e.strip()}

SIGNUP_MODE = os.getenv("SIGNUP_MODE", "open").strip().lower()
if SIGNUP_MODE not in ("open", "approval", "closed"):
    SIGNUP_MODE = "open"
SIGNUP_DOMAINS = {d.strip().lower().lstrip("@") for d in os.getenv("SIGNUP_DOMAINS", "").split(",") if d.strip()}
PASSWORD_MIN = max(8, _env_int("PASSWORD_MIN_LENGTH", 12))
REMEMBER_TTL = max(1, _env_int("REMEMBER_ME_DAYS", 30)) * 86400
RESET_TTL = max(5, _env_int("RESET_TOKEN_MINUTES", 30)) * 60
EMAIL_VERIFICATION = _env_bool("EMAIL_VERIFICATION", True)
VERIFY_TTL = max(1, _env_int("VERIFY_TOKEN_HOURS", 24)) * 3600
INVITE_TTL = max(1, _env_int("INVITE_TOKEN_HOURS", 72)) * 3600
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = _env_int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@vglearner.local")
SMTP_SSL = _env_bool("SMTP_SSL", SMTP_PORT == 465)       # implicit TLS (port 465)
SMTP_STARTTLS = _env_bool("SMTP_STARTTLS", not SMTP_SSL)  # upgrade a plain connection (port 587)
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "123456789012", "qwertyuiop", "qwerty123456",
    "letmein123", "welcome123", "iloveyou123", "admin123456", "administrator", "changeme123",
    "123456789", "1234567890", "abcdefghijkl", "aaaaaaaaaaaa", "trustno1trustno1", "football123",
}
log = logging.getLogger("vg.auth")

ROLES = ("admin", "learner")
PROFILES = {"admin": "Admin Profile", "learner": "User Profile"}
CLIENT_EVENTS = {"EXAM_STARTED", "EXAM_SUBMITTED"}
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
ISSUER = "VG Learner Portal"
# Verified against when the email is unknown, so response time does not reveal
# which accounts exist.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


# ---------------------------------------------------------------------------
# TOTP (RFC 6238)
# ---------------------------------------------------------------------------
def totp_new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_verify(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """Return the matched time step, or None. Steps <= last_step are replays."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    step = int((now or time.time()) // 30)
    for s in (step, step - 1, step + 1):
        if last_step is not None and s <= last_step:
            continue
        if hmac.compare_digest(_totp_at(secret, s), code):
            return s
    return None


def totp_uri(email: str, secret: str) -> str:
    return (f"otpauth://totp/{quote(ISSUER)}:{quote(email)}?secret={secret}"
            f"&issuer={quote(ISSUER)}&algorithm=SHA1&digits=6&period=30")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
class AuthStore:
    @staticmethod
    def _writable_path(path: str) -> str:
        """Create the DB's parent directory, falling back to /tmp if that
        location turns out to be read-only (e.g. AUTH_DB was left pointing at
        BASE_DIR/data on a serverless platform). /tmp is ephemeral - see the
        AUTH_DB comment above - so this is a last-resort safety net to avoid a
        hard crash, not a substitute for setting AUTH_DB/a real database."""
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            return path
        except OSError as exc:
            fallback = f"/tmp/{Path(path).name or 'auth.db'}"
            log.warning(
                "AUTH_DB path %r is not writable (%s); falling back to ephemeral %r. "
                "Set AUTH_DB to a writable path, or use a persistent database.",
                path, exc, fallback,
            )
            Path(fallback).parent.mkdir(parents=True, exist_ok=True)
            return fallback

    def __init__(self, path: str):
        path = self._writable_path(path)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, role TEXT NOT NULL,
                    totp_secret TEXT, totp_last_step INTEGER, created_at REAL NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                    sid TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, email TEXT NOT NULL,
                    role TEXT NOT NULL, provider TEXT NOT NULL, ip TEXT, user_agent TEXT,
                    created_at REAL NOT NULL, last_seen REAL NOT NULL, expires_at REAL NOT NULL,
                    status TEXT NOT NULL, ended_at REAL, ended_by TEXT);
                CREATE INDEX IF NOT EXISTS ix_sessions_status ON sessions(status);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, email TEXT,
                    action TEXT NOT NULL, ip TEXT, sid TEXT, detail TEXT);
                CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts);
                CREATE INDEX IF NOT EXISTS ix_audit_email_action ON audit(email, action, ts);
                CREATE TABLE IF NOT EXISTS resets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, token_hash TEXT UNIQUE,
                    status TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL, used_at REAL,
                    ip TEXT, issued_by TEXT);
                CREATE INDEX IF NOT EXISTS ix_resets_email ON resets(email, status);
            """)
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(users)")}
            if "name" not in cols:
                self.db.execute("ALTER TABLE users ADD COLUMN name TEXT")
            if "pending" not in cols:
                self.db.execute("ALTER TABLE users ADD COLUMN pending INTEGER NOT NULL DEFAULT 0")
            if "email_verified" not in cols:
                # Existing accounts were created by an admin or before verification
                # existed, so they start verified; new sign-ups insert 0.
                self.db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")
            rcols = {r[1] for r in self.db.execute("PRAGMA table_info(resets)")}
            if "purpose" not in rcols:
                self.db.execute("ALTER TABLE resets ADD COLUMN purpose TEXT NOT NULL DEFAULT 'reset'")

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            return self.db.execute(sql, args).rowcount

    # users --------------------------------------------------------------
    def user(self, email: str):
        rows = self.q("SELECT * FROM users WHERE email = ?", (email,))
        return rows[0] if rows else None

    def user_count(self) -> int:
        return self.q("SELECT COUNT(*) AS n FROM users WHERE disabled = 0 AND pending = 0 AND email_verified = 1")[0]["n"]

    def count_actions(self, actions: tuple, since: float, email: str | None = None, ip: str | None = None) -> int:
        sql = f"SELECT COUNT(*) AS n FROM audit WHERE ts > ? AND action IN ({','.join('?' * len(actions))})"
        args: list = [since, *actions]
        if email is not None:
            sql += " AND email = ?"
            args.append(email)
        if ip is not None:
            sql += " AND ip = ?"
            args.append(ip)
        return self.q(sql, tuple(args))[0]["n"]

    def revoke_user_sessions(self, email: str, by: str, keep_sid: str | None = None) -> int:
        return self.x("UPDATE sessions SET status = 'REVOKED', ended_at = ?, ended_by = ? "
                      "WHERE email = ? AND status = 'ACTIVE' AND sid != ?", (time.time(), by, email, keep_sid or ""))

    def issue_token(self, email: str, ip: str, by: str, purpose: str = "reset", ttl: int | None = None) -> str:
        """Create a single-use reset / verification token (only its hash is stored)."""
        now = time.time()
        token = secrets.token_urlsafe(32)
        ttl = ttl or (VERIFY_TTL if purpose == "verify" else RESET_TTL)
        self.x("UPDATE resets SET status = 'SUPERSEDED' WHERE email = ? AND purpose = ? AND status IN ('ISSUED', 'REQUESTED')",
               (email, purpose))
        self.x("INSERT INTO resets (email, token_hash, status, created_at, expires_at, ip, issued_by, purpose) "
               "VALUES (?, ?, 'ISSUED', ?, ?, ?, ?, ?)", (email, _hash_token(token), now, now + ttl, ip, by, purpose))
        return token

    def issue_reset(self, email: str, ip: str, by: str) -> str:
        return self.issue_token(email, ip, by, "reset")

    def queue_request(self, email: str, ip: str, purpose: str):
        """No SMTP: record that the user needs a link so an admin can send one."""
        self.x("UPDATE resets SET status = 'SUPERSEDED' WHERE email = ? AND purpose = ? AND status = 'REQUESTED'", (email, purpose))
        self.x("INSERT INTO resets (email, status, created_at, ip, purpose) VALUES (?, 'REQUESTED', ?, ?, ?)",
               (email, time.time(), ip, purpose))

    # audit --------------------------------------------------------------
    def audit(self, action: str, email: str | None, ip: str | None, sid: str | None = None, detail: str = ""):
        self.x("INSERT INTO audit (ts, email, action, ip, sid, detail) VALUES (?, ?, ?, ?, ?, ?)",
               (time.time(), (email or "")[:254], action, ip, sid, (detail or "")[:300]))

    def failures(self, email: str, ip: str) -> tuple[int, int]:
        since = time.time() - LOCKOUT
        by_email = self.q("SELECT COUNT(*) AS n FROM audit WHERE email = ? AND ts > ? "
                          "AND action IN ('LOGIN_FAILED', 'MFA_FAILED')", (email, since))[0]["n"]
        by_ip = self.q("SELECT COUNT(*) AS n FROM audit WHERE ip = ? AND ts > ? "
                       "AND action IN ('LOGIN_FAILED', 'MFA_FAILED')", (ip, since))[0]["n"]
        return by_email, by_ip

    # sessions -----------------------------------------------------------
    def sweep(self):
        """Mark expired sessions and prune old audit rows."""
        now = time.time()
        expired = self.q("SELECT sid, email, ip FROM sessions WHERE status = 'ACTIVE' AND expires_at <= ?", (now,))
        for r in expired:
            if self.x("UPDATE sessions SET status = 'EXPIRED', ended_at = ? WHERE sid = ? AND status = 'ACTIVE'",
                      (now, r["sid"])):
                self.audit("SESSION_EXPIRED", r["email"], r["ip"], r["sid"], "Absolute session lifetime reached")
        self.x("DELETE FROM audit WHERE ts < ?", (now - AUDIT_RETENTION,))

    def create_session(self, email: str, role: str, provider: str, ip: str, ua: str, ttl: int = SESSION_TTL) -> tuple[str, sqlite3.Row]:
        token = secrets.token_urlsafe(32)
        sid = secrets.token_hex(8)
        now = time.time()
        self.x("INSERT INTO sessions (sid, token_hash, email, role, provider, ip, user_agent, created_at, "
               "last_seen, expires_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')",
               (sid, _hash_token(token), email, role, provider, ip, ua[:200], now, now, now + ttl))
        return token, self.q("SELECT * FROM sessions WHERE sid = ?", (sid,))[0]

    def session_by_token(self, token: str):
        rows = self.q("SELECT * FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        return rows[0] if rows else None

    def end_session(self, sid: str, status: str, by: str) -> bool:
        return bool(self.x("UPDATE sessions SET status = ?, ended_at = ?, ended_by = ? "
                           "WHERE sid = ? AND status = 'ACTIVE'", (status, time.time(), by, sid)))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _session_json(s) -> dict:
    return {
        "sid": s["sid"], "email": s["email"], "role": s["role"], "profile": PROFILES.get(s["role"], s["role"]),
        "ip": s["ip"],
        "user_agent": s["user_agent"], "created_at": s["created_at"], "last_seen": s["last_seen"],
        "expires_at": s["expires_at"], "status": s["status"], "ended_at": s["ended_at"],
        "ended_by": s["ended_by"], "online": s["status"] == "ACTIVE" and time.time() - s["last_seen"] <= ONLINE_WINDOW,
    }


def password_problem(password: str, email: str = "", name: str = "") -> str | None:
    """Server-side password policy (the browser meter is only a hint)."""
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if len(password) > 256:
        return "Use at most 256 characters."
    low = password.lower()
    if low in COMMON_PASSWORDS or len(set(password)) < 5:
        return "That password is too common or repetitive. Try a longer passphrase."
    local = email.split("@")[0].lower()
    if len(local) >= 4 and local in low:
        return "Don't include your email address in the password."
    if name and len(name) >= 4 and name.lower().replace(" ", "") in low.replace(" ", ""):
        return "Don't include your name in the password."
    return None


def signup_email_problem(email: str) -> str | None:
    if not EMAIL_RE.match(email):
        return "Enter a valid email address."
    if email in ADMIN_EMAILS:
        return "This email address is reserved. Ask an administrator for access."
    if SIGNUP_DOMAINS and email.rsplit("@", 1)[1] not in SIGNUP_DOMAINS:
        return "Sign-up is limited to: " + ", ".join(sorted("@" + d for d in SIGNUP_DOMAINS)) + "."
    return None


def smtp_configured() -> bool:
    return bool(SMTP_HOST)


def _base_url() -> str:
    try:
        return PUBLIC_URL or request.host_url.rstrip("/")
    except RuntimeError:  # outside a request (CLI)
        return PUBLIC_URL or "http://localhost:5000"


def reset_link(token: str) -> str:
    return f"{_base_url()}/#reset={token}"


def verify_link(token: str) -> str:
    return f"{_base_url()}/#verify={token}"


def deliver_email(to: str, subject: str, body: str):
    """Send one plain-text email now (raises on failure)."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg.set_content(body, cte="7bit")  # 7bit keeps the link on one unencoded line in every mail client
    smtp_cls = smtplib.SMTP_SSL if SMTP_SSL else smtplib.SMTP
    with smtp_cls(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        if SMTP_STARTTLS and not SMTP_SSL:
            smtp.starttls()
        if SMTP_USER:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)


def send_email(to: str, subject: str, body: str, kind: str):
    """Send in a background thread so response time never reveals account existence."""
    def _send():
        try:
            deliver_email(to, subject, body)
            log.info("%s email sent to %s", kind, to)
        except Exception as exc:  # noqa: BLE001 - delivery problems must not crash the request thread
            log.error("%s email to %s failed: %s", kind, to, exc)
    threading.Thread(target=_send, name=f"{kind.lower()}-mail", daemon=True).start()


def send_reset_email(to: str, link: str):
    send_email(to, "Reset your VG Learner Portal password",
               "Someone (hopefully you) asked to reset the password for your VG Learner Portal account.\n\n"
               f"Open this link within {RESET_TTL // 60} minutes to choose a new password:\n{link}\n\n"
               "The link works once. If you did not ask for this, ignore this email; your password stays the same.\n",
               "Password reset")


def send_verify_email(to: str, name: str, link: str):
    send_email(to, "Verify your email for VG Learner Portal",
               f"Hi {name or 'there'},\n\nWelcome to VG Learner Portal! Please confirm this is your email address "
               f"by opening the link below within {VERIFY_TTL // 3600} hours:\n{link}\n\n"
               "You can log in after verifying. If you did not create an account, ignore this email.\n",
               "Verification")


def start_verification(st, email: str, name: str, ip: str, by: str) -> str:
    """Email a verification link, or queue the request for an admin without SMTP."""
    if smtp_configured():
        send_verify_email(email, name, verify_link(st.issue_token(email, ip, by, "verify")))
        return "email"
    st.queue_request(email, ip, "verify")
    return "admin"


def effective_role(email: str, stored_role: str) -> str:
    """RBAC parsing: listed admin emails always resolve to the Admin Profile."""
    return "admin" if stored_role == "admin" or email.lower() in ADMIN_EMAILS else "learner"


# ---------------------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------------------
_store: AuthStore | None = None


def store() -> AuthStore:
    global _store
    if _store is None:
        _store = AuthStore(AUTH_DB)
    return _store


def _ip() -> str:
    return request.remote_addr or "unknown"


def _authenticate():
    """Return (session_row, None) or (None, (response, status))."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None, (jsonify(error="Sign in required", reason="missing"), 401)
    s = store().session_by_token(header[7:].strip())
    if s is None:
        return None, (jsonify(error="Invalid session", reason="invalid"), 401)
    if s["status"] == "ACTIVE" and s["expires_at"] <= time.time():
        store().sweep()
        s = store().q("SELECT * FROM sessions WHERE sid = ?", (s["sid"],))[0]
    if s["status"] != "ACTIVE":
        reason = {"REVOKED": "revoked", "EXPIRED": "expired"}.get(s["status"], "ended")
        msg = {"revoked": "Your session was revoked by an administrator.",
               "expired": "Your session expired. Sign in again.",
               "ended": "You signed out of this session."}[reason]
        return None, (jsonify(error=msg, reason=reason, ended_by=s["ended_by"]), 401)
    store().x("UPDATE sessions SET last_seen = ? WHERE sid = ?", (time.time(), s["sid"]))
    return s, None


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not AUTH_ENABLED or request.method == "OPTIONS":
            g.session = None
            return fn(*args, **kwargs)
        s, err = _authenticate()
        if err:
            return err
        g.session = s
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    @wraps(fn)
    @require_auth
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return ("", 204)
        s = g.session
        if AUTH_ENABLED and (s is None or s["role"] != "admin"):
            store().audit("ACCESS_DENIED", s["email"] if s else None, _ip(), s["sid"] if s else None,
                          f"{request.method} {request.path}")
            return jsonify(error="Administrator role required"), 403
        return fn(*args, **kwargs)
    return wrapper


bp = Blueprint("auth", __name__)


@bp.get("/api/auth/config")
def auth_config():
    return jsonify(auth_enabled=AUTH_ENABLED, has_users=store().user_count() > 0 if AUTH_ENABLED else True,
                   profiles=PROFILES, session_ttl_minutes=SESSION_TTL // 60,
                   signup_mode=SIGNUP_MODE, signup_domains=sorted(SIGNUP_DOMAINS), password_min=PASSWORD_MIN,
                   remember_days=REMEMBER_TTL // 86400, reset_minutes=RESET_TTL // 60,
                   reset_delivery="email" if smtp_configured() else "admin",
                   email_verification=EMAIL_VERIFICATION,
                   heartbeat_seconds=5, max_failed_logins=MAX_FAILED, lockout_minutes=LOCKOUT // 60)


@bp.post("/api/auth/login")
def login():
    if not AUTH_ENABLED:
        return jsonify(error="Authentication is disabled on this server"), 400
    st = store()
    st.sweep()
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()[:254]
    password = str(body.get("password", ""))[:1024]
    code = str(body.get("mfa_code", "")).strip()[:12]
    remember = bool(body.get("remember"))
    ip, ua = _ip(), request.headers.get("User-Agent", "")

    if not EMAIL_RE.match(email) or not password:
        return jsonify(error="Enter a valid email address and password.", field="email" if not EMAIL_RE.match(email) else "password"), 400

    by_email, by_ip = st.failures(email, ip)
    if by_email >= MAX_FAILED or by_ip >= MAX_FAILED * 4:
        st.audit("LOGIN_LOCKED", email, ip, None, f"{max(by_email, by_ip)} failures in {LOCKOUT // 60} min")
        return jsonify(error=f"Too many failed attempts. Try again in {LOCKOUT // 60} minutes."), 429

    user = st.user(email)
    ok = check_password_hash(user["pw_hash"] if user else _DUMMY_HASH, password)
    if not user or not ok or user["disabled"]:
        st.audit("LOGIN_FAILED", email, ip, None, "Bad email or password" + (" (disabled)" if user and user["disabled"] else ""))
        return jsonify(error="Incorrect email or password."), 401

    if not user["email_verified"]:
        st.audit("LOGIN_FAILED", email, ip, None, "Email not verified")
        return jsonify(error=("Please verify your email address before logging in. Open the verification link we emailed you."
                              if smtp_configured() else
                              "Your email address isn't verified yet. An administrator must send you a verification link or verify it before you can log in."),
                       unverified=True, delivery="email" if smtp_configured() else "admin"), 403

    if user["pending"]:
        st.audit("LOGIN_FAILED", email, ip, None, "Account awaiting approval")
        return jsonify(error="Your account is waiting for administrator approval. You can log in once it is approved.", pending=True), 403

    if user["totp_secret"]:
        st.audit("MFA_TRIGGERED", email, ip, None, "Authenticator code requested")
        if not code:
            return jsonify(error="Enter the 6-digit code from your authenticator app.", mfa_required=True, field="mfa"), 401
        step = totp_verify(user["totp_secret"], code, user["totp_last_step"])
        if step is None:
            st.audit("MFA_FAILED", email, ip, None, "Invalid, expired or reused TOTP code")
            return jsonify(error="That MFA code is invalid or was already used. Wait for the next code.", mfa_required=True, field="mfa"), 401
        st.x("UPDATE users SET totp_last_step = ? WHERE email = ?", (step, email))
        st.audit("MFA_SUCCESS", email, ip, None, "TOTP verified")

    role = effective_role(email, user["role"])
    token, s = st.create_session(email, role, "password", ip, ua, REMEMBER_TTL if remember else SESSION_TTL)
    st.audit("LOGIN_SUCCESS", email, ip, s["sid"], f"{PROFILES[role]}{' · MFA' if user['totp_secret'] else ''}{' · remember me' if remember else ''}")
    return jsonify(token=token, session=_session_json(s), remember=remember,
                   user={"email": email, "name": user["name"] or "", "role": role, "profile": PROFILES[role], "mfa": bool(user["totp_secret"])})


@bp.post("/api/auth/signup")
def signup():
    if not AUTH_ENABLED or SIGNUP_MODE == "closed":
        return jsonify(error="Self sign-up is turned off. Ask an administrator to create your account."), 403
    st = store()
    body = request.get_json(silent=True) or {}
    name = " ".join(str(body.get("name", "")).split())[:80]
    email = str(body.get("email", "")).strip().lower()[:254]
    password = str(body.get("password", ""))[:1024]
    ip, ua = _ip(), request.headers.get("User-Agent", "")
    if len(name) < 2:
        return jsonify(error="Enter your full name.", field="name"), 400
    problem = signup_email_problem(email)
    if problem:
        return jsonify(error=problem, field="email"), 400
    problem = password_problem(password, email, name)
    if problem:
        return jsonify(error=problem, field="password"), 400
    if not body.get("accept_terms"):
        return jsonify(error="Please accept the Terms of Use and Privacy Notice.", field="terms"), 400
    if st.count_actions(("SIGNUP", "SIGNUP_PENDING", "SIGNUP_DUPLICATE", "SIGNUP_UNVERIFIED"), time.time() - 3600, ip=ip) >= 5:
        return jsonify(error="Too many sign-up attempts from this network. Try again in an hour."), 429
    if st.user(email):
        st.audit("SIGNUP_DUPLICATE", email, ip, None, "Email already registered")
        return jsonify(error="An account with this email already exists. Log in, or use Forgot password.", field="email"), 409
    pending = SIGNUP_MODE == "approval"
    try:
        st.x("INSERT INTO users (email, pw_hash, role, totp_secret, created_at, name, pending, email_verified) "
             "VALUES (?, ?, 'learner', NULL, ?, ?, ?, ?)",
             (email, generate_password_hash(password), time.time(), name, 1 if pending else 0, 0 if EMAIL_VERIFICATION else 1))
    except sqlite3.IntegrityError:
        return jsonify(error="An account with this email already exists. Log in, or use Forgot password.", field="email"), 409
    if EMAIL_VERIFICATION:
        how = start_verification(st, email, name, ip, "sign-up")
        st.audit("SIGNUP_UNVERIFIED", email, ip, None,
                 f"Self sign-up by {name}; verification link {'emailed' if how == 'email' else 'queued for an administrator'}"
                 + ("; approval also required" if pending else ""))
        msg = (f"Account created. We sent a verification link to {email}. Open it within {VERIFY_TTL // 3600} hours, then log in."
               if how == "email" else
               "Account created. An administrator will send you a verification link (or verify your email for you). You can log in after that.")
        if pending:
            msg += " An administrator must also approve the account."
        return jsonify(verify_required=True, delivery=how, pending=pending, message=msg), 202
    if pending:
        st.audit("SIGNUP_PENDING", email, ip, None, f"Self sign-up by {name}; awaiting approval")
        return jsonify(pending=True, message="Account created. An administrator must approve it before you can log in."), 202
    st.audit("SIGNUP", email, ip, None, f"Self sign-up by {name} · User Profile")
    token, s = st.create_session(email, "learner", "password", ip, ua)
    st.audit("LOGIN_SUCCESS", email, ip, s["sid"], "User Profile · first login after sign-up")
    return jsonify(token=token, session=_session_json(s), remember=False, created=True,
                   user={"email": email, "name": name, "role": "learner", "profile": PROFILES["learner"], "mfa": False}), 201


@bp.post("/api/auth/forgot")
def forgot_password():
    if not AUTH_ENABLED:
        return jsonify(error="Authentication is disabled on this server"), 400
    st = store()
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()[:254]
    ip = _ip()
    if not EMAIL_RE.match(email):
        return jsonify(error="Enter a valid email address.", field="email"), 400
    generic = (f"If an account exists for {email}, a password reset link has been sent. It expires in {RESET_TTL // 60} minutes."
               if smtp_configured() else
               f"If an account exists for {email}, your reset request has been sent to an administrator, who will send you a reset link.")
    hour = time.time() - 3600
    if (st.count_actions(("PASSWORD_RESET_REQUESTED",), hour, email=email) >= 3
            or st.count_actions(("PASSWORD_RESET_REQUESTED",), hour, ip=ip) >= 10):
        return jsonify(message=generic)  # silently rate limited: same answer, nothing sent
    user = st.user(email)
    eligible = bool(user and not user["disabled"] and not user["pending"])
    if eligible and smtp_configured():
        token = st.issue_reset(email, ip, "self-service")
        send_reset_email(email, reset_link(token))
        detail = "Reset link emailed"
    elif eligible:
        st.queue_request(email, ip, "reset")
        detail = "Queued for an administrator (no SMTP configured)"
    else:
        detail = "No eligible account (generic answer returned)"
    st.audit("PASSWORD_RESET_REQUESTED", email, ip, None, detail)
    return jsonify(message=generic)


@bp.post("/api/auth/verify")
def verify_email():
    st = store()
    body = request.get_json(silent=True) or {}
    token = str(body.get("token", ""))[:200]
    rows = st.q("SELECT id, email FROM resets WHERE token_hash = ? AND purpose = 'verify' AND status = 'ISSUED' AND expires_at > ?",
                (_hash_token(token), time.time()))
    if not rows:
        return jsonify(error="This verification link is invalid, already used or expired. Log in to request a new one."), 400
    email = rows[0]["email"]
    user = st.user(email)
    if not user:
        return jsonify(error="This account no longer exists."), 400
    st.x("UPDATE users SET email_verified = 1 WHERE email = ?", (email,))
    st.x("UPDATE resets SET status = 'USED', used_at = ? WHERE id = ?", (time.time(), rows[0]["id"]))
    st.x("UPDATE resets SET status = 'SUPERSEDED' WHERE email = ? AND purpose = 'verify' AND status = 'REQUESTED'", (email,))
    st.audit("EMAIL_VERIFIED", email, _ip(), None, "Verified with the emailed link")
    msg = "Your email address is verified."
    msg += " An administrator still needs to approve your account before you can log in." if user["pending"] else " You can log in now."
    return jsonify(ok=True, email=email, pending=bool(user["pending"]), message=msg)


@bp.post("/api/auth/resend-verification")
def resend_verification():
    st = store()
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()[:254]
    ip = _ip()
    if not EMAIL_RE.match(email):
        return jsonify(error="Enter a valid email address.", field="email"), 400
    generic = (f"If {email} has an unverified account, a new verification link is on its way."
               if smtp_configured() else
               f"If {email} has an unverified account, an administrator has been asked to verify it.")
    hour = time.time() - 3600
    if (st.count_actions(("VERIFICATION_RESENT",), hour, email=email) >= 3
            or st.count_actions(("VERIFICATION_RESENT",), hour, ip=ip) >= 10):
        return jsonify(message=generic)
    user = st.user(email)
    if user and not user["email_verified"] and not user["disabled"]:
        how = start_verification(st, email, user["name"] or "", ip, "resend")
        st.audit("VERIFICATION_RESENT", email, ip, None, "Emailed" if how == "email" else "Queued for an administrator")
    else:
        st.audit("VERIFICATION_RESENT", email, ip, None, "No unverified account (generic answer returned)")
    return jsonify(message=generic)


@bp.get("/api/auth/reset")
def reset_check():
    token = request.args.get("token", "")
    rows = store().q("SELECT email FROM resets WHERE token_hash = ? AND purpose = 'reset' AND status = 'ISSUED' AND expires_at > ?",
                     (_hash_token(token), time.time()))
    if not rows:
        return jsonify(valid=False, error="This reset link is invalid, already used or expired. Request a new one."), 400
    email = rows[0]["email"]
    masked = email[0] + "•••" + email[email.index("@") - 1:] if email.index("@") > 1 else email
    return jsonify(valid=True, email=masked)


@bp.post("/api/auth/reset")
def reset_password():
    st = store()
    body = request.get_json(silent=True) or {}
    token = str(body.get("token", ""))[:200]
    password = str(body.get("password", ""))[:1024]
    rows = st.q("SELECT id, email FROM resets WHERE token_hash = ? AND purpose = 'reset' AND status = 'ISSUED' AND expires_at > ?",
                (_hash_token(token), time.time()))
    if not rows:
        return jsonify(error="This reset link is invalid, already used or expired. Request a new one.", field="token"), 400
    email = rows[0]["email"]
    user = st.user(email)
    if not user or user["disabled"]:
        return jsonify(error="This account can't be reset. Contact an administrator.", field="token"), 400
    problem = password_problem(password, email, user["name"] or "")
    if problem:
        return jsonify(error=problem, field="password"), 400
    if check_password_hash(user["pw_hash"], password):
        return jsonify(error="Choose a password you have not used for this account.", field="password"), 400
    # Opening a link sent to the address proves ownership, so it also verifies it.
    st.x("UPDATE users SET pw_hash = ?, email_verified = 1 WHERE email = ?", (generate_password_hash(password), email))
    st.x("UPDATE resets SET status = 'USED', used_at = ? WHERE id = ?", (time.time(), rows[0]["id"]))
    n = st.revoke_user_sessions(email, "password-reset")
    st.audit("PASSWORD_RESET", email, _ip(), None, f"Password reset via link; {n} session(s) signed out")
    return jsonify(ok=True, message="Your password has been reset. Log in with your new password.")


@bp.route("/api/auth/session", methods=["GET", "OPTIONS"])
@require_auth
def session_heartbeat():
    if request.method == "OPTIONS" or g.session is None:
        return jsonify(ok=True, auth_enabled=AUTH_ENABLED)
    s = g.session
    return jsonify(session=_session_json(s), user={"email": s["email"], "role": s["role"], "profile": PROFILES.get(s["role"], s["role"])})


@bp.route("/api/auth/logout", methods=["POST", "OPTIONS"])
@require_auth
def logout():
    if request.method == "OPTIONS" or g.session is None:
        return ("", 204)
    s = g.session
    store().end_session(s["sid"], "ENDED", s["email"])
    store().audit("LOGOUT", s["email"], _ip(), s["sid"], "User signed out")
    return jsonify(ok=True)


@bp.route("/api/auth/change-password", methods=["POST", "OPTIONS"])
@require_auth
def change_password():
    if request.method == "OPTIONS" or g.session is None:
        return ("", 204)
    st = store()
    s = g.session
    body = request.get_json(silent=True) or {}
    current = str(body.get("current", ""))[:1024]
    new = str(body.get("new", ""))[:1024]
    user = st.user(s["email"])
    if not user or not check_password_hash(user["pw_hash"], current):
        st.audit("PASSWORD_CHANGE_FAILED", s["email"], _ip(), s["sid"], "Wrong current password")
        return jsonify(error="Your current password is incorrect.", field="current"), 400
    problem = password_problem(new, s["email"], user["name"] or "")
    if problem:
        return jsonify(error=problem, field="new"), 400
    if current == new:
        return jsonify(error="Choose a different password from your current one.", field="new"), 400
    st.x("UPDATE users SET pw_hash = ? WHERE email = ?", (generate_password_hash(new), s["email"]))
    n = st.revoke_user_sessions(s["email"], "password-change", keep_sid=s["sid"])
    st.audit("PASSWORD_CHANGED", s["email"], _ip(), s["sid"], f"Changed by the user; {n} other session(s) signed out")
    return jsonify(ok=True, message="Password changed." + (f" {n} other session(s) were signed out." if n else ""))


@bp.route("/api/audit/event", methods=["POST", "OPTIONS"])
@require_auth
def client_event():
    if request.method == "OPTIONS" or g.session is None:
        return ("", 204)
    body = request.get_json(silent=True) or {}
    action = str(body.get("action", ""))
    if action not in CLIENT_EVENTS:
        return jsonify(error="Unknown event"), 400
    s = g.session
    store().audit(action, s["email"], _ip(), s["sid"], str(body.get("detail", ""))[:300])
    return jsonify(ok=True), 201


@bp.route("/api/admin/sessions", methods=["GET", "OPTIONS"])
@require_admin
def admin_sessions():
    st = store()
    st.sweep()
    now = time.time()
    active = [_session_json(s) for s in st.q(
        "SELECT * FROM sessions WHERE status = 'ACTIVE' ORDER BY last_seen DESC")]
    day = now - 86400
    stats = {
        "active_sessions": len(active),
        "online_now": sum(1 for s in active if s["online"]),
        "unique_users": len({s["email"] for s in active}),
        "admins_active": sum(1 for s in active if s["role"] == "admin"),
        "failed_24h": st.q("SELECT COUNT(*) AS n FROM audit WHERE ts > ? AND action IN "
                           "('LOGIN_FAILED','MFA_FAILED','LOGIN_LOCKED')", (day,))[0]["n"],
        "revoked_24h": st.q("SELECT COUNT(*) AS n FROM audit WHERE ts > ? AND action = 'SESSION_REVOKED'", (day,))[0]["n"],
        "logins_24h": st.q("SELECT COUNT(*) AS n FROM audit WHERE ts > ? AND action = 'LOGIN_SUCCESS'", (day,))[0]["n"],
        "online_window_s": ONLINE_WINDOW,
    }
    return jsonify(stats=stats, sessions=active, server_time=now, me=g.session["sid"] if g.session else None)


@bp.route("/api/admin/audit", methods=["GET", "OPTIONS"])
@require_admin
def admin_audit():
    st = store()
    limit = min(1000, max(1, request.args.get("limit", 250, type=int)))
    action = request.args.get("action", "").strip().upper()
    text = request.args.get("q", "").strip().lower()
    sql = ("SELECT a.id, a.ts, a.email, a.action, a.ip, a.sid, a.detail, s.status AS session_status, "
           "s.expires_at, s.role FROM audit a LEFT JOIN sessions s ON s.sid = a.sid WHERE 1 = 1")
    args: list = []
    if action:
        sql += " AND a.action = ?"
        args.append(action)
    if text:
        sql += " AND (LOWER(a.email) LIKE ? OR a.ip LIKE ? OR LOWER(a.detail) LIKE ? OR a.sid LIKE ?)"
        like = f"%{text}%"
        args += [like, like, like, like]
    sql += " ORDER BY a.id DESC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in st.q(sql, tuple(args))]
    return jsonify(rows=rows, actions=sorted(r["action"] for r in st.q("SELECT DISTINCT action FROM audit")))


@bp.route("/api/admin/users", methods=["GET", "OPTIONS"])
@require_admin
def admin_users():
    st = store()
    rows = st.q("""
        SELECT u.email, u.name, u.role, u.disabled, u.pending, u.email_verified, u.created_at, u.totp_secret IS NOT NULL AS mfa,
          (SELECT MAX(ts) FROM audit a WHERE a.email = u.email AND a.action = 'LOGIN_SUCCESS') AS last_login,
          (SELECT COUNT(*) FROM sessions s WHERE s.email = u.email AND s.status = 'ACTIVE') AS active_sessions,
          (SELECT MAX(created_at) FROM resets r WHERE r.email = u.email AND r.status = 'REQUESTED' AND r.purpose = 'reset') AS reset_requested,
          (SELECT MAX(created_at) FROM resets r WHERE r.email = u.email AND r.status = 'REQUESTED' AND r.purpose = 'verify') AS verify_requested
        FROM users u ORDER BY u.email_verified ASC, u.pending DESC, reset_requested IS NULL, u.created_at DESC""")
    users = []
    for r in rows:
        role = effective_role(r["email"], r["role"])
        users.append({"email": r["email"], "name": r["name"] or "", "role": role, "profile": PROFILES[role],
                      "status": ("DISABLED" if r["disabled"] else "UNVERIFIED" if not r["email_verified"]
                                 else "PENDING" if r["pending"] else "ACTIVE"),
                      "verified": bool(r["email_verified"]), "pending": bool(r["pending"]),
                      "mfa": bool(r["mfa"]), "created_at": r["created_at"], "last_login": r["last_login"],
                      "active_sessions": r["active_sessions"], "reset_requested": r["reset_requested"],
                      "verify_requested": r["verify_requested"],
                      "protected": r["email"] in ADMIN_EMAILS, "stored_role": r["role"]})
    return jsonify(users=users, signup_mode=SIGNUP_MODE, reset_delivery="email" if smtp_configured() else "admin",
                   email_verification=EMAIL_VERIFICATION)


@bp.route("/api/admin/users/<path:email>/<action>", methods=["POST", "OPTIONS"])
@require_admin
def admin_user_action(email, action):
    st = store()
    email = email.strip().lower()
    user = st.user(email)
    admin = g.session["email"] if g.session else "system"
    if not user:
        return jsonify(error="No such user"), 404
    if action == "approve":
        if not user["pending"]:
            return jsonify(error="This account is not waiting for approval"), 409
        st.x("UPDATE users SET pending = 0 WHERE email = ?", (email,))
        st.audit("SIGNUP_APPROVED", email, _ip(), None, f"Approved by {admin}")
        return jsonify(ok=True, message=f"{email} approved." + (" They still need to verify their email." if not user["email_verified"] else " They can log in now."))
    if action == "reject":
        if not user["pending"]:
            return jsonify(error="Only pending sign-ups can be rejected; disable active accounts instead"), 409
        st.x("DELETE FROM users WHERE email = ?", (email,))
        st.audit("SIGNUP_REJECTED", email, _ip(), None, f"Rejected by {admin}")
        return jsonify(ok=True, message=f"Sign-up for {email} rejected and removed.")
    if action in ("disable", "enable"):
        if email == admin or email in ADMIN_EMAILS:
            return jsonify(error="You can't disable your own or a protected admin account here; use the CLI."), 409
        st.x("UPDATE users SET disabled = ? WHERE email = ?", (1 if action == "disable" else 0, email))
        n = st.revoke_user_sessions(email, admin) if action == "disable" else 0
        st.audit("USER_DISABLED" if action == "disable" else "USER_ENABLED", email, _ip(), None,
                 f"By {admin}" + (f"; {n} session(s) revoked" if n else ""))
        return jsonify(ok=True, message=f"{email} {'disabled' if action == 'disable' else 'enabled'}.")
    if action in ("make-admin", "remove-admin"):
        if email == admin:
            return jsonify(error="You can't change your own role. Ask another admin."), 409
        if action == "remove-admin" and email in ADMIN_EMAILS:
            return jsonify(error="This account is an admin through the ADMIN_EMAILS setting; change it on the server."), 409
        if user["disabled"] or not user["email_verified"] or user["pending"]:
            return jsonify(error="Verify, approve or enable the account first"), 409
        role = "admin" if action == "make-admin" else "learner"
        st.x("UPDATE users SET role = ? WHERE email = ?", (role, email))
        n = st.x("UPDATE sessions SET role = ? WHERE email = ? AND status = 'ACTIVE'", (role, email))  # applies live
        st.audit("ADMIN_GRANTED" if role == "admin" else "ADMIN_REVOKED", email, _ip(), None,
                 f"By {admin}; {n} active session(s) updated")
        return jsonify(ok=True, message=f"{email} is now {PROFILES[role]}." + (f" Their {n} open session(s) switched immediately." if n else ""))
    if action == "verify":
        if user["email_verified"]:
            return jsonify(error="This email address is already verified"), 409
        st.x("UPDATE users SET email_verified = 1 WHERE email = ?", (email,))
        st.x("UPDATE resets SET status = 'SUPERSEDED' WHERE email = ? AND purpose = 'verify' AND status IN ('ISSUED', 'REQUESTED')", (email,))
        st.audit("EMAIL_VERIFIED", email, _ip(), None, f"Marked verified by {admin}")
        return jsonify(ok=True, message=f"{email} is verified." + (" It still needs approval." if user["pending"] else " They can log in now."))
    if action == "verify-link":
        if user["email_verified"]:
            return jsonify(error="This email address is already verified"), 409
        token = st.issue_token(email, _ip(), admin, "verify")
        st.audit("VERIFICATION_ISSUED", email, _ip(), None, f"Verification link created by {admin} (valid {VERIFY_TTL // 3600} h)")
        return jsonify(ok=True, link=verify_link(token), expires_minutes=VERIFY_TTL // 60)
    if action == "reset-link":
        if user["pending"] or user["disabled"]:
            return jsonify(error="Approve or enable the account first"), 409
        token = st.issue_reset(email, _ip(), admin)
        link = reset_link(token)
        st.audit("PASSWORD_RESET_ISSUED", email, _ip(), None, f"Reset link created by {admin} (valid {RESET_TTL // 60} min)")
        return jsonify(ok=True, link=link, expires_minutes=RESET_TTL // 60)
    return jsonify(error="Unknown action"), 400


@bp.route("/api/admin/sessions/<sid>/revoke", methods=["POST", "OPTIONS"])
@require_admin
def admin_revoke(sid):
    st = store()
    rows = st.q("SELECT * FROM sessions WHERE sid = ?", (sid,))
    if not rows:
        return jsonify(error="Session not found"), 404
    target = rows[0]
    admin = g.session["email"] if g.session else "system"
    if not st.end_session(sid, "REVOKED", admin):
        return jsonify(error=f"Session is already {target['status'].lower()}"), 409
    st.audit("SESSION_REVOKED", target["email"], _ip(), sid, f"Revoked by {admin}")
    return jsonify(ok=True, sid=sid, email=target["email"], self_revoked=bool(g.session and g.session["sid"] == sid))


def init_app(app):
    app.register_blueprint(bp)
    if AUTH_ENABLED:
        store()  # create the database at startup, not on the first request


# ---------------------------------------------------------------------------
# CLI: user management
# ---------------------------------------------------------------------------
def _read_password(args) -> str:
    if args.password_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match.")
    if len(pw) < 12:
        sys.exit("Password must be at least 12 characters.")
    return pw


def _print_mfa(email: str, secret: str):
    print("\nMFA enrollment - add this account to your authenticator app:")
    print(f"  Secret key : {secret}")
    print(f"  URI        : {totp_uri(email, secret)}")
    print("  (Type: time-based, 6 digits, 30 seconds.) The secret is shown only now.\n")


def cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python auth.py", description="Manage portal users.")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="create a user (prompts for the password)")
    c.add_argument("--email", required=True)
    c.add_argument("--role", choices=ROLES, default=None,
                   help="default: admin for ADMIN_EMAILS, otherwise learner")
    c.add_argument("--mfa", action="store_true", help="also require an authenticator (TOTP) code at login")
    c.add_argument("--name", default="", help="display name")
    c.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    sub.add_parser("list-users", help="list users")
    r = sub.add_parser("set-role", help="change a user's role (revokes their sessions)")
    r.add_argument("--email", required=True)
    r.add_argument("--role", choices=ROLES, required=True)
    imp = sub.add_parser("import-users", help="bulk-create users from a CSV with columns email,name[,role]")
    imp.add_argument("--csv", required=True)
    imp.add_argument("--send", action="store_true", help="email each user their set-password link (needs SMTP_*)")
    ex = sub.add_parser("export-users", help="write all users (no password hashes) to CSV")
    ex.add_argument("--out", required=True)
    ea = sub.add_parser("export-audit", help="write the audit log to CSV")
    ea.add_argument("--out", required=True)
    ea.add_argument("--days", type=int, default=0, help="only the last N days (0 = everything kept)")
    te = sub.add_parser("test-email", help="send a test email with the current SMTP settings")
    te.add_argument("--to", required=True)
    for name in ("reset-password", "reset-mfa", "disable-mfa", "delete-user", "disable-user", "enable-user", "approve-user", "verify-user"):
        s = sub.add_parser(name)
        s.add_argument("--email", required=True)
        if name == "reset-password":
            s.add_argument("--password-stdin", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "test-email":
        return _test_email(args.to)
    st = store()

    if args.cmd == "import-users":
        return _import_users(st, args)
    if args.cmd == "export-users":
        rows = st.q("SELECT email, name, role, email_verified, pending, disabled, totp_secret IS NOT NULL AS mfa, created_at FROM users ORDER BY email")
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["email", "name", "profile", "email_verified", "pending_approval", "disabled", "mfa", "created_utc"])
            for r in rows:
                w.writerow([r["email"], r["name"] or "", PROFILES[effective_role(r["email"], r["role"])], r["email_verified"],
                            r["pending"], r["disabled"], r["mfa"], time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["created_at"]))])
        print(f"Wrote {len(rows)} user(s) to {args.out} (password hashes are never exported).")
        return 0
    if args.cmd == "export-audit":
        since = time.time() - args.days * 86400 if args.days else 0
        rows = st.q("SELECT a.ts, a.email, a.action, a.ip, a.sid, a.detail, s.status AS session_status FROM audit a "
                    "LEFT JOIN sessions s ON s.sid = a.sid WHERE a.ts >= ? ORDER BY a.id", (since,))
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp_utc", "email", "action", "client_ip", "session_id", "session_status", "detail"])
            for r in rows:
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["ts"])), r["email"], r["action"], r["ip"],
                            r["sid"] or "", r["session_status"] or "", r["detail"] or ""])
        print(f"Wrote {len(rows)} audit event(s) to {args.out}.")
        return 0

    if args.cmd == "list-users":
        rows = st.q("SELECT email, role, totp_secret IS NOT NULL AS mfa, disabled, pending, email_verified, created_at FROM users ORDER BY email")
        if not rows:
            print("No users. Create the admin with: python auth.py create-user --email admin@vglearner.local")
        for r in rows:
            role = effective_role(r["email"], r["role"])
            print(f"{r['email']:<40} {PROFILES[role]:<14} mfa={'yes' if r['mfa'] else 'no ':<3} "
                  f"{'DISABLED ' if r['disabled'] else ''}{'UNVERIFIED ' if not r['email_verified'] else ''}{'PENDING ' if r['pending'] else ''}created {time.strftime('%Y-%m-%d', time.localtime(r['created_at']))}")
        return 0

    email = args.email.strip().lower()
    if not EMAIL_RE.match(email):
        sys.exit(f"Invalid email address: {email}")
    user = st.user(email)

    if args.cmd == "create-user":
        if user:
            sys.exit(f"User {email} already exists.")
        pw = _read_password(args)
        role = args.role or ("admin" if email in ADMIN_EMAILS else "learner")
        secret = totp_new_secret() if args.mfa else None
        st.x("INSERT INTO users (email, pw_hash, role, totp_secret, created_at, name) VALUES (?, ?, ?, ?, ?, ?)",
             (email, generate_password_hash(pw), role, secret, time.time(), " ".join(args.name.split())[:80]))
        st.audit("USER_CREATED", email, "cli", None, f"{PROFILES[effective_role(email, role)]}, mfa {'on' if secret else 'off'}")
        print(f"Created {email} ({PROFILES[effective_role(email, role)]}).")
        if secret:
            _print_mfa(email, secret)
        return 0

    if not user:
        sys.exit(f"No such user: {email}")
    if args.cmd == "reset-password":
        st.x("UPDATE users SET pw_hash = ? WHERE email = ?", (generate_password_hash(_read_password(args)), email))
        print(f"Password updated for {email}.")
    elif args.cmd == "reset-mfa":
        secret = totp_new_secret()
        st.x("UPDATE users SET totp_secret = ?, totp_last_step = NULL WHERE email = ?", (secret, email))
        _print_mfa(email, secret)
    elif args.cmd == "approve-user":
        st.x("UPDATE users SET pending = 0 WHERE email = ?", (email,))
        print(f"Approved {email}.")
    elif args.cmd == "verify-user":
        st.x("UPDATE users SET email_verified = 1 WHERE email = ?", (email,))
        print(f"Marked {email} as verified.")
    elif args.cmd == "disable-mfa":
        st.x("UPDATE users SET totp_secret = NULL, totp_last_step = NULL WHERE email = ?", (email,))
        print(f"MFA removed for {email}: password-only login.")
    elif args.cmd == "delete-user":
        st.x("DELETE FROM users WHERE email = ?", (email,))
        print(f"Deleted {email}.")
    elif args.cmd == "set-role":
        st.x("UPDATE users SET role = ? WHERE email = ?", (args.role, email))
        print(f"{email} is now {PROFILES[effective_role(email, args.role)]}."
              + (" (still admin: the email is listed in ADMIN_EMAILS)" if args.role == "learner" and email in ADMIN_EMAILS else ""))
    elif args.cmd in ("disable-user", "enable-user"):
        st.x("UPDATE users SET disabled = ? WHERE email = ?", (1 if args.cmd == "disable-user" else 0, email))
        print(f"{'Disabled' if args.cmd == 'disable-user' else 'Enabled'} {email}.")
    if args.cmd in ("reset-password", "delete-user", "disable-user", "set-role"):
        n = st.x("UPDATE sessions SET status = 'REVOKED', ended_at = ?, ended_by = 'cli' "
                 "WHERE email = ? AND status = 'ACTIVE'", (time.time(), email))
        if n:
            print(f"Revoked {n} active session(s).")
    st.audit(args.cmd.upper().replace("-", "_"), email, "cli", None, "")
    return 0


def _test_email(to: str) -> int:
    print(f"Settings file (.env): {'found, ' + str(len(DOTENV_KEYS)) + ' value(s) loaded' if DOTENV_KEYS else 'not found or empty (using environment variables)'}")
    if not SMTP_HOST:
        print("SMTP_HOST is not set, so the portal cannot send email.\n"
              "Copy .env.example to .env, fill in the SMTP_* lines for your provider, then run this again.")
        return 1
    mode = "SSL" if SMTP_SSL else "STARTTLS" if SMTP_STARTTLS else "plain (no TLS)"
    print(f"Server : {SMTP_HOST}:{SMTP_PORT} ({mode})")
    print(f"Login  : {SMTP_USER or '(none)'}")
    print(f"From   : {SMTP_FROM}")
    print(f"To     : {to}")
    try:
        deliver_email(to, "VG Learner Portal: test email",
                      "This is a test from VG Learner Portal.\n\nIf you can read it, verification and "
                      "password-reset emails will be delivered automatically.\n")
    except smtplib.SMTPAuthenticationError as exc:
        print(f"FAILED: the mail server rejected the login ({exc.smtp_code}).\n"
              "  Gmail: use a 16-character App password, not your normal password (2-Step Verification must be on).\n"
              "  Microsoft 365: SMTP AUTH must be enabled for the mailbox.\n"
              "  Amazon SES: use the SMTP credentials from the SES console, not IAM access keys.")
        return 1
    except Exception as exc:  # noqa: BLE001 - show any delivery problem to the operator
        print(f"FAILED: {type(exc).__name__}: {exc}\n"
              "  Check SMTP_HOST / SMTP_PORT (587 = STARTTLS, 465 = SSL), the firewall, and that SMTP_FROM is allowed to send.")
        return 1
    print("OK: the test email was accepted by the mail server. Check the inbox (and the spam folder).")
    return 0


def _import_users(st, args) -> int:
    """CSV import: each new account gets an unusable random password and a
    set-password link (valid INVITE_TOKEN_HOURS). Opening the link sets the
    password and verifies the email address in one step."""
    created = skipped = 0
    links = []
    with open(args.csv, newline="", encoding="utf-8-sig") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            email = row.get("email", "").lower()
            name = " ".join(row.get("name", "").split())[:80]
            role = (row.get("role") or "learner").lower()
            role = {"user": "learner", "user profile": "learner", "admin profile": "admin"}.get(role, role)
            if not EMAIL_RE.match(email) or role not in ROLES:
                print(f"  line {i}: skipped (invalid email or role): {row}")
                skipped += 1
                continue
            if st.user(email):
                print(f"  line {i}: skipped (already exists): {email}")
                skipped += 1
                continue
            st.x("INSERT INTO users (email, pw_hash, role, created_at, name, email_verified) VALUES (?, ?, ?, ?, ?, 0)",
                 (email, generate_password_hash(secrets.token_urlsafe(32)), role, time.time(), name))
            token = st.issue_token(email, "cli", "import", "reset", INVITE_TTL)
            link = reset_link(token)
            st.audit("USER_IMPORTED", email, "cli", None, f"{PROFILES[effective_role(email, role)]}; set-password link valid {INVITE_TTL // 3600} h")
            if args.send and smtp_configured():
                send_email(email, "You're invited to VG Learner Portal",
                           f"Hi {name or 'there'},\n\nAn account was created for you on VG Learner Portal.\n"
                           f"Open this link within {INVITE_TTL // 3600} hours to choose your password (it also verifies your email):\n{link}\n",
                           "Invitation")
            links.append((email, link))
            created += 1
    if args.send and smtp_configured():
        time.sleep(2)  # let the background mail threads finish before the CLI exits
    print(f"Imported {created} user(s), skipped {skipped}.")
    if links and not (args.send and smtp_configured()):
        print(f"Send each person their set-password link (valid {INVITE_TTL // 3600} h; the link also verifies the email):")
        for email, link in links:
            print(f"  {email:<36} {link}")
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
