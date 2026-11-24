# TNT Athletics Academy: Roll Call

Attendance, bookings, sick/injury tracking and session-block payments for a single-venue athletics academy.
Replaces paper roll sheets and the nightly re-keying of attendance.

| Who | Signs in with | Sees |
|---|---|---|
| Admin / owner | username + password | Everything: payments, reminders, reports, schedule, staff logins, backup |
| Coach | username + password | Attendance, bookings, athletes and sick list for **their own sessions only** |
| Parent | 6-character family code | Book, cancel, waitlist, report sickness, sessions left / next payment |

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py          # http://localhost:8501
python tests/smoke_test.py    # every page × every role, plus key flows
```

With no `DATABASE_URL` the app uses `rollcall.db` (SQLite) and runs in **demo mode**: fictional sample data loads
on first start and the sign-in page lists the demo logins (`admin` / `demo1234`, coaches `tom`, `nadia`, `ahmed`,
parent codes `DEMO01`, `DEMO02`).

## Put the demo online (free, ~10 minutes): Streamlit Community Cloud

1. share.streamlit.io → **Create app** → pick this repo, branch `main`, file `app.py`.
2. Advanced settings → Secrets: `DEMO_MODE = "1"` (nothing else needed for a demo).
3. Deploy, then send the tester the URL.

SQLite on Streamlit Cloud resets when the app restarts. That suits a demo. For real use, add a database (below).

## Real use

Set these secrets (Streamlit Cloud: App → Settings → Secrets; VPS: `.streamlit/secrets.toml`):

```toml
DATABASE_URL = "..."   # see options below
DEMO_MODE = "0"        # first visit then asks you to create the admin account
```

**Option A: Supabase (managed Postgres, free tier, works with Streamlit Cloud).**
Supabase → New project → Connect → *Session pooler* URI →
`DATABASE_URL = "postgresql://postgres.REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres"`.
Tables are created automatically. Daily backups are included on paid plans; otherwise use the in-app backup.

**Option B: Hostinger VPS + MySQL.** Hostinger *shared/web* hosting can't run Streamlit; it needs a VPS.
1. hPanel → create MySQL database + user.
2. VPS (Ubuntu): `git clone`, `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`.
3. `DATABASE_URL = "mysql+pymysql://USER:PASS@localhost:3306/DBNAME"` in `.streamlit/secrets.toml`.
4. systemd service: `streamlit run app.py --server.port 8501 --server.address 127.0.0.1`.
5. nginx reverse proxy with WebSocket upgrade headers, then `certbot --nginx` for HTTPS.
6. Nightly backup: `scripts/backup_mysql.sh` via cron (instructions inside; keeps 30 days, optional rclone copy).

Then set **Settings → Club → App web address** so the family-code WhatsApp message includes the booking link.

## Backups

- **In the app:** Settings → Backup → download a ZIP of every table; restore replaces everything (one transaction).
- **On a VPS:** `scripts/backup_mysql.sh` nightly `mysqldump`, 30-day retention.
- Backups contain personal and medical data: store them privately.

## Key rules

- **Coming by default:** regular group members are coming unless they cancel or are on the sick list.
- **Unconfirmed** bookings don't hold a place; marking the athlete Present/Late confirms them.
- **Payment block** = start date → end date, N sessions. *Used* = Present + Late (+ Absent if that toggle is on).
- **Sick / Excused never use a paid session** and (by default) push the block end date to the athlete's next
  scheduled session, capped at 4 weeks and never into a prepaid next block. Admin can also add credit sessions or
  extra days per block.
- **Next payment date** = first scheduled session after the paid sessions run out, or the day after the block ends,
  whichever is sooner.
- **Families** share a code: one booking login, one payment covering several children, one WhatsApp reminder.
- **Check-in prompt:** anyone with no attendance for 14 days (not on the sick list) appears on the Overview with a
  pre-filled "is everything OK?" WhatsApp message.

## Security

- Staff passwords: argon2id; 5 failed attempts locks the account (admin unlocks).
- Sessions are server-side (cookie holds a random token; only its SHA-256 is stored), 2 h idle / 12 h max,
  revoked on sign-out, account disable or password reset.
- Coach scope is applied in the database queries, not just hidden in the UI.
- Append-only audit log: sign-ins, payments, exports, backups, staff and settings changes.
- Parents' family codes are a PIN, not an account. Re-issue a code from the athlete's Family tab if it leaks.

## Files

```
app.py          navigation + role guard
db.py           models, engine, settings, column migrator
logic.py        rosters, bookings/waitlist, attendance, health, fee engine, reminders, reports
auth.py         passwords, lockout, audit
backup.py       ZIP backup / restore
sample.py       fictional demo data
ui.py           brand CSS, hero, tabs, sessions/cookies
views/          one module per page
scripts/        backup_mysql.sh
tests/          smoke_test.py, screenshots.py
```
