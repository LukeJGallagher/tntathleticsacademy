"""Models, engine, settings and a lightweight column migrator.

DATABASE_URL (env var or .streamlit/secrets.toml) picks the database:
  sqlite:///rollcall.db                                  local / demo (default)
  mysql+pymysql://USER:PASS@HOST:3306/DB                 Hostinger MySQL
  postgresql+psycopg2://USER:PASS@HOST:5432/postgres     Supabase Postgres
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Table,
                        Text, UniqueConstraint, create_engine, inspect, select, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


session_athletes = Table(
    "session_athletes", Base.metadata,
    Column("session_id", ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True),
    Column("athlete_id", ForeignKey("athletes.id", ondelete="CASCADE"), primary_key=True),
)


class Coach(Base):
    __tablename__ = "coaches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    """Staff login. role = 'admin' | 'coach'. Coaches link to a Coach row, which scopes their data."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    full_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(10), default="coach")
    coach_id: Mapped[int | None] = mapped_column(ForeignKey("coaches.id"))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    coach: Mapped[Coach | None] = relationship()


class Athlete(Base):
    __tablename__ = "athletes"
    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(60))
    last_name: Mapped[str] = mapped_column(String(60), default="")
    dob: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    school: Mapped[str | None] = mapped_column(String(120))
    guardian_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(120))
    start_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    medical_notes: Mapped[str | None] = mapped_column(Text)
    emergency_contact: Mapped[str | None] = mapped_column(String(160))
    programme: Mapped[str | None] = mapped_column(String(60))  # ToTs / Youth / Teen & Adult / Sports Speed
    event_group: Mapped[str | None] = mapped_column(String(120))  # e.g. Sprints, Jumps, Throws, Middle distance
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    family_code: Mapped[str | None] = mapped_column(String(12), index=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    sessions: Mapped[list["TrainingSession"]] = relationship(secondary=session_athletes, back_populates="athletes")
    blocks: Mapped[list["PaymentBlock"]] = relationship(back_populates="athlete", cascade="all, delete-orphan")

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def age(self, on: date | None = None) -> int | None:
        if not self.dob:
            return None
        on = on or date.today()
        return on.year - self.dob.year - ((on.month, on.day) < (self.dob.month, self.dob.day))


class TrainingSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday
    start_time: Mapped[str] = mapped_column(String(5))  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5))
    coach_id: Mapped[int | None] = mapped_column(ForeignKey("coaches.id"))
    capacity: Mapped[int] = mapped_column(Integer, default=12)
    bookable: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    coach: Mapped[Coach | None] = relationship()
    athletes: Mapped[list[Athlete]] = relationship(secondary=session_athletes, back_populates="sessions")


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("date", "session_id", "athlete_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    status: Mapped[str] = mapped_column(String(1))  # P L A S E
    recorded_by: Mapped[str | None] = mapped_column(String(100))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint("date", "session_id", "athlete_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
    status: Mapped[str] = mapped_column(String(12))  # booked | waitlist | cancelled
    kind: Mapped[str] = mapped_column(String(12), default="casual")  # regular | casual | makeup | trial
    source: Mapped[str] = mapped_column(String(10), default="staff")  # staff | parent
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    athlete: Mapped[Athlete] = relationship()
    session: Mapped[TrainingSession] = relationship()


class PaymentBlock(Base):
    __tablename__ = "payment_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    sessions_per_week: Mapped[int] = mapped_column(Integer, default=2)
    sessions_purchased: Mapped[int] = mapped_column(Integer, default=8)
    credit_sessions: Mapped[int | None] = mapped_column(Integer, default=0)  # manual bonus / make-up credit
    extend_days: Mapped[int | None] = mapped_column(Integer, default=0)  # manual extension of the end date
    amount: Mapped[float] = mapped_column(Float, default=0)
    paid: Mapped[bool] = mapped_column(Boolean, default=True)
    paid_date: Mapped[date | None] = mapped_column(Date)
    method: Mapped[str | None] = mapped_column(String(30))
    family_ref: Mapped[str | None] = mapped_column(String(20), index=True)  # shared by blocks paid together
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    athlete: Mapped[Athlete] = relationship(back_populates="blocks")


class PaymentRequest(Base):
    """A payment the academy is expecting: one per family (or athlete) per renewal.
    items = JSON list of {athlete_id, sessions, per_week, amount}. When paid, each item becomes a PaymentBlock.
    provider 'manual' = cash/transfer marked by admin; 'skipcash' = online pay link."""
    __tablename__ = "payment_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True)  # e.g. TNT-7KQ2MX, also sent to the gateway
    family_code: Mapped[str | None] = mapped_column(String(12), index=True)
    payer_name: Mapped[str | None] = mapped_column(String(120))
    payer_phone: Mapped[str | None] = mapped_column(String(40))
    payer_email: Mapped[str | None] = mapped_column(String(120))
    items: Mapped[str] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="QAR")
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | link_sent | paid | cancelled
    provider: Mapped[str] = mapped_column(String(12), default="manual")
    provider_id: Mapped[str | None] = mapped_column(String(80))
    pay_url: Mapped[str | None] = mapped_column(String(500))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_method: Mapped[str | None] = mapped_column(String(30))
    family_ref: Mapped[str | None] = mapped_column(String(20))  # links to the PaymentBlocks created
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)


class HealthEvent(Base):
    __tablename__ = "health_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # Illness | Injury | Other
    description: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[date] = mapped_column(Date)
    expected_return: Mapped[date | None] = mapped_column(Date)
    returned_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    reported_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    athlete: Mapped[Athlete] = relationship()


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), index=True)
    sent_on: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    due_date: Mapped[date | None] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(20))
    message: Mapped[str | None] = mapped_column(Text)
    sent_by: Mapped[str | None] = mapped_column(String(100))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    athlete: Mapped[Athlete] = relationship()


class StaffSession(Base):
    """Server-side login session. The browser cookie holds a random token; only its SHA-256 is stored."""
    __tablename__ = "staff_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    """Append-only. Actor name is denormalised so the trail survives user deletion."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)  # UTC
    actor: Mapped[str] = mapped_column(String(100))
    role: Mapped[str | None] = mapped_column(String(10))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(160))
    detail: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- config

def secret(name: str, default: str | None = None) -> str | None:
    """Env var first, then .streamlit/secrets.toml."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        import streamlit as st
        val = st.secrets.get(name)
        return str(val) if val is not None else default
    except Exception:
        return default


def database_url() -> str:
    url = secret("DATABASE_URL") or "sqlite:///rollcall.db"
    if url.startswith("postgres://"):  # Supabase / Heroku style
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_lock = threading.Lock()
_engine = None
_Session = None


def get_engine():
    global _engine, _Session
    with _lock:
        if _engine is None:
            url = database_url()
            kw: dict = {}
            if url.startswith("sqlite"):
                kw["connect_args"] = {"check_same_thread": False}
            else:
                kw.update(pool_pre_ping=True, pool_recycle=280)
            eng = create_engine(url, **kw)
            Base.metadata.create_all(eng)
            _migrate(eng)
            _Session = sessionmaker(eng, expire_on_commit=False)
            _engine = eng
    return _engine


def _migrate(eng) -> None:
    """Add any model column missing from an existing table (nullable, no default)."""
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    q = eng.dialect.identifier_preparer.quote
                    coltype = col.type.compile(eng.dialect)
                    default = ""
                    if col.default is not None and getattr(col.default, "is_scalar", False):
                        v = col.default.arg
                        if isinstance(v, bool):
                            default = f" DEFAULT {'TRUE' if v else 'FALSE'}" if eng.dialect.name != "sqlite" else f" DEFAULT {int(v)}"
                        elif isinstance(v, (int, float)):
                            default = f" DEFAULT {v}"
                    conn.execute(text(f"ALTER TABLE {q(table.name)} ADD COLUMN {q(col.name)} {coltype}{default}"))


@contextmanager
def session():
    """Commit on success, and also on Streamlit's st.rerun()/st.stop() (BaseException),
    so a rerun never silently drops a write. Roll back on real errors."""
    get_engine()
    s = _Session()
    try:
        yield s
    except Exception:
        s.rollback()
        raise
    except BaseException:
        s.commit()
        raise
    else:
        s.commit()
    finally:
        s.close()


# --------------------------------------------------------------------------- settings

DEFAULT_SETTINGS: dict[str, str] = {
    "club_name": "TNT Athletics Academy",
    "club_tagline": "Qatar's first dedicated Track & Field academy",
    "venue": "UDST, Doha",
    "currency": "QAR",
    "country_code": "974",
    "timezone": "Asia/Qatar",
    "club_phone": "+974 5529 7579",
    "app_url": "",
    "reminder_template": (
        "Hi {guardian}, a quick note from {club}: {athlete}'s current block "
        "{status_text}. Next payment date: {due_date}. Sessions left: {left}. "
        "Reply here to renew. Thank you!"
    ),
    "default_sessions": "8",
    "default_per_week": "2",
    "default_amount": "480",
    "due_soon_days": "7",
    "deduct_absences": "0",
    "extend_for_sick": "1",
    "extend_for_absent": "0",
    "max_extension_weeks": "4",
    "booking_days": "14",
    "cancel_cutoff_hours": "2",
    "coaches_see_all": "0",
    "reminder_quiet_days": "3",
    "followup_days": "14",
    "followup_template": (
        "Hi {guardian}, it's {club}. We've missed {athlete} at training. Last session was {last_seen}. "
        "Is everything OK? Let us know if we can help or if you'd like to change sessions."
    ),
}


def get_settings(s) -> dict[str, str]:
    out = dict(DEFAULT_SETTINGS)
    for row in s.scalars(select(Setting)):
        if row.value is not None:
            out[row.key] = row.value
    return out


def set_setting(s, key: str, value) -> None:
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)
