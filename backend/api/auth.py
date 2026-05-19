"""Authentication endpoints: login, register, google OAuth, me."""
from __future__ import annotations
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from db import get_db
from db.models import User
from services.auth_service import verify_password, hash_password, create_access_token
from api.dependencies import get_current_user

router = APIRouter()

_USERNAME_RE = re.compile(r'^[A-Za-z0-9_\-\.]{2,32}$')


def _user_response(user: User, token: str) -> dict:
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "quota_tokens_per_day": user.quota_tokens_per_day,
        "quota_requests_per_day": user.quota_requests_per_day,
    }


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    identifier = req.email.strip()
    # Accept either email or username
    if '@' in identifier:
        user = db.query(User).filter(User.email == identifier.lower(), User.is_active == True).first()
    else:
        user = db.query(User).filter(User.username == identifier, User.is_active == True).first()
    if user is None or not user.hashed_pw or not verify_password(req.password, user.hashed_pw):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="帳號或密碼錯誤")
    token = create_access_token(user.id, user.username, user.role)
    return _user_response(user, token)


# ── Register ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str
    display_name: str | None = None


@router.post("/api/auth/register", status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    email = req.email.lower().strip()
    if not email or '@' not in email:
        raise HTTPException(400, "Email 格式不正確")
    if not _USERNAME_RE.match(req.username):
        raise HTTPException(400, "Username 只能包含英數字、底線、連字號、點，且長度 2-32")
    if len(req.password) < 8:
        raise HTTPException(400, "密碼至少 8 個字元")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "此 Email 已被註冊")
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(409, "此 Username 已被使用")

    user = User(
        email=email,
        username=req.username,
        hashed_pw=hash_password(req.password),
        display_name=req.display_name or req.username,
        role="user",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.username, user.role)
    return _user_response(user, token)


# ── Google OAuth ──────────────────────────────────────────────────────────────

class GoogleAuthRequest(BaseModel):
    credential: str   # Google ID token (JWT) from frontend


@router.post("/api/auth/google")
def google_login(req: GoogleAuthRequest, db: Session = Depends(get_db)):
    if not settings.google_client_id:
        raise HTTPException(501, "Google OAuth 未設定（缺少 GOOGLE_CLIENT_ID）")
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as g_requests
        info = id_token.verify_oauth2_token(
            req.credential,
            g_requests.Request(),
            settings.google_client_id,
        )
    except Exception as exc:
        raise HTTPException(401, f"Google token 驗證失敗：{exc}")

    email = info.get("email", "").lower().strip()
    if not email:
        raise HTTPException(400, "Google 帳號未提供 Email")

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        # Auto-create on first Google login
        username = email.split("@")[0]
        base = username
        idx = 1
        while db.query(User).filter(User.username == username).first():
            username = f"{base}{idx}"
            idx += 1
        user = User(
            email=email,
            username=username,
            hashed_pw="",           # no password for OAuth users
            display_name=info.get("name") or username,
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.is_active:
        raise HTTPException(403, "帳號已被停用")

    token = create_access_token(user.id, user.username, user.role)
    return _user_response(user, token)


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "quota_tokens_per_day": user.quota_tokens_per_day,
        "quota_requests_per_day": user.quota_requests_per_day,
    }
