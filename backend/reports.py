"""Generate and update Word, PDF, Excel, CSV + flowchart images."""

from __future__ import annotations

import csv
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# last created/updated file (for vague requests like "update it")
LAST_FILE: dict | None = None

def _remember(info: dict) -> dict:
    global LAST_FILE
    if info and not info.get("error") and info.get("filename"):
        LAST_FILE = dict(info)
    return info


def _safe_name(title: str) -> str:
    base = (title or "report").strip()
    # strip file extensions the model sometimes puts in the title
    base = re.sub(r"\.(xlsx|csv|docx|pdf|txt|doc)$", "", base, flags=re.I)
    base = re.sub(r"[^\w\s\-]", "", base).strip()
    base = re.sub(r"\s+", "_", base)[:40] or "report"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{base}_{stamp}_{uuid.uuid4().hex[:6]}"


def _pdf_safe(text: str) -> str:
    if not text:
        return ""
    repl = {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ",
        "\u2022": "-", "→": "->", "←": "<-", "⇒": "=>",
    }
    for a, b in repl.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


def _extract_flow_steps(content: str) -> list[str]:
    steps: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if "->" in line or "-->" in line or "→" in line:
            parts = re.split(r"\s*(?:-->|->|→)\s*", line)
            for p in parts:
                p = re.sub(r"[\[\]\(\)#*`]", "", p).strip()
                p = re.sub(r"^\d+[\.\)]\s*", "", p)
                if p and p not in steps and len(p) < 80:
                    steps.append(p)
    if steps:
        return steps[:12]
    for line in content.splitlines():
        m = re.match(r"^(?:\d+[\.\)]\s+|[-*•]\s+)(.+)$", line.strip())
        if m:
            s = re.sub(r"[#*`]", "", m.group(1).strip())
            if s and len(s) < 80:
                steps.append(s)
    return steps[:12]


def _draw_flowchart_png(steps: list[str], path: Path) -> bool:
    if len(steps) < 2:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False

    box_w, box_h, gap = 320, 52, 36
    pad_x, pad_y = 40, 40
    width = box_w + pad_x * 2
    height = pad_y * 2 + len(steps) * box_h + (len(steps) - 1) * gap
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    accent, text_color = (47, 111, 237), (26, 25, 22)
    box_fill, arrow = (240, 245, 255), (100, 100, 110)
    cx, y = width // 2, pad_y
    centers = []
    for step in steps:
        x0, y0 = cx - box_w // 2, y
        x1, y1 = x0 + box_w, y0 + box_h
        draw.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=box_fill, outline=accent, width=2)
        label = step if len(step) <= 36 else step[:33] + "..."
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx - tw / 2, y0 + (box_h - th) / 2 - 1), label, fill=text_color, font=font)
        centers.append((cx, y0, y1))
        y = y1 + gap
    for i in range(len(centers) - 1):
        _, _, y1 = centers[i]
        _, y2, _ = centers[i + 1]
        draw.line([(cx, y1 + 2), (cx, y2 - 8)], fill=arrow, width=2)
        draw.polygon([(cx, y2 - 2), (cx - 6, y2 - 12), (cx + 6, y2 - 12)], fill=arrow)
    img.save(str(path), "PNG")
    return True


def _blocks(content: str) -> list[tuple[str, str]]:
    lines = (content or "").replace("\r\n", "\n").split("\n")
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            out.append(("code", "\n".join(code_lines)))
            continue
        if stripped.startswith("### "):
            out.append(("h3", stripped[4:].strip()))
        elif stripped.startswith("## "):
            out.append(("h2", stripped[3:].strip()))
        elif stripped.startswith("# "):
            out.append(("h1", stripped[2:].strip()))
        elif re.match(r"^[-*•]\s+", stripped):
            out.append(("bullet", re.sub(r"^[-*•]\s+", "", stripped)))
        elif re.match(r"^\d+[\.\)]\s+", stripped):
            out.append(("number", re.sub(r"^\d+[\.\)]\s+", "", stripped)))
        else:
            out.append(("para", stripped))
        i += 1
    return out or [("para", "(Empty report)")]


def _clean_md_inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def _resolve_output(filename: str) -> Path | None:
    safe = Path(filename or "").name
    if not safe:
        return None
    path = OUTPUT_DIR / safe
    return path if path.is_file() else None


def list_output_file_infos() -> list[dict]:
    files = sorted(OUTPUT_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files:
        if not f.is_file() or f.name.endswith("_flow.png"):
            continue
        ext = f.suffix.lower().lstrip(".")
        out.append({
            "filename": f.name,
            "path": str(f),
            "url": f"/api/files/{f.name}",
            "type": ext or "file",
        })
    return out[:30]


def list_output_files() -> str:
    files = sorted(OUTPUT_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [f for f in files if f.is_file() and not f.name.endswith("_flow.png")]
    if not files:
        return "No generated files yet."
    lines = ["Generated files (newest first):"]
    for f in files[:30]:
        lines.append(f"- {f.name}  →  /api/files/{f.name}")
    return "\n".join(lines)


# ── DOCX ──────────────────────────────────────────────────────

def create_docx_report(title: str, content: str) -> dict:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    name = _safe_name(title) + ".docx"
    path = OUTPUT_DIR / name
    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)

    doc.add_heading(_clean_md_inline(title or "Report"), level=0)
    meta = doc.add_paragraph(
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    if meta.runs:
        meta.runs[0].font.size = Pt(9)
        meta.runs[0].font.color.rgb = RGBColor(0x7A, 0x75, 0x6C)

    steps = _extract_flow_steps(content)
    flow_path = OUTPUT_DIR / (name.replace(".docx", "_flow.png"))
    if len(steps) >= 2 and _draw_flowchart_png(steps, flow_path):
        doc.add_heading("Flowchart", level=1)
        doc.add_picture(str(flow_path), width=Inches(4.5))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_heading("Details", level=1)
    for kind, text in _blocks(content):
        text = _clean_md_inline(text)
        if kind == "h1":
            doc.add_heading(text, level=1)
        elif kind == "h2":
            doc.add_heading(text, level=2)
        elif kind == "h3":
            doc.add_heading(text, level=3)
        elif kind == "bullet":
            doc.add_paragraph(text, style="List Bullet")
        elif kind == "number":
            doc.add_paragraph(text, style="List Number")
        elif kind == "code":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.font.name = "Consolas"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            run.font.size = Pt(9)
        else:
            doc.add_paragraph(text)

    doc.save(str(path))
    return _remember({"filename": name, "path": str(path), "url": f"/api/files/{name}", "type": "docx"})


def update_docx_report(filename: str, section_title: str, content: str) -> dict:
    """Append a new section to an existing Word file in outputs/."""
    from docx import Document

    if not filename or filename in (".", "it", "this", "that", "the file"):
        if LAST_FILE and str(LAST_FILE.get("filename", "")).endswith(".docx"):
            filename = LAST_FILE["filename"]
        else:
            xs = sorted(OUTPUT_DIR.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not xs:
                return {"error": "No Word file found to update. Create one first."}
            filename = xs[0].name

    path = _resolve_output(filename)
    if not path or path.suffix.lower() != ".docx":
        return {"error": f"Word file not found in outputs/: {filename}"}

    doc = Document(str(path))
    doc.add_heading(_clean_md_inline(section_title or "Update"), level=1)
    meta = doc.add_paragraph(
        f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    for kind, text in _blocks(content):
        text = _clean_md_inline(text)
        if kind in ("h1", "h2", "h3"):
            doc.add_heading(text, level=2)
        elif kind == "bullet":
            doc.add_paragraph(text, style="List Bullet")
        elif kind == "number":
            doc.add_paragraph(text, style="List Number")
        else:
            doc.add_paragraph(text)
    doc.save(str(path))
    return _remember({
        "filename": path.name,
        "path": str(path),
        "url": f"/api/files/{path.name}",
        "type": "docx",
        "updated": True,
    })


# ── PDF ───────────────────────────────────────────────────────

def create_pdf_report(title: str, content: str) -> dict:
    from fpdf import FPDF

    name = _safe_name(title) + ".pdf"
    path = OUTPUT_DIR / name
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    usable = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(usable, 10, _pdf_safe(title or "Report"))
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(usable, 5, f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    steps = _extract_flow_steps(content)
    flow_path = OUTPUT_DIR / (name.replace(".pdf", "_flow.png"))
    if len(steps) >= 2 and _draw_flowchart_png(steps, flow_path):
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(usable, 8, "Flowchart")
        pdf.image(str(flow_path), w=min(usable, 120))
        pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.multi_cell(usable, 8, "Details")
    for kind, text in _blocks(content):
        text = _clean_md_inline(text)
        if kind in ("h1", "h2", "h3"):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 14 if kind == "h1" else 12)
            pdf.multi_cell(usable, 7, _pdf_safe(text))
        elif kind == "bullet":
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(usable, 6, _pdf_safe(f"- {text}"))
        elif kind == "code":
            pdf.set_font("Courier", "", 9)
            pdf.multi_cell(usable, 5, _pdf_safe(text))
        else:
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(usable, 6, _pdf_safe(text))
            pdf.ln(1)

    pdf.output(str(path))
    return _remember({"filename": name, "path": str(path), "url": f"/api/files/{name}", "type": "pdf"})


def update_pdf_report(filename: str, section_title: str, content: str) -> dict:
    """
    PDF editing is limited — we rebuild by appending text into a NEW pdf
    named from the original, or overwrite with combined note.
    Strategy: create a new PDF that states it updates the previous file.
    """
    path = _resolve_output(filename)
    base = Path(filename or "report").stem
    title = f"Update — {section_title or base}"
    body = (
        f"# Update to {filename or 'previous report'}\n\n"
        f"## {section_title or 'New section'}\n\n"
        f"{content}"
    )
    info = create_pdf_report(title, body)
    info["updated"] = True
    info["note"] = "PDF updates are saved as a new file (PDFs are not easily edited in-place)."
    return info


# ── Excel / CSV ───────────────────────────────────────────────

def _parse_table(content: str) -> list[list[str]]:
    """
    Parse table from:
      - markdown table
      - CSV lines
      - 'col1 | col2' rows
    """
    rows: list[list[str]] = []
    for line in (content or "").splitlines():
        line = line.strip()
        if not line or re.match(r"^\|?\s*-+", line):
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append(cells)
        elif "," in line:
            rows.append(next(csv.reader([line])))
        elif "\t" in line:
            rows.append([c.strip() for c in line.split("\t")])
    if not rows:
        # fallback: one column from bullets/numbers
        for line in (content or "").splitlines():
            m = re.match(r"^(?:\d+[\.\)]\s+|[-*•]\s+)?(.+)$", line.strip())
            if m and m.group(1).strip():
                rows.append([m.group(1).strip()])
    return rows or [["(empty)"]]


def create_excel_report(title: str, content: str) -> dict:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    name = _safe_name(title) + ".xlsx"
    path = OUTPUT_DIR / name
    wb = Workbook()
    ws = wb.active
    ws.title = (title or "Sheet1")[:31]

    rows = _parse_table(content)
    for r_i, row in enumerate(rows, 1):
        for c_i, val in enumerate(row, 1):
            cell = ws.cell(row=r_i, column=c_i, value=val)
            if r_i == 1:
                cell.font = Font(bold=True)

    for col in ws.columns:
        max_len = 0
        letter = col[0].column_letter
        for cell in col:
            max_len = max(max_len, len(str(cell.value or "")))
        ws.column_dimensions[letter].width = min(max_len + 4, 40)

    wb.save(str(path))
    return _remember({"filename": name, "path": str(path), "url": f"/api/files/{name}", "type": "xlsx"})


def create_csv_report(title: str, content: str) -> dict:
    name = _safe_name(title) + ".csv"
    path = OUTPUT_DIR / name
    rows = _parse_table(content)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    return _remember({"filename": name, "path": str(path), "url": f"/api/files/{name}", "type": "csv"})


def update_excel_report(filename: str, content: str) -> dict:
    """Append rows to an existing Excel file. Empty content still appends a timestamp note."""
    from openpyxl import load_workbook

    # vague "update it" → last file
    if not filename or filename in (".", "it", "this", "that", "the file"):
        if LAST_FILE and (LAST_FILE.get("type") in ("xlsx", "excel") or str(LAST_FILE.get("filename","")).endswith(".xlsx")):
            filename = LAST_FILE["filename"]
        else:
            # newest xlsx
            xs = sorted(OUTPUT_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not xs:
                return {"error": "No Excel file found to update. Create one first."}
            filename = xs[0].name

    path = _resolve_output(filename)
    if not path or path.suffix.lower() not in {".xlsx", ".xlsm"}:
        return {"error": f"Excel file not found in outputs/: {filename}"}

    wb = load_workbook(str(path))
    ws = wb.active
    start = ws.max_row + 1
    rows = _parse_table(content) if (content or "").strip() else [
        ["Updated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")]
    ]
    for r_i, row in enumerate(rows):
        for c_i, val in enumerate(row, 1):
            ws.cell(row=start + r_i, column=c_i, value=val)
    wb.save(str(path))
    return _remember({
        "filename": path.name,
        "path": str(path),
        "url": f"/api/files/{path.name}",
        "type": "xlsx",
        "updated": True,
    })
