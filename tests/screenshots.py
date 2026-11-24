"""Screenshot key pages (desktop + phone) from a running app.  python tests/screenshots.py http://localhost:8599 OUTDIR"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8501"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "shots")
OUT.mkdir(parents=True, exist_ok=True)


def wait(p):
    p.wait_for_load_state("networkidle")
    p.wait_for_timeout(2500)


def login(p, user, pw="demo1234"):
    p.goto(BASE)
    wait(p)
    p.get_by_role("textbox", name="Username").fill(user)
    p.get_by_role("textbox", name="Password").fill(pw)
    p.get_by_role("button", name="Sign in").click()
    wait(p)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    desk = b.new_page(viewport={"width": 1400, "height": 900})
    desk.goto(BASE)
    wait(desk)
    desk.screenshot(path=OUT / "01_landing.png", full_page=True)
    login(desk, "admin")
    desk.reload()
    wait(desk)
    stayed = desk.get_by_text("Signed in as").count() > 0
    print("refresh keeps session:", stayed)
    errors = []
    desk.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    for name in ["overview", "attendance", "athletes", "payments", "health", "schedule", "bookings", "reports", "settings"]:
        desk.goto(f"{BASE}/{name}")
        wait(desk)
        desk.screenshot(path=OUT / f"10_admin_{name}.png", full_page=True)
    print("console errors:", [e[:160] for e in errors][:8])
    desk.goto(f"{BASE}/payments")
    wait(desk)
    desk.get_by_role("button", name="Reminders").first.click()
    wait(desk)
    desk.screenshot(path=OUT / "11_admin_reminders.png", full_page=True)

    phone = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    login(phone, "tom")
    phone.screenshot(path=OUT / "20_coach_attendance_phone.png", full_page=True)

    par = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    par.goto(BASE)
    wait(par)
    par.get_by_label("Family code").fill("DEMO01")
    par.get_by_role("button", name="Continue").click()
    wait(par)
    par.screenshot(path=OUT / "30_parent_book_phone.png", full_page=True)
    b.close()
print("done", OUT)

