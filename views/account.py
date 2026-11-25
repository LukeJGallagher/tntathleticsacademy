"""My account: any staff member (admin or coach) changes their own password."""
import streamlit as st
from sqlalchemy import select

import auth
import db
import ui
from db import StaffSession, User, utcnow


def _change():
    me = ui.user()
    if not me:
        return
    g = st.session_state.get
    new, again = g("acc_new") or "", g("acc_again") or ""
    with db.session() as s:
        u = s.get(User, me["id"])
        if not auth.verify_password(u.password_hash, g("acc_current") or ""):
            auth.audit(s, u.full_name, u.role, "password.change_failed")
            st.session_state["acc_msg"] = ("error", "Your current password isn't right.")
            return
        if len(new) < auth.MIN_PASSWORD:
            st.session_state["acc_msg"] = ("error", f"The new password needs at least {auth.MIN_PASSWORD} characters.")
            return
        if new != again:
            st.session_state["acc_msg"] = ("error", "The two new passwords don't match.")
            return
        u.password_hash = auth.hash_password(new)
        u.must_change_password = False
        # sign out every other device; keep this browser signed in
        mine = ui._hash(st.session_state.get("auth", {}).get("token", ""))
        for ss in s.scalars(select(StaffSession).where(StaffSession.user_id == u.id, StaffSession.revoked_at.is_(None))):
            if ss.token_hash != mine:
                ss.revoked_at = utcnow()
        auth.audit(s, u.full_name, u.role, "password.change")
    st.session_state["acc_msg"] = ("success", "Password changed. Any other devices you were signed in on are signed out.")


def render():
    me = ui.user()
    with db.session() as s:
        cfg = db.get_settings(s)
        u = s.get(User, me["id"])
        ui.page_header("My account", None, cfg)
        st.markdown(f"**{u.full_name}** · username `{u.username}` · {u.role.title()}"
                    + (f" · coach profile: {u.coach.name}" if u.coach else ""))
    if msg := st.session_state.pop("acc_msg", None):
        (st.success if msg[0] == "success" else st.error)(msg[1])
    with st.container(border=True):
        st.subheader("Change password")
        with st.form("acc_pw", clear_on_submit=True):
            st.text_input("Current password", type="password", key="acc_current", autocomplete="current-password")
            st.text_input(f"New password ({auth.MIN_PASSWORD}+ characters)", type="password", key="acc_new",
                          autocomplete="new-password")
            st.text_input("New password again", type="password", key="acc_again", autocomplete="new-password")
            st.form_submit_button("Change password", type="primary", on_click=_change)
    st.caption("Forgotten your password? Ask the academy admin to set a temporary one "
               "(Settings → Staff logins → Manage a login), then change it here.")
