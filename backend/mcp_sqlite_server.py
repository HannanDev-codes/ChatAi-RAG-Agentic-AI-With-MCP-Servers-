"""SQLite MCP server — query a local project database."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP

DB_PATH = ROOT / "data" / "app.db"

mcp = FastMCP("sqlite", instructions="SQLite access for the project database data/app.db")


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # seed a tiny demo table if empty
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    n = conn.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]
    if n == 0:
        conn.execute(
            "INSERT INTO notes (title, body) VALUES (?, ?)",
            ("Welcome", "Demo note for SQLite MCP"),
        )
        conn.commit()
    return conn


@mcp.tool()
def list_tables() -> str:
    """List tables in data/app.db."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return "\n".join(r["name"] for r in rows) or "(no tables)"
    finally:
        conn.close()


@mcp.tool()
def run_select(sql: str) -> str:
    """Run a read-only SELECT query on data/app.db. Only SELECT is allowed."""
    q = (sql or "").strip().rstrip(";")
    if not q.lower().startswith("select"):
        return "Only SELECT queries are allowed."
    if any(x in q.lower() for x in ("insert", "update", "delete", "drop", "alter", "attach")):
        return "Refusing non-read SQL."
    conn = _connect()
    try:
        cur = conn.execute(q)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(50)
        lines = [" | ".join(cols)]
        lines.append("-" * max(10, len(lines[0])))
        for r in rows:
            lines.append(" | ".join("" if v is None else str(v) for v in r))
        return "\n".join(lines) if cols else "(no rows)"
    except Exception as e:
        return f"SQL error: {e}"
    finally:
        conn.close()


@mcp.tool()
def add_note(title: str, body: str = "") -> str:
    """Insert a note into the notes table."""
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO notes (title, body) VALUES (?, ?)",
            (title or "untitled", body or ""),
        )
        conn.commit()
        return f"Inserted note id={cur.lastrowid}"
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
