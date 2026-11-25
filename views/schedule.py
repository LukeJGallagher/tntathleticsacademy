"""Schedule (admin): weekly sessions, terms (start/end dates), multi-day series, coaches, capacity, groups.
Archive, never delete."""
from datetime import time, timedelta

import streamlit as st
from sqlalchemy import select

import auth
import db
import logic
import ui
from db import Athlete, TrainingSession

DAY_ORDER = [6, 0, 1, 2, 3, 4, 5]  # Sunday first
REPEAT = ["For a number of weeks", "Until a date", "Ongoing (no end date)"]


def _t(v, dflt):
    return time(*map(int, (v or dflt).split(":")))


# --------------------------------------------------------------------------- create (multi-day, term)

def _create():
    if not ui.is_admin():
        return
    g = st.session_state.get
    days = g("nw_days") or []
    if not days:
        st.toast("Pick at least one day.", icon=":material/error:")
        return
    start = g("nw_from")
    mode = g("nw_repeat")
    if mode == REPEAT[0]:
        weeks = int(g("nw_weeks") or 0)
    elif mode == REPEAT[1]:
        until = g("nw_until")
        if not until or until < start:
            st.toast("The end date must be after the start date.", icon=":material/error:")
            return
        weeks = (until - start).days // 7 + 1
    else:
        weeks = None
    with db.session() as s:
        made = logic.create_series(
            s, (g("nw_label") or "").strip() or "Session", days, g("nw_start").strftime("%H:%M"),
            g("nw_end").strftime("%H:%M"), g("nw_coach"), int(g("nw_cap")), bool(g("nw_book")), start, weeks,
            g("nw_ath") or [], programme=g("nw_prog"), location=g("nw_loc"))
        if mode == REPEAT[1]:  # exact last date rather than whole weeks
            for x in made:
                x.ends_on = g("nw_until")
        n = sum(logic.occurrences(x) for x in made) if weeks or mode == REPEAT[1] else None
        auth.audit(s, ui.user().get("name"), "admin", "session.create_series", made[0].label,
                   {"days": len(made), "weeks": weeks, "occurrences": n})
    st.session_state["sc_flash"] = (f"Created {len(made)} weekly session(s)"
                                    + (f", {n} dates in total." if n else ", ongoing."))
    st.session_state["sc_tab"] = "Edit a session"


def _new_form(coaches, athletes, d0, cfg):
    c = st.columns(3)
    c[0].text_input("Name", key="nw_label", placeholder="e.g. Ages 7–12")
    c[1].selectbox("Coach", [x.id for x in coaches], format_func={x.id: x.name for x in coaches}.get, index=None,
                   key="nw_coach")
    c[2].number_input("Capacity", 1, 200, 12, key="nw_cap")
    progs, locs = logic.setting_list(cfg, "programmes"), logic.setting_list(cfg, "locations")
    c = st.columns(2)
    c[0].selectbox("Programme", progs, index=0 if progs else None, key="nw_prog",
                   help="Edit the list in Settings → Lists")
    c[1].selectbox("Location", locs, index=0 if locs else None, key="nw_loc", help="Edit the list in Settings → Lists")
    st.multiselect("Days", DAY_ORDER, format_func=lambda i: logic.WEEKDAYS[i], key="nw_days",
                   placeholder="Pick one or more days, e.g. Thursday and Friday")
    c = st.columns(3)
    c[0].time_input("Start time", _t(None, "16:00"), key="nw_start", step=300)
    c[1].time_input("End time", _t(None, "17:00"), key="nw_end", step=300)
    c[2].toggle("Parents can book", True, key="nw_book")

    st.markdown("**Repeats weekly**")
    c = st.columns(3)
    start = c[0].date_input("Starting", d0, key="nw_from", format="DD/MM/YYYY")
    mode = c[1].selectbox("Ends", REPEAT, key="nw_repeat")
    if mode == REPEAT[0]:
        weeks = c[2].number_input("Weeks", 1, 104, 20, key="nw_weeks")
        end = start + timedelta(weeks=int(weeks)) - timedelta(days=1)
    elif mode == REPEAT[1]:
        end = c[2].date_input("Last date", start + timedelta(weeks=20), min_value=start, key="nw_until",
                              format="DD/MM/YYYY")
    else:
        end = None
    st.multiselect("Weekly group (regular athletes)", [a.id for a in athletes],
                   format_func={a.id: a.name for a in athletes}.get, key="nw_ath")

    days = st.session_state.get("nw_days") or []
    if days:
        if end:
            per = {wd: logic.occurrences(TrainingSession(weekday=wd), start, end) for wd in days}
            st.info(f"Creates **{len(days)}** weekly session(s) on "
                    + ", ".join(f"{logic.WEEKDAYS[wd]} ({n}×)" for wd, n in sorted(per.items(), key=lambda kv: DAY_ORDER.index(kv[0])))
                    + f": **{sum(per.values())} dates** from {start:%a %d %b %Y} to {end:%a %d %b %Y}.",
                    icon=":material/event_repeat:")
        else:
            st.info(f"Creates {len(days)} weekly session(s) from {start:%a %d %b %Y}, with no end date.",
                    icon=":material/event_repeat:")
    st.button("Create sessions", type="primary", icon=":material/add:", on_click=_create, disabled=not days)


# --------------------------------------------------------------------------- edit

def _save(sid):
    if not ui.is_admin():
        return
    g = st.session_state.get
    k = f"sc_{sid}"
    with db.session() as s:
        x = s.get(TrainingSession, sid)
        targets = [x]
        if x.series and g(f"{k}_all"):
            targets = list(s.scalars(select(TrainingSession).where(TrainingSession.series == x.series)))
        for t in targets:
            t.label = (g(f"{k}_label") or "").strip() or "Session"
            t.start_time = g(f"{k}_start").strftime("%H:%M")
            t.end_time = g(f"{k}_end").strftime("%H:%M")
            t.coach_id = g(f"{k}_coach")
            t.capacity = int(g(f"{k}_cap"))
            t.programme, t.location = g(f"{k}_prog"), g(f"{k}_loc")
            t.bookable = bool(g(f"{k}_book"))
            t.active = bool(g(f"{k}_active", True))
            t.starts_on = g(f"{k}_from")
            t.ends_on = None if g(f"{k}_ongoing") else g(f"{k}_to")
            t.athletes = [s.get(Athlete, i) for i in g(f"{k}_ath") or []]
        x.weekday = g(f"{k}_day")  # the day itself only changes on this one session
        s.flush()
        auth.audit(s, ui.user().get("name"), "admin", "session.save", x.label, {"sessions": len(targets)})
    st.toast(f"Saved{f' ({len(targets)} days in this series)' if len(targets) > 1 else ''}", icon=":material/save:")


def _form(s, x: TrainingSession, coaches, athletes):
    k = f"sc_{x.id}"
    cfg = db.get_settings(s)
    progs = logic.setting_list(cfg, "programmes")
    locs = logic.setting_list(cfg, "locations")
    if x.programme and x.programme not in progs:
        progs.append(x.programme)  # keep a value removed from the list readable
    if x.location and x.location not in locs:
        locs.append(x.location)
    siblings = (list(s.scalars(select(TrainingSession).where(TrainingSession.series == x.series)))
                if x.series else [x])
    with st.form(k):
        c = st.columns(3)
        c[0].text_input("Name", x.label, key=f"{k}_label")
        c[1].selectbox("Day", DAY_ORDER, index=DAY_ORDER.index(x.weekday), format_func=lambda i: logic.WEEKDAYS[i],
                       key=f"{k}_day")
        c[2].selectbox("Coach", [c_.id for c_ in coaches], format_func={c_.id: c_.name for c_ in coaches}.get,
                       index=[c_.id for c_ in coaches].index(x.coach_id) if x.coach_id in [c_.id for c_ in coaches] else None,
                       key=f"{k}_coach")
        c[0].time_input("Start", _t(x.start_time, "16:00"), key=f"{k}_start", step=300)
        c[1].time_input("End", _t(x.end_time, "17:00"), key=f"{k}_end", step=300)
        c[2].number_input("Capacity", 1, 200, x.capacity, key=f"{k}_cap")
        p1, p2 = st.columns(2)
        p1.selectbox("Programme", progs, index=progs.index(x.programme) if x.programme in progs else None,
                     key=f"{k}_prog")
        p2.selectbox("Location", locs, index=locs.index(x.location) if x.location in locs else None, key=f"{k}_loc")
        c = st.columns(3)
        c[0].date_input("First date", x.starts_on, key=f"{k}_from", format="DD/MM/YYYY")
        c[1].date_input("Last date", x.ends_on, key=f"{k}_to", format="DD/MM/YYYY")
        c[2].checkbox("Ongoing (no last date)", x.ends_on is None, key=f"{k}_ongoing")
        c[0].toggle("Parents can book", x.bookable, key=f"{k}_book")
        c[1].toggle("Active (off = archived)", x.active, key=f"{k}_active")
        st.multiselect("Weekly group (regular athletes)", [a.id for a in athletes], default=[a.id for a in x.athletes],
                       format_func={a.id: a.name for a in athletes}.get, key=f"{k}_ath")
        if len(siblings) > 1:
            st.checkbox(f"Apply to all {len(siblings)} days in this series ("
                        + ", ".join(logic.WEEKDAYS[y.weekday][:3] for y in sorted(siblings, key=lambda y: DAY_ORDER.index(y.weekday)))
                        + "), except the day", True, key=f"{k}_all")
        st.form_submit_button("Save session", type="primary", on_click=_save, args=(x.id,))
    if x.starts_on and x.ends_on:
        st.caption(f"{logic.occurrences(x)} dates in this term ({x.term_label()}).")


# --------------------------------------------------------------------------- page

def render():
    ui.require_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        d0 = logic.today(cfg)
        ui.page_header("Schedule", f"Weekly sessions at {cfg.get('venue') or 'the academy'}", cfg)
        sessions = logic.list_sessions(s, ui.user(), cfg)
        coaches = logic.list_coaches(s)
        athletes = list(s.scalars(select(Athlete).where(Athlete.active.is_(True)).order_by(Athlete.first_name)))

        cols = st.columns(7)
        for col, wd in zip(cols, DAY_ORDER):
            col.markdown(f"**{logic.WEEKDAYS[wd][:3].upper()}**")
            for x in [x for x in sessions if x.weekday == wd]:
                regs = len([a for a in x.athletes if a.active])
                ended = x.ends_on and x.ends_on < d0
                upcoming = x.starts_on and x.starts_on > d0
                tag = " · ended" if ended else (f" · from {x.starts_on:%d %b}" if upcoming else "")
                col.markdown(f"<div style='border-left:4px solid {'#54595F' if ended else '#2D98C6'};background:#1B1D20;"
                             f"border-radius:6px;padding:6px 8px;margin-bottom:6px;font-size:.85rem;"
                             f"opacity:{.55 if ended else 1}'><b>{x.start_time}</b> {x.label}<br>"
                             f"<span class='tnt-muted'>{x.coach.name if x.coach else '—'} · {regs}/{x.capacity}"
                             f"{'<br>' + x.location if x.location else ''}{' · ' + x.programme if x.programme else ''}"
                             f"<br>{x.term_label()}{tag}</span></div>", unsafe_allow_html=True)

        st.write("")
        tab = ui.tabs(["Edit a session", "New session", "Archived"], key="sc_tab")
        if tab == "Edit a session":
            if msg := st.session_state.pop("sc_flash", None):
                st.success(msg, icon=":material/check_circle:")
            if sessions:
                sid = st.selectbox("Session", [x.id for x in sessions], key="sc_pick",
                                   format_func={x.id: f"{logic.WEEKDAYS[x.weekday][:3]} · {logic.session_title(x)} · "
                                                      f"{x.term_label()}" for x in sessions}.get)
                _form(s, s.get(TrainingSession, sid), coaches, athletes)
            else:
                st.caption("No sessions yet.")
        elif tab == "New session":
            if not coaches:
                st.info("Add a coach first in **Settings → Coaches**.")
            _new_form(coaches, athletes, d0, cfg)
        else:
            arch = [x for x in logic.list_sessions(s, ui.user(), cfg, include_archived=True) if not x.active]
            if not arch:
                st.caption("Nothing archived. Archived sessions keep their history.")
            for x in arch:
                st.markdown(f"- {logic.WEEKDAYS[x.weekday]} · {logic.session_title(x)} · {x.term_label()}")
            if arch:
                sid = st.selectbox("Restore / edit", [x.id for x in arch], format_func={x.id: x.label for x in arch}.get)
                _form(s, s.get(TrainingSession, sid), coaches, athletes)
