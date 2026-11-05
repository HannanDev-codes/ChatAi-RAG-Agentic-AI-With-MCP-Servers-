"""Filesystem MCP server — safe read/list inside project sandbox folders."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP

# Only these folders are visible (safety)
ALLOWED_ROOTS = [
    ROOT / "data",
    ROOT / "outputs",
    ROOT / "uploads",
]

mcp = FastMCP("filesystem", instructions="Safe filesystem access under data/, outputs/, uploads/")


def _resolve_safe(path: str) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        # try each root
        candidates = [(root / raw).resolve() for root in ALLOWED_ROOTS]
    else:
        candidates = [raw.resolve()]
    for p in candidates:
        for root in ALLOWED_ROOTS:
            try:
                p.relative_to(root.resolve())
                if p.exists():
                    return p
            except ValueError:
                continue
    # default under data/
    p = (ALLOWED_ROOTS[0] / path).resolve()
    p.relative_to(ALLOWED_ROOTS[0].resolve())
    return p


@mcp.tool()
def list_directory(path: str = "data") -> str:
    """List files and folders under an allowed path (data, outputs, uploads)."""
    try:
        p = _resolve_safe(path)
        if not p.exists():
            return f"Not found: {path}"
        if not p.is_dir():
            return f"Not a directory: {path}"
        lines = []
        for child in sorted(p.iterdir())[:200]:
            kind = "dir" if child.is_dir() else "file"
            lines.append(f"{kind}\t{child.name}")
        return "\n".join(lines) or "(empty)"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def read_text_file(path: str, max_chars: int = 8000) -> str:
    """Read a text file from allowed folders (txt/md/csv/json)."""
    try:
        p = _resolve_safe(path)
        if not p.is_file():
            return f"File not found: {path}"
        if p.suffix.lower() not in {".txt", ".md", ".csv", ".json", ".log", ".py", ".env.example"}:
            return f"Refusing binary/unsupported type: {p.suffix}"
        text = p.read_text(encoding="utf-8", errors="ignore")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return text
    except Exception as e:
        return f"Error: {e}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
