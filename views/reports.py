"""Reports (admin): attendance totals and breakdowns for any period. CSV on every table."""
from datetime import timedelta

import streamlit as st

import auth
import db
import logic
import ui


def _logged(name):
    with db.session() as s:
        auth.audit(s, ui.user().get("name"), "admin", "export.run", name)


def _table(df, name):
    if df.empty:
        st.caption("No data for this period.")
        return
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"Attendance %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%")})
    st.download_button("Download CSV", df.to_csv(index=False).encode(), f"{name}.csv", "text/csv",
                       key=f"dl_{name}", icon=":material/download:", on_click=_logged, args=(name,))


def render():
    ui.require_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        d = logic.today(cfg)
        ui.page_header("Reports", "Attendance rate = (Present + Late) ÷ (Present + Late + Absent). Sick and Excused excluded.", cfg)
        week0 = d - timedelta(days=(d.weekday() + 1) % 7)
        month0 = d.replace(day=1)
        last_month_end = month0 - timedelta(days=1)
        ranges = {"This week": (week0, d), "Last week": (week0 - timedelta(days=7), week0 - timedelta(days=1)),
                  "This month": (month0, d), "Last month": (last_month_end.replace(day=1), last_month_end),
                  "Custom": None}
        c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
        with c1:
            pick = ui.segmented("Period", list(ranges), default="This month", key="rp_period") or "This month"
        if pick == "Custom":
            rng = c2.date_input("Range", (d - timedelta(days=30), d), format="DD/MM/YYYY", key="rp_range")
            if len(rng) != 2:
                st.stop()
            start, end = rng
        else:
            start, end = ranges[pick]
            c2.caption(f"{start:%d %b %Y} → {end:%d %b %Y}")

        df = logic.attendance_df(s, start, end)
        rate = logic.attendance_rate(df)
        c = df["Code"].value_counts() if not df.empty else {}
        m = st.columns(5)
        m[0].metric("Attendance rate", f"{rate:.1f}%" if rate is not None else "—", border=True)
        m[1].metric("Marks recorded", len(df), border=True)
        m[2].metric("Present + late", int(c.get("P", 0) + c.get("L", 0)) if len(df) else 0, border=True)
        m[3].metric("Absent", int(c.get("A", 0)) if len(df) else 0, border=True)
        m[4].metric("Sick / excused", int(c.get("S", 0) + c.get("E", 0)) if len(df) else 0, border=True)

        tab = ui.tabs(["By athlete", "By coach", "By session", "Raw log"], key="rp_tab")
        if tab == "Raw log":
            _table(df.drop(columns=["Code"]) if not df.empty else df, "attendance_log")
        else:
            by = tab.removeprefix("By ").title()
            _table(logic.summarise(df, by), f"by_{by.lower()}")
