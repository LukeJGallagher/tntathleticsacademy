"""Parents: book, cancel, join the waitlist, or report sickness. Family code only, no password."""
from datetime import timedelta

import streamlit as st

import db
import logic
import ui
from db import Athlete, TrainingSession


def _owns(s, aid) -> bool:
    """The child must still belong to the family code this browser signed in with."""
    return aid in {k.id for k in logic.family_athletes(s, st.session_state.get("parent_code") or "")}


def _act(action, aid, sid, d, reason=None):
    with db.session() as s:
        if not _owns(s, aid):
            st.toast("Please sign in again with your family code.", icon=":material/lock:")
            return
        cfg = db.get_settings(s)
        sess = s.get(TrainingSession, sid)
        cutoff = int(cfg.get("cancel_cutoff_hours", 2))
        if logic.session_start(sess, d) - timedelta(hours=cutoff) < logic.now_local(cfg):
            st.toast(f"Changes close {cutoff} hours before the session. Please message the academy.", icon=":material/lock:")
            return
        if action == "book":
            res = logic.book(s, aid, sid, d, kind="casual", source="parent")
            st.toast({"booked": "Booked!", "waitlist": "Session full: added to the waitlist.",
                      "already": "Already booked."}.get(res, res),
                     icon=":material/event_available:")
        elif action == "cancel":
            promoted = logic.cancel(s, aid, sid, d, reason=reason or "Other", source="parent")
            st.toast("Cancelled. Thanks for letting us know." + (" Your place went to the next family on the waitlist." if promoted else ""),
                     icon=":material/event_busy:")
        elif action == "undo":  # leave waitlist / withdraw: just remove the booking
            logic.remove_booking(s, aid, sid, d)
            st.toast("Done.", icon=":material/undo:")
        elif action == "reinstate":  # "we're coming after all": only if the place is still free
            res = logic.reinstate(s, aid, sid, d, staff=False)
            st.toast({"booked": "Great, see you there.", "waitlist": "The session filled up: you're on the waitlist.",
                      "full": "Sorry, the session is now full. Please message the academy."}.get(res, "Done."),
                     icon=":material/undo:")


def _report_sick(aid):
    g = st.session_state.get
    with db.session() as s:
        a = s.get(Athlete, aid)
        if not a or not _owns(s, aid):
            return
        start = max(g("ps_start") or logic.today(db.get_settings(s)), logic.today(db.get_settings(s)))
        _, n = logic.report_health(s, aid, g("ps_kind"), (g("ps_desc") or "")[:200], start, g("ps_ret"),
                                   by=f"Parent ({a.guardian_name or a.last_name})", source="parent")
    st.session_state["book_flash"] = (f"Thanks. We've let the coaches know"
                                      + (f" and cancelled {n} session(s)." if n else "."))


def _sign_out():
    for k in ("parent_code", "book_child", "parent_tries"):
        st.session_state.pop(k, None)


def render():
    code = st.session_state.get("parent_code")
    with db.session() as s:
        cfg = db.get_settings(s)
        if not code:
            ui.hero(cfg["club_name"], "Bookings for parents")
            st.info("Enter your family code on the [sign-in page](./) to book.", icon=":material/key:")
            return

        kids = logic.family_athletes(s, code)
        if not kids:
            _sign_out()
            st.rerun()
        d0 = logic.today(cfg)
        ui.hero(cfg["club_name"], f"Bookings for the {kids[0].last_name or ''} family".replace("  ", " "),
                photo="training.jpg")
        ids = [k.id for k in kids]
        aid = ids[0] if len(ids) == 1 else st.selectbox(
            "Child", ids, format_func={k.id: k.first_name for k in kids}.get, key="book_child")
        a = s.get(Athlete, aid)

        if msg := st.session_state.pop("book_flash", None):
            st.success(msg, icon=":material/check_circle:")

        f = logic.athlete_finance(s, a, d0, cfg)
        w = f["window"]
        with st.container(border=True):
            if w:
                st.markdown(f"**{a.first_name}** · **{max(w['left'], 0)}** session(s) left · block ends "
                            f"**{w['end']:%a %d %b}** · next payment **{logic.fmt_date(f['next_due'])}**")
            else:
                st.markdown(f"**{a.first_name}** · No active block yet. The academy will set one up when you pay.")
        if f["status"] in ("due", "unpaid"):
            st.warning(f"Payment {logic.FEE_LABEL[f['status']].lower()}. Please contact the academy"
                       + (f" on {cfg['club_phone']}" if cfg.get("club_phone") else "") + ".", icon=":material/payments:")

        out = logic.out_on(s, d0).get(a.id)
        with st.expander(f":material/sick: {a.first_name} is sick or injured" if not out else
                         f":material/sick: {a.first_name} is on the sick list ({out.kind}), back {logic.fmt_date(out.expected_return)}",
                         expanded=False):
            with st.form("parent_sick", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                c1.selectbox("Type", ["Illness", "Injury", "Other"], key="ps_kind")
                c2.date_input("From", d0, min_value=d0, key="ps_start", format="DD/MM/YYYY")
                c3.date_input("Expected back (optional)", None, min_value=d0, key="ps_ret", format="DD/MM/YYYY")
                st.text_input("What's wrong? (optional)", key="ps_desc", placeholder="e.g. fever, sprained ankle",
                              max_chars=200)
                st.caption("We'll cancel their sessions until the return date. Sick days never use a paid session.")
                st.form_submit_button("Tell the academy", type="primary", on_click=_report_sick, args=(a.id,))

        st.subheader("Upcoming sessions")
        horizon = int(cfg.get("booking_days", 14))
        cutoff = int(cfg.get("cancel_cutoff_hours", 2))
        sessions = [x for x in logic.list_sessions(s, {"role": "admin"}, cfg)]
        now = logic.now_local(cfg)
        shown = 0
        for i in range(horizon + 1):
            d = d0 + timedelta(days=i)
            todays = [x for x in sessions if x.weekday == d.weekday()
                      and (x.bookable or logic._is_regular(x, a.id) or logic.athlete_place(s, a, x, d) != "none")]
            for x in todays:
                start = logic.session_start(x, d)
                if start < now:
                    continue
                place = logic.athlete_place(s, a, x, d)
                r = logic.day_roster(s, x, d)
                locked = start - timedelta(hours=cutoff) < now
                shown += 1
                with st.container(border=True):
                    c1, c2 = st.columns([3, 2], vertical_alignment="center")
                    status = {"regular": ui.badge("Booked (regular)", "green"), "booked": ui.badge("Booked", "green"),
                              "cancelled": ui.badge("Not coming", "gray"),
                              "pending": ui.badge("Awaiting confirmation", "orange"), "none": ""}.get(place, "")
                    if place.startswith("waitlist"):
                        status = ui.badge(f"Waitlist #{place.split(':')[1]}", "orange")
                    spots = ui.badge("Full", "red") if r["spots"] <= 0 else ui.badge(f"{r['spots']} places left", "blue")
                    c1.markdown(f"**{d:%a %d %b}** · {x.start_time}–{x.end_time}  \n{x.label}  {status} "
                                + (spots if place == "none" else ""))
                    k = f"{x.id}_{d}"
                    if locked:
                        c2.caption(f":material/lock: Changes closed ({cutoff} h before)")
                    elif place in ("regular", "booked"):
                        b1, b2 = c2.columns(2)
                        reason = b1.selectbox("Reason", ["Sick", "Injured", "Other"], key=f"rs_{k}",
                                              label_visibility="collapsed")
                        b2.button("Can't make it", key=f"cx_{k}", on_click=_act, args=("cancel", a.id, x.id, d, reason),
                                  width="stretch")
                    elif place == "cancelled":
                        c2.button("Undo: we're coming", key=f"un_{k}", on_click=_act, args=("reinstate", a.id, x.id, d),
                                  width="stretch")
                    elif place == "pending":
                        c2.button("Withdraw", key=f"wd_{k}", on_click=_act, args=("undo", a.id, x.id, d),
                                  width="stretch")
                    elif place.startswith("waitlist"):
                        c2.button("Leave waitlist", key=f"lw_{k}", on_click=_act, args=("undo", a.id, x.id, d),
                                  width="stretch")
                    elif x.bookable:
                        c2.button("Join waitlist" if r["spots"] <= 0 else "Book", key=f"bk_{k}", type="primary",
                                  on_click=_act, args=("book", a.id, x.id, d), width="stretch")
        if not shown:
            st.caption("No sessions in the next two weeks.")
        st.caption(f"Changes close {cutoff} hours before a session. If a session is full you can join the waitlist; "
                   f"you'll be moved in automatically if a place opens.")
        st.button("Sign out", on_click=_sign_out, icon=":material/logout:", type="tertiary")
