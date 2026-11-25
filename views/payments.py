"""Payments (admin): balances, record single or family payments, WhatsApp reminders, history."""
import json
from collections import defaultdict
from datetime import timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

import auth
import db
import gateway
import logic
import ui
from db import Athlete, PaymentBlock, PaymentRequest, Reminder, utcnow

METHODS = ["Cash", "Card", "Bank transfer", "Online", "Other"]


def render():
    ui.require_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        d = logic.today(cfg)
        ui.page_header("Payments", "Who's paid, who's due and when. Reminders go out by WhatsApp in one tap.", cfg)
        athletes = logic.list_athletes(s, ui.user(), cfg)
        tab = ui.tabs(["Balances", "Upcoming", "Requests", "Record payment", "Reminders", "Import", "History"],
                      key="pay_tab")
        if tab == "Upcoming":
            _upcoming(s, athletes, cfg, d)
        elif tab == "Requests":
            _requests(s, cfg, d)
        elif tab == "Import":
            _import(s, cfg, d)
        elif tab == "Record payment":
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
    mode = ui.segmented("Payment for", ["One athlete", "A family (one payment)"], default="One athlete",
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
                text = logic.family_reminder_text(kids, cfg)
                req = logic.open_request(s, lead.family_code, None if lead.family_code else lead.id)
                if req and req.pay_url:
                    text += f"\nPay online ({ui.money(req.amount, cfg)}): {req.pay_url}"
                elif req:
                    text += f"\nPayment reference: {req.reference} ({ui.money(req.amount, cfg)})"
                st.session_state[tkey] = text
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


# --------------------------------------------------------------------------- upcoming / requests / import

def _make_request(aids, due, key):
    if not ui.is_admin():
        return
    with db.session() as s:
        cfg = db.get_settings(s)
        kids = []
        for aid in aids:
            a = s.get(Athlete, aid)
            item = logic.expected_item(a, cfg)
            item["amount"] = float(st.session_state.get(f"{key}_amt_{aid}", item["amount"]))
            kids.append((a, item))
        req = logic.create_request(s, kids, due, cfg, ui.user().get("name"))
        auth.audit(s, ui.user().get("name"), "admin", "payreq.create", req.reference, {"amount": req.amount})
        if gateway.is_configured():
            try:
                lead = kids[0][0]
                first, _, last = (lead.guardian_name or lead.name).partition(" ")
                req.provider_id, req.pay_url = gateway.create_pay_link(
                    req.reference, req.amount, first, last, logic.intl_phone(lead.phone, cfg["country_code"]),
                    lead.email or "", f"{cfg['club_name']} sessions")
                req.provider = "skipcash"
            except Exception as e:  # request still exists; admin can retry the link or take payment manually
                st.session_state["pay_flash_err"] = f"Request {req.reference} created, but the pay link failed: {e}"
        ref = req.reference
    st.toast(f"Request {ref} created", icon=":material/request_quote:")


def _upcoming(s, athletes, cfg, d):
    c1, c2 = st.columns([2, 3], vertical_alignment="bottom")
    days = c1.segmented_control("Window", [30, 60, 90], default=30, key="up_days",
                                format_func=lambda x: f"Next {x} days") if not ui.TEST_MODE else 30
    days = days or 30
    rows = logic.upcoming(s, athletes, d, cfg, days)
    total = sum(r["amount"] for r in rows)
    overdue = sum(r["amount"] for r in rows if r["overdue"])
    m = st.columns(3)
    m[0].metric("Expected income", ui.money(total, cfg), border=True, help="Based on each athlete's last block")
    m[1].metric("Overdue now", ui.money(overdue, cfg), border=True)
    m[2].metric("Families", len(rows), border=True)
    if not rows:
        st.success("Nothing due in this window.", icon=":material/celebration:")
        return
    # weekly forecast table
    wk = pd.DataFrame([{"Week of": (r["due"] - timedelta(days=(r["due"].weekday() + 1) % 7)) if not r["overdue"] else d,
                        "Amount": r["amount"]} for r in rows]).groupby("Week of", as_index=False).sum()
    with st.expander("Weekly forecast", icon=":material/calendar_view_week:"):
        st.dataframe(wk, hide_index=True, width="stretch",
                     column_config={"Week of": st.column_config.DateColumn(format="ddd DD MMM"),
                                    "Amount": st.column_config.NumberColumn(format=f"{cfg['currency']} %.0f")})
        st.download_button("Download CSV", pd.DataFrame([{
            "Family": r["lead"].guardian_name or r["lead"].name, "Children": ", ".join(a.first_name for a, *_ in r["kids"]),
            "Due": r["due"], "Amount": r["amount"], "Request": r["request"].reference if r["request"] else ""}
            for r in rows]).to_csv(index=False).encode(), f"upcoming_{d}.csv", "text/csv", icon=":material/download:")
    for r in rows:
        lead = r["lead"]
        key = f"up_{r['key']}"
        with st.container(border=True):
            h1, h2 = st.columns([3, 2], vertical_alignment="center")
            h1.markdown(f"**{lead.guardian_name or lead.name}** · "
                        + ", ".join(a.first_name for a, *_ in r["kids"])
                        + ("  " + ui.badge("Overdue", "red") if r["overdue"] else ""))
            h1.caption(f"Due **{logic.fmt_date(r['due'])}** · expected **{ui.money(r['amount'], cfg)}**")
            req = r["request"]
            if req:
                h2.markdown(f"{ui.badge('Request ' + req.reference, 'blue')} "
                            + (ui.badge("Pay link ready", "green") if req.pay_url else ui.badge("Manual", "gray")))
            else:
                with h2.popover("Create payment request", icon=":material/request_quote:", width="stretch"):
                    for a, f, item in r["kids"]:
                        st.number_input(f"{a.first_name}: {item['sessions']} sessions", 0.0, 100000.0,
                                        float(item["amount"]), step=10.0, key=f"{key}_amt_{a.id}")
                    st.caption("SkipCash pay link will be created automatically." if gateway.is_configured()
                               else "Online payments are off: the request is tracked and marked paid by hand.")
                    st.button("Create request", key=f"{key}_go", type="primary", on_click=_make_request,
                              args=([a.id for a, *_ in r["kids"]], r["due"], key))


def _settle(rid, method):
    if not ui.is_admin():
        return
    with db.session() as s:
        cfg = db.get_settings(s)
        req = s.get(PaymentRequest, rid)
        blocks = logic.settle_request(s, req, logic.today(cfg), method, cfg)
        auth.audit(s, ui.user().get("name"), "admin", "payreq.paid", req.reference, {"method": method, "blocks": len(blocks)})
        ref = req.reference
    st.toast(f"{ref} paid: {len(blocks)} block(s) created.", icon=":material/check_circle:")


def _cancel_req(rid):
    if not ui.is_admin():
        return
    with db.session() as s:
        req = s.get(PaymentRequest, rid)
        req.status = "cancelled"
        auth.audit(s, ui.user().get("name"), "admin", "payreq.cancel", req.reference)


def _check_online(rid):
    if not ui.is_admin():
        return
    with db.session() as s:
        cfg = db.get_settings(s)
        req = s.get(PaymentRequest, rid)
        try:
            status = gateway.payment_status(req.provider_id)
        except Exception as e:
            st.toast(f"Couldn't reach SkipCash: {e}", icon=":material/error:")
            return
        if status == "paid":
            logic.settle_request(s, req, logic.today(cfg), "SkipCash", cfg)
            auth.audit(s, "SkipCash", "system", "payreq.paid", req.reference)
        st.toast(f"{req.reference}: {status}")


def _check_all_online():
    if not ui.is_admin():
        return
    with db.session() as s:
        ids = [r.id for r in s.scalars(select(PaymentRequest).where(
            PaymentRequest.provider == "skipcash", PaymentRequest.status.in_(["open", "link_sent"])))]
    for rid in ids:
        _check_online(rid)


def _requests(s, cfg, d):
    if msg := st.session_state.pop("pay_flash_err", None):
        st.error(msg)
    on = gateway.is_configured()
    st.caption((f":green-badge[SkipCash {gateway.mode()}] Pay links are created automatically." if on else
                ":gray-badge[Online payments off] Add SkipCash keys to switch on pay links. Until then, mark requests "
                "paid when cash or a transfer arrives."))
    show = ui.segmented("Show", ["Open", "Paid", "Cancelled"], default="Open", key="rq_show",
                        label_visibility="collapsed") or "Open"
    statuses = {"Open": ["open", "link_sent"], "Paid": ["paid"], "Cancelled": ["cancelled"]}[show]
    reqs = s.scalars(select(PaymentRequest).where(PaymentRequest.status.in_(statuses))
                     .order_by(PaymentRequest.due_date, PaymentRequest.created_at)).all()
    if on and show == "Open" and any(r.provider == "skipcash" for r in reqs):
        st.button("Check SkipCash for payments", on_click=_check_all_online, icon=":material/sync:")
    if not reqs:
        st.caption("No requests here. Create them from **Upcoming**.")
    for r in reqs:
        items = json.loads(r.items)
        names = ", ".join(s.get(Athlete, i["athlete_id"]).first_name for i in items if s.get(Athlete, i["athlete_id"]))
        with st.container(border=True):
            c1, c2 = st.columns([3, 2], vertical_alignment="center")
            c1.markdown(f"**{r.reference}** · {r.payer_name or ''} · {names}  "
                        + ui.badge(ui.money(r.amount, cfg), "blue"))
            c1.caption(f"Due {logic.fmt_date(r.due_date)} · created {r.created_at:%d %b} by {r.created_by or '—'}"
                       + (f" · paid {r.paid_at:%d %b} ({r.paid_method})" if r.paid_at else ""))
            if r.status in ("open", "link_sent"):
                if r.pay_url:
                    c2.link_button("Open pay link", r.pay_url, icon=":material/link:", width="stretch")
                    c2.button("Check payment", key=f"rqc_{r.id}", on_click=_check_online, args=(r.id,), width="stretch")
                m1, m2, m3 = c2.columns(3)
                m1.button("Cash", key=f"rqp_cash_{r.id}", on_click=_settle, args=(r.id, "Cash"), width="stretch",
                          help="Mark paid in cash and create the blocks")
                m2.button("Transfer", key=f"rqp_bank_{r.id}", on_click=_settle, args=(r.id, "Bank transfer"),
                          width="stretch")
                m3.button("Cancel", key=f"rqx_{r.id}", on_click=_cancel_req, args=(r.id,), width="stretch")


IMPORT_FIELDS = {"reference": "Reference / transaction ID", "amount": "Amount", "status": "Status (optional)",
                 "date": "Date (optional)"}


def _apply_import(matches):
    if not ui.is_admin():
        return
    n = 0
    with db.session() as s:
        cfg = db.get_settings(s)
        for rid, pay_date in matches:
            req = s.get(PaymentRequest, rid)
            if req and req.status != "paid":
                logic.settle_request(s, req, pay_date or logic.today(cfg), "SkipCash (import)", cfg)
                n += 1
        auth.audit(s, ui.user().get("name"), "admin", "payreq.import", f"{n} paid")
    st.session_state["import_done"] = n


def _import(s, cfg, d):
    st.markdown("Upload a payments export (SkipCash portal, bank statement…) as **CSV or Excel**. Rows whose "
                "reference matches an open request (e.g. `TNT-7KQ2MX`) are marked paid and their blocks created.")
    if (n := st.session_state.pop("import_done", None)) is not None:
        st.success(f"{n} request(s) marked paid.", icon=":material/check_circle:")
    up = st.file_uploader("Payments file", type=["csv", "xlsx"], key="imp_file")
    if not up:
        return
    try:
        df = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
    except Exception as e:
        st.error(f"Couldn't read that file: {e}")
        return
    st.caption(f"{len(df)} rows · columns: {', '.join(map(str, df.columns))}")
    cols = ["—"] + list(map(str, df.columns))
    guess = lambda *words: next((i for i, c in enumerate(cols) if any(w in c.lower() for w in words)), 0)  # noqa: E731
    c = st.columns(4)
    pick = {k: c[i].selectbox(label, cols, index=guess(*{"reference": ["ref", "transaction", "custom", "order"],
                                                          "amount": ["amount", "total"], "status": ["status"],
                                                          "date": ["date", "time"]}[k]), key=f"imp_{k}")
            for i, (k, label) in enumerate(IMPORT_FIELDS.items())}
    if pick["reference"] == "—":
        st.info("Pick the column that holds the reference.")
        return
    open_reqs = {r.reference.upper(): r for r in s.scalars(select(PaymentRequest).where(
        PaymentRequest.status.in_(["open", "link_sent"])))}
    matches, rows = [], []
    for _, row in df.iterrows():
        text = " ".join(str(row[x]) for x in [pick["reference"]] if x != "—").upper()
        ref = next((k for k in open_reqs if k in text), None)
        ok_status = pick["status"] == "—" or str(row[pick["status"]]).strip().lower() in (
            "paid", "success", "successful", "completed", "captured", "2", "approved")
        amt = pd.to_numeric(row[pick["amount"]], errors="coerce") if pick["amount"] != "—" else None
        req = open_reqs.get(ref) if ref else None
        amount_ok = req is None or amt is None or pd.isna(amt) or abs(float(amt) - req.amount) < 0.01
        pay_date = pd.to_datetime(row[pick["date"]], errors="coerce", dayfirst=True).date() \
            if pick["date"] != "—" and not pd.isna(pd.to_datetime(row[pick["date"]], errors="coerce", dayfirst=True)) else None
        verdict = ("Will mark paid" if req and ok_status and amount_ok else
                   "Amount differs: check" if req and not amount_ok else
                   "Not paid status" if req else "No matching request")
        rows.append({"Reference": text[:40], "Matched": req.reference if req else "", "Amount": amt,
                     "Expected": req.amount if req else None, "Result": verdict})
        if verdict == "Will mark paid":
            matches.append((req.id, pay_date))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.button(f"Mark {len(matches)} request(s) paid", type="primary", disabled=not matches,
              on_click=_apply_import, args=(matches,), icon=":material/done_all:")


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
