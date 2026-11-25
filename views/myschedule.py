"""My schedule (coaches): read-only list of their own upcoming session dates."""
from datetime import timedelta

import pandas as pd
import streamlit as st

import db
import logic
import ui


def render():
    user = ui.user()
    with db.session() as s:
        cfg = db.get_settings(s)
        d0 = logic.today(cfg)
        ui.page_header("My schedule", "Your sessions for the coming weeks. Ask the admin to change anything.", cfg)
        sessions = logic.list_sessions(s, user, cfg)
        if not sessions:
            st.info("You don't have any sessions yet. The admin assigns sessions to coaches in Schedule.",
                    icon=":material/event_busy:")
            return
        weeks = st.select_slider("Show", [1, 2, 4, 8, 12], value=4, format_func=lambda w: f"Next {w} week{'s' if w > 1 else ''}",
                                 key="ms_weeks")
        rows = []
        for i in range(weeks * 7):
            d = d0 + timedelta(days=i)
            for x in sessions:
                if not x.runs_on(d):
                    continue
                r = logic.day_roster(s, x, d)
                rows.append({"Date": d, "Time": f"{x.start_time}–{x.end_time}", "Session": x.label,
                             "Location": x.location or "", "Coming": len(r["attending"]), "Capacity": x.capacity,
                             "Sick": len(r["sick"]), "Waitlist": len(r["waitlist"])})
        st.caption(f"{len(rows)} session(s) · " + ", ".join(sorted({f'{logic.WEEKDAYS[x.weekday][:3]} {x.start_time} {x.label}'
                                                                   for x in sessions})))
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"Date": st.column_config.DateColumn(format="ddd DD MMM"),
                                        "Coming": st.column_config.ProgressColumn(
                                            min_value=0, max_value=max(x.capacity for x in sessions), format="%d")})
        terms = [x for x in sessions if x.ends_on]
        if terms:
            st.caption("Term ends: " + ", ".join(f"{x.label} ({logic.WEEKDAYS[x.weekday][:3]}) {x.ends_on:%d %b %Y}" for x in terms))
