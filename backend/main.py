"""
FastAPI application entry point for Schlage Lock Manager.

Serves static files from ../static/ and exposes the REST API.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, PlainTextResponse

from .database import init_db
from .routes import auth, locks, groups, codes, sync

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ─── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Schlage Lock Manager",
    description="Manage Schlage smart locks, groups, and access codes.",
    version="1.0.0",
)

# ─── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Database ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    logger.info("Database initialized at data/schlage.db")
    from .database import migrate_sync_schema
    migrate_sync_schema()
    logger.info("Sync schema migrated")
    from .database import migrate_session_auth
    migrate_session_auth()
    logger.info("Session auth schema migrated")

    # Start V4 check-time scheduler
    from backend.scheduler import start_scheduler
    start_scheduler()
    logger.info("CheckTimeScheduler started")


@app.on_event("shutdown")
def shutdown():
    from backend.scheduler import stop_scheduler
    stop_scheduler()
    logger.info("CheckTimeScheduler stopped")
# ─── Static files ─────────────────────────────────────────────────────────────

# Serve the frontend SPA from ../static/ relative to this file
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
def root():
    """Redirect root to the frontend."""
    return RedirectResponse(url="/static/index.html")

# ─── API Routes ───────────────────────────────────────────────────────────────


# ─── ACME HTTP-01 Challenge (for Let's Encrypt) ───────────────────────────────
@app.get("/.well-known/acme-challenge/{token}", response_class=PlainTextResponse)
def acme_challenge(token: str):
    """Respond with the ACME challenge token so Traefik can complete HTTP-01 validation."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content=token, media_type="text/plain")
app.include_router(auth.router)
app.include_router(locks.router)
app.include_router(groups.router)
app.include_router(codes.router)
app.include_router(sync.router, prefix="/sync", tags=["sync"])


