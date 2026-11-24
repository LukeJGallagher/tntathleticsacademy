"""Payments (admin): balances, record single or family payments, WhatsApp reminders, history."""
from collections import defaultdict
from datetime import timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

import auth
import db
import logic
import ui
from db import Athlete, PaymentBlock, Reminder, utcnow

METHODS = ["Cash", "Card", "Bank transfer", "Online", "Other"]


def render():
    ui.require_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        d = logic.today(cfg)
        ui.page_header("Payments", "Who's paid, who's due and when. Reminders go out by WhatsApp in one tap.", cfg)
        athletes = logic.list_athletes(s, ui.user(), cfg)
        tab = ui.tabs(["Balances", "Record payment", "Reminders", "History"], key="pay_tab")
        if tab == "Record payment":
            _record(s, athletes, cfg, d)
        elif tab == "Reminders":
            _reminders(s, athletes, cfg, d)
        elif tab == "History":
            _history(s, cfg)
        else:
            _balances(s, athletes, cfg, d)


# --------------------------------------------------------------------------- balances

def _balances(s, athletes, cfg, d):
    df = logic.balances_df(s, athletes, d, cfg)
    if df.empty:
        st.info("No active athletes yet.")
        return
    counts = df["_status"].value_counts()
    cols = st.columns(5)
    for i, k in enumerate(["due", "unpaid", "none", "soon", "ok"]):
        cols[i].metric(logic.FEE_LABEL[k], int(counts.get(k, 0)), border=True)

    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    pick = c1.pills("Show", [logic.FEE_LABEL[k] for k in ["due", "unpaid", "none", "soon", "ok"]],
                    selection_mode="multi", key="bal_filter")
    by_family = c2.toggle("Group by family", key="bal_family")
    if pick:
        df = df[df["Status"].isin(pick)]

    if by_family:
        fam = {a.id: logic.family_key(a) for a in athletes}
        df = df.assign(Family=df["id"].map(fam))
        g = (df.sort_values("_order").groupby("Family", sort=False)
             .agg(Children=("Athlete", lambda x: ", ".join(x)), Guardian=("Guardian", "first"),
                  Phone=("Phone", "first"), Status=("Status", "first"),
                  **{"Next payment": ("Next payment", "min"), "Sessions left": ("Left", "sum")})
             .reset_index())
        st.dataframe(g.drop(columns=["Family"]), hide_index=True, width="stretch")
        return

    show = df.drop(columns=["id", "_order", "_status"])
    st.dataframe(show, hide_index=True, width="stretch", height=min(38 * len(show) + 40, 600),
                 column_config={
                     "Block start": st.column_config.DateColumn(format="DD MMM"),
                     "Block end": st.column_config.DateColumn(format="DD MMM", help="Includes any extension for sick/excused misses"),
                     "Next payment": st.column_config.DateColumn(format="ddd DD MMM"),
                     "Last reminder": st.column_config.DateColumn(format="DD MMM"),
                     "Left": st.column_config.NumberColumn(help="Paid-for sessions (incl. credits) minus sessions used"),
                     "Extended (days)": st.column_config.NumberColumn("Ext. days"),
                 })
    st.download_button("Download CSV", show.to_csv(index=False).encode(), f"balances_{d}.csv", "text/csv",
                       icon=":material/download:")
    rules = [f"Sick and excused never use a paid session"
             + (", and they extend the block end date" if cfg.get("extend_for_sick", "1") == "1" else ""),
             "Absences " + ("use a paid session" if cfg.get("deduct_absences") == "1" else "don't use a paid session")
             + (" and extend the block" if cfg.get("extend_for_absent") == "1" and cfg.get("deduct_absences") != "1" else ""),
             f"Extensions capped at {cfg.get('max_extension_weeks', 4)} weeks; change in Settings"]
    st.caption("Rules: " + " · ".join(rules) + ".")


# --------------------------------------------------------------------------- record

def _block_preview(s, a, pay_date, sessions, per_week, stack, cfg):
    start = pay_date
    if stack:
        live = [w for w in logic.block_windows(s, a, cfg) if w["end"] >= pay_date]
        if live:
            start = max(w["end"] for w in live) + timedelta(days=1)
    return start, logic.block_end(start, sessions, per_week)


def _do_single(aid, pay_date, sessions, per_week, amount, method, stack, paid, notes, end_override):
    if not ui.is_admin():
        return
    with db.session() as s:
        cfg = db.get_settings(s)
        b = logic.record_payment(s, aid, pay_date, sessions, per_week, amount, method, stack, cfg,
                                 end_override=end_override, paid=paid, notes=notes)
        a = s.get(Athlete, aid)
        auth.audit(s, ui.user().get("name"), "admin", "payment.record", a.name,
                   {"sessions": sessions, "amount": amount, "start": str(b.start_date), "end": str(b.end_date)})
        st.session_state["pay_flash"] = (f"Recorded for {a.name}: {sessions} sessions, "
                                         f"{b.start_date:%d %b} → {b.end_date:%d %b}.")


def _do_family(items, pay_date, method, stack, paid, notes):
    if not ui.is_admin():
        return
    with db.session() as s:
        cfg = db.get_settings(s)
        ref, blocks = logic.record_family_payment(s, items, pay_date, method, stack, cfg, paid=paid, notes=notes)
        names = ", ".join(s.get(Athlete, it["athlete_id"]).first_name for it in items)
        auth.audit(s, ui.user().get("name"), "admin", "payment.record_family", ref,
                   {"children": len(items), "total": sum(it["amount"] for it in items)})
        st.session_state["pay_flash"] = f"Family payment {ref} recorded for {names}."


def _record(s, athletes, cfg, d):
    if msg := st.session_state.pop("pay_flash", None):
        st.success(msg, icon=":material/check_circle:")
    mode = st.segmented_control("Payment for", ["One athlete", "A family (one payment)"], default="One athlete",
                                key="rec_mode")
    by_id = {a.id: a for a in athletes}
    c = st.columns(3)
    pay_date = c[0].date_input("Payment date", d, key="rec_date", format="DD/MM/YYYY")
    method = c[1].selectbox("Method", METHODS, key="rec_method")
    paid = c[2].toggle("Paid now", True, key="rec_paid", help="Turn off to set up a block that is still to be paid.")
    stack = st.radio("Block starts", ["After the current block ends", "On the payment date"], horizontal=True,
                     key="rec_stack") == "After the current block ends"
    notes = st.text_input("Notes", key="rec_notes")

    if mode == "One athlete":
        aid = st.selectbox("Athlete", list(by_id), index=None, placeholder="Search athlete", key="rec_aid",
                           format_func=lambda i: f"{by_id[i].name} · {by_id[i].programme or ''}")
        if not aid:
            return
        a = by_id[aid]
        f = logic.athlete_finance(s, a, d, cfg)
        w = f["window"]
        st.caption(f"Now: {ui.badge(logic.FEE_LABEL[f['status']], logic.FEE_COLOUR[f['status']])} "
                   + (f"block {w['start']:%d %b} → {w['end']:%d %b}, {w['left']} left" if w else "no current block")
                   + f" · next payment {logic.fmt_date(f['next_due'])}")
        c = st.columns(4)
        per_default = max(1, len([x for x in a.sessions if x.active]) or int(cfg["default_per_week"]))
        sessions = c[0].number_input("Sessions paid for", 1, 200, int(cfg["default_sessions"]), key="rec_sessions")
        per_week = c[1].number_input("Sessions / week", 1, 7, per_default, key=f"rec_pw_{aid}")
        amount = c[2].number_input(f"Amount ({cfg['currency']})", 0.0, 100000.0, float(cfg["default_amount"]),
                                   step=10.0, key="rec_amount")
        start, end = _block_preview(s, a, pay_date, sessions, per_week, stack, cfg)
        override = c[3].date_input("Valid until", end, key=f"rec_end_{aid}_{start}_{sessions}_{per_week}",
                                   format="DD/MM/YYYY", help="Calculated from sessions ÷ sessions per week. Override if needed.")
        st.info(f"Block **{start:%a %d %b}** → **{override:%a %d %b %Y}** · {sessions} sessions · "
                f"{ui.money(amount, cfg)}", icon=":material/date_range:")
        st.button("Record payment", type="primary", icon=":material/payments:", on_click=_do_single,
                  args=(aid, pay_date, int(sessions), int(per_week), float(amount), method, stack, paid, notes,
                        override if override != end else None))
        return

    families = defaultdict(list)
    for a in athletes:
        if a.family_code:
            families[a.family_code].append(a)
    multi = {k: v for k, v in families.items() if len(v) > 1}
    if not multi:
        st.info("No linked families yet. Link siblings with the same family code on the athlete profile.")
        return
    code = st.selectbox("Family", list(multi), index=None, placeholder="Search family", key="rec_fam",
                        format_func=lambda k: f"{multi[k][0].guardian_name or multi[k][0].last_name} · "
                                              + ", ".join(x.first_name for x in multi[k]))
    if not code:
        return
    items, total = [], 0.0
    for a in multi[code]:
        f = logic.athlete_finance(s, a, d, cfg)
        with st.container(border=True):
            c = st.columns([2.2, 1, 1, 1.2, 2.2], vertical_alignment="bottom")
            inc = c[0].checkbox(f"**{a.name}**", True, key=f"fam_inc_{a.id}",
                                help=f"{logic.FEE_LABEL[f['status']]} · next payment {logic.fmt_date(f['next_due'])}")
            per_default = max(1, len([x for x in a.sessions if x.active]) or int(cfg["default_per_week"]))
            n = c[1].number_input("Sessions", 1, 200, int(cfg["default_sessions"]), key=f"fam_n_{a.id}")
            pw = c[2].number_input("/ week", 1, 7, per_default, key=f"fam_pw_{a.id}")
            amt = c[3].number_input(cfg["currency"], 0.0, 100000.0, float(cfg["default_amount"]), step=10.0,
                                    key=f"fam_amt_{a.id}")
            start, end = _block_preview(s, a, pay_date, n, pw, stack, cfg)
            c[4].caption(f"{start:%d %b} → {end:%d %b %Y}")
            if inc:
                items.append({"athlete_id": a.id, "sessions": int(n), "per_week": int(pw), "amount": float(amt)})
                total += amt
    st.metric("Family total", ui.money(total, cfg))
    st.button("Record family payment", type="primary", icon=":material/family_restroom:", disabled=not items,
              on_click=_do_family, args=(items, pay_date, method, stack, paid, notes))


# --------------------------------------------------------------------------- reminders

def _mark_sent(ids, due, channel, key, by):
    if not ui.is_admin():
        return
    msg = st.session_state.get(key, "")
    with db.session() as s:
        for aid in ids:
            s.add(Reminder(athlete_id=aid, sent_on=utcnow(), due_date=due, channel=channel, message=msg, sent_by=by))
    st.toast("Logged. Hidden from this list for a few days.", icon=":material/check:")


def _reminders(s, athletes, cfg, d):
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    window = c1.slider("Due within (days)", 0, 30, int(cfg.get("due_soon_days", 7)), key="rem_window")
    show_all = c2.toggle("Include recently reminded", key="rem_all")
    quiet = int(cfg.get("reminder_quiet_days", 3))
    last = dict(s.execute(select(Reminder.athlete_id, Reminder.sent_on).where(
        ~Reminder.channel.like("Follow-up%")).order_by(Reminder.sent_on)).all())
    fams = defaultdict(list)
    for a in athletes:
        f = logic.athlete_finance(s, a, d, cfg)
        due = f["status"] in ("due", "unpaid") or (f["next_due"] and f["next_due"] <= d + timedelta(days=window))
        if not due or f["status"] == "none":
            continue
        if not show_all and a.id in last and last[a.id].date() > d - timedelta(days=quiet):
            continue
        fams[logic.family_key(a)].append((a, f))
    if not fams:
        st.success("Nobody to remind right now.", icon=":material/celebration:")
        return
    st.caption(f"{len(fams)} famil{'y' if len(fams) == 1 else 'ies'} to remind. Edit the message if needed, tap "
               f"WhatsApp, then **Mark sent**. Families reminded in the last {quiet} days are hidden.")
    for key_, kids in sorted(fams.items(), key=lambda kv: min(f["next_due"] or d for _, f in kv[1])):
        lead = kids[0][0]
        due = min(f["next_due"] or d for _, f in kids)
        with st.container(border=True):
            h1, h2 = st.columns([3, 1])
            h1.markdown(f"**{lead.guardian_name or lead.name}** · " + "  ".join(
                f"{a.first_name} {ui.badge(logic.FEE_LABEL[f['status']], logic.FEE_COLOUR[f['status']])}" for a, f in kids))
            h2.caption(f"Next payment **{logic.fmt_date(due)}**")
            tkey = f"rem_txt_{key_}"
            if tkey not in st.session_state:
                st.session_state[tkey] = logic.family_reminder_text(kids, cfg)
            msg = st.text_area("Message", key=tkey, height=110, label_visibility="collapsed")
            links = logic.contact_links(lead, msg, cfg)
            b = st.columns(4)
            if "WhatsApp" in links:
                b[0].link_button("WhatsApp", links["WhatsApp"], icon=":material/chat:", type="primary", width="stretch")
            if "SMS" in links:
                b[1].link_button("SMS", links["SMS"], icon=":material/sms:", width="stretch")
            if "Email" in links:
                b[2].link_button("Email", links["Email"], icon=":material/mail:", width="stretch")
            if not links:
                b[0].caption("No phone or email on file")
            b[3].button("Mark sent", key=f"rem_sent_{key_}", icon=":material/check:", width="stretch",
                        on_click=_mark_sent, args=([a.id for a, _ in kids], due, "WhatsApp", tkey, ui.user().get("name")))


# --------------------------------------------------------------------------- history

def _history(s, cfg):
    blocks = s.scalars(select(PaymentBlock).order_by(PaymentBlock.created_at.desc())).all()
    df = pd.DataFrame([{"Recorded": b.created_at.date() if b.created_at else None, "Athlete": b.athlete.name,
                        "Paid on": b.paid_date, "Amount": b.amount, "Method": b.method, "Paid": b.paid,
                        "Sessions": b.sessions_purchased, "Credits": b.credit_sessions or 0,
                        "Start": b.start_date, "End": b.end_date, "Family payment": b.family_ref or "",
                        "Notes": b.notes or ""} for b in blocks])
    st.subheader("Payments")
    if df.empty:
        st.caption("No payments yet.")
    else:
        paid = df[df["Paid"]]
        m = st.columns(3)
        m[0].metric("Total recorded (paid)", ui.money(paid["Amount"].sum(), cfg))
        m[1].metric("Outstanding (unpaid blocks)", ui.money(df[~df["Paid"]]["Amount"].sum(), cfg))
        m[2].metric("Family payments", df["Family payment"].replace("", pd.NA).nunique())
        st.dataframe(df, hide_index=True, width="stretch", height=360,
                     column_config={"Amount": st.column_config.NumberColumn(format=f"{cfg['currency']} %.0f")})
        st.download_button("Download payments CSV", df.to_csv(index=False).encode(), "payments.csv", "text/csv",
                           icon=":material/download:")
    st.subheader("Reminders sent")
    rem = s.scalars(select(Reminder).order_by(Reminder.sent_on.desc()).limit(300)).all()
    if rem:
        st.dataframe(pd.DataFrame([{"Sent": r.sent_on, "Athlete": r.athlete.name, "Channel": r.channel,
                                    "Due": r.due_date, "By": r.sent_by, "Message": r.message} for r in rem]),
                     hide_index=True, width="stretch", height=300)
    else:
        st.caption("No reminders yet.")
