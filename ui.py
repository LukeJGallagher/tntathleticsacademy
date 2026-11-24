"""Shared UI: brand CSS, headers, badges, auth session handling."""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import select

import db
from db import StaffSession, User, utcnow

ASSETS = Path(__file__).parent / "assets"
IDLE_TIMEOUT = timedelta(hours=2)
ABSOLUTE_TIMEOUT = timedelta(hours=12)

BRAND = {"black": "#000000", "blue": "#2D98C6", "grey": "#54595F", "card": "#1B1D20", "muted": "#9AA3AB"}


@lru_cache
def asset_b64(name: str) -> str:
    p = ASSETS / name
    return base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


def demo_mode() -> bool:
    default = "1" if db.database_url().startswith("sqlite") else "0"
    return (db.secret("DEMO_MODE", default) or default) == "1"


def inject_css() -> None:
    st.markdown(f"""
<style>
h1, h2, h3 {{ letter-spacing: .02em; }}
h1 {{ font-size: 2.6rem !important; line-height: 1.05 !important; }}
[data-testid="stSidebar"] [data-testid="stSidebarNav"] a span {{ font-weight: 500; }}
.tnt-eyebrow {{ color: {BRAND['blue']}; font-weight: 700; font-size: .78rem; letter-spacing: .14em;
               text-transform: uppercase; margin-bottom: -.4rem; }}
.tnt-hero {{ background: linear-gradient(120deg, #000 0%, #15181b 100%); border: 1px solid #22252a; border-radius: .75rem; padding: 1.6rem 1.8rem; color: #fff;
            display: flex; align-items: center; gap: 1.4rem; border-left: 6px solid {BRAND['blue']};
            margin-bottom: 1.2rem; }}
.tnt-hero img {{ height: 64px; }}
.tnt-hero h1 {{ color: #fff; margin: 0; padding: 0; font-size: 2.4rem !important; }}
.tnt-hero p {{ color: #cfd4d9; margin: .2rem 0 0 0; }}
.tnt-avatar {{ width: 64px; height: 64px; border-radius: 50%; background: {BRAND['blue']}; color: #fff;
              display: flex; align-items: center; justify-content: center; font-size: 1.5rem; font-weight: 700;
              border: 3px solid {BRAND['blue']}; }}
.tnt-muted {{ color: {BRAND['muted']}; font-size: .88rem; }}
.tnt-row-name {{ font-weight: 600; }}
.tnt-nav-section {{ color: {BRAND['muted']}; font-size: .7rem; font-weight: 700; letter-spacing: .14em;
                   text-transform: uppercase; margin: .9rem 0 .1rem .5rem; }}
[data-testid="stSidebar"] [data-testid="stPageLink"] a {{ border-radius: .4rem; }}
[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"] {{ background: #17191B;
                   box-shadow: inset 3px 0 0 {BRAND['blue']}; }}
iframe[height="0"] {{ display: none; }}
@media (max-width: 640px) {{
  h1 {{ font-size: 2rem !important; }}
  .tnt-hero {{ padding: 1.1rem 1.2rem; }}
  .tnt-hero img {{ height: 44px; }}
  .tnt-hero h1 {{ font-size: 1.8rem !important; }}
}}
</style>""", unsafe_allow_html=True)


def hero(title: str, subtitle: str = "", photo: str | None = "hero.jpg", tall: bool = False) -> None:
    """Dark photo banner like the TNT site: club photo under a black gradient, white logo, blue edge."""
    logo = asset_b64("logo.png")
    img = f'<img src="data:image/png;base64,{logo}" alt="TNT logo">' if logo else ""
    bg = ""
    if photo and (p := asset_b64(photo)):
        bg = (f"background-image: linear-gradient(90deg, rgba(0,0,0,.94) 0%, rgba(0,0,0,.78) 45%, rgba(0,0,0,.35) 100%),"
              f" url(data:image/jpeg;base64,{p}); background-size: cover; background-position: center 40%;")
    height = "min-height: 240px;" if tall else "min-height: 150px;"
    st.markdown(f'<div class="tnt-hero" style="{bg}{height}">{img}<div><h1>{title}</h1><p>{subtitle}</p></div></div>',
                unsafe_allow_html=True)


def page_header(title: str, subtitle: str | None = None, cfg: dict | None = None) -> None:
    club = (cfg or {}).get("club_name", "")
    if club:
        st.markdown(f'<div class="tnt-eyebrow">{club}</div>', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def tabs(options: list[str], key: str) -> str:
    """Tab bar that survives reruns (st.tabs jumps back to the first tab after a form submit)."""
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]
    choice = st.segmented_control("Section", options, key=key, label_visibility="collapsed")
    if choice is None:  # clicking the active segment deselects it; keep the last one
        choice = st.session_state.get(f"_{key}_last", options[0])
    st.session_state[f"_{key}_last"] = choice
    return choice


def badge(label: str, colour: str = "gray") -> str:
    label = label.replace("[", "(").replace("]", ")")
    return f":{colour}-badge[{label}]"


def avatar(name: str) -> str:
    initials = "".join(p[0] for p in name.split()[:2]).upper()
    return f'<div class="tnt-avatar">{initials}</div>'


def money(v, cfg) -> str:
    return f"{cfg.get('currency', '')} {v:,.0f}" if v is not None else "—"


# --------------------------------------------------------------------------- auth

COOKIE = "rollcall_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign_in(u: User) -> None:
    """Create a server-side session and queue the browser cookie (written by flush_cookie on the next run)."""
    token = secrets.token_urlsafe(32)
    with db.session() as s:
        s.add(StaffSession(token_hash=_hash(token), user_id=u.id, created_at=utcnow(), last_seen=utcnow()))
    st.session_state["auth"] = {"token": token}
    st.session_state["_cookie"] = (token, int(ABSOLUTE_TIMEOUT.total_seconds()))
    st.session_state.pop("parent_code", None)


def sign_out(actor: str | None = None, role: str | None = None) -> None:
    a = st.session_state.get("auth") or {}
    token = a.get("token") or _cookie_token()
    with db.session() as s:
        if token:
            ss = s.scalar(select(StaffSession).where(StaffSession.token_hash == _hash(token)))
            if ss and not ss.revoked_at:
                ss.revoked_at = utcnow()
        if actor:
            import auth
            auth.audit(s, actor, role, "logout")
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.session_state["_cookie"] = ("", 0)  # clear it in the browser
    st.session_state["_ignore_cookie"] = token  # the browser may still send it on this tab


def _cookie_token() -> str | None:
    try:
        return st.context.cookies.get(COOKIE)
    except Exception:
        return None


def flush_cookie() -> None:
    """Write or clear the session cookie in the browser. Call once per run, early."""
    pending = st.session_state.pop("_cookie", None)
    if pending is None:
        return
    token, max_age = pending
    secure = "; Secure" if (db.secret("COOKIE_SECURE", "1") == "1") else ""
    components.html(
        f"<script>window.parent.document.cookie = '{COOKIE}={token}; Max-Age={max_age}; Path=/; SameSite=Lax{secure}';</script>",
        height=0)


def current_user() -> dict | None:
    """Re-validated against the server-side session on every rerun. Disabled users, revoked or expired
    sessions drop out immediately. A browser refresh restores the session from the cookie."""
    a = st.session_state.get("auth") or {}
    token = a.get("token")
    if not token:
        token = _cookie_token()
        if not token or token == st.session_state.get("_ignore_cookie"):
            return None
    now = utcnow()
    with db.session() as s:
        ss = s.scalar(select(StaffSession).where(StaffSession.token_hash == _hash(token)))
        expired = ss is not None and (now - ss.created_at > ABSOLUTE_TIMEOUT or now - ss.last_seen > IDLE_TIMEOUT)
        u = s.get(User, ss.user_id) if ss else None
        if ss is None or ss.revoked_at or expired or u is None or not u.active:
            if ss is not None and not ss.revoked_at:
                ss.revoked_at = now
            if a:
                st.session_state.pop("auth", None)
                st.session_state["_cookie"] = ("", 0)
                if expired:
                    st.session_state["flash"] = "You were signed out after a period of inactivity."
            return None
        ss.last_seen = now
        st.session_state["auth"] = {"token": token}
        return {"id": u.id, "username": u.username, "name": u.full_name, "role": u.role, "coach_id": u.coach_id}


def revoke_user_sessions(s, user_id: int) -> None:
    for ss in s.scalars(select(StaffSession).where(StaffSession.user_id == user_id, StaffSession.revoked_at.is_(None))):
        ss.revoked_at = utcnow()


def user() -> dict:
    return st.session_state.get("_user") or {}


def is_admin() -> bool:
    return user().get("role") == "admin"


def require_admin() -> None:
    if not is_admin():
        st.error("Only the admin can open this page.")
        st.stop()
