"""Business rules: scoping, rosters, bookings/waitlist, attendance, health, fees, reminders, reports."""
from __future__ import annotations

import json
import math
import re
import secrets
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import delete, func, or_, select

from db import (Athlete, Attendance, Booking, Coach, HealthEvent, PaymentBlock, Reminder,
                TrainingSession, session_athletes, utcnow)

STATUS = {"P": "Present", "L": "Late", "A": "Absent", "S": "Sick", "E": "Excused"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
AUTO_SICK = "auto: sick cancellation"
HEALTH_REASON = "Health: "

FEE_LABEL = {"due": "Due now", "unpaid": "Unpaid", "none": "No block", "soon": "Due soon", "ok": "Up to date"}
FEE_COLOUR = {"due": "red", "unpaid": "orange", "none": "gray", "soon": "orange", "ok": "green"}
FEE_ORDER = {"due": 0, "unpaid": 1, "none": 2, "soon": 3, "ok": 4}


# --------------------------------------------------------------------------- time

def now_local(cfg: dict) -> datetime:
    try:
        return datetime.now(ZoneInfo(cfg.get("timezone") or "Asia/Qatar")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def today(cfg: dict) -> date:
    return now_local(cfg).date()


def session_start(sess: TrainingSession, d: date) -> datetime:
    h, m = (int(x) for x in sess.start_time.split(":"))
    return datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m)


def fmt_date(d: date | None) -> str:
    return d.strftime("%a %d %b") if d else "—"


def session_title(sess: TrainingSession) -> str:
    coach = f" · {sess.coach.name}" if sess.coach else ""
    where = f" · {sess.location}" if sess.location else ""
    return f"{sess.start_time}–{sess.end_time} · {sess.label}{coach}{where}"


# --------------------------------------------------------------------------- scope (enforced in queries)

def visible_session_ids(s, user: dict, cfg: dict) -> set[int] | None:
    """None = unrestricted. A set = exactly these sessions. Empty set = nothing."""
    if user.get("role") == "admin" or cfg.get("coaches_see_all") == "1":
        return None
    if not user.get("coach_id"):
        return set()
    return set(s.scalars(select(TrainingSession.id).where(TrainingSession.coach_id == user["coach_id"])))


def visible_athlete_ids(s, user: dict, cfg: dict) -> set[int] | None:
    sids = visible_session_ids(s, user, cfg)
    if sids is None:
        return None
    if not sids:
        return set()
    ids = set(s.scalars(select(session_athletes.c.athlete_id).where(session_athletes.c.session_id.in_(sids))))
    # One-off bookings (make-up, trial, casual) only grant visibility around the booked date,
    # so adding a child as a make-up doesn't hand a coach their profile indefinitely.
    d = today(cfg)
    ids |= set(s.scalars(select(Booking.athlete_id).where(
        Booking.session_id.in_(sids), Booking.status.in_(["booked", "pending"]),
        Booking.date >= d - timedelta(days=14), Booking.date <= d + timedelta(days=14))))
    return ids


def list_sessions(s, user: dict, cfg: dict, weekday: int | None = None, include_archived=False,
                  on: date | None = None) -> list[TrainingSession]:
    """on=date: only sessions that actually run that day (weekday + inside their term)."""
    q = select(TrainingSession)
    if not include_archived:
        q = q.where(TrainingSession.active.is_(True))
    if on is not None:
        weekday = on.weekday()
    if weekday is not None:
        q = q.where(TrainingSession.weekday == weekday)
    sids = visible_session_ids(s, user, cfg)
    if sids is not None:
        if not sids:
            return []
        q = q.where(TrainingSession.id.in_(sids))
    rows = list(s.scalars(q.order_by(TrainingSession.weekday, TrainingSession.start_time)))
    return [x for x in rows if x.runs_on(on)] if on is not None else rows


def create_series(s, label: str, weekdays: list[int], start_time: str, end_time: str, coach_id, capacity: int,
                  bookable: bool, starts_on: date, weeks: int | None, athlete_ids: list[int],
                  programme: str | None = None, location: str | None = None) -> list[TrainingSession]:
    """One weekly session per chosen weekday, all sharing a term (starts_on → starts_on + weeks).
    weeks=None/0 → ongoing. Each weekday's first run is its first occurrence on/after starts_on."""
    ends_on = starts_on + timedelta(weeks=weeks) - timedelta(days=1) if weeks else None
    series = "S-" + "".join(secrets.choice(_CODE_CHARS) for _ in range(6)) if len(weekdays) > 1 else None
    athletes = [s.get(Athlete, i) for i in athlete_ids]
    out = []
    for wd in sorted(set(weekdays)):
        x = TrainingSession(label=label, weekday=wd, start_time=start_time, end_time=end_time, coach_id=coach_id,
                            capacity=capacity, bookable=bookable, active=True, starts_on=starts_on, ends_on=ends_on,
                            series=series, programme=programme, location=location)
        x.athletes = list(athletes)
        s.add(x)
        out.append(x)
    s.flush()
    return out


def occurrences(sess: TrainingSession, start: date | None = None, end: date | None = None) -> int:
    """How many times a session runs between start and end (defaults: its own term)."""
    a = start or sess.starts_on
    b = end or sess.ends_on
    if a is None or b is None:
        return 0
    first = a + timedelta(days=(sess.weekday - a.weekday()) % 7)
    return 0 if first > b else (b - first).days // 7 + 1


def list_athletes(s, user: dict, cfg: dict, active_only=True) -> list[Athlete]:
    q = select(Athlete)
    if active_only:
        q = q.where(Athlete.active.is_(True))
    ids = visible_athlete_ids(s, user, cfg)
    if ids is not None:
        if not ids:
            return []
        q = q.where(Athlete.id.in_(ids))
    return list(s.scalars(q.order_by(Athlete.first_name, Athlete.last_name)))


def list_coaches(s, active_only=True) -> list[Coach]:
    q = select(Coach)
    if active_only:
        q = q.where(Coach.active.is_(True))
    return list(s.scalars(q.order_by(Coach.name)))


# --------------------------------------------------------------------------- roster & bookings

def day_roster(s, sess: TrainingSession, d: date) -> dict:
    """Roster for a date.
    coming      = regular group (coming by default) + confirmed casual/make-up/trial bookings
    sick        = regular members on the sick list for that date (not coming, place freed)
    pending     = pencilled in, not confirmed yet (no place held)
    waitlist / cancelled as booked."""
    bookings = {b.athlete_id: b for b in s.scalars(
        select(Booking).where(Booking.date == d, Booking.session_id == sess.id))}
    out = out_on(s, d, for_roster=True)
    attending, cancelled, waitlist, sick, pending = [], [], [], [], []
    regular_ids = set()
    if sess.runs_on(d):
        for a in sess.athletes:
            if not a.active:
                continue
            regular_ids.add(a.id)
            b = bookings.get(a.id)
            if b and b.status == "cancelled":
                cancelled.append((a, b))
            elif a.id in out and not (b and b.status == "booked"):
                sick.append((a, out[a.id]))
            else:
                attending.append((a, "regular"))
    for b in bookings.values():
        if b.athlete_id in regular_ids:
            continue
        if b.status == "booked":
            attending.append((b.athlete, b.kind))
        elif b.status == "pending":
            pending.append((b.athlete, b))
        elif b.status == "waitlist":
            waitlist.append((b.athlete, b))
        elif b.status == "cancelled":
            cancelled.append((b.athlete, b))
    attending.sort(key=lambda x: x[0].name.lower())
    waitlist.sort(key=lambda x: x[1].created_at)
    pending.sort(key=lambda x: x[1].created_at)
    return {"attending": attending, "cancelled": cancelled, "waitlist": waitlist, "sick": sick,
            "pending": pending, "capacity": sess.capacity, "spots": sess.capacity - len(attending)}


def confirm(s, athlete_id: int, session_id: int, d: date, override=False) -> str:
    """Confirm an unconfirmed booking. Takes a place, or joins the waitlist if full."""
    b = _get_booking(s, athlete_id, session_id, d)
    if b is None or b.status != "pending":
        return "not_pending"
    r = day_roster(s, s.get(TrainingSession, session_id), d)
    b.status = "booked" if (r["spots"] > 0 or override) else "waitlist"
    if b.status == "waitlist":
        b.created_at = utcnow()
    s.flush()
    return b.status


def _get_booking(s, athlete_id, session_id, d) -> Booking | None:
    return s.scalar(select(Booking).where(Booking.date == d, Booking.session_id == session_id,
                                          Booking.athlete_id == athlete_id))


def _is_regular(sess: TrainingSession, athlete_id: int) -> bool:
    return any(a.id == athlete_id for a in sess.athletes)


def book(s, athlete_id: int, session_id: int, d: date, kind="casual", source="staff", override=False,
         confirmed=True) -> str:
    """Returns 'booked' | 'waitlist' | 'pending' | 'already' | 'wrong_day'."""
    sess = s.get(TrainingSession, session_id)
    if not sess.runs_on(d):
        return "wrong_day"
    r = day_roster(s, sess, d)
    if any(a.id == athlete_id for a, _ in r["attending"]):
        return "already"
    b = _get_booking(s, athlete_id, session_id, d)
    if b is None:
        b = Booking(date=d, session_id=session_id, athlete_id=athlete_id, created_at=utcnow(), status="booked")
        s.add(b)
    b.kind = "regular" if _is_regular(sess, athlete_id) else kind
    b.source, b.reason = source, None
    if not confirmed:
        b.status = "pending"
        s.flush()
        return "pending"
    if r["spots"] <= 0 and not override:
        if b.status != "waitlist":
            b.created_at = utcnow()
        b.status = "waitlist"
        s.flush()
        return "waitlist"
    b.status = "booked"
    _clear_auto_sick(s, athlete_id, session_id, d)
    s.flush()
    return "booked"


def cancel(s, athlete_id: int, session_id: int, d: date, reason="", source="staff") -> list[str]:
    """Cancel a place. Sick/Injured pre-marks attendance as Sick. Returns names promoted off the waitlist."""
    sess = s.get(TrainingSession, session_id)
    b = _get_booking(s, athlete_id, session_id, d)
    if b is None:
        b = Booking(date=d, session_id=session_id, athlete_id=athlete_id, created_at=utcnow(),
                    kind="regular" if _is_regular(sess, athlete_id) else "casual")
        s.add(b)
    b.status, b.reason, b.source = "cancelled", (reason or "")[:200], source
    low = (reason or "").lower()
    existing = s.scalar(select(Attendance).where(Attendance.date == d, Attendance.session_id == session_id,
                                                 Attendance.athlete_id == athlete_id))
    if any(w in low for w in ("sick", "ill", "injur")) and existing is None:
        set_mark(s, d, session_id, athlete_id, "S", AUTO_SICK)  # never overwrite a mark a coach recorded
    s.flush()
    return promote_waitlist(s, session_id, d)


def promote_waitlist(s, session_id: int, d: date) -> list[str]:
    sess = s.get(TrainingSession, session_id)
    promoted = []
    while True:
        r = day_roster(s, sess, d)
        if r["spots"] <= 0 or not r["waitlist"]:
            break
        a, b = r["waitlist"][0]
        b.status = "booked"
        s.flush()
        promoted.append(a.name)
    return promoted


def remove_booking(s, athlete_id: int, session_id: int, d: date) -> None:
    """Leave waitlist / undo a casual booking / reinstate a regular's cancellation."""
    b = _get_booking(s, athlete_id, session_id, d)
    if b:
        s.delete(b)
    _clear_auto_sick(s, athlete_id, session_id, d)
    s.flush()


def reinstate(s, athlete_id: int, session_id: int, d: date, staff: bool = True) -> str:
    """Undo a cancellation. Staff may overbook; a parent whose place was given away joins the waitlist."""
    sess = s.get(TrainingSession, session_id)
    b = _get_booking(s, athlete_id, session_id, d)
    if b is None:
        return "none"
    if not staff and b.status == "cancelled" and day_roster(s, sess, d)["spots"] <= 0:
        if _is_regular(sess, athlete_id):
            return "full"
        b.status, b.reason, b.created_at = "waitlist", None, utcnow()
        s.flush()
        return "waitlist"
    if _is_regular(sess, athlete_id):
        s.delete(b)
    else:
        b.status, b.reason = "booked", None
    _clear_auto_sick(s, athlete_id, session_id, d)
    s.flush()
    return "booked"


def _clear_auto_sick(s, athlete_id, session_id, d):
    m = s.scalar(select(Attendance).where(Attendance.date == d, Attendance.session_id == session_id,
                                          Attendance.athlete_id == athlete_id))
    if m and m.recorded_by == AUTO_SICK:
        s.delete(m)


def athlete_sessions_on(s, a: Athlete, d: date) -> list[TrainingSession]:
    """Sessions this athlete is on for date d (regular group or an active booking)."""
    out = {x.id: x for x in a.sessions if x.active and x.runs_on(d)}
    for b in s.scalars(select(Booking).where(Booking.athlete_id == a.id, Booking.date == d,
                                             Booking.status.in_(["booked", "waitlist"]))):
        out[b.session_id] = b.session
    return sorted(out.values(), key=lambda x: x.start_time)


def athlete_place(s, a: Athlete, sess: TrainingSession, d: date) -> str:
    """'regular' | 'booked' | 'waitlist:N' | 'cancelled' | 'none'."""
    b = _get_booking(s, a.id, sess.id, d)
    if b and b.status == "cancelled":
        return "cancelled"
    if _is_regular(sess, a.id):
        return "regular"
    if b and b.status == "booked":
        return "booked"
    if b and b.status == "pending":
        return "pending"
    if b and b.status == "waitlist":
        r = day_roster(s, sess, d)
        pos = [x.id for x, _ in r["waitlist"]].index(a.id) + 1
        return f"waitlist:{pos}"
    return "none"


# --------------------------------------------------------------------------- attendance

def set_mark(s, d: date, session_id: int, athlete_id: int, status: str | None, by: str) -> None:
    m = s.scalar(select(Attendance).where(Attendance.date == d, Attendance.session_id == session_id,
                                          Attendance.athlete_id == athlete_id))
    if status is None:
        if m:
            s.delete(m)
    elif m:
        m.status, m.recorded_by, m.recorded_at = status, by, utcnow()
    else:
        s.add(Attendance(date=d, session_id=session_id, athlete_id=athlete_id, status=status,
                         recorded_by=by, recorded_at=utcnow()))
    if status in ("P", "L"):  # turning up confirms an unconfirmed booking
        b = _get_booking(s, athlete_id, session_id, d)
        if b and b.status == "pending":
            b.status = "booked"
    s.flush()


def marks_for(s, d: date, session_id: int) -> dict[int, str]:
    return {m.athlete_id: m.status for m in s.scalars(
        select(Attendance).where(Attendance.date == d, Attendance.session_id == session_id))}


def mark_rest_present(s, d: date, sess: TrainingSession, by: str) -> int:
    marks = marks_for(s, d, sess.id)
    out = out_on(s, d, for_roster=True)
    n = 0
    r = day_roster(s, sess, d)
    for a, _ in r["attending"] + r["sick"]:
        if a.id not in marks:
            set_mark(s, d, sess.id, a.id, "S" if a.id in out else "P", by)
            n += 1
    return n


# --------------------------------------------------------------------------- health

OPEN_ENDED_DAYS = 7  # no return date given: assume out for a week, then they reappear on lists


def out_on(s, d: date, for_roster: bool = False) -> dict[int, HealthEvent]:
    """Open health events on date d. The sick list shows all of them (overdue ones flagged).
    for_roster=True drops events past their expected return, or older than a week with no return
    date, so a forgotten entry never removes a child from sessions (or gives free sessions) forever."""
    evs = s.scalars(select(HealthEvent).where(
        HealthEvent.start_date <= d,
        or_(HealthEvent.returned_date.is_(None), HealthEvent.returned_date > d)))
    out = {}
    for e in evs:
        if for_roster:
            if e.expected_return and e.expected_return <= d:
                continue
            if not e.expected_return and (d - e.start_date).days >= OPEN_ENDED_DAYS:
                continue
        out[e.athlete_id] = e
    return out


def report_health(s, athlete_id: int, kind: str, description: str, start: date, expected_return: date | None,
                  by: str, notes: str = "", source="staff", cancel_sessions=True) -> tuple[HealthEvent, int]:
    """Log an illness/injury. Cancels the athlete's sessions from start until the day before
    expected return (or just the start day), marking them Sick and promoting the waitlist."""
    ev = HealthEvent(athlete_id=athlete_id, kind=(kind or "Other")[:20], description=(description or "")[:255],
                     start_date=start, expected_return=expected_return, notes=notes, reported_by=(by or "")[:100])
    s.add(ev)
    s.flush()
    n = 0
    a = s.get(Athlete, athlete_id)
    last = (expected_return - timedelta(days=1)) if expected_return and expected_return > start else start
    last = min(last, start + timedelta(days=28))
    d = start
    while d <= last:
        for sess in athlete_sessions_on(s, a, d):
            if cancel_sessions:
                cancel(s, athlete_id, sess.id, d, reason=f"{HEALTH_REASON}{kind}", source=source)
                n += 1
            else:
                promote_waitlist(s, sess.id, d)  # the sick list frees their place either way
        d += timedelta(days=1)
    return ev, n


def mark_returned(s, ev: HealthEvent, d: date) -> int:
    """Back from date d: close the event and undo health cancellations AFTER d (today's sick credit stays)."""
    ev.returned_date = d
    n = 0
    for b in s.scalars(select(Booking).where(Booking.athlete_id == ev.athlete_id, Booking.date > d,
                                             Booking.status == "cancelled",
                                             Booking.reason.like(f"{HEALTH_REASON}%"))).all():
        reinstate(s, b.athlete_id, b.session_id, b.date)
        n += 1
    s.flush()
    return n


# --------------------------------------------------------------------------- fees
#
# A payment block runs from start_date to an EFFECTIVE end date:
#   effective end = end_date + manual extend_days + auto-extension
#   auto-extension = the athlete's next N scheduled sessions after the end date, where N is the
#   number of credited misses (Sick/Excused when extend_for_sick=1, Absent when extend_for_absent=1),
#   capped at max_extension_weeks, and never past the day before the next block starts.
# Sessions available = sessions_purchased + credit_sessions. Used = Present + Late (+ Absent if deducted).

def _status_counts(s, athlete_id: int, start: date, end: date) -> dict[str, int]:
    rows = s.execute(select(Attendance.status, func.count()).where(
        Attendance.athlete_id == athlete_id, Attendance.date >= start, Attendance.date <= end
    ).group_by(Attendance.status)).all()
    return {k: v for k, v in rows}


class _Sched(list):
    """An athlete's weekly sessions; .on(d) = how many of them actually run on date d (respects terms)."""

    def on(self, d: date) -> int:
        return sum(1 for x in self if x.runs_on(d))


def _weekdays(a: Athlete) -> _Sched:
    return _Sched(x for x in a.sessions if x.active)


def _advance_sessions(weekdays: _Sched, after: date, n: int, per_week: int) -> date:
    """Date of the n-th scheduled session after `after`. Falls back to whole weeks with no schedule."""
    if n <= 0:
        return after
    if not weekdays:
        return after + timedelta(weeks=math.ceil(n / max(per_week, 1)))
    cur, left = after, n
    for _ in range(400):
        cur += timedelta(days=1)
        left -= weekdays.on(cur)
        if left <= 0:
            return cur
    return cur


def block_end(start: date, sessions: int, per_week: int) -> date:
    weeks = max(1, math.ceil(sessions / max(per_week, 1)))
    return start + timedelta(weeks=weeks) - timedelta(days=1)


def block_windows(s, a: Athlete, cfg: dict) -> list[dict]:
    deduct = cfg.get("deduct_absences") == "1"
    ext_sick = cfg.get("extend_for_sick", "1") == "1"
    ext_abs = cfg.get("extend_for_absent", "0") == "1" and not deduct
    max_weeks = cfg_int(cfg, "max_extension_weeks", 4)
    wd = _weekdays(a)
    blocks = sorted(a.blocks, key=lambda b: (b.start_date, b.id or 0))
    out = []
    carry_in = 0
    for i, b in enumerate(blocks):
        nxt = blocks[i + 1].start_date if i + 1 < len(blocks) else None
        cap = nxt - timedelta(days=1) if nxt else None
        manual_end = b.end_date + timedelta(days=b.extend_days or 0)
        if cap:
            manual_end = min(manual_end, cap)
        c0 = _status_counts(s, a.id, b.start_date, manual_end)
        credited = (c0.get("S", 0) + c0.get("E", 0) if ext_sick else 0) + (c0.get("A", 0) if ext_abs else 0)
        end = _advance_sessions(wd, manual_end, credited, b.sessions_per_week)
        end = min(end, manual_end + timedelta(weeks=max_weeks))
        carry_out = 0
        if cap and end > cap:
            # A prepaid block starts first: credited sessions that don't fit roll into it instead of being lost
            fit = _sessions_between(wd, manual_end, cap, b.sessions_per_week)
            carry_out = max(min(credited, _sessions_between(wd, manual_end, end, b.sessions_per_week)) - fit, 0)
            end = cap
        c = _status_counts(s, a.id, b.start_date, end)
        attended = c.get("P", 0) + c.get("L", 0)
        used = attended + (c.get("A", 0) if deduct else 0)
        purchased = b.sessions_purchased + (b.credit_sessions or 0) + carry_in
        out.append({"block": b, "start": b.start_date, "end": end, "base_end": b.end_date,
                    "auto_ext_days": (end - manual_end).days, "manual_ext_days": b.extend_days or 0,
                    "credited": credited, "carried_in": carry_in, "carried_out": carry_out,
                    "attended": attended, "absent": c.get("A", 0),
                    "sick": c.get("S", 0) + c.get("E", 0), "used": used, "purchased": purchased,
                    "left": purchased - used})
        carry_in = carry_out
    return out


def _sessions_between(weekdays: _Sched, after: date, until: date, per_week: int) -> int:
    """Scheduled sessions in (after, until]."""
    if until <= after:
        return 0
    if not weekdays:
        return math.ceil((until - after).days / 7 * max(per_week, 1))
    n, cur = 0, after
    while cur < until:
        cur += timedelta(days=1)
        n += weekdays.on(cur)
    return n


def cfg_int(cfg: dict, key: str, default: int) -> int:
    try:
        return int(str(cfg.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def athlete_finance(s, a: Athlete, d: date, cfg: dict) -> dict:
    window = cfg_int(cfg, "due_soon_days", 7)
    wins = block_windows(s, a, cfg)
    started = [w for w in wins if w["start"] <= d <= w["end"]]
    cur = started[-1] if started else None
    future = [w for w in wins if w["start"] > d]
    past = [w for w in wins if w["end"] < d]
    out = {"current": cur["block"] if cur else None, "window": cur, "last": past[-1] if past else None, "future": [w["block"] for w in future],
           "windows": wins, "status": "ok", "used": 0, "purchased": 0, "left": None,
           "attended": 0, "absent": 0, "sick": 0, "next_due": None}
    if cur:
        out.update({k: cur[k] for k in ("used", "purchased", "left", "attended", "absent", "sick")})
    out["next_due"] = next_payment_date(s, a, d, cur, future)

    if not wins:
        out["status"] = "none"
    elif cur is None and not future:
        out["status"] = "due"
    elif cur is not None and cur["left"] <= 0 and not future:
        out["status"] = "due"
    elif (cur and not cur["block"].paid) or any(not w["block"].paid for w in future):
        out["status"] = "unpaid"
    elif cur and not future and (cur["left"] <= 1 or (cur["end"] - d).days <= window):
        out["status"] = "soon"
    else:
        out["status"] = "ok"
    return out


def next_payment_date(s, a: Athlete, d: date, cur: dict | None, future: list[dict]) -> date | None:
    """First scheduled session after the paid sessions run out, or the day after the paid period
    ends, whichever is sooner. Prepaid future blocks count."""
    active = ([cur] if cur else []) + future
    if not active:
        return d if a.blocks else None
    left = max(cur["left"], 0) if cur else 0
    left += sum(w["purchased"] for w in future)
    period_end_next = max(w["end"] for w in active) + timedelta(days=1)
    wd = _weekdays(a)
    if not wd:
        return period_end_next
    marked_today = (s.scalar(select(func.count()).select_from(Attendance).where(
        Attendance.athlete_id == a.id, Attendance.date == d)) or 0) > 0
    cur_d = d + timedelta(days=1) if marked_today else d
    for _ in range(400):
        n = wd.on(cur_d)
        if n:
            if left <= 0:
                return min(cur_d, period_end_next)
            left -= n
        cur_d += timedelta(days=1)
    return period_end_next


def record_payment(s, athlete_id: int, pay_date: date, sessions: int, per_week: int, amount: float,
                   method: str, stack: bool, cfg: dict, end_override: date | None = None, paid=True,
                   notes="", family_ref: str | None = None) -> PaymentBlock:
    """New block. stack=True starts it the day after the current block's effective end."""
    a = s.get(Athlete, athlete_id)
    start = pay_date
    if stack:
        live = [w for w in block_windows(s, a, cfg) if w["end"] >= pay_date]
        if live:
            start = max(w["end"] for w in live) + timedelta(days=1)
    end = end_override or block_end(start, sessions, per_week)
    blk = PaymentBlock(athlete_id=athlete_id, start_date=start, end_date=end, sessions_per_week=per_week,
                       sessions_purchased=sessions, amount=amount, paid=paid, credit_sessions=0, extend_days=0,
                       paid_date=pay_date if paid else None, method=method, notes=notes, family_ref=family_ref)
    s.add(blk)
    s.flush()
    s.refresh(a)
    return blk


def record_family_payment(s, items: list[dict], pay_date: date, method: str, stack: bool, cfg: dict,
                          paid=True, notes="") -> tuple[str, list[PaymentBlock]]:
    """One payment covering several children. items = [{athlete_id, sessions, per_week, amount}].
    Each child gets their own block; all share a family_ref so the payment reads as one."""
    ref = "FP-" + "".join(secrets.choice(_CODE_CHARS) for _ in range(6))
    blocks = [record_payment(s, it["athlete_id"], pay_date, it["sessions"], it["per_week"], it["amount"],
                             method, stack, cfg, paid=paid, notes=notes, family_ref=ref) for it in items]
    return ref, blocks


def family_key(a: Athlete) -> str:
    return (a.family_code or f"solo-{a.id}").upper()


def family_reminder_text(children: list[tuple[Athlete, dict]], cfg: dict) -> str:
    """One message for a family listing each child that needs renewing."""
    if len(children) == 1:
        return reminder_text(children[0][0], children[0][1], cfg)
    guardian = next((a.guardian_name for a, _ in children if a.guardian_name), "") or "there"
    lines = [f"Hi {guardian.split(' ')[0]}, a quick note from {cfg['club_name']} about your family's sessions:"]
    for a, f in children:
        lines.append(f"• {a.first_name}: {FEE_LABEL[f['status']].lower()}, "
                     f"{max(f['left'] or 0, 0)} session(s) left, next payment {fmt_date(f['next_due'])}")
    lines.append("You can renew everyone in one payment. Reply here and we'll sort it. Thank you!")
    return "\n".join(lines)


def balances_df(s, athletes: list[Athlete], d: date, cfg: dict) -> pd.DataFrame:
    last_rem = dict(s.execute(select(Reminder.athlete_id, func.max(Reminder.sent_on)).group_by(Reminder.athlete_id)).all())
    rows = []
    for a in athletes:
        f = athlete_finance(s, a, d, cfg)
        w = f["window"] or f["last"]
        rows.append({
            "id": a.id, "Athlete": a.name, "Category": a.programme or "", "Guardian": a.guardian_name or "",
            "Phone": a.phone or "",
            "Status": FEE_LABEL[f["status"]], "_order": FEE_ORDER[f["status"]], "_status": f["status"],
            "Block start": w["start"] if w else None,
            "Block end": w["end"] if w else None,
            "Extended (days)": (w["auto_ext_days"] + w["manual_ext_days"]) if w else None,
            "Paid for": w["purchased"] if w else None, "Attended": w["attended"] if w else None,
            "Absent": w["absent"] if w else None, "Sick/Exc.": w["sick"] if w else None,
            "Used": w["used"] if w else None, "Left": w["left"] if w else None,
            "Next payment": f["next_due"],
            "Prepaid blocks": len(f["future"]),
            "Last reminder": last_rem.get(a.id).date() if last_rem.get(a.id) else None,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["_order", "Next payment"], na_position="last")
    return df


# --------------------------------------------------------------------------- reminders

def intl_phone(phone: str | None, cc: str) -> str:
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith(cc) and len(digits) > 8:
        return digits
    return cc + digits.lstrip("0")


def reminder_text(a: Athlete, fin: dict, cfg: dict) -> str:
    status_text = {"due": "has run out", "unpaid": "is awaiting payment", "soon": "is nearly used",
                   "none": "has not been set up yet", "ok": "is up to date"}[fin["status"]]
    try:
        return cfg["reminder_template"].format(
            guardian=(a.guardian_name or "there").split(" ")[0], athlete=a.first_name, club=cfg["club_name"],
            due_date=fmt_date(fin["next_due"]), left=max(fin["left"] or 0, 0), status_text=status_text)
    except (KeyError, IndexError, ValueError):
        return cfg["reminder_template"]


def contact_links(a: Athlete, msg: str, cfg: dict) -> dict[str, str]:
    num = intl_phone(a.phone, cfg.get("country_code", "974"))
    out = {}
    if num:
        out["WhatsApp"] = f"https://wa.me/{num}?text={quote(msg)}"
        out["SMS"] = f"sms:+{num}?&body={quote(msg)}"
    if a.email:
        out["Email"] = f"mailto:{a.email}?subject={quote(cfg['club_name'] + ' – payment reminder')}&body={quote(msg)}"
    return out


# --------------------------------------------------------------------------- payment requests

def expected_item(a: Athlete, cfg: dict) -> dict:
    """What this athlete's next block will most likely be: same as their last block, else the defaults."""
    last = max(a.blocks, key=lambda b: (b.start_date, b.id or 0)) if a.blocks else None
    per_week = max(1, len([x for x in a.sessions if x.active]) or cfg_int(cfg, "default_per_week", 2))
    return {"athlete_id": a.id,
            "sessions": last.sessions_purchased if last else cfg_int(cfg, "default_sessions", 8),
            "per_week": last.sessions_per_week if last else per_week,
            "amount": float(last.amount if last and last.amount else cfg.get("default_amount") or 0)}


def open_request(s, family_code: str | None, athlete_id: int | None = None):
    from db import PaymentRequest
    q = select(PaymentRequest).where(PaymentRequest.status.in_(["open", "link_sent"]))
    if family_code:
        q = q.where(PaymentRequest.family_code == family_code)
    rows = s.scalars(q.order_by(PaymentRequest.created_at.desc())).all()
    if athlete_id is not None:
        rows = [r for r in rows if athlete_id in {i["athlete_id"] for i in json.loads(r.items)}]
    return rows[0] if rows else None


def upcoming(s, athletes: list[Athlete], d: date, cfg: dict, days: int) -> list[dict]:
    """Families with a payment due within `days` (or overdue), with the expected amount per child."""
    fams: dict[str, list] = {}
    for a in athletes:
        f = athlete_finance(s, a, d, cfg)
        due = f["next_due"]
        if f["status"] == "none" or due is None or due > d + timedelta(days=days):
            continue
        fams.setdefault(family_key(a), []).append((a, f, expected_item(a, cfg)))
    out = []
    for key, kids in fams.items():
        lead = kids[0][0]
        due = min(f["next_due"] for _, f, _ in kids)
        out.append({"key": key, "family_code": lead.family_code, "lead": lead, "kids": kids, "due": due,
                    "overdue": due < d, "amount": sum(i["amount"] for *_, i in kids),
                    "request": open_request(s, lead.family_code, None if lead.family_code else lead.id)})
    return sorted(out, key=lambda r: r["due"])


def create_request(s, kids: list[tuple[Athlete, dict]], due: date | None, cfg: dict, by: str):
    """kids = [(athlete, item)]. Returns the PaymentRequest."""
    from db import PaymentRequest
    lead = kids[0][0]
    ref = "TNT-" + "".join(secrets.choice(_CODE_CHARS) for _ in range(6))
    req = PaymentRequest(reference=ref, family_code=lead.family_code, payer_name=lead.guardian_name or lead.name,
                         payer_phone=lead.phone, payer_email=lead.email,
                         items=json.dumps([i for _, i in kids]), amount=sum(i["amount"] for _, i in kids),
                         currency=(cfg.get("currency") or "QAR")[:3], due_date=due, created_by=by, status="open")
    s.add(req)
    s.flush()
    return req


def settle_request(s, req, pay_date: date, method: str, cfg: dict, stack: bool = True) -> list[PaymentBlock]:
    """Mark a request paid: every child gets their block (sharing one family reference)."""
    if req.status == "paid":
        return []
    items = json.loads(req.items)
    if len(items) > 1:
        ref, blocks = record_family_payment(s, items, pay_date, method, stack, cfg, notes=f"Request {req.reference}")
    else:
        it = items[0]
        ref = req.reference
        blocks = [record_payment(s, it["athlete_id"], pay_date, it["sessions"], it["per_week"], it["amount"],
                                 method, stack, cfg, notes=f"Request {req.reference}", family_ref=ref)]
    req.status, req.paid_at, req.paid_method, req.family_ref = "paid", utcnow(), method, ref
    s.flush()
    return blocks


# --------------------------------------------------------------------------- follow-up

def followups(s, athletes: list[Athlete], d: date, cfg: dict) -> list[dict]:
    """Active athletes with a weekly group who haven't attended (P/L) for followup_days,
    excluding anyone on the sick list and anyone contacted within that window."""
    days = cfg_int(cfg, "followup_days", 14)
    cutoff = d - timedelta(days=days)
    ids = [a.id for a in athletes]
    if not ids:
        return []
    last = dict(s.execute(select(Attendance.athlete_id, func.max(Attendance.date)).where(
        Attendance.athlete_id.in_(ids), Attendance.status.in_(["P", "L"])).group_by(Attendance.athlete_id)).all())
    contacted = dict(s.execute(select(Reminder.athlete_id, func.max(Reminder.sent_on)).where(
        Reminder.athlete_id.in_(ids), Reminder.channel.like("Follow-up%")).group_by(Reminder.athlete_id)).all())
    out_now = out_on(s, d)
    rows = []
    for a in athletes:
        if not a.active or not any(x.active for x in a.sessions) or a.id in out_now:
            continue
        seen = last.get(a.id)
        since = seen or a.start_date
        if since is None or since > cutoff:
            continue
        c = contacted.get(a.id)
        if c and c.date() > cutoff:
            continue
        rows.append({"athlete": a, "last_seen": seen, "days": (d - since).days})
    return sorted(rows, key=lambda r: -r["days"])


def followup_text(a: Athlete, last_seen: date | None, cfg: dict) -> str:
    try:
        return cfg["followup_template"].format(
            guardian=(a.guardian_name or "there").split(" ")[0], athlete=a.first_name, club=cfg["club_name"],
            last_seen=fmt_date(last_seen) if last_seen else "a while ago")
    except (KeyError, IndexError, ValueError):
        return cfg["followup_template"]


# --------------------------------------------------------------------------- reports

def attendance_df(s, start: date, end: date, session_ids: set[int] | None = None) -> pd.DataFrame:
    q = (select(Attendance.date, TrainingSession.label, TrainingSession.start_time, Coach.name,
                Athlete.first_name, Athlete.last_name, Attendance.status, Attendance.recorded_by)
         .join(TrainingSession, TrainingSession.id == Attendance.session_id)
         .join(Athlete, Athlete.id == Attendance.athlete_id)
         .outerjoin(Coach, Coach.id == TrainingSession.coach_id)
         .where(Attendance.date >= start, Attendance.date <= end))
    if session_ids is not None:
        if not session_ids:
            return pd.DataFrame(columns=["Date", "Session", "Coach", "Athlete", "Status", "Recorded by"])
        q = q.where(Attendance.session_id.in_(session_ids))
    rows = s.execute(q).all()
    df = pd.DataFrame([{
        "Date": r[0], "Session": f"{r[2]} {r[1]}", "Coach": r[3] or "—", "Athlete": f"{r[4]} {r[5]}".strip(),
        "Status": STATUS.get(r[6], r[6]), "Code": r[6], "Recorded by": r[7] or ""} for r in rows])
    return df.sort_values(["Date", "Session", "Athlete"]) if not df.empty else df


def sessions_attended(s, athletes: list[Athlete], start: date, end: date,
                      session_ids: set[int] | None = None) -> pd.DataFrame:
    """One row per athlete (including those with zero) for marks between start and end inclusive.
    Attended = Present + Late. Attendance % = attended ÷ (attended + absent); sick/excused excluded."""
    ids = [a.id for a in athletes]
    if not ids:
        return pd.DataFrame()
    q = (select(Attendance.athlete_id, Attendance.status, func.count(), func.max(Attendance.date))
         .where(Attendance.athlete_id.in_(ids), Attendance.date >= start, Attendance.date <= end)
         .group_by(Attendance.athlete_id, Attendance.status))
    if session_ids is not None:
        q = q.where(Attendance.session_id.in_(session_ids or {-1}))
    counts: dict[int, dict] = {}
    last_in: dict[int, date] = {}
    for aid, status, n, last in s.execute(q).all():
        counts.setdefault(aid, {})[status] = n
        if status in ("P", "L"):
            last_in[aid] = max(last_in.get(aid, last), last)
    dq = (select(Attendance.athlete_id, Attendance.date)
          .where(Attendance.athlete_id.in_(ids), Attendance.date >= start, Attendance.date <= end,
                 Attendance.status.in_(["P", "L"])).order_by(Attendance.date))
    if session_ids is not None:
        dq = dq.where(Attendance.session_id.in_(session_ids or {-1}))
    dates_in: dict[int, list[date]] = {}
    for aid, dd in s.execute(dq).all():
        dates_in.setdefault(aid, []).append(dd)
    rows = []
    for a in athletes:
        c = counts.get(a.id, {})
        attended = c.get("P", 0) + c.get("L", 0)
        absent = c.get("A", 0)
        rows.append({
            "Athlete": a.name, "Category": a.programme or "", "Groups": ", ".join(
                f"{WEEKDAYS[x.weekday][:3]} {x.label}" for x in a.sessions if x.active),
            "Attended": attended, "Present": c.get("P", 0), "Late": c.get("L", 0), "Absent": absent,
            "Sick": c.get("S", 0), "Excused": c.get("E", 0),
            "Attendance %": round(100 * attended / (attended + absent), 1) if attended + absent else None,
            "Last attended": last_in.get(a.id),
            "Dates attended": ", ".join(x.strftime("%a %d %b") for x in dates_in.get(a.id, []))})
    return pd.DataFrame(rows).sort_values(["Attended", "Athlete"], ascending=[False, True])


def register(df: pd.DataFrame) -> pd.DataFrame:
    """Roll-sheet grid from attendance_df rows: athletes × dates, cell = mark code(s) that day."""
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(["Athlete", "Date"])["Code"].apply(lambda x: "/".join(sorted(x))).unstack("Date").fillna("")
    g = g.reindex(sorted(g.columns), axis=1)
    g.columns = [c.strftime("%a %d %b") for c in g.columns]
    att = df[df["Code"].isin(["P", "L"])].groupby("Athlete").size()
    g.insert(0, "Attended", att.reindex(g.index).fillna(0).astype(int))
    return g.reset_index().sort_values("Athlete")


def summarise(df: pd.DataFrame, by: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    t = pd.crosstab(df[by], df["Code"]).reindex(columns=list(STATUS), fill_value=0)
    t.columns = [STATUS[c] for c in t.columns]
    t["Sessions"] = t.sum(axis=1)
    denom = t["Present"] + t["Late"] + t["Absent"]
    t["Attendance %"] = (100 * (t["Present"] + t["Late"]) / denom.where(denom > 0)).round(1)
    return t.reset_index().sort_values("Attendance %", ascending=False, na_position="last")


def attendance_rate(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    c = df["Code"].value_counts()
    denom = c.get("P", 0) + c.get("L", 0) + c.get("A", 0)
    return round(100 * (c.get("P", 0) + c.get("L", 0)) / denom, 1) if denom else None


# --------------------------------------------------------------------------- age categories

def setting_list(cfg: dict, key: str) -> list[str]:
    """Admin-editable lists in Settings (stored one per line; commas also accepted)."""
    raw = (cfg.get(key) or "").replace("\r", "")
    parts = raw.split("\n") if "\n" in raw else raw.split(",")
    return [p.strip() for p in parts if p.strip()]


def categories(cfg: dict) -> list[str]:
    """Athlete age categories, e.g. 3–6 years / 7–12 years / 13+ years."""
    return setting_list(cfg, "categories")


def _band(label: str) -> tuple[int, int] | None:
    """'3–6 years' → (3, 6); '13+ years' → (13, 200); anything without numbers → None."""
    nums = [int(n) for n in re.findall(r"\d+", label)]
    if "+" in label and nums:
        return nums[0], 200
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None


def category_for_age(age: int | None, cfg: dict) -> str | None:
    if age is None:
        return None
    for c in categories(cfg):
        b = _band(c)
        if b and b[0] <= age <= b[1]:
            return c
    return None


# --------------------------------------------------------------------------- misc

_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_family_code(s) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_CHARS) for _ in range(6))
        if not s.scalar(select(Athlete.id).where(Athlete.family_code == code)):
            return code


def family_athletes(s, code: str) -> list[Athlete]:
    code = (code or "").strip().upper()
    if len(code) < 4:
        return []
    return list(s.scalars(select(Athlete).where(func.upper(Athlete.family_code) == code,
                                                Athlete.active.is_(True)).order_by(Athlete.first_name)))
