"""建立初始 admin 帳號。

用法：
    python backend/scripts/seed_admin.py --email admin@example.com --password mypassword
    python backend/scripts/seed_admin.py --email admin@example.com --password mypassword --username admin
"""
import argparse
import sys
import os

_backend = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _backend)
os.chdir(_backend)

from db import db_session
from db.models import User
from services.auth_service import hash_password


def seed(email: str, password: str, username: str) -> None:
    with db_session() as db:
        existing = db.query(User).filter(
            (User.email == email.lower()) | (User.username == username)
        ).first()
        if existing:
            print(f"[warn] 帳號已存在：{existing.email} (role={existing.role})")
            if existing.role != "admin":
                existing.role = "admin"
                existing.hashed_pw = hash_password(password)
                db.commit()
                print("[ok] 已升級為 admin 並更新密碼")
            return

        user = User(
            email=email.lower().strip(),
            username=username,
            hashed_pw=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"[ok] Admin 帳號已建立：{user.email} (id={user.id})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    seed(args.email, args.password, args.username)
