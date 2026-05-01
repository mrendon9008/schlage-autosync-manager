"""Authentication helpers: AES-256-GCM encryption and SchlageSession management."""

import os
import logging
import sqlite3
import secrets
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional

from fastapi import Request, HTTPException, status
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .database import get_db, init_db

# ── Session constants ──────────────────────────────────────────────────────────
SESSION_TOKEN_BYTES = 32          # 32 bytes → 64 hex chars
SESSION_TTL_DAYS    = 7           # sessions live 7 days
SESSION_COOKIE_NAME = "schlage_session"
SESSION_COOKIE_SECURE = True     # True in production (HTTPS); set False for local dev
SESSION_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = "lax"

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
KEY_PATH = BASE_DIR / "encryption_key"
NONCE_SIZE = 12  # bytes — standard for AES-GCM

# ─── Encryption key management ─────────────────────────────────────────────────


def _load_key() -> bytes:
    """Load or generate the 32-byte AES encryption key."""
    if KEY_PATH.exists():
        key = KEY_PATH.read_bytes()
        if len(key) == 32:
            return key
        raise ValueError("encryption_key must be exactly 32 bytes")
    # Generate a new key
    key = os.urandom(32)
    KEY_PATH.write_bytes(key)
    os.chmod(KEY_PATH, 0o600)
    return key


_KEY: Optional[bytes] = None


def _get_key() -> bytes:
    global _KEY
    if _KEY is None:
        _KEY = _load_key()
    return _KEY


def encrypt_password(password: str) -> tuple[bytes, bytes]:
    """
    Encrypt a plaintext password using AES-256-GCM.

    Returns:
        (ciphertext, nonce) — both are opaque bytes
    """
    key = _get_key()
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, password.encode(), None)
    return ciphertext, nonce


def decrypt_password(ciphertext: bytes, nonce: bytes) -> str:
    """
    Decrypt an AES-256-GCM ciphertext back to plaintext password.
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode()


# ── Session management ────────────────────────────────────────────────────────

def generate_session_token() -> str:
    """Generate a cryptographically random 64-char hex token."""
    return os.urandom(SESSION_TOKEN_BYTES).hex()


def create_session(username: str) -> tuple[str, str]:
    """
    Create a new session for username.
    Returns (session_token, expires_at_iso).
    Deletes any existing sessions for this user first (single-device constraint).
    """
    token   = generate_session_token()
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()

    init_db()
    conn = get_db()
    try:
        # Single-device: revoke existing sessions for this user
        conn.execute("DELETE FROM user_sessions WHERE username = ?", (username,))
        conn.execute(
            """INSERT INTO user_sessions
               (session_token, username, expires_at, last_active_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (token, username, expires),
        )
        conn.commit()
    finally:
        conn.close()

    return token, expires


def get_session_by_token(token: str) -> Optional[dict]:
    """Look up a session by token. Returns the row dict or None."""
    init_db()
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT us.*, c.encrypted_password, c.nonce
               FROM user_sessions us
               JOIN credentials c ON c.username = us.username
               WHERE us.session_token = ?""",
            (token,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def refresh_session(token: str) -> bool:
    """
    Extend session last_active_at to now (called on every authenticated request).
    Returns True if refreshed, False if session not found.
    """
    init_db()
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE user_sessions SET last_active_at = datetime('now') "
            "WHERE session_token = ?",
            (token,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_session(token: str) -> bool:
    """Delete a session by token. Returns True if deleted."""
    init_db()
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM user_sessions WHERE session_token = ?",
            (token,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def require_auth(func):
    """Decorator that requires a valid session cookie. Injects session into kwargs."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        session = get_current_session(request)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        kwargs["_schlage_session"] = session
        return await func(request, *args, **kwargs)
    return wrapper


# ─── SchlageSession ───────────────────────────────────────────────────────────


class SchlageSession:
    """
    Manages a Schlage API session backed by encrypted credentials in SQLite.

    Lifecycle:
        login()  → stores encrypted creds in DB
        logout() → removes encrypted creds from DB
        All other methods call the Schlage API server-side.
    """

    def __init__(self, username: str, _session_token: Optional[str] = None):
        self.username = username
        self._session_token = _session_token
        self._encrypted_password: Optional[bytes] = None
        self._nonce: Optional[bytes] = None
        self._auth: Optional[object] = None
        self._schlage_api: Optional[object] = None

    @property
    def is_authenticated(self) -> bool:
        # Authenticated if we have an active Schlage auth object
        return self._auth is not None

    def login(self) -> None:
        """
        Re-authenticate with stored credentials.
        NOTE: This will fail if credentials aren't stored, which is the new expected behavior.
        """
        if self._encrypted_password is None or self._nonce is None:
            raise ValueError("No encrypted credentials found - please log in again")

        password = decrypt_password(self._encrypted_password, self._nonce)

        from pyschlage import Auth, Schlage

        auth = Auth(username=self.username, password=password)
        auth.authenticate()
        self._auth = auth
        self._schlage_api = Schlage(auth)
        logger.info("Schlage session re-authenticated for user: %s", self.username)

    @staticmethod
    def from_credentials(username: str, password: str) -> "SchlageSession":
        """
        Factory: authenticate with raw creds, return session.
        Stores encrypted credentials for session persistence and creates a session row.
        """
        from pyschlage import Auth, Schlage

        auth = Auth(username=username, password=password)
        auth.authenticate()

        ciphertext, nonce = encrypt_password(password)

        init_db()
        conn = get_db()
        try:
            conn.execute(
                """INSERT INTO credentials (username, encrypted_password, nonce, is_owner)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(username) DO UPDATE SET
                       encrypted_password = excluded.encrypted_password,
                       nonce = excluded.nonce,
                       updated_at = CURRENT_TIMESTAMP,
                       is_owner = 1""",
                (username, ciphertext, nonce),
            )
            conn.commit()
        finally:
            conn.close()

        session = SchlageSession(username)
        session._encrypted_password = ciphertext
        session._nonce = nonce
        session._auth = auth
        session._schlage_api = Schlage(auth)

        global _current_auth, _current_schlage_api
        _current_auth = auth
        _current_schlage_api = session._schlage_api

        # Create a session row and store the token on the session object
        token, expires = create_session(username)
        session._session_token = token

        return session

    def logout(self) -> None:
        """Remove stored credentials from SQLite."""
        init_db()
        conn = get_db()
        try:
            conn.execute("DELETE FROM credentials WHERE username = ?", (self.username,))
            conn.commit()
        finally:
            conn.close()
        # Clear global auth state
        global _current_auth, _current_schlage_api
        _current_auth = None
        _current_schlage_api = None
        self._encrypted_password = None
        self._nonce = None
        self._auth = None
        self._schlage_api = None
        logger.info("Schlage session logged out for user: %s", self.username)

    # ─── Lock operations ────────────────────────────────────────────────────

    def get_locks(self) -> list[dict]:
        """Fetch all locks from Schlage Cloud."""
        if self._schlage_api is None:
            raise RuntimeError("Not authenticated with Schlage")

        from pyschlage import Lock

        locks = self._schlage_api.locks()
        result = []
        for lock in locks:
            result.append({
                "device_id": lock.device_id,
                "name": lock.name,
                "battery_level": lock.battery_level,
                "is_online": lock.connected,
                "model": getattr(lock, "model_name", "Schlage Encode Plus"),
                "last_activity": getattr(lock, "last_activity", None),
            })
        return result

    def get_access_codes(self, device_id: str) -> list[dict]:
        """Fetch access codes for a specific lock."""
        if self._auth is None:
            raise RuntimeError("Not authenticated with Schlage")
        from pyschlage import Lock

        lock = Lock(device_id=device_id); lock._auth = self._auth
        codes = lock.get_access_codes()
        result = []
        for code in codes:
            # schedule=None means always valid
            is_always = code.schedule is None
            start_dt = None
            end_dt = None
            if isinstance(code.schedule, type(code.schedule).__bases__[0]):
                # inspect TemporarySchedule vs RecurringSchedule
                sched_class = type(code.schedule).__name__
                if sched_class == 'TemporarySchedule':
                    start_dt = getattr(code.schedule, 'start', None)
                    end_dt = getattr(code.schedule, 'end', None)
            result.append({
                "access_code_id": code.access_code_id,
                "name": code.name,
                "code": code.code,
                "is_always_valid": is_always,
                "start_datetime": start_dt.isoformat() if start_dt else None,
                "end_datetime": end_dt.isoformat() if end_dt else None,
            })
        return result

    def create_access_code(
        self, device_id: str, *, name: str, code: str, is_always_valid: bool,
        start_datetime: Optional[str] = None, end_datetime: Optional[str] = None,
    ) -> dict:
        """
        Create an access code on a specific lock via Schlage API.
        """
        if self._auth is None:
            raise RuntimeError("Not authenticated with Schlage")
        from pyschlage import Lock
        from pyschlage.code import AccessCode, TemporarySchedule
        from datetime import datetime, timezone

        lock = Lock(device_id=device_id); lock._auth = self._auth

        schedule = None
        if not is_always_valid:
            start_dt = None
            end_dt = None
            if start_datetime:
                start_dt = datetime.fromisoformat(start_datetime.replace("Z", "+00:00"))
            if end_datetime:
                end_dt = datetime.fromisoformat(end_datetime.replace("Z", "+00:00"))
            if start_dt and end_dt:
                schedule = TemporarySchedule(start=start_dt, end=end_dt)

        code_obj = AccessCode(
            name=name,
            code=code,
            schedule=schedule,
        )
        logger.info("DEBUG add_access_code: lock_id=%s code_name=%s", device_id, name)
        try:
            lock.add_access_code(code_obj)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            logger.error("add_access_code EXCEPTION: %s: %s traceback: %s", type(exc).__name__, exc, tb)
            raise

        return {
            "code_id": getattr(code_obj, "code_id", None) or getattr(code_obj, "access_code_id", None) or "created",
            "name": name,
        }

    def delete_access_code(self, device_id: str, code_id: str) -> None:
        """Delete an access code from a specific lock via Schlage API."""
        if self._auth is None:
            raise RuntimeError("Not authenticated with Schlage")
        from pyschlage import Lock

        lock = Lock(device_id=device_id); lock._auth = self._auth
        # Get the AccessCode object directly and call .delete() on it
        all_codes = lock.get_access_codes()
        target = next((c for c in all_codes if c.access_code_id == code_id), None)
        if target is None:
            raise RuntimeError(
                f"Access code {code_id} not found on lock {device_id}. "
                "It may have already been deleted from Schlage."
            )
        target.delete()


# ─── Per-request session lookup ───────────────────────────────────────────────

# Global to store the active auth object between requests
_current_auth: Optional[object] = None
_current_schlage_api: Optional[object] = None


def get_current_session(request: Request) -> SchlageSession | None:
    """
    Return the SchlageSession for the user whose session cookie matches the
    current request. Re-authenticates using stored encrypted credentials.
    Returns None if no valid session cookie is present.
    """
    # Try cookie first, then Authorization header (Bearer token from localStorage)
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None

    session_row = get_session_by_token(token)
    if not session_row:
        return None

    # Check expiry
    expires_at = datetime.fromisoformat(session_row["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        delete_session(token)
        return None

    # Refresh last_active_at
    refresh_session(token)

    # Build and return SchlageSession
    session = SchlageSession(session_row["username"])

    # Look up encrypted credentials from credentials table (not session row)
    username_from_session = session_row["username"]
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT encrypted_password, nonce FROM credentials WHERE username = ?",
            (username_from_session,),
        )
        cred_row = cur.fetchone()
        if cred_row:
            session._encrypted_password = cred_row["encrypted_password"]
            session._nonce = cred_row["nonce"]
        else:
            session._encrypted_password = None
            session._nonce = None
    finally:
        conn.close()

    # Restore auth state if already authenticated this request
    if _current_auth is not None:
        session._auth = _current_auth
        session._schlage_api = _current_schlage_api
    else:
        try:
            session.login()
        except Exception as exc:
            logger.warning("Failed to re-authenticate session: %s", exc)
            return None

    return session