"""Overview: what needs doing today. Admin sees the whole academy; coaches see their own athletes."""
from datetime import timedelta

import streamlit as st

import db
import logic
import ui
from db import Reminder, utcnow


def _contacted(aid, msg, by):
    if not ui.is_admin():
        return
    with db.session() as s:
        s.add(Reminder(athlete_id=aid, channel="Follow-up (WhatsApp)", message=msg, sent_by=by, sent_on=utcnow()))
    st.toast("Logged. They'll drop off this list.", icon=":material/check:")


def render():
    ui.require_admin()
    user = ui.user()
    admin = ui.is_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        d = logic.today(cfg)
        ui.page_header("Overview" if admin else "My overview", d.strftime("%A %d %B %Y"), cfg)

        athletes = logic.list_athletes(s, user, cfg)
        sessions = logic.list_sessions(s, user, cfg, on=d)
        out = logic.out_on(s, d)
        out_mine = {k: v for k, v in out.items() if k in {a.id for a in athletes}}
        vis = logic.visible_session_ids(s, user, cfg)
        week0 = d - timedelta(days=(d.weekday() + 1) % 7)  # week starts Sunday
        this_wk = logic.attendance_df(s, week0, d, vis)
        last_wk = logic.attendance_df(s, week0 - timedelta(days=7), week0 - timedelta(days=1), vis)
        r_now, r_prev = logic.attendance_rate(this_wk), logic.attendance_rate(last_wk)

        marked = total = 0
        today_rows = []
        for x in sessions:
            r = logic.day_roster(s, x, d)
            m = logic.marks_for(s, d, x.id)
            n = sum(1 for a, _ in r["attending"] if a.id in m)
            marked += n
            total += len(r["attending"])
            today_rows.append((x, n, len(r["attending"]), len(r["waitlist"])))

        fins = {a.id: logic.athlete_finance(s, a, d, cfg) for a in athletes} if admin else {}
        due_now = [a for a in athletes if admin and fins[a.id]["status"] in ("due", "unpaid", "none")]
        soon_days = int(cfg.get("due_soon_days", 7))
        due_soon = [a for a in athletes if admin and fins[a.id]["status"] == "soon"]

        cols = st.columns(5 if admin else 4)
        cols[0].metric("Active athletes", len(athletes), border=True)
        cols[1].metric("Marked today", f"{marked}/{total}" if total else "No sessions", border=True)
        cols[2].metric("Sick or injured", len(out_mine), border=True)
        cols[3].metric("Attendance this week", f"{r_now:.0f}%" if r_now is not None else "—",
                       delta=f"{r_now - r_prev:+.0f} pts vs last week" if r_now is not None and r_prev is not None else None,
                       border=True)
        if admin:
            cols[4].metric("Fees to chase now", len(due_now), border=True,
                           help=f"Due now, unpaid or no block. Another {len(due_soon)} due within {soon_days} days.")

        left, right = st.columns(2, gap="large")
        with left:
            with st.container(border=True):
                st.subheader("Today's sessions")
                if not today_rows:
                    st.caption("No sessions today.")
                for x, n, tot, wl in today_rows:
                    a, b = st.columns([3, 1], vertical_alignment="center")
                    status = ui.badge("Done", "green") if tot and n == tot else ui.badge(f"{n}/{tot} marked", "orange" if n else "gray")
                    a.markdown(f"**{x.start_time} {x.label}**  {status}"
                               + (f"  {ui.badge(f'{wl} waiting', 'blue')}" if wl else ""))
                    a.caption(x.coach.name if x.coach else "No coach set")

            with st.container(border=True):
                st.subheader("Sick & injured")
                if not out_mine:
                    st.caption("Nobody on the list.")
                for aid, ev in out_mine.items():
                    a = ev.athlete
                    overdue = ev.expected_return and ev.expected_return < d
                    tag = ui.badge("Return overdue", "red") if overdue else ui.badge(
                        f"Back {logic.fmt_date(ev.expected_return)}" if ev.expected_return else "Return date not set", "gray")
                    st.markdown(f"**{a.name}** · {ev.kind}: {ev.description or ''}  {tag}")

        with right:
            with st.container(border=True):
                fu_days = cfg.get("followup_days", "14")
                st.subheader("Check in with")
                st.caption(f"No attendance for {fu_days}+ days and not on the sick list")
                fus = logic.followups(s, athletes, d, cfg)
                if not fus:
                    st.caption("Everyone's been in recently.")
                for row in fus[:10]:
                    a = row["athlete"]
                    msg = logic.followup_text(a, row["last_seen"], cfg)
                    links = logic.contact_links(a, msg, cfg)
                    st.markdown(f"**{a.name}**  {ui.badge(str(row['days']) + ' days', 'orange')}")
                    st.caption(f"{a.guardian_name or 'Guardian'} · last in {logic.fmt_date(row['last_seen'])}")
                    c2, c3 = st.columns(2)
                    if "WhatsApp" in links:
                        c2.link_button("WhatsApp: everything OK?", links["WhatsApp"], icon=":material/chat:",
                                       width="stretch")
                    c3.button("Mark contacted", key=f"fu_{a.id}", on_click=_contacted, width="stretch",
                              args=(a.id, msg, user.get("name")), icon=":material/check:")

            if admin:
                with st.container(border=True):
                    st.subheader("Payments to chase")
                    rows = sorted(due_now + due_soon, key=lambda a: (logic.FEE_ORDER[fins[a.id]["status"]],
                                                                      fins[a.id]["next_due"] or d))
                    if not rows:
                        st.caption("Everyone is paid up.")
                    for a in rows[:8]:
                        f = fins[a.id]
                        st.markdown(f"**{a.name}**  {ui.badge(logic.FEE_LABEL[f['status']], logic.FEE_COLOUR[f['status']])}"
                                    f"  · next payment {logic.fmt_date(f['next_due'])}")
                    if len(rows) > 8:
                        st.caption(f"+ {len(rows) - 8} more")
                    st.caption("Send reminders from **Payments → Reminders**.")
