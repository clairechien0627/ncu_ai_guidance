"""User management endpoints — admin only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from db.models import User
from services.auth_service import hash_password
from api.dependencies import require_admin

router = APIRouter()


class UserCreateRequest(BaseModel):
    email: str
    username: str
    password: str
    role: str = "user"
    display_name: str | None = None
    quota_tokens_per_day: int | None = None
    quota_requests_per_day: int | None = None


class UserUpdateRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    display_name: str | None = None
    quota_tokens_per_day: int | None = None
    quota_requests_per_day: int | None = None
    password: str | None = None


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active,
        "quota_tokens_per_day": u.quota_tokens_per_day,
        "quota_requests_per_day": u.quota_requests_per_day,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/api/users")
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [_user_dict(u) for u in users]


@router.post("/api/users", status_code=201)
def create_user(
    req: UserCreateRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    if req.role not in ("user", "admin"):
        raise HTTPException(400, "role 只能是 user 或 admin")
    email = req.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "Email 已存在")
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(409, "Username 已存在")
    user = User(
        email=email,
        username=req.username,
        hashed_pw=hash_password(req.password),
        role=req.role,
        display_name=req.display_name,
        quota_tokens_per_day=req.quota_tokens_per_day,
        quota_requests_per_day=req.quota_requests_per_day,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.patch("/api/users/{user_id}")
def update_user(
    user_id: int,
    req: UserUpdateRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(404, "使用者不存在")
    if req.role is not None:
        if req.role not in ("user", "admin"):
            raise HTTPException(400, "role 只能是 user 或 admin")
        user.role = req.role
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.display_name is not None:
        user.display_name = req.display_name
    if req.quota_tokens_per_day is not None:
        user.quota_tokens_per_day = req.quota_tokens_per_day
    if req.quota_requests_per_day is not None:
        user.quota_requests_per_day = req.quota_requests_per_day
    if req.password:
        user.hashed_pw = hash_password(req.password)
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.delete("/api/users/{user_id}", status_code=204)
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(400, "不能停用自己的帳號")
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(404, "使用者不存在")
    user.is_active = False
    db.commit()
