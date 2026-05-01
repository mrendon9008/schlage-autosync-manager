"""Group routes."""

import logging
from fastapi import APIRouter, Body, HTTPException, Request, status

from ..models import (
    GroupsResponse, GroupItem, GroupLockItem,
    CreateGroupRequest, CreateGroupResponse,
    AddLocksRequest,
)
from ..database import db_cursor
from ..auth import get_current_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/groups", tags=["groups"])


def _require_auth(request: Request):
    session = get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


def _fetch_all_groups() -> list[GroupItem]:
    """Helper to fetch all groups with their locks."""
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, name, created_at FROM `groups` ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
    # Use separate cursor for locks to avoid nested cursor issue
    groups = []
    for row in rows:
        with db_cursor() as cur_locks:
            cur_locks.execute(
                "SELECT lock_id, lock_name, is_master FROM group_locks WHERE group_id = ?",
                (row["id"],),
            )
            locks = [
                GroupLockItem(lock_id=r["lock_id"], lock_name=r["lock_name"], is_master=r["is_master"])
                for r in cur_locks
            ]
        groups.append(GroupItem(id=row["id"], name=row["name"], locks=locks))
    return groups


@router.get("", response_model=GroupsResponse)
def list_groups(request: Request) -> GroupsResponse:
    """List all lock groups."""
    _require_auth(request)
    return GroupsResponse(groups=_fetch_all_groups())


@router.post("", response_model=CreateGroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(request: Request, body: CreateGroupRequest) -> CreateGroupResponse:
    """Create a new lock group."""
    _require_auth(request)
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO `groups` (name) VALUES (?)",
            (body.name,),
        )
        group_id = cur.lastrowid
    return CreateGroupResponse(id=group_id, name=body.name, locks=[])


@router.get("/{group_id}", response_model=GroupItem)
def get_group(request: Request, group_id: int) -> GroupItem:
    """Get a single group with its locks."""
    _require_auth(request)
    with db_cursor() as cur:
        cur.execute("SELECT id, name FROM `groups` WHERE id = ?", (group_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Group not found")
    with db_cursor() as cur_locks:
        cur_locks.execute(
            "SELECT lock_id, lock_name FROM group_locks WHERE group_id = ?",
            (group_id,),
        )
        locks = [
            GroupLockItem(lock_id=r["lock_id"], lock_name=r["lock_name"])
            for r in cur_locks
        ]
    return GroupItem(id=row["id"], name=row["name"], locks=locks)


@router.put("/{group_id}", response_model=GroupItem)
def update_group(request: Request, group_id: int, body: CreateGroupRequest) -> GroupItem:
    """Update a group's name."""
    _require_auth(request)
    with db_cursor() as cur:
        cur.execute(
            "UPDATE `groups` SET name = ? WHERE id = ?",
            (body.name, group_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Group not found")
    with db_cursor() as cur_locks:
        cur_locks.execute(
            "SELECT lock_id, lock_name FROM group_locks WHERE group_id = ?",
            (group_id,),
        )
        locks = [
            GroupLockItem(lock_id=r["lock_id"], lock_name=r["lock_name"])
            for r in cur_locks
        ]
    return GroupItem(id=group_id, name=body.name, locks=locks)


@router.delete("/{group_id}")
def delete_group(request: Request, group_id: int) -> dict:
    """Delete a group. Locks are unlinked but not deleted."""
    _require_auth(request)
    with db_cursor() as cur:
        cur.execute("DELETE FROM `groups` WHERE id = ?", (group_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Group not found")
    return {"message": "Group deleted"}


@router.post("/{group_id}/locks")
def add_locks_to_group(request: Request, group_id: int, body: AddLocksRequest) -> dict:
    """Add one or more locks to a group."""
    _require_auth(request)
    with db_cursor() as cur:
        cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")
        lock_names = body.lock_names or []
        for i, lock_id in enumerate(body.lock_ids):
            lock_name = lock_names[i] if i < len(lock_names) else lock_id
            cur.execute(
                "INSERT OR IGNORE INTO group_locks (group_id, lock_id, lock_name) VALUES (?, ?, ?)",
                (group_id, lock_id, lock_name),
            )
    return {"message": f"{len(body.lock_ids)} lock(s) added to group"}


from pydantic import BaseModel

class MasterLockRequest(BaseModel):
    lock_id: str

@router.put("/{group_id}/master-lock")
def set_master_lock(request: Request, group_id: int, body: MasterLockRequest) -> dict:
    """Set the master lock for a group by updating is_master=1 on that lock and is_master=0 on all others in the group."""
    lock_id: str = body.lock_id
    logger.info("set_master_lock called: group_id=%s, lock_id=%s", group_id, lock_id)
    if not lock_id:
        raise HTTPException(status_code=400, detail="lock_id is required")
    try:
        _require_auth(request)
    except Exception as exc:
        logger.error("Auth failed: %s", exc)
        raise
    with db_cursor() as cur:
        cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")
        cur.execute("SELECT id FROM group_locks WHERE group_id = ? AND lock_id = ?", (group_id, lock_id))
        if not cur.fetchone():
            logger.error("Lock not found: group_id=%s, lock_id=%s", group_id, lock_id)
            raise HTTPException(status_code=404, detail="Lock not found in this group")
        # Set all locks in group to not master
        cur.execute("UPDATE group_locks SET is_master = 0 WHERE group_id = ?", (group_id,))
        # Set the specified lock as master
        cur.execute("UPDATE group_locks SET is_master = 1 WHERE group_id = ? AND lock_id = ?", (group_id, lock_id))
    logger.info("Master lock updated successfully: group_id=%s, lock_id=%s", group_id, lock_id)
    return {"message": "Master lock updated", "lock_id": lock_id}






@router.delete("/{group_id}/locks/{lock_id}")
def remove_lock_from_group(request: Request, group_id: int, lock_id: str) -> dict:
    """Remove a lock from a group."""
    _require_auth(request)
    with db_cursor() as cur:
        cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")
        cur.execute(
            "DELETE FROM group_locks WHERE group_id = ? AND lock_id = ?",
            (group_id, lock_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lock not found in this group")
    return {"message": "Lock removed from group"}
