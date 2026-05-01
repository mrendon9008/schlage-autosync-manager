"""Pydantic request/response models."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ─── Auth ──────────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    message: str
    username: str


class MeResponse(BaseModel):
    authenticated: bool
    username: Optional[str] = None


class LogoutResponse(BaseModel):
    message: str


class DisconnectResponse(BaseModel):
    message: str


# ─── Locks ────────────────────────────────────────────────────────────────────


class LockItem(BaseModel):
    device_id: str
    name: str
    battery_level: int
    is_online: bool
    model: str = "Schlage Encode Plus"
    last_activity: Optional[str] = None


class LocksResponse(BaseModel):
    locks: list[LockItem]


# ─── Groups ───────────────────────────────────────────────────────────────────


class GroupLockItem(BaseModel):
    lock_id: str
    lock_name: Optional[str] = None
    is_master: int = 0


class GroupItem(BaseModel):
    id: int
    name: str
    locks: list[GroupLockItem] = Field(default_factory=list)


class GroupsResponse(BaseModel):
    groups: list[GroupItem]


class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1)


class CreateGroupResponse(BaseModel):
    id: int
    name: str
    locks: list[GroupLockItem] = Field(default_factory=list)


class AddLocksRequest(BaseModel):
    lock_ids: list[str] = Field(..., min_length=1)
    lock_names: Optional[list[str]] = None


# ─── Access Codes ─────────────────────────────────────────────────────────────


class CreatedCodeItem(BaseModel):
    local_id: int
    schlage_lock_id: str
    schlage_code_id: Optional[str] = None


class CodeItem(BaseModel):
    id: int
    name: str
    code_value: str
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    is_always_valid: bool
    start_datetime: Optional[str] = None
    end_datetime: Optional[str] = None
    schlage_lock_id: str
    lock_name: Optional[str] = None
    schlage_code_id: Optional[str] = None
    created_at: Optional[str] = None


class CodesResponse(BaseModel):
    codes: list[CodeItem]


class CreateCodeRequest(BaseModel):
    name: str = Field(..., min_length=1)
    code_value: str = Field(..., min_length=4, max_length=8, pattern=r"^\d+$")
    group_id: int
    is_always_valid: bool = False
    start_datetime: Optional[str] = None
    end_datetime: Optional[str] = None


class OverwriteCodeRequest(BaseModel):
    name: str = Field(..., min_length=1)
    code_value: str = Field(..., min_length=4, max_length=8, pattern=r"^\d+$")
    group_id: int
    is_always_valid: bool = False
    start_datetime: Optional[str] = None
    end_datetime: Optional[str] = None


class CreateCodeResponse(BaseModel):
    message: str
    codes: list[CreatedCodeItem]


class DeleteCodesRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1)


class DeleteCodesResponse(BaseModel):
    message: str
    deleted: int
