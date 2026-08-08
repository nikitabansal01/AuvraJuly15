#!/usr/bin/env python3
"""Build the AUVRA architecture handbook from its version-controlled sources."""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "architecture"
OUT = ROOT / "output" / "pdf"
PDF = OUT / "AUVRA_Architecture_and_Operations_Handbook.pdf"
HTML = OUT / "AUVRA_Architecture_and_Operations_Handbook.html"
MD = OUT / "AUVRA_Architecture_and_Operations_Handbook.md"
MANIFEST = OUT / "AUVRA_Architecture_and_Operations_Handbook.manifest.json"
SHA = OUT / "AUVRA_Architecture_and_Operations_Handbook.sha256"

SOURCE_GLOBS = (
    "*.md", "adr/*.md", "runbooks/*.md", "catalogs/*.json", "schemas/*.json",
    "evidence/*", "diagrams/**/*.mmd",
)


def source_files() -> list[Path]:
    return sorted({p for pat in SOURCE_GLOBS for p in DOCS.glob(pat) if p.is_file()})


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = text.replace("**", "")
    return html.escape(text).replace("\n", "<br/>")


def parse_markdown(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks, index = [], 0
    while index < len(lines):
        line = lines[index]
        if not line.strip(): index += 1; continue
        if line.startswith("```"):
            index += 1; code = []
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index]); index += 1
            blocks.append(("code", "\n".join(code))); index += 1; continue
        match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if match:
            blocks.append((f"h{len(match.group(1))}", match.group(2))); index += 1; continue
        if "|" in line and index + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1]):
            table = [line]; index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table.append(lines[index]); index += 1
            blocks.append(("table", table)); continue
        para = [line]; index += 1
        while index < len(lines) and lines[index].strip() and not lines[index].startswith("#") and not lines[index].startswith("```") and "|" not in lines[index]:
            para.append(lines[index]); index += 1
        blocks.append(("p", " ".join(para)))
    return blocks


def table_rows(lines):
    return [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]


def html_document(chapters: list[Path]) -> str:
    chunks = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>AUVRA Architecture and Operations Handbook</title><style>body{font:16px system-ui;max-width:1000px;margin:auto;padding:2rem;line-height:1.45}table{border-collapse:collapse;width:100%;font-size:.88rem}td,th{border:1px solid #aaa;padding:.4rem;vertical-align:top}code,pre{background:#f3f5f7;padding:.8rem;display:block;white-space:pre-wrap}nav a{margin-right:1rem}</style></head><body><header><h1>AUVRA Architecture and Operations Handbook</h1><p>Generated snapshot. Status labels are evidence-driven; target design is not proof of implementation.</p><nav>"]
    for n, path in enumerate(chapters, 1): chunks.append(f'<a href="#c{n}">{html.escape(path.stem)}</a>')
    chunks.append("</nav></header><main>")
    for n, path in enumerate(chapters, 1):
        chunks.append(f'<article id="c{n}" aria-labelledby="t{n}">')
        for kind, data in parse_markdown(path):
            if kind.startswith("h"): chunks.append(f'<{kind} id="t{n}">{clean(data)}</{kind}>')
            elif kind == "p": chunks.append(f"<p>{clean(data)}</p>")
            elif kind == "code": chunks.append(f"<pre><code>{clean(data)}</code></pre>")
            elif kind == "table":
                rows = table_rows(data); chunks.append("<table><thead><tr>" + "".join(f"<th>{clean(x)}</th>" for x in rows[0]) + "</tr></thead><tbody>")
                chunks.extend("<tr>" + "".join(f"<td>{clean(x)}</td>" for x in row) + "</tr>" for row in rows[1:]); chunks.append("</tbody></table>")
        chunks.append("</article>")
    return "".join(chunks) + "</main></body></html>\n"


class Handbook(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and getattr(flowable, "bookmark", None):
            key = flowable.bookmark
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.getPlainText(), key, flowable.level, False)


def build_pdf(chapters: list[Path]) -> None:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleAUVRA", parent=styles["Title"], textColor=colors.HexColor("#17324d"), fontSize=25, leading=30, alignment=TA_CENTER, spaceAfter=14))
    styles.add(ParagraphStyle(name="H1AUVRA", parent=styles["Heading1"], textColor=colors.HexColor("#17324d"), spaceBefore=10, spaceAfter=8))
    styles.add(ParagraphStyle(name="H2AUVRA", parent=styles["Heading2"], textColor=colors.HexColor("#255f85"), spaceBefore=8, spaceAfter=6))
    styles.add(ParagraphStyle(name="BodyAUVRA", parent=styles["BodyText"], fontSize=8.6, leading=11.4, spaceAfter=5))
    styles.add(ParagraphStyle(name="CodeAUVRA", parent=styles["Code"], fontSize=7, leading=8.5, backColor=colors.HexColor("#f1f4f7"), borderPadding=5, spaceAfter=6))
    story = [Paragraph("AUVRA Architecture and Operations Handbook", styles["TitleAUVRA"]), Paragraph("Initial v2 rebuild handover package | generated evidence snapshot | 2026-08-08", styles["BodyAUVRA"]), Spacer(1, 8), Paragraph("Important: AS-IS is frozen evidence. TARGET PLANNED/PARTIAL items are designs or incomplete work, not production claims. Legacy restore is blocked pending an isolated PostgreSQL 17 rehearsal; the supplied 22-byte storage ZIP is empty.", styles["BodyAUVRA"]), Spacer(1, 10), Paragraph("Contents", styles["H1AUVRA"])]
    for n, path in enumerate(chapters, 1): story.append(Paragraph(f'<link href="chapter-{n}">{n}. {path.stem.replace("-", " ").title()}</link>', styles["BodyAUVRA"]))
    story.append(PageBreak())
    for n, path in enumerate(chapters, 1):
        for kind, data in parse_markdown(path):
            if kind.startswith("h"):
                level = min(int(kind[1]) - 1, 1)
                style = styles["H1AUVRA"] if kind == "h1" else styles["H2AUVRA"]
                p = Paragraph(clean(data), style); p.bookmark = f"chapter-{n}" if kind == "h1" else f"chapter-{n}-{len(story)}"; p.level = level; story.append(p)
            elif kind == "p": story.append(Paragraph(clean(data), styles["BodyAUVRA"]))
            elif kind == "code": story.append(Paragraph(clean(data), styles["CodeAUVRA"]))
            elif kind == "table":
                rows = table_rows(data)
                cells = [[Paragraph(clean(x), styles["BodyAUVRA"]) for x in row] for row in rows]
                widths = [170*mm / max(1, len(rows[0]))] * len(rows[0])
                t = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
                t.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0), colors.HexColor("#dbeaf3")), ("GRID", (0,0),(-1,-1), .25, colors.HexColor("#9aa8b3")), ("VALIGN", (0,0),(-1,-1),"TOP"), ("LEFTPADDING", (0,0),(-1,-1),3), ("RIGHTPADDING", (0,0),(-1,-1),3), ("TOPPADDING", (0,0),(-1,-1),3), ("BOTTOMPADDING", (0,0),(-1,-1),3)])); story.extend([t, Spacer(1,5)])
        if n != len(chapters): story.append(PageBreak())
    def page(canvas, doc):
        canvas.saveState(); canvas.setFont("Helvetica", 7.5); canvas.setFillColor(colors.HexColor("#506070")); canvas.drawString(20*mm, 12*mm, "AUVRA Architecture and Operations Handbook - evidence-driven snapshot"); canvas.drawRightString(190*mm, 12*mm, f"Page {doc.page}"); canvas.restoreState()
    Handbook(str(PDF), pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=18*mm, bottomMargin=18*mm, title="AUVRA Architecture and Operations Handbook", author="AUVRA", subject="Architecture and operations evidence snapshot").build(story, onFirstPage=page, onLaterPages=page)


def main() -> None:
    # Keep the checked-in physical appendix synchronized with current migration DDL.
    from build_physical_schema_appendix import main as build_physical_schema_appendix
    build_physical_schema_appendix()
    OUT.mkdir(parents=True, exist_ok=True)
    chapters = sorted(DOCS.glob("0[0-9]-*.md"))
    chapters += sorted((DOCS / "adr").glob("*.md")) + sorted((DOCS / "runbooks").glob("*.md")) + [DOCS / "evidence" / "migration-evidence.md"]
    MD.write_text("# AUVRA Architecture and Operations Handbook\n\nGenerated evidence snapshot. TARGET labels are not implementation claims.\n\n" + "\n\n---\n\n".join(path.read_text(encoding="utf-8") for path in chapters) + "\n", encoding="utf-8")
    HTML.write_text(html_document(chapters), encoding="utf-8")
    build_pdf(chapters)
    manifest = {"artifact": PDF.name, "generated_at": datetime.now(timezone.utc).isoformat(), "generator": "ReportLab", "status": "Generated snapshot; TARGET labels are not implementation claims.", "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in source_files()}, "companions": [MD.name, HTML.name, SHA.name], "verification": "Run docs/architecture/build/validate_release.py after this build."}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    SHA.write_text(f"{digest(PDF)}  {PDF.name}\n{digest(MD)}  {MD.name}\n{digest(HTML)}  {HTML.name}\n{digest(MANIFEST)}  {MANIFEST.name}\n", encoding="utf-8")
    print(PDF)

if __name__ == "__main__": main()
