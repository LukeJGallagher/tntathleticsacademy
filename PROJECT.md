# Roll Call — Project Spec

**Type:** Personal project
**Owner:** Luke Gallagher
**Status:** Built and tested locally on sample data (Phase 1 plus bookings, health log and payments admin). Not yet deployed.
**Last updated:** 24 Nov 2026

---

## 1. Purpose

A simple system for a junior sport program to replace paper roll sheets and the nightly re-keying of attendance into a master database.

Core problems it solves:

1. **Attendance:** coaches mark who attended each session (on a phone), instead of a paper sheet re-typed at night.
2. **Fee cycle:** families pay for blocks of sessions (e.g. 4 weeks × 2/week). The app subtracts attendance from what was paid, and shows who's due and when.
3. **Bookings:** parents book, cancel or join a waitlist themselves. Capacity is enforced.
4. **Sickness and injury:** log who's out and for how long, and flag them on the coach's list.

Single location. One admin (owner) plus several coaches plus parents.

---

## 2. Users and access

| User | Access | How |
|---|---|---|
| Admin / owner | Everything | Staff password (`APP_PASSWORD`) |
| Coach | Everything (Phase 1); mainly the Attendance page on a phone | Same staff password; "Recorded by" name stored on each mark |
| Parent | **Book** page only | No password. A 6-character **family code** per family (siblings share one) |

If `APP_PASSWORD` is not set, every page is open, which is fine for local testing only.

---

## 3. Features (built)

### Attendance (coaches, phone-first)
- Pick a date and the sessions for that weekday appear, filterable by coach.
- Each athlete gets one tap: **Present / Late / Absent / Sick / Excused**. Each tap saves immediately; tap again to clear.
- "Mark the rest present" marks anyone on the illness/injury list as **Sick** instead.
- Add a make-up athlete on the spot.
- Flags next to each name: fees due/unpaid, casual/trial/make-up booking, currently ill/injured.
- Cancellations and the waitlist are listed under each session. There's an over-capacity warning.

### Athletes
- Profile: first and last name, date of birth (shows age), gender, school, parent/guardian, phone, email, start date, notes, active flag, family booking code.
- Weekly groups (which sessions they belong to).
- Tabs: Profile · Payments · Attendance history · Health.

### Schedule
- Recurring weekly sessions with day, start/end time, name, coach, capacity, an athlete list, and an "online booking allowed" toggle.
- Week grid view. Sessions are archived (never deleted) so history is kept.

### Bookings (staff)
- Per date and session: attending (regular, casual, make-up or trial), capacity, spots left, waitlist, cancelled.
- Actions: cancel with a reason, add a booking (optionally over capacity), move someone in from the waitlist, reinstate a cancellation.
- 14-day overview table.

### Book (parents)
- Enter the family code and pick a child (when siblings share a code).
- See sessions for the next N days: book, join the waitlist when full, leave the waitlist, or "Can't make it" (Sick / Injured / Other).
- A Sick cancellation pre-marks attendance as Sick.
- Shows sessions left in the current block and whether payment is due.
- Changes close X hours before the start (default 2).

### Health (illness / injury log)
- Entry: type (Illness / Injury / Other), description, out-from date, expected return, returned date, notes.
- "Currently out" list with days out, an overdue-return flag, and a "Back today" button.
- History (CSV export), and sessions missed through sickness over the last 90 days.

### Payments (admin)
- **Balances:** sessions paid, attended, absent, sick/excused, used, **left**, **next payment date**, last reminder. Status counts: Due now / Unpaid / Due soon / No block / Up to date. CSV export.
- **Record payment:** athlete, payment date, sessions paid for, sessions/week, amount, method. It can count from the payment date or stack after the current block. "Valid until" is calculated automatically and can be overridden.
- **Reminders:** everyone due within N days, with an editable pre-filled message. WhatsApp / SMS / email buttons, **Mark sent** logs it, and recently reminded families are hidden. Reminder history.
- Toggle: deduct unexcused absences from sessions paid (default off).

### Reports
- This week, last week, this month, last month or a custom range.
- Totals, plus breakdowns by athlete, by coach and by session, and a raw log. Every table has a CSV download.
- Attendance rate = (Present + Late) ÷ (Present + Late + Absent). Sick and Excused are excluded.

### Settings
- Club name, currency, phone country code (default **974**, Qatar), reminder template, default block length, due-soon window, absence deduction, booking horizon, cancellation cutoff, coaches, load/remove sample data.

---

## 4. Business rules

**Attendance codes**

| Code | Meaning | Uses a paid session? | Counts against attendance rate? |
|---|---|---|---|
| P | Present | Yes | Attended |
| L | Late | Yes | Attended |
| A | Absent | Only if "deduct absences" is on | Yes (absence) |
| S | Sick | Never | Excluded |
| E | Excused | Never | Excluded |

**Payment block:** a start date, an end date, sessions purchased, sessions/week, amount, a paid flag and a paid date. *Used* is the number of session-using marks between the start and end dates. *Left* = purchased − used.

**Next payment date:** the first scheduled session (from the athlete's weekly groups) after the paid sessions run out, or the day after the paid period ends, whichever is sooner. Prepaid future blocks are included.

**Fee status** (most urgent first):
1. **Due now:** no paid sessions left, over the limit, or the block has ended with no renewal.
2. **Unpaid:** the current or next block isn't marked paid.
3. **No block:** nothing recorded.
4. **Due soon:** 1 session left, or the block ends within the warning window (default 7 days).
5. **Up to date.**

**Roster for a date** = the regular group, minus cancellations, plus casual, make-up and trial bookings.
**Capacity:** when full, new bookings go on the waitlist (first in, first out). A cancellation automatically promotes the first person waiting. Staff can override capacity.

---

## 5. Tech stack

| Layer | Choice |
|---|---|
| UI | Streamlit (≥ 1.40), multipage via `st.navigation` |
| Data | SQLAlchemy 2.x ORM |
| DB (local) | SQLite (`rollcall.db`, created automatically) |
| DB (production) | MySQL on Hostinger via `pymysql` |
| Config | `DATABASE_URL`, `APP_PASSWORD` (env vars or `.streamlit/secrets.toml`) |
| Hosting | **Hostinger VPS** (shared/web hosting can't run Streamlit) + nginx reverse proxy + HTTPS (certbot) + systemd |

Migrations: tables are created on start, and a small built-in migrator adds new columns. Switch to Alembic if the schema starts changing often.

---

## 6. Data model

```
coaches          id, name, phone, email, active
athletes         id, first_name, last_name, dob, gender, school, guardian_name, phone, email,
                 start_date, notes, active, family_code
sessions         id, label, weekday(0=Mon), start_time, end_time, coach_id→coaches, capacity,
                 bookable, active
session_athletes session_id→sessions, athlete_id→athletes            (weekly group membership)
attendance       id, date, session_id, athlete_id, status(P/L/A/S/E), recorded_by, recorded_at
                 UNIQUE(date, session_id, athlete_id)
bookings         id, date, session_id, athlete_id, status(booked/waitlist/cancelled),
                 kind(regular/casual/makeup/trial), source(staff/parent), reason, created_at
                 UNIQUE(date, session_id, athlete_id)
payment_blocks   id, athlete_id, start_date, end_date, sessions_per_week, sessions_purchased,
                 amount, paid, paid_date, method, notes
health_events    id, athlete_id, kind, description, start_date, expected_return, returned_date, notes
reminders        id, athlete_id, sent_on, due_date, channel, message
settings         key, value
```
Every table except settings and session_athletes has an `is_sample` flag, so sample data can be removed cleanly.

---

## 7. File structure

```
rollcall/
├── app.py                 UI: all pages + navigation + login
├── db.py                  Models, engine, settings, lightweight migration
├── logic.py               Fee status, block usage, next payment, bookings/waitlist, health, sample data
├── requirements.txt
├── README.md              Run + deploy guide
├── PROJECT.md             This file
├── .gitignore
└── .streamlit/
    ├── config.toml        Theme
    └── secrets.toml.example
```

---

## 8. Run and deploy

**Local**
```bash
pip install -r requirements.txt
streamlit run app.py        # → http://localhost:8501, then Settings → Load sample data
```

**Hostinger**
1. hPanel → create a MySQL database and user (enable Remote MySQL if it's not on the VPS).
2. VPS (Ubuntu): clone or upload, create a venv, `pip install -r requirements.txt`, copy `secrets.toml.example` → `secrets.toml` and set `DATABASE_URL=mysql+pymysql://USER:PASS@HOST:3306/DB` and `APP_PASSWORD`.
3. Set up a systemd service running `streamlit run app.py --server.port 8501 --server.address 127.0.0.1`.
4. nginx reverse proxy (with WebSocket upgrade headers), then `certbot --nginx` for HTTPS.
5. Send parents `https://<domain>/book` plus their family code.

The full steps are in README.md.

---

## 9. Testing done

- All 9 pages render without errors on an empty database and on sample data (Streamlit `AppTest`).
- Flows verified: marking, clearing and bulk-marking attendance; the session count dropping; adding an athlete; parent booking; joining and leaving the waitlist; cancellation with automatic promotion; Sick cancellation pre-marking attendance; "Back today"; recording a payment (next due date calculated correctly); logging a reminder; the absence-deduction toggle; WhatsApp number formatting (Qatar local, +974, 00974, already prefixed).

---

## 10. Known limits

- Staff share one password. There are no per-coach logins or permissions.
- Family codes act as a simple PIN (no parent accounts).
- Reminders are one tap each through WhatsApp/SMS/email links. There's no automatic sending, and no notification when someone comes off the waitlist.
- No online payment. Payments are recorded manually.
- One location. Not designed for multiple clubs or venues.

---

## 11. Roadmap

**Next (Phase 2)**
- [ ] Coach logins that see only their own sessions
- [ ] Automatic SMS/WhatsApp/email for due payments and waitlist promotions (Twilio / Brevo / WhatsApp Business API)
- [ ] Parent accounts (email login) instead of family codes
- [ ] Online payments (Stripe or a local gateway) that create payment blocks automatically
- [ ] Alembic migrations; automated MySQL backups on the VPS

**Later (Phase 3: performance)**
- [ ] Results and testing data per athlete (times, distances, PBs)
- [ ] Training load and attendance trends dashboards
- [ ] Squad selection / grouping suggestions from attendance and results
- [ ] Multi-location support

---

## 12. Decisions log

| Date | Decision |
|---|---|
| 24 Nov 2026 | Phase 1 scope = attendance plus fee cycle only |
| 24 Nov 2026 | Build in Streamlit; host on Hostinger (VPS + MySQL) |
| 24 Nov 2026 | Added Sick status; Sick and Excused never use a paid session |
| 24 Nov 2026 | Bookings: both parent self-booking and staff booking, with a waitlist |
| 24 Nov 2026 | Illness/injury log (not just a status) |
| 24 Nov 2026 | Payments admin: record payment date and sessions paid, subtract attendance, next-payment reminders |
| 24 Nov 2026 | Absence deduction is a toggle, off by default |
| 24 Nov 2026 | Default phone country code 974 (Qatar) |
