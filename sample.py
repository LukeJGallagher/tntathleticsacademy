"""Fictional demo data for an athletics academy. Every row is flagged is_sample=True."""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select

import auth
import logic
from db import (Athlete, Attendance, AuditLog, Booking, Coach, HealthEvent, PaymentBlock, PaymentRequest, Reminder,
                TrainingSession, User, session_athletes, utcnow)

DEMO_PASSWORD = "demo1234"

# weekday: 0=Mon ... 6=Sun. Qatar week: Sun–Thu + Saturday.
SESSIONS = [
    # key, label, weekday, start, end, coach, capacity, programme
    ("tots_sun", "ToTs (4–6)", 6, "16:00", "16:45", "Nadia", 10, "ToTs"),
    ("youth_sun", "Youth Athletics (7–12)", 6, "17:00", "18:15", "Tom", 16, "Youth"),
    ("teen_sun", "Teen & Adult (13+)", 6, "18:30", "20:00", "Ahmed", 18, "Teen & Adult"),
    ("tots_tue", "ToTs (4–6)", 1, "16:00", "16:45", "Nadia", 10, "ToTs"),
    ("youth_tue", "Youth Athletics (7–12)", 1, "17:00", "18:15", "Tom", 16, "Youth"),
    ("teen_tue", "Teen & Adult (13+)", 1, "18:30", "20:00", "Ahmed", 18, "Teen & Adult"),
    ("speed_thu", "Sports Speed (max 6)", 3, "17:00", "18:00", "Tom", 6, "Sports Speed"),
    ("weekend_sat", "Saturday Squad (9+)", 5, "08:00", "09:30", "Ahmed", 20, "Teen & Adult"),
]

FIRST_M = ["Omar", "Yousef", "Liam", "Adam", "Hamad", "Noah", "Ali", "Ethan", "Khalid", "Lucas", "Faisal",
           "Zayed", "Ryan", "Saif", "Jacob", "Rayan", "Tamim", "Leo"]
FIRST_F = ["Mariam", "Sara", "Aisha", "Emily", "Noor", "Layla", "Hessa", "Mia", "Fatima", "Chloe", "Reem",
           "Grace", "Dana", "Lina", "Amira", "Zara", "Maha", "Ella"]
LAST = ["Al-Kuwari", "Haddad", "Thompson", "Al-Marri", "Nasser", "O'Brien", "Rahman", "Silva", "Al-Thani",
        "Patel", "Khoury", "Walker", "Mansour", "Ibrahim", "Fernandes", "Al-Sulaiti", "Murphy", "Saleh"]
SCHOOLS = ["Doha College", "Qatar Academy", "ACS Doha", "Park House", "DESS", "Al Bayan School",
           "Compass International", "Newton British"]
EVENTS = ["Sprints", "Sprints, Long jump", "Middle distance", "Hurdles", "Throws", "High jump", "Sprints, Relay",
          "Long jump, Triple jump", "Cross country", "Multi-events"]
ROSTER_PLAN = [  # programme, group keys, count, age range
    ("ToTs", ["tots_sun", "tots_tue"], 10, (4, 6)),
    ("Youth", ["youth_sun", "youth_tue"], 14, (7, 12)),
    ("Teen & Adult", ["teen_sun", "teen_tue", "weekend_sat"], 10, (13, 17)),
    ("Sports Speed", ["speed_thu"], 4, (10, 16)),
]


def has_sample(s) -> bool:
    return s.scalar(select(Athlete.id).where(Athlete.is_sample.is_(True)).limit(1)) is not None


def remove_sample(s, keep_logins: bool = False) -> None:
    sample_sessions = select(TrainingSession.id).where(TrainingSession.is_sample.is_(True))
    sample_athletes = select(Athlete.id).where(Athlete.is_sample.is_(True))
    for model in (Attendance, Booking, Reminder, HealthEvent, PaymentBlock, PaymentRequest):
        s.execute(delete(model).where(model.is_sample.is_(True)))
    s.execute(delete(session_athletes).where(session_athletes.c.session_id.in_(sample_sessions)))
    s.execute(delete(session_athletes).where(session_athletes.c.athlete_id.in_(sample_athletes)))
    s.execute(delete(TrainingSession).where(TrainingSession.is_sample.is_(True)))
    s.execute(delete(Athlete).where(Athlete.is_sample.is_(True)))
    if not keep_logins:
        s.execute(delete(User).where(User.is_sample.is_(True)))
        s.execute(delete(Coach).where(Coach.is_sample.is_(True)))
    s.flush()
    s.expire_all()


def load_sample(s, cfg: dict) -> None:
    rng = random.Random(42)
    today = logic.today(cfg)

    coaches = {}
    for i, n in enumerate(["Nadia", "Tom", "Ahmed"]):
        c = s.scalar(select(Coach).where(Coach.name == f"Coach {n}", Coach.is_sample.is_(True)))
        coaches[n] = c or Coach(name=f"Coach {n}", phone=f"5500{1000 + i}", email=f"{n.lower()}@example.com",
                                is_sample=True)
    s.add_all(coaches.values())
    s.flush()

    if not s.scalar(select(User.id).where(User.username == "admin")):
        auth.create_user(s, "admin", "Academy Admin", "admin", DEMO_PASSWORD, is_sample=True)
    for n, c in coaches.items():
        if not s.scalar(select(User.id).where(User.username == n.lower())):
            auth.create_user(s, n.lower(), c.name, "coach", DEMO_PASSWORD, coach_id=c.id, is_sample=True)

    sess = {}
    for key, label, wd, st_, en, coach, cap, _ in SESSIONS:
        sess[key] = TrainingSession(label=label, weekday=wd, start_time=st_, end_time=en,
                                    coach_id=coaches[coach].id, capacity=cap, bookable=True, is_sample=True)
    s.add_all(sess.values())
    s.flush()

    # Athletes, grouped into families (some siblings share a family code)
    athletes: list[Athlete] = []
    fixed_codes = ["DEMO01", "DEMO02"]
    family_code = None
    family_left = 0
    family_last = family_guardian = family_phone = None
    used_names = set()
    for programme, keys, count, (lo, hi) in ROSTER_PLAN:
        for _ in range(count):
            if family_left <= 0:
                family_code = fixed_codes.pop(0) if fixed_codes else "".join(rng.choice(logic._CODE_CHARS) for _ in range(6))
                family_left = rng.choice([1, 1, 1, 2, 2, 3])
                family_last = rng.choice(LAST)
                g_first = rng.choice(FIRST_F + FIRST_M)
                family_guardian = f"{g_first} {family_last}"
                family_phone = f"{rng.choice(['3', '5', '6', '7'])}{rng.randint(1000000, 9999999)}"
            family_left -= 1
            female = rng.random() < 0.5
            while True:
                first = rng.choice(FIRST_F if female else FIRST_M)
                if (first, family_last) not in used_names:
                    used_names.add((first, family_last))
                    break
            age = rng.randint(lo, hi)
            dob = today - timedelta(days=365 * age + rng.randint(0, 360))
            a = Athlete(
                first_name=first, last_name=family_last, dob=dob, gender="Female" if female else "Male",
                school=rng.choice(SCHOOLS), guardian_name=family_guardian, phone=family_phone,
                email=f"{family_guardian.split()[0].lower()}.{family_last.lower().replace(' ', '').replace('-', '').replace(chr(39), '')}@example.com",
                start_date=today - timedelta(days=rng.randint(30, 700)), active=True, family_code=family_code,
                programme=programme, event_group=None if programme == "ToTs" else rng.choice(EVENTS),
                emergency_contact=f"{family_guardian} · {family_phone}",
                medical_notes=rng.choice([None] * 7 + ["Asthma – inhaler in bag", "Nut allergy (EpiPen)",
                                                       "Previous ankle sprain (R)"]),
                is_sample=True)
            a.sessions = [sess[k] for k in keys]
            athletes.append(a)
    s.add_all(athletes)
    s.flush()

    # Attendance history: last 6 weeks up to yesterday. Two athletes drop off 3 weeks ago (follow-up demo).
    ghosts = {athletes[4].id, athletes[17].id}
    for back in range(42, 0, -1):
        d = today - timedelta(days=back)
        for x in sess.values():
            if x.weekday != d.weekday():
                continue
            for a in x.athletes:
                r = rng.random()
                code = "P" if r < 0.80 else "L" if r < 0.86 else "A" if r < 0.93 else "S" if r < 0.97 else "E"
                if a.id in ghosts and back <= 21:
                    code = "A"
                s.add(Attendance(date=d, session_id=x.id, athlete_id=a.id, status=code,
                                 recorded_by=x.coach.name if x.coach else "Coach", is_sample=True,
                                 recorded_at=datetime.combine(d, datetime.min.time()).replace(hour=19)))
    s.flush()

    # Payment blocks: varied so every fee status shows up
    for i, a in enumerate(athletes):
        per_week = len(a.sessions)
        sessions = 8 if per_week <= 2 else 12
        amount = {"ToTs": 360, "Youth": 480, "Teen & Adult": 600, "Sports Speed": 400}[a.programme]
        pattern = i % 9
        if pattern == 0:
            continue  # no block yet
        weeks_ago = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 3, 7: 4, 8: 2}[pattern]
        start = today - timedelta(weeks=weeks_ago, days=rng.randint(0, 3))
        blk = PaymentBlock(athlete_id=a.id, start_date=start, end_date=logic.block_end(start, sessions, per_week),
                           sessions_per_week=per_week, sessions_purchased=sessions, amount=amount,
                           paid=pattern != 6, paid_date=start if pattern != 6 else None,
                           method=rng.choice(["Cash", "Card", "Bank transfer"]), credit_sessions=0, extend_days=0,
                           is_sample=True)
        s.add(blk)
        if pattern == 8:  # prepaid next block
            nstart = blk.end_date + timedelta(days=1)
            s.add(PaymentBlock(athlete_id=a.id, start_date=nstart, end_date=logic.block_end(nstart, sessions, per_week),
                               sessions_per_week=per_week, sessions_purchased=sessions, amount=amount, paid=True,
                               paid_date=today - timedelta(days=2), method="Bank transfer", credit_sessions=0,
                               extend_days=0, is_sample=True))
    s.flush()

    # Health: one current illness, one overdue injury, two historic
    youth = [a for a in athletes if a.programme == "Youth"]
    teen = [a for a in athletes if a.programme == "Teen & Adult"]
    s.add(HealthEvent(athlete_id=youth[3].id, kind="Illness", description="Flu", start_date=today - timedelta(days=2),
                      expected_return=today + timedelta(days=3), reported_by="Parent", is_sample=True))
    s.add(HealthEvent(athlete_id=teen[1].id, kind="Injury", description="Hamstring strain (L)",
                      start_date=today - timedelta(days=12), expected_return=today - timedelta(days=2),
                      reported_by="Coach Ahmed", notes="Physio cleared light jogging only", is_sample=True))
    s.add(HealthEvent(athlete_id=youth[6].id, kind="Injury", description="Shin soreness",
                      start_date=today - timedelta(days=30), expected_return=today - timedelta(days=23),
                      returned_date=today - timedelta(days=22), reported_by="Coach Tom", is_sample=True))
    s.add(HealthEvent(athlete_id=teen[4].id, kind="Illness", description="Cold",
                      start_date=today - timedelta(days=18), expected_return=today - timedelta(days=15),
                      returned_date=today - timedelta(days=15), reported_by="Parent", is_sample=True))
    s.flush()

    # Upcoming bookings: casual, trial, cancellation and a waitlist on the full Sports Speed group
    def next_wd(wd: int, after: date = today) -> date:
        return after + timedelta(days=(wd - after.weekday()) % 7 or 7)

    sat = next_wd(5)
    thu = next_wd(3)
    sun = next_wd(6)
    s.add(Booking(date=sat, session_id=sess["weekend_sat"].id, athlete_id=youth[0].id, status="booked",
                  kind="casual", source="parent", is_sample=True))
    s.add(Booking(date=sun, session_id=sess["youth_sun"].id, athlete_id=youth[9].id, status="cancelled",
                  kind="regular", source="parent", reason="Family travel", is_sample=True))
    # Sports Speed: 4 regulars, capacity 6 → 2 casuals fill it, then 1 waitlist
    for k, a in enumerate([youth[1], youth[2], teen[2]]):
        s.add(Booking(date=thu, session_id=sess["speed_thu"].id, athlete_id=a.id,
                      status="booked" if k < 2 else "waitlist", kind="casual", source="parent",
                      created_at=utcnow() + timedelta(seconds=k), is_sample=True))
    s.add(Reminder(athlete_id=athletes[1].id, sent_on=utcnow() - timedelta(days=1), due_date=today + timedelta(days=3),
                   channel="WhatsApp", message="Renewal reminder", sent_by="Academy Admin", is_sample=True))
    s.flush()

    # Two open payment requests (one family, one single) so Requests / Import have something to show
    s.refresh(athletes[0])
    fam = [a for a in athletes if a.family_code == athletes[0].family_code]
    for kids, ref in ((fam, "TNT-DEMO01"), ([athletes[12]], "TNT-DEMO02")):
        items = [logic.expected_item(a, cfg) for a in kids]
        s.add(PaymentRequest(reference=ref, family_code=kids[0].family_code, payer_name=kids[0].guardian_name,
                             payer_phone=kids[0].phone, payer_email=kids[0].email, items=json.dumps(items),
                             amount=sum(i["amount"] for i in items), currency=cfg.get("currency", "QAR")[:3],
                             due_date=today + timedelta(days=3), created_by="Academy Admin", is_sample=True))
    s.flush()
