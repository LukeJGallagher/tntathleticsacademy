"""Schedule (admin): weekly sessions, coaches, capacity, group lists. Archive, never delete."""
import streamlit as st
from sqlalchemy import select

import auth
import db
import logic
import ui
from db import Athlete, TrainingSession

DAY_ORDER = [6, 0, 1, 2, 3, 4, 5]  # Sunday first


def _save(sid):
    g = st.session_state.get
    k = f"sc_{sid}"
    with db.session() as s:
        x = s.get(TrainingSession, sid) if sid else TrainingSession()
        x.label = (g(f"{k}_label") or "").strip() or "Session"
        x.weekday = g(f"{k}_day")
        x.start_time = g(f"{k}_start").strftime("%H:%M")
        x.end_time = g(f"{k}_end").strftime("%H:%M")
        x.coach_id = g(f"{k}_coach")
        x.capacity = int(g(f"{k}_cap"))
        x.bookable = bool(g(f"{k}_book"))
        x.active = bool(g(f"{k}_active", True))
        x.athletes = [s.get(Athlete, i) for i in g(f"{k}_ath") or []]
        if not sid:
            s.add(x)
        s.flush()
        auth.audit(s, ui.user().get("name"), "admin", "session.save", x.label)
    st.toast("Saved", icon=":material/save:")


def _form(s, x: TrainingSession | None, coaches, athletes):
    from datetime import time
    sid = x.id if x else 0
    k = f"sc_{sid}"
    with st.form(k):
        c = st.columns(3)
        c[0].text_input("Name", x.label if x else "", key=f"{k}_label", placeholder="e.g. Youth Athletics (7–12)")
        c[1].selectbox("Day", DAY_ORDER, index=DAY_ORDER.index(x.weekday) if x else 0,
                       format_func=lambda i: logic.WEEKDAYS[i], key=f"{k}_day")
        c[2].selectbox("Coach", [c_.id for c_ in coaches], format_func={c_.id: c_.name for c_ in coaches}.get,
                       index=[c_.id for c_ in coaches].index(x.coach_id) if x and x.coach_id in [c_.id for c_ in coaches] else None,
                       key=f"{k}_coach")
        t = lambda v, dflt: time(*map(int, (v or dflt).split(":")))
        c[0].time_input("Start", t(x.start_time if x else None, "16:00"), key=f"{k}_start", step=300)
        c[1].time_input("End", t(x.end_time if x else None, "17:00"), key=f"{k}_end", step=300)
        c[2].number_input("Capacity", 1, 200, x.capacity if x else 12, key=f"{k}_cap")
        c[0].toggle("Parents can book", x.bookable if x else True, key=f"{k}_book")
        if x:
            c[1].toggle("Active (off = archived)", x.active, key=f"{k}_active")
        st.multiselect("Weekly group (regular athletes)", [a.id for a in athletes],
                       default=[a.id for a in x.athletes] if x else [],
                       format_func={a.id: a.name for a in athletes}.get, key=f"{k}_ath")
        st.form_submit_button("Save session" if x else "Create session", type="primary", on_click=_save, args=(sid,))


def render():
    ui.require_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        ui.page_header("Schedule", f"Weekly sessions at {cfg.get('venue') or 'the academy'}", cfg)
        sessions = logic.list_sessions(s, ui.user(), cfg)
        coaches = logic.list_coaches(s)
        athletes = list(s.scalars(select(Athlete).where(Athlete.active.is_(True)).order_by(Athlete.first_name)))

        cols = st.columns(7)
        for col, wd in zip(cols, DAY_ORDER):
            col.markdown(f"**{logic.WEEKDAYS[wd][:3].upper()}**")
            for x in [x for x in sessions if x.weekday == wd]:
                regs = len([a for a in x.athletes if a.active])
                col.markdown(f"<div style='border-left:4px solid #2D98C6;background:#1B1D20;border-radius:6px;"
                             f"padding:6px 8px;margin-bottom:6px;font-size:.85rem'><b>{x.start_time}</b> {x.label}<br>"
                             f"<span class='tnt-muted'>{x.coach.name if x.coach else '—'} · {regs}/{x.capacity}</span></div>",
                             unsafe_allow_html=True)

        st.write("")
        tab = ui.tabs(["Edit a session", "New session", "Archived"], key="sc_tab")
        if tab == "Edit a session":
            if sessions:
                sid = st.selectbox("Session", [x.id for x in sessions], key="sc_pick",
                                   format_func={x.id: f"{logic.WEEKDAYS[x.weekday][:3]} · {logic.session_title(x)}" for x in sessions}.get)
                _form(s, s.get(TrainingSession, sid), coaches, athletes)
            else:
                st.caption("No sessions yet.")
        elif tab == "New session":
            if not coaches:
                st.info("Add a coach first in **Settings → Coaches**.")
            _form(s, None, coaches, athletes)
        else:
            arch = [x for x in logic.list_sessions(s, ui.user(), cfg, include_archived=True) if not x.active]
            if not arch:
                st.caption("Nothing archived. Archived sessions keep their history.")
            for x in arch:
                st.markdown(f"- {logic.WEEKDAYS[x.weekday]} · {logic.session_title(x)}")
            if arch:
                sid = st.selectbox("Restore / edit", [x.id for x in arch], format_func={x.id: x.label for x in arch}.get)
                _form(s, s.get(TrainingSession, sid), coaches, athletes)
