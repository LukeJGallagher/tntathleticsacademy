"""Sick & injured: who's out, log new, history, sessions missed. Coaches see their own athletes."""
from datetime import timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

import auth
import db
import logic
import ui
from db import Attendance, HealthEvent


def _back(eid):
    if not ui.is_admin():
        return
    with db.session() as s:
        ev = s.get(HealthEvent, eid)
        n = logic.mark_returned(s, ev, logic.today(db.get_settings(s)))
        auth.audit(s, ui.user().get("name"), ui.user().get("role"), "health.returned", ev.athlete.name)
        name = ev.athlete.name
    st.toast(f"{name} is back." + (f" {n} future session(s) reinstated." if n else ""), icon=":material/directions_run:")


def _log(by):
    if not ui.is_admin():
        return
    g = st.session_state.get
    if not g("hl_ath"):
        st.toast("Pick an athlete first.")
        return
    with db.session() as s:
        _, n = logic.report_health(s, g("hl_ath"), g("hl_kind"), g("hl_desc") or "", g("hl_start"), g("hl_ret"),
                                   by=by, notes=g("hl_notes") or "", cancel_sessions=g("hl_cancel"))
        auth.audit(s, by, ui.user().get("role"), "health.log", str(g("hl_ath")))
    st.toast(f"Logged." + (f" {n} session(s) cancelled and marked Sick." if n else ""), icon=":material/healing:")


def render():
    ui.require_admin()
    user = ui.user()
    with db.session() as s:
        cfg = db.get_settings(s)
        d = logic.today(cfg)
        ui.page_header("Sick & injured", "Parents can report from the booking page. Coaches see flags on the attendance list.", cfg)
        athletes = logic.list_athletes(s, user, cfg, active_only=False)
        ids = {a.id for a in athletes}
        by_id = {a.id: a for a in athletes}
        out = {k: v for k, v in logic.out_on(s, d).items() if k in ids}

        st.subheader(f"Out now ({len(out)})")
        if not out:
            st.caption("Nobody is on the list.")
        for aid, ev in sorted(out.items(), key=lambda kv: kv[1].start_date):
            overdue = ev.expected_return and ev.expected_return < d
            with st.container(border=True):
                c = st.columns([3, 2, 1.3], vertical_alignment="center")
                c[0].markdown(f"**{ev.athlete.name}** · {ev.kind}: {ev.description or '—'}  "
                              + (ui.badge("Return overdue", "red") if overdue else ""))
                c[0].caption(f"Reported by {ev.reported_by or '—'}" + (f" · {ev.notes}" if ev.notes else ""))
                c[1].markdown(f"Out **{(d - ev.start_date).days} days** (from {ev.start_date:%d %b})  \n"
                              f"Expected back: **{logic.fmt_date(ev.expected_return)}**")
                c[2].button("Back today", key=f"back_{ev.id}", on_click=_back, args=(ev.id,), type="primary",
                            icon=":material/directions_run:", width="stretch")

        with st.expander("Log illness or injury", icon=":material/add:"):
            with st.form("hl_form", clear_on_submit=True):
                st.selectbox("Athlete", [a.id for a in athletes if a.active], format_func=lambda i: by_id[i].name,
                             index=None, placeholder="Search name", key="hl_ath")
                c = st.columns(3)
                c[0].selectbox("Type", ["Illness", "Injury", "Other"], key="hl_kind")
                c[1].date_input("Out from", d, key="hl_start", format="DD/MM/YYYY")
                c[2].date_input("Expected back", None, key="hl_ret", format="DD/MM/YYYY")
                st.text_input("What happened", key="hl_desc")
                st.text_area("Notes (e.g. physio advice)", key="hl_notes")
                st.checkbox("Cancel their sessions until they're back", True, key="hl_cancel")
                st.form_submit_button("Log", type="primary", on_click=_log, args=(user.get("name"),))

        st.subheader("History")
        evs = [e for e in s.scalars(select(HealthEvent).order_by(HealthEvent.start_date.desc())) if e.athlete_id in ids]
        if evs:
            df = pd.DataFrame([{"Athlete": e.athlete.name, "Type": e.kind, "What": e.description, "From": e.start_date,
                                "Expected back": e.expected_return, "Returned": e.returned_date,
                                "Days out": ((e.returned_date or d) - e.start_date).days,
                                "Reported by": e.reported_by, "Notes": e.notes} for e in evs])
            st.dataframe(df, hide_index=True, width="stretch", height=300)
            st.download_button("Download CSV", df.to_csv(index=False).encode(), "health_history.csv", "text/csv",
                               icon=":material/download:")
        else:
            st.caption("Nothing logged yet.")

        st.subheader("Sessions missed through sickness (90 days)")
        rows = s.execute(select(Attendance.athlete_id, Attendance.status).where(
            Attendance.date >= d - timedelta(days=90), Attendance.status == "S")).all()
        cnt = pd.Series([by_id[r[0]].name for r in rows if r[0] in by_id]).value_counts()
        if cnt.empty:
            st.caption("None.")
        else:
            st.dataframe(cnt.rename_axis("Athlete").reset_index(name="Sick sessions"), hide_index=True, width="stretch")
