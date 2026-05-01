"""Authentication routes: login, logout, me."""

import logging
from fastapi import APIRouter, HTTPException, Request, Response, status

from ..models import LoginRequest, LoginResponse, MeResponse, LogoutResponse, DisconnectResponse
from ..auth import (
    SchlageSession, get_current_session,
    SESSION_COOKIE_NAME, SESSION_COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY, SESSION_COOKIE_SAMESITE,
    create_session, delete_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_MAX_AGE = 7 * 24 * 60 * 60   # 7 days in seconds


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, response: Response) -> LoginResponse:
    """
    Authenticate with Schlage credentials.

    On success:
      - Stores encrypted credentials in SQLite
      - Creates a session row in user_sessions
      - Sets a HTTP-only Secure cookie with the session token
    """
    try:
        session = SchlageSession.from_credentials(body.username, body.password)
        logger.info("User %s logged in successfully", body.username)

        # discover_codes on login (unchanged)
        try:
            from backend.sync_logic import SyncEngine
            import pyschlage
            creds = {"username": body.username, "password": body.password}
            from backend.database import DB_PATH
            engine = SyncEngine(str(DB_PATH), pyschlage, creds)
            discovered = engine.discover_codes()
            total = sum(len(codes) for codes in discovered.values())
            logger.info("Login discover_codes: found %d new codes", total)
        except Exception as discover_exc:
            logger.warning("Login discover_codes failed: %s", discover_exc)

        # Set session cookie
        token = getattr(session, "_session_token", None)
        if token:
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=token,
                max_age=COOKIE_MAX_AGE,
                httponly=SESSION_COOKIE_HTTPONLY,
                secure=SESSION_COOKIE_SECURE,
                samesite=SESSION_COOKIE_SAMESITE,
            )

        return LoginResponse(message="Login successful", username=body.username)

    except Exception as exc:
        logger.warning("Login failed for %s: %s", body.username, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )


@router.post("/logout", response_model=LogoutResponse)
def logout(request: Request, response: Response) -> LogoutResponse:
    """
    Delete the session row and clear the session cookie.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(token)

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=SESSION_COOKIE_HTTPONLY,
        secure=SESSION_COOKIE_SECURE,
        samesite=SESSION_COOKIE_SAMESITE,
    )

    return LogoutResponse(message="Logged out")


@router.post("/disconnect", response_model=DisconnectResponse)
def disconnect(request: Request, response: Response) -> DisconnectResponse:
    """
    Wipe all local data AND remove the Schlage credentials for this account.
    Clears: sync_code_history, sync_jobs, sync_schedules, access_codes,
            group_locks, groups, user_sessions, credentials.
    After this the app returns to the login screen with no stored credentials.
    """
    from ..database import get_db

    # Delete the session cookie first
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(token)

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=SESSION_COOKIE_HTTPONLY,
        secure=SESSION_COOKIE_SECURE,
        samesite=SESSION_COOKIE_SAMESITE,
    )

    # Wipe all local data — single-user app, clear everything
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM sync_code_history")
        cur.execute("DELETE FROM sync_jobs")
        cur.execute("DELETE FROM sync_schedules")
        cur.execute("DELETE FROM access_codes")
        cur.execute("DELETE FROM group_locks")
        cur.execute("DELETE FROM `groups`")
        cur.execute("DELETE FROM user_sessions")
        cur.execute("DELETE FROM credentials")
        conn.commit()
        logger.info("Disconnect: all data wiped")
    finally:
        conn.close()

    return DisconnectResponse(message="Disconnected and all data cleared")


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    """
    Check whether the current request has a valid session.
    Reads the session cookie; does NOT verify credential validity against Schlage API.
    """
    session = get_current_session(request)
    if session is None:
        return MeResponse(authenticated=False)
    return MeResponse(authenticated=True, username=session.username)
