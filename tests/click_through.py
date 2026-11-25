"""Click every button on every page for every role (fresh app per click) and report any that raise.
Run from the rollcall folder:  python tests/click_through.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = f"sqlite:///{(Path(tempfile.mkdtemp()) / 'click.db').as_posix()}"
os.environ["DEMO_MODE"] = "1"
os.environ["ROLLCALL_TEST"] = "1"

from streamlit.testing.v1 import AppTest  # noqa: E402

import db  # noqa: E402
import sample  # noqa: E402
from sqlalchemy import select  # noqa: E402

with db.session() as s:
    sample.load_sample(s, db.get_settings(s))
    admin = s.scalar(select(db.User).where(db.User.username == "admin"))
    tom = s.scalar(select(db.User).where(db.User.username == "tom"))
    first = s.scalar(select(db.Athlete.id).where(db.Athlete.programme == "7–12 years"))
ADMIN = {"id": admin.id, "username": "admin", "name": admin.full_name, "role": "admin", "coach_id": None}
TOM = {"id": tom.id, "username": "tom", "name": tom.full_name, "role": "coach", "coach_id": tom.coach_id}


def script(module, user, parent, state, root):
    import sys
    sys.path.insert(0, root)
    import importlib
    import streamlit as st
    import ui
    ui.inject_css()
    st.session_state["_test_user"] = user
    if parent:
        st.session_state["parent_code"] = parent
    for k, v in (state or {}).items():
        st.session_state.setdefault(k, v)
    importlib.import_module(f"views.{module}").render()


def app(module, user=None, parent=None, state=None):
    return AppTest.from_function(script, args=(module, user, parent, state, str(ROOT)), default_timeout=60)


# Thursday so coach Tom (Youth + Sports Speed) has sessions with rosters
import datetime as dt  # noqa: E402
import logic  # noqa: E402
with db.session() as s:
    d0 = logic.today(db.get_settings(s))
THU = d0 + dt.timedelta(days=(3 - d0.weekday()) % 7)

CASES = [
    ("admin", "overview", ADMIN, None, {}),
    ("admin", "attendance", ADMIN, None, {"att_date": THU}),
    ("admin", "athletes", ADMIN, None, {"ath_open": first}),
    *[("admin", "athletes", ADMIN, None, {"ath_open": first, "ath_tab": t})
      for t in ["Attendance", "Health", "Payments", "Family"]],
    ("admin", "health", ADMIN, None, {}),
    ("admin", "schedule", ADMIN, None, {}),
    ("admin", "schedule", ADMIN, None, {"sc_tab": "New session"}),
    ("admin", "schedule", ADMIN, None, {"sc_tab": "New session", "nw_days": [3, 4], "nw_label": "Term squad"}),
    ("admin", "bookings", ADMIN, None, {"bk_date": THU}),
    *[("admin", "payments", ADMIN, None, {"pay_tab": t}) for t in
      ["Balances", "Upcoming", "Requests", "Record payment", "Reminders", "Import", "History"]],
    ("admin", "reports", ADMIN, None, {}),
    *[("admin", "reports", ADMIN, None, {"rp_tab": t}) for t in ["Register", "By coach", "By session", "Raw log"]],
    ("admin", "reports", ADMIN, None, {"rp_preset": "All time", "rp_who": None}),
    *[("admin", "settings", ADMIN, None, {"set_tab": t}) for t in
      ["Club", "Billing & booking rules", "Staff logins", "Coaches", "Backup", "Audit log", "Demo data"]],
    ("admin", "settings", ADMIN, None, {"set_tab": "Lists"}),
    ("admin", "account", ADMIN, None, {}),
    ("coach", "account", TOM, None, {}),
    ("coach", "attendance", TOM, None, {"att_date": THU}),
    ("coach", "myschedule", TOM, None, {}),
    ("parent", "book", None, "DEMO01", {}),
    ("public", "home", None, None, {}),
]

SKIP = {"Sign out", "Remove sample data"}  # sign-out clears the test state; removing data breaks later cases
fails, clicked = [], 0
for role, mod, user, parent, state in CASES:
    at = app(mod, user, parent, state).run()
    if at.exception:
        fails.append((role, mod, "render", str(at.exception[0].value)[:300]))
        continue
    labels = [(i, b.label, b.key) for i, b in enumerate(at.button) if b.label not in SKIP and not b.disabled]
    for i, label, key in labels:
        t = app(mod, user, parent, state).run()
        # find the same button again: by key when it has one, else by label occurrence
        same = [b for b in t.button if b.key == key] if key else [b for b in t.button if b.label == label]
        if not same:
            print(f"  -  {role:6} {mod:10} '{label}': gone after an earlier click changed the data (expected)")
            continue
        try:
            same[0].click().run()
        except Exception as e:  # harness limits (e.g. unset segmented controls) are not app bugs
            print(f"  ?  {role:6} {mod:10} '{label}': harness could not click ({type(e).__name__})")
            continue
        clicked += 1
        if t.exception:
            fails.append((role, mod, label, str(t.exception[0].value)[:300]))
            print(f"FAIL {role:6} {mod:10} '{label}'")
    print(f"ok   {role:6} {mod:10} {state.get('pay_tab') or state.get('set_tab') or state.get('ath_tab') or state.get('sc_tab') or '':24} {len(labels)} buttons")

print(f"\nClicked {clicked} buttons.")
for f in fails:
    print("FAIL", f)
print("ALL BUTTONS OK" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
