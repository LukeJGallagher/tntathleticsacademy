"""Reports (admin): attendance between any two dates. Sessions attended per athlete, plus breakdowns
by coach and session and the raw log. CSV (and Excel for the per-athlete report) on every table."""
import io
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

import auth
import db
import logic
import pdfs
import ui
from db import Attendance

PRESETS = ["This week", "Last week", "This month", "Last month", "This year", "All time"]


def _logged(name):
    with db.session() as s:
        auth.audit(s, ui.user().get("name"), "admin", "export.run", name)


def _apply_preset():
    """Preset buttons just fill the From/To boxes; the dates stay editable."""
    pick = st.session_state.get("rp_preset")
    if not pick:
        return
    with db.session() as s:
        d = logic.today(db.get_settings(s))
        first_mark = s.scalar(select(func.min(Attendance.date))) or d
    week0 = d - timedelta(days=(d.weekday() + 1) % 7)  # weeks start Sunday
    month0 = d.replace(day=1)
    last_month_end = month0 - timedelta(days=1)
    st.session_state["rp_from"], st.session_state["rp_to"] = {
        "This week": (week0, d), "Last week": (week0 - timedelta(days=7), week0 - timedelta(days=1)),
        "This month": (month0, d), "Last month": (last_month_end.replace(day=1), last_month_end),
        "This year": (date(d.year, 1, 1), d), "All time": (first_mark, d)}[pick]


def _excel(sheets: dict[str, pd.DataFrame], title: str) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for sheet, frame in sheets.items():
            frame.to_excel(xw, index=False, sheet_name=sheet[:31], startrow=2)
            ws = xw.sheets[sheet[:31]]
            ws["A1"] = f"{title}: {sheet}"
            ws.freeze_panes = "B4"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(
                    8, min(45, max(len(str(c.value or "")) for c in col[2:]) + 2))
    return buf.getvalue()


def _table(df, name, excel: dict | None = None, title="", hide=()):
    if df.empty:
        st.caption("No data for this period.")
        return
    st.dataframe(df.drop(columns=[c for c in hide if c in df.columns]), hide_index=True, width="stretch",
                 height=min(38 * len(df) + 40, 620),
                 column_config={"Attendance %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
                                "Last attended": st.column_config.DateColumn(format="DD MMM YYYY"),
                                "Dates attended": st.column_config.TextColumn(width="large")})
    c1, c2, _ = st.columns([1, 1, 3])
    c1.download_button("Download CSV", df.to_csv(index=False).encode(), f"{name}.csv", "text/csv",
                       key=f"dl_{name}", icon=":material/download:", on_click=_logged, args=(name,), width="stretch")
    if excel:
        c2.download_button("Download Excel", _excel(excel, title), f"{name}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"dlx_{name}", icon=":material/table:", on_click=_logged, args=(name + ".xlsx",),
                           width="stretch", help="Sheets: summary, register grid (date by date) and every attended date")


def render():
    ui.require_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        d = logic.today(cfg)
        ui.page_header("Reports", "Attendance between any two dates. Attended = Present + Late. Attendance % = "
                       "attended ÷ (attended + absent); sick and excused are excluded.", cfg)

        st.session_state.setdefault("rp_from", d.replace(day=1))
        st.session_state.setdefault("rp_to", d)
        c1, c2, c3 = st.columns([1, 1, 3], vertical_alignment="bottom")
        start = c1.date_input("From", key="rp_from", format="DD/MM/YYYY")
        end = c2.date_input("To", key="rp_to", format="DD/MM/YYYY")
        with c3:
            ui.segmented("Quick pick", PRESETS, key="rp_preset", on_change=_apply_preset,
                         label_visibility="collapsed")
        if start > end:
            st.error("'From' is after 'To'.")
            st.stop()
        st.caption(f"Showing **{start:%a %d %b %Y} → {end:%a %d %b %Y}** ({(end - start).days + 1} days)")

        df = logic.attendance_df(s, start, end)
        rate = logic.attendance_rate(df)
        c = df["Code"].value_counts() if not df.empty else {}
        m = st.columns(5)
        m[0].metric("Sessions attended", int(c.get("P", 0) + c.get("L", 0)) if len(df) else 0, border=True,
                    help="Present + Late marks, all athletes")
        m[1].metric("Attendance rate", f"{rate:.1f}%" if rate is not None else "—", border=True)
        m[2].metric("Absent", int(c.get("A", 0)) if len(df) else 0, border=True)
        m[3].metric("Sick / excused", int(c.get("S", 0) + c.get("E", 0)) if len(df) else 0, border=True)
        m[4].metric("Marks recorded", len(df), border=True)

        tab = ui.tabs(["Sessions attended", "Register", "By coach", "By session", "Raw log"], key="rp_tab")
        title = f"{cfg['club_name']} {start:%d %b %Y} to {end:%d %b %Y}"
        if tab in ("Sessions attended", "Register"):
            f1, f2 = st.columns([3, 1], vertical_alignment="bottom")
            inactive = f2.toggle("Include inactive", key="rp_inactive")
            athletes = logic.list_athletes(s, ui.user(), cfg, active_only=not inactive)
            progs = sorted({a.programme for a in athletes if a.programme})
            pick = f1.multiselect("Category", progs, placeholder="All categories", key="rp_prog")
            if pick:
                athletes = [a for a in athletes if a.programme in pick]
            names = {a.name for a in athletes}
            log = df[df["Athlete"].isin(names)] if not df.empty else df
            rep = logic.sessions_attended(s, athletes, start, end)
            grid = logic.register(log)
            dates = (log[log["Code"].isin(["P", "L"])][["Athlete", "Date", "Session", "Status", "Recorded by"]]
                     .sort_values(["Athlete", "Date"]) if not log.empty else log)
            sheets = {"Sessions attended": rep, "Register": grid, "Dates attended": dates}
            fname = f"attendance_{start:%Y%m%d}_{end:%Y%m%d}"
            if tab == "Register":
                st.caption("P Present · L Late · A Absent · S Sick · E Excused · blank = not on the list that day")
                _table(grid, fname + "_register", excel=sheets, title=title)
            else:
                if not rep.empty:
                    zero = int((rep["Attended"] == 0).sum())
                    st.caption(f"{len(rep)} athletes · {int(rep['Attended'].sum())} sessions attended · "
                               f"average {rep['Attended'].mean():.1f} per athlete"
                               + (f" · **{zero} with none**" if zero else ""))
                _table(rep, fname, excel=sheets, title=title)
                with st.container(border=True):
                    st.markdown("**Dates for one athlete**")
                    who = st.selectbox("Athlete", sorted(names), index=None, placeholder="Pick an athlete",
                                       key="rp_who", label_visibility="collapsed")
                    if who:
                        one = log[log["Athlete"] == who] if not log.empty else log
                        if one.empty:
                            st.caption(f"No marks for {who} between these dates.")
                        else:
                            n_att = int(one["Code"].isin(["P", "L"]).sum())
                            st.caption(f"{who}: attended **{n_att}** of {len(one)} marked sessions")
                            st.dataframe(one[["Date", "Session", "Status", "Recorded by"]].sort_values("Date"),
                                         hide_index=True, width="stretch",
                                         column_config={"Date": st.column_config.DateColumn(format="ddd DD MMM YYYY")})
                        cat = next((a.programme for a in athletes if a.name == who), "") or ""
                        pdf = pdfs.athlete_statement(cfg["club_name"], who, cat, start, end,
                                                     one[["Date", "Session", "Status", "Recorded by"]].to_dict("records")
                                                     if not one.empty else [])
                        st.download_button("Download PDF statement", pdf,
                                           f"{who.replace(' ', '_')}_attendance_{start:%Y%m%d}_{end:%Y%m%d}.pdf",
                                           "application/pdf", icon=":material/picture_as_pdf:", type="primary",
                                           on_click=_logged, args=(f"pdf {who}",))
        elif tab == "Raw log":
            _table(df.drop(columns=["Code"]) if not df.empty else df, "attendance_log")
        else:
            by = tab.removeprefix("By ").title()
            _table(logic.summarise(df, by), f"by_{by.lower()}")
