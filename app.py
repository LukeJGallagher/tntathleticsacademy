"""Roll Call: attendance, bookings, health and fees for TNT Athletics Academy.

Roles
  admin   everything
  coach   Attendance, overview, athletes, bookings and health for their own sessions (scoped in queries)
  parent  Book page only, via a family code

Navigation: every page URL is always registered, so links, refreshes and sign-in/out never hit
"page not found". Each page checks the role itself; the sidebar menu only lists what the role can open.
"""
import os
import sys
import time

import streamlit as st

_T0 = time.time()
_TIMING = os.environ.get("ROLLCALL_TIMING") == "1"


def _tick(label):
    if _TIMING:
        print(f"[timing] {time.time() - _T0:6.2f}s {label}", file=sys.stderr, flush=True)


import auth  # noqa: E402
import db
import sample
import ui

st.set_page_config(page_title="TNT Athletics Academy", page_icon="assets/icon.png", layout="wide",
                   initial_sidebar_state="auto")
ui.inject_css()

# Connect (creates tables on first run). A bad DATABASE_URL shows a plain fix, not a stack trace.
try:
    db.get_engine()
except db.DatabaseUnavailable as e:
    print(f"[rollcall] database connection failed: {e.__cause__!r}", file=sys.stderr, flush=True)
    ui.hero("TNT Athletics Academy", "We'll be right back")
    st.error("The app can't reach its database right now.", icon=":material/cloud_off:")
    st.info(f"**For the admin:** {e}", icon=":material/build:")
    st.caption("After fixing the secret, reboot the app (Manage app → ⋮ → Reboot app). Full details are in the app log.")
    st.stop()

# First run in demo mode: fill the database with fictional sample data
with db.session() as s:
    if ui.demo_mode() and auth.user_count(s) == 0 and not sample.has_sample(s):
        sample.load_sample(s, db.get_settings(s))

from views import (account, athletes, attendance, book, bookings, health, home, myschedule, overview,  # noqa: E402
                   payments, reports, schedule, settings)

_tick("setup + seed check")
user = ui.current_user()
cookie_written = ui.flush_cookie()  # after current_user: an expired session queues a cookie clear
_tick(f"current_user -> {user and user['username']}")
role = user["role"] if user else ("parent" if st.session_state.get("parent_code") else None)

ADMIN, COACH, PARENT = {"admin"}, {"admin", "coach"}, {"parent"}
# key, render, title (admin), title (coach), icon, section, roles
SPEC = [
    ("overview", overview.render, "Overview", "Overview", ":material/space_dashboard:", "Today", ADMIN),
    ("attendance", attendance.render, "Attendance", "Attendance", ":material/how_to_reg:", "Today", COACH),
    ("myschedule", myschedule.render, "My schedule", "My schedule", ":material/calendar_month:", "Today", {"coach"}),
    ("athletes", athletes.render, "Athletes", "Athletes", ":material/groups:", "Athletes", ADMIN),
    ("health", health.render, "Sick & injured", "Sick & injured", ":material/healing:", "Athletes", ADMIN),
    ("bookings", bookings.render, "Bookings", "Bookings", ":material/event_seat:", "Sessions", ADMIN),
    ("schedule", schedule.render, "Schedule", "Schedule", ":material/calendar_month:", "Sessions", ADMIN),
    ("payments", payments.render, "Payments", "Payments", ":material/payments:", "Money", ADMIN),
    ("reports", reports.render, "Reports", "Reports", ":material/insights:", "Money", ADMIN),
    ("settings", settings.render, "Settings", "Settings", ":material/settings:", "Admin", ADMIN),
    ("account", account.render, "My account", "My account", ":material/manage_accounts:", "Account", COACH),
    ("book", book.render, "Book a session", "Book a session", ":material/event_available:", None, PARENT | {None}),
]
HOME_FOR = {"admin": "overview", "coach": "attendance", "parent": "book", None: "home"}


def guarded(key, fn, roles):
    def page():
        if role in roles:
            fn()
        elif role is None:
            home.render()  # signed out: show sign-in; after signing in they stay on this URL
        elif role == "parent":
            book.render()
        else:
            st.warning("This page is for the academy admin.", icon=":material/lock:")
            st.page_link(pages[HOME_FOR[role]], label="Back to your start page", icon=":material/arrow_back:")
    page.__name__ = f"page_{key}"
    return page


def home_page():
    if role is None:
        home.render()
    elif cookie_written:
        # Just signed in on "/": show the start page right here. Navigating away in this same run could
        # cancel the cookie script; the next click or refresh moves on normally.
        start = {"admin": overview.render, "coach": attendance.render, "parent": book.render}[role]
        start()
    else:
        st.switch_page(pages[HOME_FOR[role]])


# "/" is always the sign-in page, which forwards signed-in users to their start page. Every other
# URL (/overview, /payments, …) always exists, whatever the role.
pages = {"home": st.Page(home_page, title="Sign in", icon=":material/login:", url_path="home", default=True)}
for key, fn, t_admin, t_coach, icon, _, roles in SPEC:
    pages[key] = st.Page(guarded(key, fn, roles), title=t_coach if role == "coach" else t_admin, icon=icon,
                         url_path=key)

nav = st.navigation(list(pages.values()), position="hidden")

if user:
    st.logo(str(ui.ASSETS / "logo.png"), size="large", icon_image=str(ui.ASSETS / "icon_white.png"))
    with st.sidebar:
        section = None
        for key, _, _, _, _, sec, roles in SPEC:
            if role not in roles:
                continue
            if sec != section:
                section = sec
                st.markdown(f'<div class="tnt-nav-section">{sec}</div>', unsafe_allow_html=True)
            st.page_link(pages[key])
        st.divider()
        st.caption(f"Signed in as **{user['name']}** · {user['role'].title()}")
        if st.button("Sign out", icon=":material/logout:", width="stretch"):
            ui.sign_out(user["name"], user["role"])
            st.rerun()
        if ui.demo_mode():
            st.caption(":material/science: Demo mode: fictional data")

if user and user.get("must_change") and nav.url_path != "account":
    st.warning("You're using a temporary password. Please set your own now.", icon=":material/key:")
    st.page_link(pages["account"], label="Change my password", icon=":material/arrow_forward:")
_tick(f"nav built, running page {nav.url_path or 'home'}")
nav.run()
_tick("page done")
