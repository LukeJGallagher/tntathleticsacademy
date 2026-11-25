"""Staff accounts: argon2id hashing, lockout, audit log."""
from __future__ import annotations

import json
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select

from db import AuditLog, User, utcnow

_ph = PasswordHasher()
MAX_FAILED = 5
LOCK_FOR = timedelta(minutes=15)
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
    now = utcnow()
    if u.locked_until and u.locked_until > now:
        mins = max(1, int((u.locked_until - now).total_seconds() // 60) + 1)
        audit(s, u.full_name, u.role, "login.failure", detail="locked")
        return None, f"Too many failed attempts. Try again in {mins} minute(s), or ask the admin to unlock it."
    if not verify_password(u.password_hash, password or ""):
        u.failed_attempts += 1
        audit(s, u.full_name, u.role, "login.failure", detail=f"attempt {u.failed_attempts}")
        if u.failed_attempts >= MAX_FAILED:
            u.locked_until = now + LOCK_FOR
            u.failed_attempts = 0
            return None, f"Too many failed attempts. Locked for {int(LOCK_FOR.total_seconds() // 60)} minutes."
        left = MAX_FAILED - u.failed_attempts
        return None, "Wrong username or password." + (f" {left} attempt(s) left." if left <= 2 else "")
    u.failed_attempts = 0
    u.locked_until = None
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


def temp_password() -> str:
    """Readable one-time password, e.g. tnt-K7QM4X (no 0/O/1/I)."""
    import secrets
    return "tnt-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))


def unique_username(s, name: str) -> str:
    base = "".join(ch for ch in (name.split()[0] if name.split() else "coach").lower() if ch.isalnum()) or "coach"
    u, n = base, 1
    while s.scalar(select(User.id).where(func.lower(User.username) == u)):
        n += 1
        u = f"{base}{n}"
    return u


def add_coach_with_login(s, name: str, login: bool = True, role: str = "coach"):
    """Create (or reuse) a coach profile and, optionally, a login with a temporary password.
    Returns (coach, username or None, temp password or None)."""
    from db import Coach
    name = name.strip()
    coach = s.scalar(select(Coach).where(func.lower(Coach.name) == name.lower()))
    if coach is None:
        coach = Coach(name=name, active=True)
        s.add(coach)
        s.flush()
    if not login or s.scalar(select(User.id).where(User.coach_id == coach.id)):
        return coach, None, None
    pw = temp_password()
    u = create_user(s, unique_username(s, name), name, role, pw, coach_id=coach.id)
    u.must_change_password = True
    return coach, u.username, pw


def user_count(s) -> int:
    return s.scalar(select(func.count()).select_from(User)) or 0
