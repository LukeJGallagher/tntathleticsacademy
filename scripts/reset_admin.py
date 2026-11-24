"""Emergency admin recovery: unlock and set a new password, or create an admin.
Run on the server (uses the same DATABASE_URL as the app):

    python scripts/reset_admin.py USERNAME NEWPASSWORD
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
from db import StaffSession, User, utcnow  # noqa: E402

if len(sys.argv) != 3:
    sys.exit(__doc__)
username, password = sys.argv[1], sys.argv[2]
if len(password) < auth.MIN_PASSWORD:
    sys.exit(f"Password must be at least {auth.MIN_PASSWORD} characters.")

with db.session() as s:
    u = s.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if u is None:
        u = auth.create_user(s, username, "Admin", "admin", password)
        print(f"Created admin '{username}'.")
    else:
        u.password_hash = auth.hash_password(password)
        u.failed_attempts, u.locked_until, u.active, u.role = 0, None, True, "admin"
        for ss in s.scalars(select(StaffSession).where(StaffSession.user_id == u.id, StaffSession.revoked_at.is_(None))):
            ss.revoked_at = utcnow()
        print(f"Reset '{username}': unlocked, active admin, new password, old sessions revoked.")
    auth.audit(s, "server console", "admin", "user.recovery", username)
