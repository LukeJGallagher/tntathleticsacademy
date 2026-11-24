"""Settings (admin): club, billing rules, staff logins, coaches, backup/restore, audit log, demo data."""
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

import auth
import backup
import db
import logic
import sample
import ui
from db import AuditLog, Coach, User

GENERAL = [("club_name", "Club name"), ("club_tagline", "Tagline"), ("venue", "Venue"), ("club_phone", "Club phone"),
           ("app_url", "App web address (for links sent to parents)"), ("currency", "Currency"),
           ("country_code", "Phone country code"), ("timezone", "Time zone")]


def _me():
    u = ui.user()
    return u.get("name"), u.get("role")


def _save_settings(keys):
    with db.session() as s:
        for k in keys:
            v = st.session_state.get(f"set_{k}")
            if isinstance(v, bool):
                v = "1" if v else "0"
            db.set_setting(s, k, v if v is not None else "")
        auth.audit(s, *_me(), "settings.change", ",".join(keys))
    st.toast("Settings saved", icon=":material/save:")


def render():
    ui.require_admin()
    with db.session() as s:
        cfg = db.get_settings(s)
        ui.page_header("Settings", None, cfg)
        sections = {"Club": lambda: _general(cfg), "Billing & booking rules": lambda: _rules(cfg),
                    "Staff logins": lambda: _staff(s), "Coaches": lambda: _coaches(s), "Backup": lambda: _backup(s),
                    "Audit log": lambda: _audit(s), "Demo data": lambda: _demo(s, cfg)}
        sections[ui.tabs(list(sections), key="set_tab")]()


def _general(cfg):
    with st.form("set_general"):
        c = st.columns(2)
        for i, (k, label) in enumerate(GENERAL):
            c[i % 2].text_input(label, cfg.get(k, ""), key=f"set_{k}")
        st.text_area("Payment reminder message", cfg["reminder_template"], key="set_reminder_template", height=110,
                     help="Placeholders: {guardian} {athlete} {club} {due_date} {left} {status_text}")
        st.text_area("Check-in message (missed training)", cfg["followup_template"], key="set_followup_template", height=90,
                     help="Placeholders: {guardian} {athlete} {club} {last_seen}")
        st.form_submit_button("Save", type="primary", on_click=_save_settings,
                              args=([k for k, _ in GENERAL] + ["reminder_template", "followup_template"],))


def _rules(cfg):
    keys = ["default_sessions", "default_per_week", "default_amount", "due_soon_days", "deduct_absences",
            "extend_for_sick", "extend_for_absent", "max_extension_weeks", "reminder_quiet_days", "followup_days",
            "booking_days", "cancel_cutoff_hours", "coaches_see_all"]
    with st.form("set_rules"):
        st.markdown("**Payment blocks**")
        c = st.columns(3)
        c[0].text_input("Default sessions per block", cfg["default_sessions"], key="set_default_sessions")
        c[1].text_input("Default sessions per week", cfg["default_per_week"], key="set_default_per_week")
        c[2].text_input(f"Default amount ({cfg['currency']})", cfg["default_amount"], key="set_default_amount")
        st.markdown("**Missed sessions**")
        st.toggle("Sick / excused extend the block end date", cfg["extend_for_sick"] == "1", key="set_extend_for_sick",
                  help="Each credited miss pushes the end date to the athlete's next scheduled session. Sick and excused never use a paid session.")
        st.toggle("Absences (no reason) use a paid session", cfg["deduct_absences"] == "1", key="set_deduct_absences")
        st.toggle("Absences extend the block end date (only when they don't use a session)", cfg["extend_for_absent"] == "1",
                  key="set_extend_for_absent")
        c = st.columns(3)
        c[0].text_input("Max automatic extension (weeks)", cfg["max_extension_weeks"], key="set_max_extension_weeks")
        c[1].text_input("'Due soon' warning (days)", cfg["due_soon_days"], key="set_due_soon_days")
        c[2].text_input("Hide reminded families for (days)", cfg["reminder_quiet_days"], key="set_reminder_quiet_days")
        st.markdown("**Booking and follow-up**")
        c = st.columns(3)
        c[0].text_input("Parents can book ahead (days)", cfg["booking_days"], key="set_booking_days")
        c[1].text_input("Changes close before start (hours)", cfg["cancel_cutoff_hours"], key="set_cancel_cutoff_hours")
        c[2].text_input("Check in after no attendance for (days)", cfg["followup_days"], key="set_followup_days")
        st.toggle("Coaches can see all sessions (cover / small staff)", cfg["coaches_see_all"] == "1", key="set_coaches_see_all")
        st.form_submit_button("Save rules", type="primary", on_click=_save_settings, args=(keys,))


def _create_user():
    g = st.session_state.get
    try:
        with db.session() as s:
            u = auth.create_user(s, g("nu_user") or "", g("nu_name") or g("nu_user") or "", g("nu_role"), g("nu_pass") or "",
                                 coach_id=g("nu_coach") if g("nu_role") == "coach" else None)
            auth.audit(s, *_me(), "user.create", u.username, {"role": u.role})
        st.toast(f"Login created for {u.full_name}", icon=":material/person_add:")
    except ValueError as e:
        st.session_state["nu_err"] = str(e)


def _user_action(uid, action):
    me = ui.user()
    with db.session() as s:
        u = s.get(User, uid)
        admins = s.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True)))
        if action == "toggle":
            if u.id == me.get("id"):
                st.toast("You can't disable your own account.")
                return
            if u.active and u.role == "admin" and admins <= 1:
                st.toast("Keep at least one active admin.")
                return
            u.active = not u.active
            if not u.active:
                ui.revoke_user_sessions(s, u.id)
            auth.audit(s, *_me(), "user.enable" if u.active else "user.disable", u.username)
        elif action == "unlock":
            u.failed_attempts = 0
            auth.audit(s, *_me(), "user.unlock", u.username)
        elif action == "reset":
            pw = st.session_state.get(f"pw_{uid}") or ""
            if len(pw) < auth.MIN_PASSWORD:
                st.toast(f"Password must be {auth.MIN_PASSWORD}+ characters.")
                return
            u.password_hash = auth.hash_password(pw)
            u.failed_attempts = 0
            if u.id != me.get("id"):
                ui.revoke_user_sessions(s, u.id)
            auth.audit(s, *_me(), "user.password_reset", u.username)
        st.toast("Done", icon=":material/check:")


def _staff(s):
    st.caption("Admin controls everything. Coaches only see the sessions linked to their coach profile.")
    users = s.scalars(select(User).order_by(User.role, User.full_name)).all()
    st.dataframe(pd.DataFrame([{"Name": u.full_name, "Username": u.username, "Role": u.role.title(),
                                "Coach profile": u.coach.name if u.coach else "", "Active": u.active,
                                "Locked": u.failed_attempts >= auth.MAX_FAILED, "Last sign-in": u.last_login_at} for u in users]),
                 hide_index=True, width="stretch")
    with st.expander("Add a login", icon=":material/person_add:"):
        coaches = logic.list_coaches(s)
        with st.form("nu", clear_on_submit=True):
            c = st.columns(2)
            c[0].text_input("Full name", key="nu_name")
            c[1].text_input("Username", key="nu_user")
            c[0].selectbox("Role", ["coach", "admin"], format_func=str.title, key="nu_role")
            c[1].selectbox("Coach profile (for coaches)", [x.id for x in coaches], format_func={x.id: x.name for x in coaches}.get,
                           index=None, key="nu_coach")
            st.text_input(f"Temporary password ({auth.MIN_PASSWORD}+ characters)", type="password", key="nu_pass")
            st.form_submit_button("Create login", type="primary", on_click=_create_user)
        if err := st.session_state.pop("nu_err", None):
            st.error(err)
    with st.expander("Manage a login", icon=":material/manage_accounts:"):
        uid = st.selectbox("Login", [u.id for u in users], format_func={u.id: f"{u.full_name} ({u.username})" for u in users}.get)
        u = next(x for x in users if x.id == uid)
        c = st.columns([2, 1, 1, 1], vertical_alignment="bottom")
        c[0].text_input("New password", type="password", key=f"pw_{uid}")
        c[1].button("Set password", on_click=_user_action, args=(uid, "reset"), width="stretch")
        c[2].button("Unlock", on_click=_user_action, args=(uid, "unlock"), width="stretch",
                    disabled=u.failed_attempts < auth.MAX_FAILED)
        c[3].button("Disable" if u.active else "Enable", on_click=_user_action, args=(uid, "toggle"), width="stretch")


def _add_coach():
    g = st.session_state.get
    if not (g("nc_name") or "").strip():
        return
    with db.session() as s:
        s.add(Coach(name=g("nc_name").strip(), phone=g("nc_phone") or None, email=g("nc_email") or None))
        auth.audit(s, *_me(), "coach.create", g("nc_name"))
    st.toast("Coach added. Create their login under Staff logins.")


def _toggle_coach(cid):
    with db.session() as s:
        c = s.get(Coach, cid)
        c.active = not c.active
        auth.audit(s, *_me(), "coach.toggle", c.name)


def _coaches(s):
    coaches = logic.list_coaches(s, active_only=False)
    for c in coaches:
        a, b = st.columns([4, 1], vertical_alignment="center")
        a.markdown(f"**{c.name}** · {c.phone or ''} {c.email or ''} {'' if c.active else ui.badge('Inactive', 'gray')}")
        b.button("Deactivate" if c.active else "Reactivate", key=f"tc_{c.id}", on_click=_toggle_coach, args=(c.id,), width="stretch")
    with st.form("nc", clear_on_submit=True):
        c = st.columns(3)
        c[0].text_input("Name", key="nc_name", placeholder="Coach Sam")
        c[1].text_input("Phone", key="nc_phone")
        c[2].text_input("Email", key="nc_email")
        st.form_submit_button("Add coach", on_click=_add_coach)


def _backup(s):
    st.markdown("**Download a full backup.** Everything (athletes, attendance, payments, health, logins, settings) "
                "goes into one ZIP file. Keep it somewhere safe: it holds personal and medical data.")
    if st.button("Prepare backup", icon=":material/backup:"):
        st.session_state["bk_zip"] = backup.make_backup(s)
        auth.audit(s, *_me(), "backup.download")
    if z := st.session_state.get("bk_zip"):
        st.download_button("Download backup ZIP", z, f"rollcall_backup_{datetime.now():%Y%m%d_%H%M}.zip",
                           "application/zip", type="primary", icon=":material/download:")
    st.divider()
    st.markdown("**Restore from a backup.** This **replaces all current data** with the backup.")
    up = st.file_uploader("Backup ZIP", type=["zip"], key="restore_file")
    if up:
        data = up.getvalue()
        try:
            man = backup.read_manifest(data)
        except Exception:
            st.error("That isn't a Roll Call backup file.")
            return
        st.caption(f"Backup from {man.get('created', '?')[:16]} · " + ", ".join(f"{k}: {v}" for k, v in man["tables"].items() if v))
        ok = st.text_input("Type RESTORE to confirm") == "RESTORE"
        if st.button("Restore now", type="primary", disabled=not ok, icon=":material/restore:"):
            try:
                backup.restore_backup(s, data)
                auth.audit(s, *_me(), "backup.restore", man.get("created", ""))
                s.commit()
            except Exception as e:
                s.rollback()
                st.error(f"Restore failed, nothing changed: {e}")
                return
            ui.sign_out()
            st.session_state["flash"] = "Backup restored. Please sign in again."
            st.rerun()


def _audit(s):
    rows = s.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(500)).all()
    st.caption("Append-only record of sign-ins, payments, exports and admin changes (times in UTC).")
    st.dataframe(pd.DataFrame([{"Time (UTC)": r.ts, "Who": r.actor, "Role": r.role, "Action": r.action,
                                "Target": r.target, "Detail": r.detail} for r in rows]), hide_index=True, width="stretch", height=420)


def _demo(s, cfg):
    has = sample.has_sample(s)
    st.markdown("Sample data is fictional and flagged, so it can be removed without touching real records. "
                f"Demo logins use the password `{sample.DEMO_PASSWORD}`.")
    c = st.columns(3)
    if c[0].button("Reset demo data", disabled=not has, help="Remove and reload: handy between testers",
                   icon=":material/refresh:"):
        sample.remove_sample(s, keep_logins=True)
        sample.load_sample(s, cfg)
        auth.audit(s, *_me(), "sample.reset")
        st.toast("Demo data reset")
    if c[1].button("Remove sample data", disabled=not has, icon=":material/delete:"):
        me = ui.user()
        if s.get(User, me.get("id")) and s.get(User, me.get("id")).is_sample:
            st.warning("You're signed in with a demo login. Create your own admin login first (Staff logins), sign in "
                       "with it, then remove the sample data.")
        else:
            sample.remove_sample(s)
            auth.audit(s, *_me(), "sample.remove")
            st.toast("Sample data removed")
    if c[2].button("Load sample data", disabled=has, icon=":material/add:"):
        sample.load_sample(s, cfg)
        auth.audit(s, *_me(), "sample.load")
        st.toast("Sample data loaded")
