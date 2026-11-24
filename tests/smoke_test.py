"""Smoke test: every page renders for every role on a fresh demo database, plus key flows.
Run from the rollcall folder:  python tests/smoke_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp()) / "smoke.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TMP.as_posix()}"
os.environ["DEMO_MODE"] = "1"

from streamlit.testing.v1 import AppTest  # noqa: E402

FAIL = []


def check(at, label):
    errs = [e.value for e in at.exception]
    if errs:
        FAIL.append(label)
        print(f"FAIL {label}:\n  " + "\n  ".join(str(e)[:1500] for e in errs))
    else:
        print(f"ok   {label}")


def page(module: str, user: dict | None = None, parent: str | None = None, state: dict | None = None):
    def script(module, user, parent, state, root):
        import sys
        sys.path.insert(0, root)
        import importlib
        import streamlit as st
        import ui
        ui.inject_css()
        st.session_state["_user"] = user
        if parent:
            st.session_state["parent_code"] = parent
        for k, v in (state or {}).items():
            st.session_state.setdefault(k, v)
        importlib.import_module(f"views.{module}").render()
    at = AppTest.from_function(script, args=(module, user, parent, state, str(ROOT)), default_timeout=60)
    return at


# 1. Landing + demo auto-seed + admin login through the real app
at = AppTest.from_file("app.py", default_timeout=60).run()
check(at, "landing (app.py)")
at.text_input(key="li_user").input("admin")
at.text_input(key="li_pass").input("demo1234")
at.button[0].click().run()
check(at, "admin sign-in → overview")
assert any("Overview" in m.value for m in at.title), [t.value for t in at.title]

bad = AppTest.from_file("app.py", default_timeout=60).run()
bad.text_input(key="li_user").input("admin")
bad.text_input(key="li_pass").input("wrong")
bad.button[0].click().run()
assert any("Wrong username" in e.value for e in bad.error), "bad password should error"
print("ok   wrong password rejected")

import db  # noqa: E402
from sqlalchemy import select  # noqa: E402

with db.session() as s:
    admin = s.scalar(select(db.User).where(db.User.username == "admin"))
    tom = s.scalar(select(db.User).where(db.User.username == "tom"))
    ADMIN = {"id": admin.id, "username": "admin", "name": admin.full_name, "role": "admin", "coach_id": None}
    TOM = {"id": tom.id, "username": "tom", "name": tom.full_name, "role": "coach", "coach_id": tom.coach_id}
    first_ath = s.scalar(select(db.Athlete.id).where(db.Athlete.programme == "Youth"))

# 2. Every page, admin
for mod in ["overview", "attendance", "athletes", "health", "schedule", "bookings", "payments", "reports", "settings"]:
    at = page(mod, ADMIN).run()
    check(at, f"admin · {mod}")

# athlete profile open
at = page("athletes", ADMIN, state={"ath_open": first_ath}).run()
check(at, "admin · athlete profile")

# payments sub-views
for tab in ["Record payment", "Reminders", "History"]:
    at = page("payments", ADMIN, state={"pay_tab": tab}).run()
    check(at, f"admin · payments/{tab}")

# 3. Coach pages + scope
for mod in ["attendance", "overview", "athletes", "bookings", "health"]:
    at = page(mod, TOM).run()
    check(at, f"coach · {mod}")
at = page("payments", TOM).run()
assert at.error and "Only the admin" in at.error[0].value, "coach must be blocked from payments"
print("ok   coach blocked from payments")

# 4. Attendance: mark, re-render shows it, "mark the rest" fills the roster
# (AppTest can't drive a segmented control while others are unset, so marks go through the callback's function.)
import logic  # noqa: E402
with db.session() as s:
    cfg = db.get_settings(s)
    d = logic.today(cfg)
    sess = logic.list_sessions(s, TOM, cfg, weekday=d.weekday())
    if sess:
        r = logic.day_roster(s, sess[0], d)
        logic.set_mark(s, d, sess[0].id, r["attending"][0][0].id, "L", "test")
        n = logic.mark_rest_present(s, d, sess[0], "test")
        marks = logic.marks_for(s, d, sess[0].id)
        expected = len(r["attending"]) + len(r["sick"])
        assert len(marks) == expected, (len(marks), expected)
        print(f"ok   attendance marked {len(marks)}/{expected} (rest-present filled {n}, {len(r['sick'])} sick)")
at = page("attendance", TOM).run()
check(at, "coach · attendance after marking")

# 5. Parent booking page with a family code
at = page("book", None, parent="DEMO01").run()
check(at, "parent · book")
assert any(b.label in ("Book", "Join waitlist", "Can't make it") for b in at.button), "parent sees session actions"
with db.session() as s:
    cfg = db.get_settings(s)
    kid = logic.family_athletes(s, "DEMO01")[0]
    _, n = logic.report_health(s, kid.id, "Illness", "test fever", logic.today(cfg), None, by="Parent", source="parent")
    print(f"ok   parent sickness report cancelled {n} session(s) today")
at = page("book", None, parent="DEMO01").run()
check(at, "parent · book after sickness report")

# 5b. Booking states: sick regulars drop off "coming", unconfirmed don't hold a place, marking present confirms
import datetime as _dt  # noqa: E402
with db.session() as s:
    cfg = db.get_settings(s)
    d = logic.today(cfg)
    sess = [x for x in logic.list_sessions(s, ADMIN, cfg) if "Youth" in x.label][0]
    nd = d + _dt.timedelta(days=(sess.weekday - d.weekday()) % 7 or 7)
    r0 = logic.day_roster(s, sess, nd)
    victim = r0["attending"][0][0]
    s.add(db.HealthEvent(athlete_id=victim.id, kind="Illness", start_date=nd - _dt.timedelta(days=1),
                         expected_return=nd + _dt.timedelta(days=3)))
    s.flush()
    r1 = logic.day_roster(s, sess, nd)
    assert victim.id in {a.id for a, _ in r1["sick"]} and r1["spots"] == r0["spots"] + 1
    outsider = s.scalar(select(db.Athlete).where(db.Athlete.programme == "ToTs"))
    assert logic.book(s, outsider.id, sess.id, nd, kind="trial", confirmed=False) == "pending"
    r2 = logic.day_roster(s, sess, nd)
    assert r2["spots"] == r1["spots"] and outsider.id in {a.id for a, _ in r2["pending"]}
    logic.set_mark(s, nd, sess.id, outsider.id, "P", "test")
    r3 = logic.day_roster(s, sess, nd)
    assert outsider.id in {a.id for a, _ in r3["attending"]}
    print("ok   sick regular frees a place; unconfirmed holds none; present confirms")

# 6. Record a payment (single) through the logic layer, then balances again
import logic  # noqa: E402
with db.session() as s:
    cfg = db.get_settings(s)
    d = logic.today(cfg)
    a = s.get(db.Athlete, first_ath)
    before = logic.athlete_finance(s, a, d, cfg)["next_due"]
    logic.record_payment(s, a.id, d, 8, 2, 480, "Cash", True, cfg)
    after = logic.athlete_finance(s, a, d, cfg)["next_due"]
    print(f"ok   payment stacks: next due {before} → {after}")
    assert after is None or before is None or after > before

print("\nALL PASSED" if not FAIL else f"\n{len(FAIL)} FAILED: {FAIL}")
sys.exit(1 if FAIL else 0)
