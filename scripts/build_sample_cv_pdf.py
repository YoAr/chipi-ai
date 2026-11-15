"""Build fixtures/sample-cv-sam.pdf from fixtures/sample-cv-sam.md (Sam's fictional demo CV).

    cd backend && uv run --with reportlab python ../scripts/build_sample_cv_pdf.py

The PDF has real, extractable text, so uploading it through "Upload your CV" works the
same as a real CV. Re-run after editing the Markdown. Handles the Markdown this CV uses:
# name, the lines under it, > note, ## sections, ### role headings, **dates**, - bullets.
"""

import re
from html import escape
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, KeepTogether, ListFlowable, ListItem, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "fixtures" / "sample-cv-sam.md"
OUT = ROOT / "fixtures" / "sample-cv-sam.pdf"

INK, TEAL, MUTED, LINE, AMBER_INK, CREAM = (HexColor(c) for c in
                                             ("#13201c", "#0f6e5c", "#5a6763", "#c9d6d2", "#6b4a00", "#fdf1d8"))

S = {
    "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=24, leading=28, textColor=INK),
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=11.5, leading=15, textColor=TEAL, spaceBefore=2),
    "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=9.5, leading=13, textColor=MUTED),
    "note": ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=8.5, leading=11, textColor=AMBER_INK,
                           backColor=CREAM, borderPadding=(3, 5, 3, 5), spaceBefore=6),
    "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=TEAL,
                              spaceBefore=12, spaceAfter=2),
    "role": ParagraphStyle("role", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK),
    "dates": ParagraphStyle("dates", fontName="Helvetica", fontSize=9.5, leading=14, textColor=MUTED, alignment=TA_RIGHT),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.8, leading=13.6, textColor=INK),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=9.8, leading=13.2, textColor=INK),
}


def md_inline(text: str) -> str:
    """Escape for ReportLab's mini-HTML, then turn **bold** into <b>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(text, quote=False))


def section_heading(text: str) -> list:
    return [Paragraph(escape(text.upper()), S["section"]),
            HRFlowable(width="100%", thickness=0.6, color=LINE, spaceBefore=1, spaceAfter=5)]


def bullets(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(md_inline(i), S["bullet"]), leftIndent=11, value="•") for i in items],
        bulletType="bullet", bulletColor=TEAL, bulletFontSize=8, leftIndent=11, spaceBefore=2, spaceAfter=2)


def build() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines()
    story: list = []
    pending_bullets: list[str] = []
    pending_role: str | None = None
    header_lines = 0

    def flush_bullets() -> None:
        nonlocal pending_bullets
        if pending_bullets:
            story.append(bullets(pending_bullets))
            pending_bullets = []

    for raw in lines:
        line = raw.strip()
        if line.startswith("# "):
            story.append(Paragraph(escape(line[2:]), S["name"]))
            header_lines = 2  # the two lines under the name: title, then contact details
        elif header_lines and line and not line.startswith((">", "#")):
            story.append(Paragraph(md_inline(line), S["title"] if header_lines == 2 else S["contact"]))
            header_lines -= 1
        elif line.startswith(">"):
            story.append(Paragraph(md_inline(line.lstrip("> ")), S["note"]))
        elif line.startswith("## "):
            flush_bullets()
            story.extend(section_heading(line[3:]))
        elif line.startswith("### "):
            flush_bullets()
            pending_role = line[4:]
        elif pending_role and re.fullmatch(r"\*\*.+\*\*", line):
            row = Table([[Paragraph(md_inline(pending_role), S["role"]), Paragraph(escape(line.strip("*")), S["dates"])]],
                        colWidths=["76%", "24%"])
            row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            story.append(KeepTogether([row]))
            pending_role = None
        elif line.startswith("- "):
            pending_bullets.append(line[2:])
        elif line:
            flush_bullets()
            story.append(Paragraph(md_inline(line), S["body"]))
            story.append(Spacer(1, 3))
    flush_bullets()

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 10 * mm, "Sample CV for demos. Sam Verhoeven and every organisation named are fictional.")
        canvas.drawRightString(A4[0] - doc.rightMargin, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=17 * mm, rightMargin=17 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title="Sam Verhoeven — CV (sample)", author="Chipi demo", subject="Sample CV, fictional person")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build()
