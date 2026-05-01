"""Lock routes."""

import logging
from fastapi import APIRouter, HTTPException, Request, status

from ..models import LocksResponse, LockItem
from ..auth import get_current_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/locks", tags=["locks"])


@router.get("", response_model=LocksResponse)
def list_locks(request: Request) -> LocksResponse:
    """
    Fetch all Schlage locks and their current status (battery, online/offline).

    The lock list is always fetched fresh from the Schlage Cloud API;
    no caching is performed.
    """
    session = get_current_session(request)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        session.login()
        locks_data = session.get_locks()
        locks = [
            LockItem(
                device_id=l["device_id"],
                name=l["name"],
                battery_level=l["battery_level"],
                is_online=l["is_online"],
                model=l.get("model", "Schlage Encode Plus"),
                last_activity=l.get("last_activity"),
            )
            for l in locks_data
        ]
        return LocksResponse(locks=locks)
    except Exception as exc:
        logger.error("Failed to fetch locks: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch locks from Schlage API",
        )
