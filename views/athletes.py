"""Athletes: list + full profile. Admin edits; coaches see their own athletes read-only (plus health logging)."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

import auth
import db
import logic
import ui
from db import Athlete, Attendance, HealthEvent, PaymentBlock, TrainingSession

PROGRAMMES = ["ToTs", "Youth", "Teen & Adult", "Sports Speed", "Schools", "Other"]
GENDERS = ["Female", "Male", "Other"]


def _open_from_table():
    ev = st.session_state.get("ath_table")
    ids = st.session_state.get("_ath_ids", [])
    if ev and ev.selection.rows and ids:
        st.session_state["ath_open"] = ids[ev.selection.rows[0]]


@st.dialog("New athlete", width="large")
def _new_athlete_dialog():
    with db.session() as s:
        sessions = logic.list_sessions(s, {"role": "admin"}, db.get_settings(s))
        families = sorted({(a.family_code, a.guardian_name or "") for a in s.scalars(select(Athlete))
                           if a.family_code}, key=lambda x: x[1])
    with st.form("new_ath"):
        c1, c2 = st.columns(2)
        first = c1.text_input("First name *")
        last = c2.text_input("Last name *")
        dob = c1.date_input("Date of birth", value=None, min_value=date(1950, 1, 1), max_value=date.today(),
                            format="DD/MM/YYYY")
        gender = c2.selectbox("Gender", GENDERS, index=None)
        programme = c1.selectbox("Programme", PROGRAMMES, index=None)
        events = c2.text_input("Event group", placeholder="e.g. Sprints, Long jump")
        groups = st.multiselect("Weekly sessions", [x.id for x in sessions],
                                format_func={x.id: f"{logic.WEEKDAYS[x.weekday][:3]} {x.start_time} {x.label}" for x in sessions}.get)
        st.markdown("**Family**")
        fam_opts = ["new"] + [f[0] for f in families]
        fam = st.selectbox("Family link", fam_opts, format_func=lambda c: "New family (new code)" if c == "new" else
                           f"Sibling of {dict(families).get(c, '')} · {c}",
                           help="Siblings share one family code. Parents book for all children with one code, "
                                "and one payment can cover them all.")
        g1, g2 = st.columns(2)
        guardian = g1.text_input("Parent / guardian")
        phone = g2.text_input("Phone (WhatsApp)")
        email = g1.text_input("Email")
        school = g2.text_input("School")
        emergency = g1.text_input("Emergency contact")
        medical = st.text_area("Medical notes (visible to coaches)")
        if st.form_submit_button("Add athlete", type="primary"):
            if not first.strip():
                st.error("First name is required.")
                return
            with db.session() as s:
                cfg = db.get_settings(s)
                code = logic.new_family_code(s) if fam == "new" else fam
                if fam != "new":
                    sib = s.scalar(select(Athlete).where(Athlete.family_code == fam))
                    guardian = guardian or sib.guardian_name
                    phone = phone or sib.phone
                    email = email or sib.email
                a = Athlete(first_name=first.strip(), last_name=last.strip(), dob=dob, gender=gender,
                            programme=programme, event_group=events or None, guardian_name=guardian or None,
                            phone=phone or None, email=email or None, school=school or None,
                            emergency_contact=emergency or None, medical_notes=medical or None,
                            start_date=logic.today(cfg), family_code=code, active=True)
                a.sessions = [s.get(TrainingSession, i) for i in groups]
                s.add(a)
                s.flush()
                auth.audit(s, ui.user().get("name"), "admin", "athlete.create", a.name)
                st.session_state["ath_open"] = a.id
            st.rerun()


def render():
    user = ui.user()
    admin = ui.is_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        d = logic.today(cfg)
        ui.page_header("Athletes" if admin else "My athletes",
                       "Profiles, groups, attendance, payments and health" if admin else
                       "Athletes in your sessions. Contact details and medical notes for training.", cfg)

        f1, f2, f3, f4 = st.columns([3, 2, 1.2, 1.3], vertical_alignment="bottom")
        q = f1.text_input("Search", placeholder="Name, parent, school…", key="ath_q")
        prog = f2.multiselect("Programme", PROGRAMMES, key="ath_prog", placeholder="All programmes")
        show_inactive = f3.toggle("Inactive", key="ath_inactive") if admin else False
        if admin and f4.button("New athlete", icon=":material/person_add:", type="primary", width="stretch"):
            _new_athlete_dialog()

        athletes = logic.list_athletes(s, user, cfg, active_only=not show_inactive)
        if q:
            ql = q.lower()
            athletes = [a for a in athletes if ql in " ".join(filter(None, [a.name, a.guardian_name, a.school, a.phone])).lower()]
        if prog:
            athletes = [a for a in athletes if a.programme in prog]

        out = logic.out_on(s, d)
        rows = []
        for a in athletes:
            f = logic.athlete_finance(s, a, d, cfg)
            rows.append({"Name": a.name, "Age": a.age(d), "Programme": a.programme or "",
                         "Events": a.event_group or "", "Parent": a.guardian_name or "", "Phone": a.phone or "",
                         "Fees": logic.FEE_LABEL[f["status"]],
                         "Health": f"{out[a.id].kind}" if a.id in out else "",
                         "Active": a.active})
        df = pd.DataFrame(rows)
        st.session_state["_ath_ids"] = [a.id for a in athletes]
        if df.empty:
            st.info("No athletes match." if (q or prog) else "No athletes yet.")
            return
        if not admin:
            df = df.drop(columns=["Fees", "Active"])
        elif not show_inactive:
            df = df.drop(columns=["Active"])
        st.caption(f"{len(df)} athlete(s). Select a row to open the profile.")
        st.dataframe(df, hide_index=True, width="stretch", height=min(38 * len(df) + 40, 420),
                     on_select=_open_from_table, selection_mode="single-row", key="ath_table",
                     column_config={"Age": st.column_config.NumberColumn(width="small")})

        aid = st.session_state.get("ath_open")
        a = s.get(Athlete, aid) if aid else None
        if a is None or a.id not in {x.id for x in logic.list_athletes(s, user, cfg, active_only=False)}:
            return
        st.divider()
        _profile(s, a, cfg, d, admin, user)


def _profile(s, a: Athlete, cfg, d, admin, user):
    f = logic.athlete_finance(s, a, d, cfg)
    out = logic.out_on(s, d).get(a.id)

    with st.container(border=True):
        c0, c1, c2 = st.columns([0.6, 5, 2.4], vertical_alignment="center")
        c0.markdown(ui.avatar(a.name), unsafe_allow_html=True)
        c1.subheader(a.name)
        tags = [ui.badge(a.programme, "blue") if a.programme else "",
                ui.badge(f"Age {a.age(d)}", "gray") if a.dob else "",
                ui.badge(a.event_group, "violet") if a.event_group else "",
                ui.badge("Fees " + logic.FEE_LABEL[f["status"]].lower(), logic.FEE_COLOUR[f["status"]]) if admin else "",
                ui.badge(f"{out.kind}: {out.description or ''}", "red") if out else "",
                ui.badge("Inactive", "gray") if not a.active else ""]
        c1.markdown(" ".join(t for t in tags if t))
        groups = ", ".join(f"{logic.WEEKDAYS[x.weekday][:3]} {x.start_time} {x.label}" for x in a.sessions if x.active)
        c1.caption(groups or "No weekly sessions")
        num = logic.intl_phone(a.phone, cfg.get("country_code", "974"))
        if num:
            c2.link_button("WhatsApp parent", f"https://wa.me/{num}", icon=":material/chat:", width="stretch")
            c2.link_button("Call", f"tel:+{num}", icon=":material/call:", width="stretch")

    if a.medical_notes:
        st.warning(f"**Medical:** {a.medical_notes}", icon=":material/medical_information:")

    tabs = ["Profile", "Attendance", "Health"] + (["Payments", "Family"] if admin else [])
    tab = ui.tabs(tabs, key="ath_tab")
    if tab == "Profile":
        _tab_profile(s, a, admin, cfg)
    elif tab == "Attendance":
        _tab_attendance(s, a, d)
    elif tab == "Health":
        _tab_health(s, a, d, user)
    elif tab == "Payments":
        _tab_payments(s, a, f, cfg, d)
    elif tab == "Family":
        _tab_family(s, a, cfg, d)


def _tab_profile(s, a, admin, cfg):
    if not admin:
        info = {"Date of birth": a.dob.strftime("%d %b %Y") if a.dob else "—", "Gender": a.gender or "—",
                "School": a.school or "—", "Parent / guardian": a.guardian_name or "—", "Phone": a.phone or "—",
                "Emergency contact": a.emergency_contact or "—", "Started": a.start_date.strftime("%d %b %Y") if a.start_date else "—",
                "Coach notes": a.notes or "—"}
        c1, c2 = st.columns(2)
        for i, (k, v) in enumerate(info.items()):
            (c1 if i % 2 == 0 else c2).markdown(f"<span class='tnt-muted'>{k}</span><br>{v}", unsafe_allow_html=True)
        return
    sessions = logic.list_sessions(s, {"role": "admin"}, cfg)
    with st.form(f"prof_{a.id}"):
        c1, c2, c3 = st.columns(3)
        first = c1.text_input("First name", a.first_name)
        last = c2.text_input("Last name", a.last_name)
        dob = c3.date_input("Date of birth", a.dob, min_value=date(1950, 1, 1), format="DD/MM/YYYY")
        gender = c1.selectbox("Gender", GENDERS, index=GENDERS.index(a.gender) if a.gender in GENDERS else None)
        programme = c2.selectbox("Programme", PROGRAMMES, index=PROGRAMMES.index(a.programme) if a.programme in PROGRAMMES else None)
        events = c3.text_input("Event group", a.event_group or "")
        school = c1.text_input("School", a.school or "")
        start = c2.date_input("Start date", a.start_date, format="DD/MM/YYYY")
        active = c3.toggle("Active", a.active)
        groups = st.multiselect("Weekly sessions", [x.id for x in sessions], default=[x.id for x in a.sessions if x.active],
                                format_func={x.id: f"{logic.WEEKDAYS[x.weekday][:3]} {x.start_time} {x.label}" for x in sessions}.get)
        g1, g2, g3 = st.columns(3)
        guardian = g1.text_input("Parent / guardian", a.guardian_name or "")
        phone = g2.text_input("Phone (WhatsApp)", a.phone or "")
        email = g3.text_input("Email", a.email or "")
        emergency = g1.text_input("Emergency contact", a.emergency_contact or "")
        medical = st.text_area("Medical notes (visible to coaches)", a.medical_notes or "")
        notes = st.text_area("Coach / admin notes", a.notes or "")
        if st.form_submit_button("Save profile", type="primary"):
            a.first_name, a.last_name, a.dob, a.gender = first.strip(), last.strip(), dob, gender
            a.programme, a.event_group, a.school, a.start_date, a.active = programme, events or None, school or None, start, active
            archived = [x for x in a.sessions if not x.active]
            a.sessions = archived + [s.get(TrainingSession, i) for i in groups]
            a.guardian_name, a.phone, a.email = guardian or None, phone or None, email or None
            a.emergency_contact, a.medical_notes, a.notes = emergency or None, medical or None, notes or None
            auth.audit(s, ui.user().get("name"), "admin", "athlete.update", a.name)
            st.toast("Profile saved", icon=":material/save:")


def _tab_attendance(s, a, d):
    since = d - timedelta(weeks=12)
    q = (select(Attendance.date, TrainingSession.label, Attendance.status, Attendance.recorded_by)
         .join(TrainingSession, TrainingSession.id == Attendance.session_id)
         .where(Attendance.athlete_id == a.id, Attendance.date >= since)
         .order_by(Attendance.date.desc()))
    vis = logic.visible_session_ids(s, ui.user(), db.get_settings(s))  # coaches: their own sessions only
    if vis is not None:
        q = q.where(Attendance.session_id.in_(vis or {-1}))
    rows = s.execute(q).all()
    df = pd.DataFrame([{"Date": r[0], "Session": r[1], "Status": logic.STATUS.get(r[2], r[2]), "Code": r[2],
                        "Recorded by": r[3]} for r in rows])
    if df.empty:
        st.caption("No attendance in the last 12 weeks.")
        return
    c = df["Code"].value_counts()
    rate = logic.attendance_rate(df)
    m = st.columns(4)
    m[0].metric("Attendance (12 wk)", f"{rate:.0f}%" if rate is not None else "—")
    m[1].metric("Sessions attended", int(c.get("P", 0) + c.get("L", 0)))
    m[2].metric("Absent", int(c.get("A", 0)))
    m[3].metric("Sick / excused", int(c.get("S", 0) + c.get("E", 0)))
    st.dataframe(df.drop(columns=["Code"]), hide_index=True, width="stretch", height=300)


def _log_health(aid, key, by):
    if not ui.user():
        return
    g = lambda k: st.session_state.get(f"{key}_{k}")
    with db.session() as s:
        _, n = logic.report_health(s, aid, g("kind"), g("desc") or "", g("start"), g("ret"), by=by,
                                   notes=g("notes") or "", source="staff", cancel_sessions=g("cancel"))
        auth.audit(s, by, ui.user().get("role"), "health.log", str(aid))
    st.toast(f"Logged. {n} session(s) cancelled and marked Sick." if n else "Logged.", icon=":material/healing:")


def _tab_health(s, a, d, user):
    evs = list(s.scalars(select(HealthEvent).where(HealthEvent.athlete_id == a.id).order_by(HealthEvent.start_date.desc())))
    if evs:
        st.dataframe(pd.DataFrame([{"From": e.start_date, "Type": e.kind, "What": e.description,
                                    "Expected back": e.expected_return, "Returned": e.returned_date,
                                    "Reported by": e.reported_by, "Notes": e.notes} for e in evs]),
                     hide_index=True, width="stretch")
    else:
        st.caption("No illness or injury logged.")
    key = f"hl_{a.id}"
    with st.expander("Log illness or injury", icon=":material/add:"):
        with st.form(key, clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            c1.selectbox("Type", ["Illness", "Injury", "Other"], key=f"{key}_kind")
            c2.date_input("Out from", d, key=f"{key}_start", format="DD/MM/YYYY")
            c3.date_input("Expected back", None, key=f"{key}_ret", format="DD/MM/YYYY")
            st.text_input("What happened", key=f"{key}_desc")
            st.text_area("Notes", key=f"{key}_notes")
            st.checkbox("Cancel their sessions until they're back (frees places for the waitlist)", True, key=f"{key}_cancel")
            st.form_submit_button("Log", type="primary", on_click=_log_health, args=(a.id, key, user.get("name")))


def _save_block(bid, key):
    if not ui.is_admin():
        return
    g = lambda k: st.session_state.get(f"{key}_{k}")
    with db.session() as s:
        b = s.get(PaymentBlock, bid)
        if g("delete"):
            s.delete(b)
            auth.audit(s, ui.user().get("name"), "admin", "payment.delete", f"block {bid}")
            st.toast("Block deleted")
            return
        b.credit_sessions, b.extend_days = int(g("credit") or 0), int(g("ext") or 0)
        b.paid = bool(g("paid"))
        b.paid_date = g("paid_date") if b.paid else None
        b.start_date, b.end_date = g("start"), g("end")
        b.notes = g("notes") or None
        auth.audit(s, ui.user().get("name"), "admin", "payment.adjust", f"block {bid}",
                   {"credit": b.credit_sessions, "extend_days": b.extend_days, "paid": b.paid})
    st.toast("Block updated", icon=":material/save:")


def _tab_payments(s, a, f, cfg, d):
    w = f["window"]
    m = st.columns(5)
    m[0].metric("Status", logic.FEE_LABEL[f["status"]])
    m[1].metric("Block", f"{w['start']:%d %b} → {w['end']:%d %b}" if w else "—")
    m[2].metric("Used / paid for", f"{w['used']}/{w['purchased']}" if w else "—")
    m[3].metric("Sessions left", w["left"] if w else "—")
    m[4].metric("Next payment", logic.fmt_date(f["next_due"]))
    if w and (w["auto_ext_days"] or w["manual_ext_days"]):
        st.caption(f":material/update: End date extended {w['auto_ext_days'] + w['manual_ext_days']} day(s): "
                   f"{w['credited']} credited miss(es) (sick/excused) and {w['manual_ext_days']} manual day(s).")
    wins = f["windows"]
    if not wins:
        st.info("No payments recorded yet. Use **Payments → Record payment**.")
        return
    st.dataframe(pd.DataFrame([{
        "Start": x["start"], "Ends (base)": x["base_end"], "Ends (incl. extension)": x["end"],
        "Paid for": x["purchased"], "Used": x["used"], "Left": x["left"], "Sick/Exc.": x["sick"],
        "Amount": ui.money(x["block"].amount, cfg), "Paid": "Yes" if x["block"].paid else "No",
        "Method": x["block"].method, "Family payment": x["block"].family_ref or ""} for x in reversed(wins)]),
        hide_index=True, width="stretch")
    with st.expander("Adjust a block: credit sessions, extend, mark paid", icon=":material/tune:"):
        blocks = [x["block"] for x in reversed(wins)]
        bid = st.selectbox("Block", [b.id for b in blocks], key=f"adj_pick_{a.id}",
                           format_func={b.id: f"{b.start_date:%d %b %Y} · {b.sessions_purchased} sessions · "
                                              f"{'paid' if b.paid else 'UNPAID'}" for b in blocks}.get)
        b = s.get(PaymentBlock, bid)
        key = f"adj_{bid}"
        with st.form(key):
            c1, c2, c3 = st.columns(3)
            c1.number_input("Credit sessions (+)", 0, 50, b.credit_sessions or 0, key=f"{key}_credit",
                            help="Extra sessions added to this block, e.g. a coach-cancelled session or goodwill.")
            c2.number_input("Extend end date by (days)", 0, 120, b.extend_days or 0, key=f"{key}_ext")
            c3.toggle("Paid", b.paid, key=f"{key}_paid")
            c1.date_input("Start", b.start_date, key=f"{key}_start", format="DD/MM/YYYY")
            c2.date_input("Base end", b.end_date, key=f"{key}_end", format="DD/MM/YYYY")
            c3.date_input("Paid on", b.paid_date or d, key=f"{key}_paid_date", format="DD/MM/YYYY")
            st.text_input("Notes / reason", b.notes or "", key=f"{key}_notes")
            st.checkbox("Delete this block", key=f"{key}_delete")
            st.form_submit_button("Save", type="primary", on_click=_save_block, args=(bid, key))


def _new_code(aid):
    if not ui.is_admin():
        return
    with db.session() as s:
        a = s.get(Athlete, aid)
        old = a.family_code
        code = logic.new_family_code(s)
        for sib in s.scalars(select(Athlete).where(Athlete.family_code == old)) if old else [a]:
            sib.family_code = code
        auth.audit(s, ui.user().get("name"), "admin", "family.code_reset", a.name)
    st.toast(f"New family code: {code}")


def _tab_family(s, a, cfg, d):
    sibs = [x for x in s.scalars(select(Athlete).where(Athlete.family_code == a.family_code)) if x.id != a.id] if a.family_code else []
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    c1.markdown(f"Family code: **`{a.family_code or '—'}`**")
    c1.caption("Parents use this code on the booking page. Siblings share it, and one payment can cover them all.")
    c2.button("Issue new code", on_click=_new_code, args=(a.id,), icon=":material/key:",
              help="Use if the code was shared with the wrong person. Applies to the whole family.")
    if sibs:
        st.markdown("**Siblings**")
        for x in sibs:
            fx = logic.athlete_finance(s, x, d, cfg)
            st.markdown(f"- {x.name} · {x.programme or ''}  {ui.badge(logic.FEE_LABEL[fx['status']], logic.FEE_COLOUR[fx['status']])}")
    else:
        st.caption("No siblings linked. To link one, add the sibling with **Family link → Sibling of …**.")
    msg = (f"Hi {(a.guardian_name or 'there').split(' ')[0]}, here's your {cfg['club_name']} booking code: "
           f"{a.family_code}. Book, cancel or report sickness here: "
           f"{(cfg.get('app_url') or '').rstrip('/') + '/book' if cfg.get('app_url') else 'the academy booking page'}")
    links = logic.contact_links(a, msg, cfg)
    if "WhatsApp" in links:
        st.link_button("Send code to parent on WhatsApp", links["WhatsApp"], icon=":material/chat:")
