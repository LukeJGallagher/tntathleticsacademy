"""Landing page: staff sign in, parent family code, first-run admin setup."""
from datetime import datetime, timedelta

import streamlit as st

import auth
import db
import logic
import sample
import ui

MAX_PARENT_TRIES = 10


def _staff_login():
    with db.session() as s:
        u, msg = auth.authenticate(s, st.session_state.get("li_user", ""), st.session_state.get("li_pass", ""))
        uid = u.id if u else None
    if uid:  # after commit, so the session row can reference the user
        ui.sign_in(uid)
    else:
        st.session_state["login_error"] = msg


# Family-code guessing throttle, shared across all browser sessions on this server process
_CODE_FAILS: list[datetime] = []
CODE_WINDOW = timedelta(minutes=10)
CODE_MAX_FAILS = 30


def _parent_login():
    now = datetime.now()
    _CODE_FAILS[:] = [t for t in _CODE_FAILS if now - t < CODE_WINDOW]
    tries = st.session_state.get("parent_tries", 0)
    if tries >= MAX_PARENT_TRIES or len(_CODE_FAILS) >= CODE_MAX_FAILS:
        st.session_state["parent_error"] = "Too many attempts. Please wait a few minutes or contact the academy."
        return
    code = (st.session_state.get("pc_code") or "").strip().upper()
    with db.session() as s:
        kids = logic.family_athletes(s, code)
    if kids:
        if st.session_state.get("auth") or ui._cookie_token():
            ui.sign_out()  # a parent code on a staff browser ends the staff session
        st.session_state["parent_code"] = code
    else:
        _CODE_FAILS.append(now)
        st.session_state["parent_tries"] = tries + 1
        st.session_state["parent_error"] = "That code wasn't recognised. Check the message from the academy."


def _create_admin():
    g = st.session_state.get
    if g("fa_pass") != g("fa_pass2"):
        st.session_state["setup_error"] = "Passwords don't match."
        return
    try:
        with db.session() as s:
            if auth.user_count(s):  # someone beat us to it
                return
            u = auth.create_user(s, g("fa_user") or "admin", g("fa_name") or "Admin", "admin", g("fa_pass") or "")
            auth.audit(s, u.full_name, "admin", "user.create", u.username, "first-run admin")
            uid = u.id
    except ValueError as e:
        st.session_state["setup_error"] = str(e)
        return
    ui.sign_in(uid)


def render():
    with db.session() as s:
        cfg = db.get_settings(s)
        n_users = auth.user_count(s)

    ui.hero(cfg["club_name"], cfg.get("club_tagline", "") + (f" · {cfg['venue']}" if cfg.get("venue") else ""),
            photo="hero.jpg", tall=True)
    if msg := st.session_state.pop("flash", None):
        st.info(msg)

    if n_users == 0 and not ui.demo_mode():
        with st.container(border=True):
            st.subheader("Set up the admin account")
            st.caption("First run. This account controls everything: staff logins, payments, settings.")
            with st.form("first_admin"):
                st.text_input("Your name", key="fa_name")
                st.text_input("Username", value="admin", key="fa_user")
                st.text_input("Password (8+ characters)", type="password", key="fa_pass")
                st.text_input("Confirm password", type="password", key="fa_pass2")
                st.form_submit_button("Create admin account", type="primary", on_click=_create_admin)
            if err := st.session_state.pop("setup_error", None):
                st.error(err)
        return

    left, right = st.columns(2, gap="large")
    with left, st.container(border=True):
        st.subheader(":material/badge: Staff")
        st.caption("Admin and coaches")
        with st.form("staff_login"):
            st.text_input("Username", key="li_user", autocomplete="username")
            st.text_input("Password", type="password", key="li_pass", autocomplete="current-password")
            st.form_submit_button("Sign in", type="primary", width="stretch", on_click=_staff_login)
        if err := st.session_state.pop("login_error", None):
            st.error(err)

    with right, st.container(border=True):
        st.subheader(":material/family_restroom: Parents")
        st.caption("Book sessions, cancel, or tell us your child is sick")
        with st.form("parent_login"):
            st.text_input("Family code", key="pc_code", placeholder="6 characters, e.g. AB12CD", max_chars=12)
            st.form_submit_button("Continue", type="primary", width="stretch", on_click=_parent_login)
        if err := st.session_state.pop("parent_error", None):
            st.error(err)
        if cfg.get("club_phone"):
            st.caption(f"No code? WhatsApp the academy on {cfg['club_phone']}.")

    with db.session() as s:
        show_demo = ui.demo_mode() and sample.has_sample(s)
    if show_demo:
        with st.expander(":material/science: Demo logins (fictional data)", expanded=True):
            st.markdown(f"""
| Who | Username | Password | What they see |
|---|---|---|---|
| Admin / owner | `admin` | `{sample.DEMO_PASSWORD}` | Everything: payments, reminders, reports, settings |
| Coach | `tom` | `{sample.DEMO_PASSWORD}` | Youth + Sports Speed sessions and athletes only |
| Coach | `nadia` | `{sample.DEMO_PASSWORD}` | ToTs sessions only |
| Coach | `ahmed` | `{sample.DEMO_PASSWORD}` | Teen & Adult + Saturday Squad only |
| Parent | family code `DEMO01` or `DEMO02` | none | Book, cancel, report sickness |
""")
