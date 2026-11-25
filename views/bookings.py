"""Bookings (staff): who's coming per session and date, waitlist, cancellations, 14-day overview."""
from datetime import timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

import db
import logic
import ui
from db import Athlete


def _do(action, aid, sid, d, extra=None):
    if not ui.is_admin():
        return
    with db.session() as s:
        if action == "cancel":
            promoted = logic.cancel(s, aid, sid, d, reason=extra or "Staff", source="staff")
            st.toast("Cancelled." + (f" Moved in from waitlist: {', '.join(promoted)}" if promoted else ""))
        elif action == "promote":
            res = logic.book(s, aid, sid, d, source="staff", override=True)
            st.toast("Moved in from the waitlist." if res == "booked" else res)
        elif action == "reinstate":
            logic.reinstate(s, aid, sid, d)
            st.toast("Reinstated.")
        elif action == "remove":
            logic.remove_booking(s, aid, sid, d)
            st.toast("Removed.")
        elif action == "confirm":
            res = logic.confirm(s, aid, sid, d)
            st.toast("Confirmed." if res == "booked" else "Session full: moved to the waitlist.")


def _add(sid, d):
    if not ui.is_admin():
        return
    g = st.session_state.get
    aid = g("bk_add_ath")
    if not aid:
        return
    with db.session() as s:
        res = logic.book(s, aid, sid, d, kind=g("bk_add_kind", "casual"), source="staff", override=g("bk_add_over", False),
                         confirmed=g("bk_add_conf", "Confirmed") == "Confirmed")
    st.toast({"booked": "Booked.", "waitlist": "Session full: added to the waitlist.", "already": "Already on the list.",
              "pending": "Pencilled in as unconfirmed.",
              "wrong_day": "That session doesn't run on this date."}.get(res, res))


def render():
    ui.require_admin()
    user = ui.user()
    with db.session() as s:
        cfg = db.get_settings(s)
        d0 = logic.today(cfg)
        ui.page_header("Bookings", "Who's coming, who's on the waitlist, and who's cancelled.", cfg)
        sessions = logic.list_sessions(s, user, cfg)
        if not sessions:
            st.info("No sessions to show.")
            return
        c1, c2 = st.columns([1, 3])
        d = c1.date_input("Date", d0, key="bk_date", format="DD/MM/YYYY")
        todays = [x for x in sessions if x.runs_on(d)]
        if not todays:
            c2.info(f"No sessions on {d:%A %d %b}.")
        else:
            sid = c2.selectbox("Session", [x.id for x in todays], format_func={x.id: logic.session_title(x) for x in todays}.get,
                               key=f"bk_sess_{d}")
            sess = next(x for x in todays if x.id == sid)
            r = logic.day_roster(s, sess, d)
            m = st.columns(4)
            m[0].metric("Coming", len(r["attending"]), border=True)
            m[1].metric("Capacity", sess.capacity, border=True)
            m[2].metric("Places left", max(r["spots"], 0), border=True)
            m[3].metric("Waitlist", len(r["waitlist"]), border=True)
            if r["spots"] < 0:
                st.warning(f"Over capacity by {-r['spots']}.")

            left, right = st.columns(2, gap="large")
            with left, st.container(border=True):
                st.subheader("Coming")
                st.caption("Regular group members are coming unless they cancel or are on the sick list.")
                if not r["attending"]:
                    st.info("No athletes confirmed yet. Add them with **Add a booking**.", icon=":material/person_add:")
                for a, kind in r["attending"]:
                    c = st.columns([3, 2, 1.3], vertical_alignment="center")
                    c[0].markdown(f"{a.name}  {ui.badge(kind.title(), 'blue') if kind != 'regular' else ''}")
                    c[1].selectbox("Reason", ["Sick", "Injured", "Travel", "Other"], key=f"bkr_{sid}_{d}_{a.id}",
                                   label_visibility="collapsed")
                    c[2].button("Cancel", key=f"bkc_{sid}_{d}_{a.id}", on_click=_do, width="stretch",
                                args=("cancel", a.id, sid, d, st.session_state.get(f"bkr_{sid}_{d}_{a.id}", "Sick")))
            with right:
                if r["pending"]:
                    with st.container(border=True):
                        st.subheader("Unconfirmed")
                        st.caption("Pencilled in. They don't hold a place until confirmed. Marking them present confirms them.")
                        for a, b in r["pending"]:
                            c = st.columns([3, 1.4, 1.2], vertical_alignment="center")
                            c[0].markdown(f"{a.name}  {ui.badge(b.kind.title(), 'blue')}")
                            c[1].button("Confirm", key=f"bkf_{sid}_{d}_{a.id}", on_click=_do, args=("confirm", a.id, sid, d),
                                        width="stretch", type="primary")
                            c[2].button("Remove", key=f"bkpx_{sid}_{d}_{a.id}", on_click=_do, args=("remove", a.id, sid, d),
                                        width="stretch")
                if r["sick"]:
                    with st.container(border=True):
                        st.subheader("Out sick")
                        st.caption("On the sick list, so not coming. Their place is free.")
                        for a, ev in r["sick"]:
                            st.markdown(f"{a.name} · {ev.kind}: {ev.description or ''} · back {logic.fmt_date(ev.expected_return)}")
                with st.container(border=True):
                    st.subheader("Waitlist")
                    if not r["waitlist"]:
                        st.caption("Empty")
                    for i, (a, b) in enumerate(r["waitlist"]):
                        c = st.columns([3, 1.5, 1.2], vertical_alignment="center")
                        c[0].markdown(f"{i + 1}. {a.name}")
                        c[1].button("Move in", key=f"bkp_{sid}_{d}_{a.id}", on_click=_do, args=("promote", a.id, sid, d),
                                    width="stretch", help="Adds them even if the session is full")
                        c[2].button("Remove", key=f"bkx_{sid}_{d}_{a.id}", on_click=_do, args=("remove", a.id, sid, d),
                                    width="stretch")
                with st.container(border=True):
                    st.subheader("Cancelled")
                    if not r["cancelled"]:
                        st.caption("None")
                    for a, b in r["cancelled"]:
                        c = st.columns([3, 1.4], vertical_alignment="center")
                        c[0].markdown(f"{a.name} · {b.reason or ''} {ui.badge('by parent', 'gray') if b.source == 'parent' else ''}")
                        c[1].button("Reinstate", key=f"bki_{sid}_{d}_{a.id}", on_click=_do, args=("reinstate", a.id, sid, d),
                                    width="stretch")
                with st.container(border=True):
                    st.subheader("Add a booking")
                    on = ({a.id for a, _ in r["attending"]} | {a.id for a, _ in r["waitlist"]}
                          | {a.id for a, _ in r["pending"]})
                    others = [a for a in s.scalars(select(Athlete).where(Athlete.active.is_(True)).order_by(Athlete.first_name))
                              if a.id not in on]
                    st.selectbox("Athlete", [a.id for a in others], format_func={a.id: a.name for a in others}.get,
                                 index=None, placeholder="Search name", key="bk_add_ath")
                    c = st.columns(2, vertical_alignment="bottom")
                    c[0].selectbox("Type", ["casual", "makeup", "trial"], format_func=str.title, key="bk_add_kind")
                    c[1].checkbox("Allow over capacity", key="bk_add_over")
                    st.radio("Status", ["Confirmed", "Unconfirmed (pencil in)"], horizontal=True, key="bk_add_conf")
                    st.button("Book", type="primary", on_click=_add, args=(sid, d))

        st.subheader("Next 14 days")
        rows = []
        for i in range(14):
            dd = d0 + timedelta(days=i)
            for x in sessions:
                if not x.runs_on(dd):
                    continue
                r = logic.day_roster(s, x, dd)
                rows.append({"Date": dd, "Day": dd.strftime("%a"), "Session": f"{x.start_time} {x.label}",
                             "Coach": x.coach.name if x.coach else "", "Coming": len(r["attending"]),
                             "Capacity": x.capacity, "Places left": r["spots"], "Unconfirmed": len(r["pending"]),
                             "Sick": len(r["sick"]), "Waitlist": len(r["waitlist"]), "Cancelled": len(r["cancelled"])})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"Date": st.column_config.DateColumn(format="DD MMM"),
                                        "Coming": st.column_config.ProgressColumn(min_value=0, max_value=max(x.capacity for x in sessions), format="%d")})
