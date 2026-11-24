"""Staff accounts: argon2id hashing, lockout, audit log."""
from __future__ import annotations

import json

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select

from db import AuditLog, User, utcnow

_ph = PasswordHasher()
MAX_FAILED = 5
MIN_PASSWORD = 8


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(stored: str, plain: str) -> bool:
    try:
        return _ph.verify(stored, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def audit(s, actor: str, role: str | None, action: str, target: str = "", detail=None) -> None:
    s.add(AuditLog(ts=utcnow(), actor=actor or "unknown", role=role, action=action, target=target[:160],
                   detail=json.dumps(detail) if isinstance(detail, (dict, list)) else detail))


def authenticate(s, username: str, password: str) -> tuple[User | None, str]:
    username = (username or "").strip()
    u = s.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if u is None:
        audit(s, username or "?", None, "login.failure", detail="unknown user")
        return None, "Wrong username or password."
    if not u.active:
        audit(s, u.full_name, u.role, "login.failure", detail="disabled")
        return None, "This account is disabled. Ask the admin."
    if u.failed_attempts >= MAX_FAILED:
        audit(s, u.full_name, u.role, "login.failure", detail="locked")
        return None, f"Account locked after {MAX_FAILED} failed attempts. Ask the admin to unlock it."
    if not verify_password(u.password_hash, password or ""):
        u.failed_attempts += 1
        audit(s, u.full_name, u.role, "login.failure", detail=f"attempt {u.failed_attempts}")
        left = MAX_FAILED - u.failed_attempts
        return None, "Wrong username or password." + (f" {left} attempt(s) left." if left <= 2 else "")
    u.failed_attempts = 0
    u.last_login_at = utcnow()
    audit(s, u.full_name, u.role, "login.success")
    return u, ""


def create_user(s, username: str, full_name: str, role: str, password: str, coach_id=None, is_sample=False) -> User:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")
    if s.scalar(select(User.id).where(func.lower(User.username) == username.strip().lower())):
        raise ValueError("That username is taken.")
    u = User(username=username.strip(), full_name=full_name.strip(), role=role, coach_id=coach_id,
             password_hash=hash_password(password), is_sample=is_sample)
    s.add(u)
    s.flush()
    return u


def user_count(s) -> int:
    return s.scalar(select(func.count()).select_from(User)) or 0
