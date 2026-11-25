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
os.environ["ROLLCALL_TEST"] = "1"

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
        st.session_state["_test_user"] = user
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
at.button[0].click().run()  # sign-in run: cookie written, page asks the browser to reload
at.run()                     # the reload (AppTest has no browser; session_state carries the token)
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

# 6b. Payment requests: upcoming forecast → request → paid creates one block per child
with db.session() as s:
    cfg = db.get_settings(s)
    d = logic.today(cfg)
    ups = logic.upcoming(s, logic.list_athletes(s, ADMIN, cfg), d, cfg, 30)
    fam = next(r for r in ups if len(r["kids"]) > 1 and not r["request"])
    req = logic.create_request(s, [(a, i) for a, _, i in fam["kids"]], fam["due"], cfg, "test")
    before = {a.id: len(a.blocks) for a, *_ in fam["kids"]}
    blocks = logic.settle_request(s, req, d, "Cash", cfg)
    assert req.status == "paid" and len(blocks) == len(fam["kids"])
    assert all(len(s.get(db.Athlete, aid).blocks) == n + 1 for aid, n in before.items())
    assert logic.settle_request(s, req, d, "Cash", cfg) == []  # idempotent
    print(f"ok   payment request {req.reference}: {len(blocks)} blocks, {len(ups)} families upcoming")

# 7. Regression tests for the Nov 2026 code audit
import auth  # noqa: E402
import backup  # noqa: E402
import ui  # noqa: E402

# 7.1 sign-in after creating a user in the same flow (first-run admin) must not deadlock
with db.session() as s:
    uid = auth.create_user(s, "owner2", "Owner Two", "admin", "longpassword1").id
ui.sign_in(uid)
print("ok   audit#1 sign-in after user creation")

with db.session() as s:
    cfg = db.get_settings(s)
    d = logic.today(cfg)
    a = s.scalar(select(db.Athlete).where(db.Athlete.programme == "Youth", db.Athlete.active.is_(True)))
    sess = [x for x in a.sessions if x.active][0]
    past = d - _dt.timedelta(days=(d.weekday() - sess.weekday) % 7 or 7)
    # 7.2 a sickness cancellation never overwrites a mark the coach recorded
    logic.set_mark(s, past, sess.id, a.id, "P", "coach")
    logic.cancel(s, a.id, sess.id, past, reason="Sick", source="parent")
    assert logic.marks_for(s, past, sess.id)[a.id] == "P"
    print("ok   audit#3 cancel keeps coach's Present mark")
    # 7.3 open-ended sickness stops removing the child from rosters after a week
    ev = db.HealthEvent(athlete_id=a.id, kind="Illness", start_date=d - _dt.timedelta(days=10))
    s.add(ev)
    s.flush()
    assert a.id in logic.out_on(s, d) and a.id not in logic.out_on(s, d, for_roster=True)
    s.delete(ev)
    print("ok   audit#5 open-ended sickness expires from rosters")
    # 7.4 backup without users / without an admin is refused, data untouched
    z = backup.make_backup(s)
    import io, json, zipfile  # noqa: E401,E402
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(z)) as zin, zipfile.ZipFile(buf, "w") as zout:
        for n in zin.namelist():
            if n == "users.json":
                zout.writestr(n, json.dumps([]))
            else:
                zout.writestr(n, zin.read(n))
    try:
        backup.restore_backup(s, buf.getvalue())
        raise AssertionError("restore without admin should fail")
    except ValueError:
        pass
    print("ok   audit#4 restore without an admin refused")
    # 7.5 coach can't see a one-off make-up athlete outside the ±14 day window
    tom_cfg = dict(cfg)
    toto = s.scalar(select(db.Athlete).where(db.Athlete.programme == "ToTs"))
    vis_before = logic.visible_athlete_ids(s, TOM, tom_cfg)
    s.add(db.Booking(date=d - _dt.timedelta(days=40), session_id=logic.list_sessions(s, TOM, tom_cfg)[0].id,
                     athlete_id=toto.id, status="booked", kind="makeup"))
    s.flush()
    assert toto.id not in logic.visible_athlete_ids(s, TOM, tom_cfg) or toto.id in vis_before
    print("ok   audit#2 old make-up booking doesn't grant coach visibility")
    s.rollback()

print("\nALL PASSED" if not FAIL else f"\n{len(FAIL)} FAILED: {FAIL}")
sys.exit(1 if FAIL else 0)
