"""Chat attachments — editable files in the conversation (NOT the RAG knowledge base)."""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAT_UPLOAD_DIR = ROOT / "uploads" / "chat"
CHAT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# last chat attachment (for "update it" in chat context)
LAST_CHAT_FILE: dict | None = None

ALLOWED = {".txt", ".md", ".csv", ".docx", ".pdf", ".xlsx"}


def _safe(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r"[^\w.\- ]", "", name).strip() or "file"
    return name[:80]


def save_chat_upload(filename: str, data: bytes) -> dict:
    global LAST_CHAT_FILE
    original = _safe(filename)
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED:
        return {"error": f"Unsupported type {ext}. Allowed: {', '.join(sorted(ALLOWED))}"}

    uid = uuid.uuid4().hex[:8]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stored = f"{Path(original).stem}_{stamp}_{uid}{ext}"
    path = CHAT_UPLOAD_DIR / stored
    path.write_bytes(data)

    info = {
        "id": uid,
        "filename": stored,
        "original_name": original,
        "path": str(path),
        "url": f"/api/chat-files/{stored}",
        "type": ext.lstrip("."),
        "size": len(data),
        "kind": "chat_upload",
    }
    LAST_CHAT_FILE = dict(info)
    return info


def resolve_chat_file(filename: str) -> Path | None:
    safe = Path(filename or "").name
    path = CHAT_UPLOAD_DIR / safe
    return path if path.is_file() else None


def read_chat_file_text(filename: str, max_chars: int = 50000) -> str:
    path = resolve_chat_file(filename)
    if not path:
        # also try outputs/ for continuity
        from backend.reports import OUTPUT_DIR

        alt = OUTPUT_DIR / Path(filename or "").name
        path = alt if alt.is_file() else None
    if not path:
        return f"File not found: {filename}"

    ext = path.suffix.lower()
    try:
        if ext in {".txt", ".md", ".csv"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
        elif ext == ".docx":
            from docx import Document

            doc = Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        elif ext == ".xlsx":
            from openpyxl import load_workbook

            wb = load_workbook(str(path), read_only=True, data_only=True)
            ws = wb.active
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i > 80:
                    rows.append("... (truncated)")
                    break
                rows.append(" | ".join("" if c is None else str(c) for c in row))
            text = "\n".join(rows)
        else:
            return f"Cannot read type {ext} as text."
    except Exception as e:
        return f"Failed to read file: {e}"

    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text or "(File is empty)"


def update_chat_docx(filename: str, section_title: str, content: str) -> dict:
    global LAST_CHAT_FILE
    from docx import Document

    path = resolve_chat_file(filename)
    if not path or path.suffix.lower() != ".docx":
        if LAST_CHAT_FILE and str(LAST_CHAT_FILE.get("filename", "")).endswith(".docx"):
            path = resolve_chat_file(LAST_CHAT_FILE["filename"])
        if not path:
            return {"error": "Chat Word file not found. Upload a .docx in chat first."}

    doc = Document(str(path))
    doc.add_heading(section_title or "Update", level=1)
    for line in (content or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith(("- ", "* ")):
            doc.add_paragraph(line[2:], style="List Bullet")
        else:
            doc.add_paragraph(line)
    doc.save(str(path))
    info = {
        "filename": path.name,
        "path": str(path),
        "url": f"/api/chat-files/{path.name}",
        "type": "docx",
        "updated": True,
        "kind": "chat_upload",
    }
    LAST_CHAT_FILE = dict(info)
    return info


def update_chat_excel(filename: str, content: str) -> dict:
    global LAST_CHAT_FILE
    from openpyxl import load_workbook

    path = resolve_chat_file(filename)
    if not path or path.suffix.lower() not in {".xlsx", ".xlsm"}:
        if LAST_CHAT_FILE and str(LAST_CHAT_FILE.get("filename", "")).endswith(".xlsx"):
            path = resolve_chat_file(LAST_CHAT_FILE["filename"])
        if not path:
            return {"error": "Chat Excel file not found. Upload a .xlsx in chat first."}

    wb = load_workbook(str(path))
    ws = wb.active
    start = ws.max_row + 1
    # simple row parse
    rows = []
    for line in (content or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            rows.append([c.strip() for c in line.strip("|").split("|")])
        elif "," in line:
            rows.append([c.strip() for c in line.split(",")])
        else:
            rows.append([line])
    if not rows:
        rows = [["Updated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")]]
    for r_i, row in enumerate(rows):
        for c_i, val in enumerate(row, 1):
            ws.cell(row=start + r_i, column=c_i, value=val)
    wb.save(str(path))
    info = {
        "filename": path.name,
        "path": str(path),
        "url": f"/api/chat-files/{path.name}",
        "type": "xlsx",
        "updated": True,
        "kind": "chat_upload",
    }
    LAST_CHAT_FILE = dict(info)
    return info


def overwrite_chat_text_file(filename: str, content: str) -> dict:
    global LAST_CHAT_FILE
    path = resolve_chat_file(filename)
    if not path or path.suffix.lower() not in {".txt", ".md", ".csv"}:
        return {"error": "Text/CSV chat file not found."}
    path.write_text(content or "", encoding="utf-8")
    info = {
        "filename": path.name,
        "path": str(path),
        "url": f"/api/chat-files/{path.name}",
        "type": path.suffix.lstrip("."),
        "updated": True,
        "kind": "chat_upload",
    }
    LAST_CHAT_FILE = dict(info)
    return info

def rewrite_chat_pdf(filename: str, new_text: str, title: str | None = None) -> dict:
    """Rebuild a PDF from edited text (layout-preserving PDF edit is not supported)."""
    global LAST_CHAT_FILE
    from fpdf import FPDF

    base = Path(filename or "document").stem
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:6]
    out_name = f"{base}_edited_{stamp}_{uid}.pdf"
    path = CHAT_UPLOAD_DIR / out_name

    def pdf_safe(s: str) -> str:
        return (s or "").encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    usable = pdf.w - 30
    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(usable, 8, pdf_safe(title or base.replace("_", " ")))
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)
    body = (new_text or "").replace("\r\n", "\n")
    for para in body.split("\n"):
        line = para.strip()
        if not line:
            pdf.ln(3)
            continue
        try:
            pdf.multi_cell(usable, 6, pdf_safe(line))
        except Exception:
            for i in range(0, len(line), 90):
                pdf.multi_cell(usable, 6, pdf_safe(line[i : i + 90]))
    pdf.output(str(path))
    info = {
        "filename": out_name,
        "path": str(path),
        "url": f"/api/chat-files/{out_name}",
        "type": "pdf",
        "updated": True,
        "kind": "chat_upload",
        "note": "PDF rebuilt from text; original layout/images are not preserved.",
    }
    LAST_CHAT_FILE = dict(info)
    return info
