"""Fetch MCP server — HTTP GET text content from public URLs."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fetch", instructions="Fetch public HTTP(S) pages as text")


def _clean_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@mcp.tool()
def fetch_url(url: str, max_chars: int = 6000) -> str:
    """Fetch a public http/https URL and return plain text (HTML tags stripped)."""
    u = (url or "").strip()
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"}:
        return "Only http/https URLs are allowed."
    if parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return "Refusing local network URLs."
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            r = client.get(u, headers={"User-Agent": "RAG-Agent-MCP-Fetch/1.0"})
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            body = r.text
            if "html" in ctype or body.lstrip().startswith("<"):
                body = _clean_html(body)
            if len(body) > max_chars:
                body = body[:max_chars] + "\n... (truncated)"
            return f"URL: {u}\nStatus: {r.status_code}\n\n{body}"
    except Exception as e:
        return f"Fetch failed: {e}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
