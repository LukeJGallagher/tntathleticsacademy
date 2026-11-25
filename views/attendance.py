"""Attendance: one tap per athlete, saved immediately. Phone-first."""
import streamlit as st
from sqlalchemy import select

import db
import logic
import ui
from db import Athlete, TrainingSession

OPTIONS = ["P", "L", "A", "S", "E"]
LABELS = {"P": "Present", "L": "Late", "A": "Absent", "S": "Sick", "E": "Excused"}


def _bump():
    st.session_state["att_ver"] = st.session_state.get("att_ver", 0) + 1


def _save(d, sid, aid, key, by):
    if not ui.user():
        return
    with db.session() as s:
        logic.set_mark(s, d, sid, aid, st.session_state.get(key), by)


def _rest(d, sid, by):
    if not ui.user():
        return
    with db.session() as s:
        n = logic.mark_rest_present(s, d, s.get(TrainingSession, sid), by)
    _bump()
    st.toast(f"Marked {n} athlete(s). Anyone on the sick list was marked Sick.", icon=":material/done_all:")


def _add_makeup(d, sid, key, kind_key):
    if not ui.user():
        return
    aid = st.session_state.get(key)
    if not aid:
        return
    with db.session() as s:
        res = logic.book(s, aid, sid, d, kind=st.session_state.get(kind_key, "makeup"), source="staff", override=True)
        name = s.get(Athlete, aid).name
    st.toast(f"{name}: {'added' if res == 'booked' else res}", icon=":material/person_add:")


def render():
    user = ui.user()
    with db.session() as s:
        cfg = db.get_settings(s)
        ui.page_header("Attendance", "Tap once per athlete. Each tap saves. Tap again to clear.", cfg)

        c1, c2 = st.columns([1, 2])
        d = c1.date_input("Date", value=logic.today(cfg), key="att_date", format="DD/MM/YYYY")
        sessions = logic.list_sessions(s, user, cfg, on=d)
        if ui.is_admin() and sessions:
            coaches = sorted({x.coach.name for x in sessions if x.coach})
            pick = c2.multiselect("Coach", coaches, placeholder="All coaches", key="att_coach")
            if pick:
                sessions = [x for x in sessions if x.coach and x.coach.name in pick]

        if not sessions:
            st.info(f"No {'of your ' if not ui.is_admin() else ''}sessions on {d:%A %d %b}.",
                    icon=":material/event_busy:")
            return

        out = logic.out_on(s, d, for_roster=True)
        ver = st.session_state.get("att_ver", 0)
        admin = ui.is_admin()
        fee_cache: dict[int, str] = {}  # fee flags are admin-only (payment data)
        everyone = list(s.scalars(select(Athlete).where(Athlete.active.is_(True)).order_by(Athlete.first_name)))
        st.caption("**P** Present · **L** Late · **A** Absent · **S** Sick · **E** Excused")

        for sess in sessions:
            r = logic.day_roster(s, sess, d)
            marks = logic.marks_for(s, d, sess.id)
            rows = r["attending"] + [(a, "sick") for a, _ in r["sick"]] + [(a, "unconfirmed") for a, _ in r["pending"]]
            on_roster = {a.id for a, _ in rows}
            expected = r["attending"] + [(a, "sick") for a, _ in r["sick"]]
            done = sum(1 for a, _ in expected if a.id in marks)
            with st.container(border=True):
                st.subheader(sess.label)
                st.caption(f":material/schedule: {sess.start_time}–{sess.end_time}"
                           f"{'  ·  ' + sess.coach.name if sess.coach else ''}"
                           f"  ·  {len(r['attending'])}/{sess.capacity} places  ·  **{done}/{len(expected)} marked**")
                if expected:
                    st.progress(done / len(expected))
                if r["spots"] < 0:
                    st.warning(f"Over capacity by {-r['spots']}.", icon=":material/warning:")

                for a, kind in rows:
                    flags = []
                    if kind == "unconfirmed":
                        flags.append(ui.badge("Unconfirmed", "gray"))
                    elif kind not in ("regular", "sick"):
                        flags.append(ui.badge(kind.title(), "blue"))
                    if a.id in out:
                        flags.append(ui.badge(f"{out[a.id].kind}", "red"))
                    if admin:
                        if a.id not in fee_cache:
                            fee_cache[a.id] = logic.athlete_finance(s, a, d, cfg)["status"]
                        if fee_cache[a.id] in ("due", "unpaid"):
                            flags.append(ui.badge("Fees " + logic.FEE_LABEL[fee_cache[a.id]].lower(), "orange"))
                    if a.medical_notes:
                        flags.append(ui.badge("Medical", "violet"))
                    with st.container(key=f"row_{sess.id}_{a.id}"):
                        col_a, col_b = st.columns([2, 3], vertical_alignment="center", gap="small")
                        col_a.markdown(f"**{a.name}**  {' '.join(flags)}", help=a.medical_notes or None)
                        key = f"m_{d}_{sess.id}_{a.id}_{ver}"
                        with col_b:
                            ui.segmented(
                                "Mark", OPTIONS, key=key, default=marks.get(a.id), width="stretch",
                                on_change=_save, args=(d, sess.id, a.id, key, user.get("name", "")),
                                label_visibility="collapsed")

                if not rows:
                    st.info("No athletes confirmed for this session yet. Add them below or in Bookings.",
                            icon=":material/person_add:")
                else:
                    st.button("Mark the rest present", key=f"rest_{sess.id}_{d}", icon=":material/done_all:",
                              on_click=_rest, args=(d, sess.id, user.get("name", "")), width="stretch",
                              disabled=done == len(expected), type="primary",
                              help="Anyone on the sick list is marked Sick. Unconfirmed athletes are left for you to tap.")

                notes = []
                if r["cancelled"]:
                    notes.append("**Cancelled:** " + ", ".join(f"{a.name} ({b.reason or 'no reason'})"
                                                               for a, b in r["cancelled"]))
                if r["waitlist"]:
                    notes.append("**Waitlist:** " + ", ".join(f"{i + 1}. {a.name}" for i, (a, _) in enumerate(r["waitlist"])))
                for n in notes:
                    st.caption(n)

                with st.expander("Add a make-up, trial or casual athlete"):
                    choices = [a for a in everyone if a.id not in on_roster]
                    k1, k2, k3 = st.columns([3, 2, 1], vertical_alignment="bottom")
                    k1.selectbox("Athlete", [a.id for a in choices], key=f"mk_{sess.id}_{d}",
                                 format_func=lambda i, m={a.id: a for a in choices}: m[i].name,
                                 index=None, placeholder="Search name")
                    k2.selectbox("Type", ["makeup", "trial", "casual"], key=f"mkk_{sess.id}_{d}",
                                 format_func=str.title)
                    k3.button("Add", key=f"mkb_{sess.id}_{d}", on_click=_add_makeup,
                              args=(d, sess.id, f"mk_{sess.id}_{d}", f"mkk_{sess.id}_{d}"))
