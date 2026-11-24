"""Roll Call: attendance, bookings, health and fees for TNT Athletics Academy.

Roles
  admin   everything
  coach   Attendance, overview, athletes, bookings and health for their own sessions (scoped in queries)
  parent  Book page only, via a family code

Navigation: every page URL is always registered, so links, refreshes and sign-in/out never hit
"page not found". Each page checks the role itself; the sidebar menu only lists what the role can open.
"""
import streamlit as st

import auth
import db
import sample
import ui

st.set_page_config(page_title="TNT Athletics Academy", page_icon="assets/icon.png", layout="wide",
                   initial_sidebar_state="auto")
ui.inject_css()

# First run in demo mode: fill the database with fictional sample data
with db.session() as s:
    if ui.demo_mode() and auth.user_count(s) == 0 and not sample.has_sample(s):
        sample.load_sample(s, db.get_settings(s))

from views import (athletes, attendance, book, bookings, health, home, overview, payments, reports,  # noqa: E402
                   schedule, settings)

if ui.flush_cookie():  # just signed in: cookie written, page reloads on the same URL
    st.caption("Signing you in…")
    st.stop()
user = ui.current_user()
role = user["role"] if user else ("parent" if st.session_state.get("parent_code") else None)

ADMIN, COACH, PARENT = {"admin"}, {"admin", "coach"}, {"parent"}
# key, render, title (admin), title (coach), icon, section, roles
SPEC = [
    ("overview", overview.render, "Overview", "My overview", ":material/space_dashboard:", "Today", COACH),
    ("attendance", attendance.render, "Attendance", "Attendance", ":material/how_to_reg:", "Today", COACH),
    ("athletes", athletes.render, "Athletes", "My athletes", ":material/groups:", "Athletes", COACH),
    ("health", health.render, "Sick & injured", "Sick & injured", ":material/healing:", "Athletes", COACH),
    ("bookings", bookings.render, "Bookings", "Bookings", ":material/event_seat:", "Sessions", COACH),
    ("schedule", schedule.render, "Schedule", "Schedule", ":material/calendar_month:", "Sessions", ADMIN),
    ("payments", payments.render, "Payments", "Payments", ":material/payments:", "Money", ADMIN),
    ("reports", reports.render, "Reports", "Reports", ":material/insights:", "Money", ADMIN),
    ("settings", settings.render, "Settings", "Settings", ":material/settings:", "Admin", ADMIN),
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

nav.run()
