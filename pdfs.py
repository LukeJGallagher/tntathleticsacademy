"""Branded PDFs (ReportLab). Attendance statement for one athlete between two dates."""
from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ASSETS = Path(__file__).parent / "assets"
BLACK = colors.HexColor("#111111")
BLUE = colors.HexColor("#2D98C6")
GREY = colors.HexColor("#54595F")
LIGHT = colors.HexColor("#F3F5F7")
STATUS_COLOUR = {"Present": colors.HexColor("#1E7F4F"), "Late": colors.HexColor("#B26B00"),
                 "Absent": colors.HexColor("#C0392B"), "Sick": colors.HexColor("#7D3C98"),
                 "Excused": GREY}

H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=BLACK, spaceAfter=3)
SUB = ParagraphStyle("s", fontName="Helvetica", fontSize=9.5, textColor=GREY, leading=13)
CELL = ParagraphStyle("c", fontName="Helvetica", fontSize=9, leading=11)


def _header(canvas, doc, club: str, title: str):
    w, h = A4
    canvas.saveState()
    canvas.setFillColor(BLACK)
    canvas.rect(0, h - 28 * mm, w, 28 * mm, stroke=0, fill=1)
    canvas.setFillColor(BLUE)
    canvas.rect(0, h - 29.5 * mm, w, 1.5 * mm, stroke=0, fill=1)
    logo = ASSETS / "logo.png"
    if logo.exists():
        canvas.drawImage(str(logo), w - 50 * mm, h - 24 * mm, width=36 * mm, height=17 * mm,
                         preserveAspectRatio=True, mask="auto")
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 17)
    canvas.drawString(15 * mm, h - 14 * mm, club.upper())
    canvas.setFont("Helvetica", 10)
    canvas.setFillColor(colors.HexColor("#CFD4D9"))
    canvas.drawString(15 * mm, h - 21 * mm, title)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(15 * mm, 10 * mm, f"Generated {datetime.now():%d %b %Y %H:%M}")
    canvas.drawRightString(w - 15 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def athlete_statement(club: str, athlete: str, category: str, start: date, end: date,
                      rows: list[dict], phone: str = "") -> bytes:
    """rows: [{Date, Session, Status, Recorded by}] for the period (all marks, not only present)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=36 * mm, bottomMargin=18 * mm,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            title=f"Attendance {athlete} {start:%Y-%m-%d} to {end:%Y-%m-%d}", author=club)
    n = {k: sum(1 for r in rows if r["Status"] == k) for k in STATUS_COLOUR}
    attended = n["Present"] + n["Late"]
    denom = attended + n["Absent"]
    rate = f"{100 * attended / denom:.0f}%" if denom else "—"

    story = [Paragraph(athlete, H),
             Paragraph(f"{category or ''}{' · ' if category else ''}Attendance {start:%d %b %Y} to {end:%d %b %Y}", SUB),
             Spacer(1, 6 * mm)]
    summary = Table([["Sessions attended", "Present", "Late", "Absent", "Sick / excused", "Attendance"],
                     [str(attended), str(n["Present"]), str(n["Late"]), str(n["Absent"]),
                      str(n["Sick"] + n["Excused"]), rate]],
                    colWidths=[36 * mm, 26 * mm, 22 * mm, 24 * mm, 32 * mm, 30 * mm])
    summary.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica", 8), ("TEXTCOLOR", (0, 0), (-1, 0), GREY),
        ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 15), ("TEXTCOLOR", (0, 1), (0, 1), BLUE),
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E3E6EA")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [summary, Spacer(1, 7 * mm)]

    if rows:
        data = [["Date", "Session", "Status", "Recorded by"]]
        for r in sorted(rows, key=lambda r: r["Date"]):
            data.append([r["Date"].strftime("%a %d %b %Y"), Paragraph(str(r["Session"]), CELL), r["Status"],
                         r.get("Recorded by") or ""])
        t = Table(data, colWidths=[34 * mm, 82 * mm, 24 * mm, 40 * mm], repeatRows=1)
        style = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                 ("BACKGROUND", (0, 0), (-1, 0), BLACK), ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E3E6EA")),
                 ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        for i, r in enumerate(data[1:], start=1):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
            style.append(("TEXTCOLOR", (2, i), (2, i), STATUS_COLOUR.get(r[2], BLACK)))
            style.append(("FONT", (2, i), (2, i), "Helvetica-Bold", 9))
        t.setStyle(TableStyle(style))
        story.append(t)
    else:
        story.append(Paragraph("No sessions were marked for this athlete between these dates.", SUB))

    story += [Spacer(1, 6 * mm), Paragraph(
        "Attended = Present + Late. Attendance % = attended ÷ (attended + absent); sick and excused sessions are "
        "not counted against the athlete and never use a paid session.", SUB)]
    title = f"Attendance statement · {start:%d %b %Y} – {end:%d %b %Y}"
    doc.build(story, onFirstPage=lambda c, d: _header(c, d, club, title),
              onLaterPages=lambda c, d: _header(c, d, club, title))
    return buf.getvalue()
