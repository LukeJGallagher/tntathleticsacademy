"""Fresh club setup: admin login + coaches with logins. Works on any DATABASE_URL (local SQLite, Supabase,
Hostinger MySQL). Prints temporary passwords ONCE: send them to each person.

    python scripts/setup_club.py --admin brendan "Brendan Tammo" --coaches Hossam Mike Oussema Jamal Obada
    python scripts/setup_club.py --wipe ...     # delete ALL data first (asks to confirm)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--admin", nargs=2, metavar=("USERNAME", "FULL_NAME"))
p.add_argument("--admin-is-coach", action="store_true", help="also add the admin to the coach list")
p.add_argument("--coaches", nargs="*", default=[])
p.add_argument("--wipe", action="store_true", help="delete every row in every table first")
a = p.parse_args()

print("Database:", db.database_url().split("@")[-1])
if a.wipe:
    if input("Type WIPE to delete ALL data: ").strip() != "WIPE":
        sys.exit("Cancelled.")
    eng = db.get_engine()
    with eng.begin() as conn:
        for t in reversed(db.Base.metadata.sorted_tables):
            conn.execute(delete(t))
    print("All data deleted.")

rows = []
with db.session() as s:
    if a.admin:
        username, full = a.admin
        if s.scalar(select(db.User.id).where(func.lower(db.User.username) == username.lower())):
            print(f"Admin '{username}' already exists, skipped.")
        else:
            coach_id = None
            if a.admin_is_coach:
                coach, _, _ = auth.add_coach_with_login(s, full, login=False)
                coach_id = coach.id
            pw = auth.temp_password()
            u = auth.create_user(s, username, full, "admin", pw, coach_id=coach_id)
            u.must_change_password = True
            rows.append((full, "admin", username, pw))
    for name in a.coaches:
        coach, username, pw = auth.add_coach_with_login(s, name)
        rows.append((coach.name, "coach", username or "(had a login)", pw or ""))
    auth.audit(s, "server console", "admin", "club.setup", f"{len(rows)} logins")

print(f"\n{'Name':22} {'Role':6} {'Username':12} Temporary password")
for r in rows:
    print(f"{r[0]:22} {r[1]:6} {r[2]:12} {r[3]}")
print("\nEach person changes their password under My account after signing in.")
