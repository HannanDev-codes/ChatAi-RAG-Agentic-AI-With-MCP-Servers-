"""Local memory and restricted developer operations; no MCP dependency here."""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
from pathlib import Path
import shlex
import socket
import sqlite3
import subprocess
import tempfile
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 1_000_000
BLOCKED = {".git", ".ssh", ".aws", ".venv", "venv", "node_modules", "__pycache__"}


def safe_root() -> Path:
    return Path(os.getenv("MCP_SAFE_ROOT", str(ROOT))).resolve(strict=True)


def safe_path(path: str) -> Path:
    root = safe_root()
    raw = Path(path)
    candidate = (root / raw).resolve(strict=True)
    relative = candidate.relative_to(root)
    for part in (*raw.parts, *relative.parts):
        low = part.lower()
        if (low in BLOCKED or low == ".env" or low.startswith(".env.")
                or low.endswith((".pem", ".key", ".p12", ".sqlite3", ".db"))):
            raise ValueError("Access to credentials, databases, or private folders is blocked")
    return candidate


def _db() -> sqlite3.Connection:
    # Separate from app.db so memory has its own schema and lifecycle.
    path = ROOT / "data" / "memory.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, "
                 "content TEXT NOT NULL, tags TEXT NOT NULL, "
                 "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    return conn


def _notes(rows) -> list[dict]:
    return [{**dict(r), "tags": json.loads(r["tags"])} for r in rows]


def save_note(content: str, tags: list[str] | None = None) -> dict:
    """Persist a fact for later sessions. Never save passwords or API keys."""
    if not content.strip() or len(content) > 20_000:
        raise ValueError("content must contain 1–20,000 characters")
    tags = tags or []
    if len(tags) > 30 or any(not isinstance(t, str) or len(t) > 100 for t in tags):
        raise ValueError("Use at most 30 text tags, each up to 100 characters")
    conn = _db()
    try:
        with conn:
            cur = conn.execute("INSERT INTO memories(content,tags) VALUES (?,?)",
                               (content.strip(), json.dumps(tags)))
        return _notes(conn.execute("SELECT * FROM memories WHERE id=?", (cur.lastrowid,)))[0]
    finally:
        conn.close()


def get_all_notes(limit: int = 100, offset: int = 0) -> list[dict]:
    """List saved facts newest first; paginate with limit and offset."""
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("limit must be 1–200 and offset nonnegative")
    conn = _db()
    try:
        return _notes(conn.execute("SELECT * FROM memories ORDER BY id DESC LIMIT ? OFFSET ?",
                                   (limit, offset)))
    finally:
        conn.close()


def search_notes(query: str, limit: int = 50) -> list[dict]:
    """Find saved facts by literal substring in content or tags (case insensitive)."""
    if not query.strip() or len(query) > 1000 or not 1 <= limit <= 200:
        raise ValueError("Provide a query of 1–1000 characters and limit of 1–200")
    term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    conn = _db()
    try:
        return _notes(conn.execute("SELECT * FROM memories WHERE content LIKE ? ESCAPE '\\' "
                                   "OR tags LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
                                   (term, term, limit)))
    finally:
        conn.close()


def read_file(path: str, max_chars: int = 20_000) -> str:
    """Read UTF-8 project text inside MCP_SAFE_ROOT, excluding private paths."""
    if not 1 <= max_chars <= 100_000:
        raise ValueError("max_chars must be 1–100,000")
    p = safe_path(path)
    if not p.is_file():
        raise ValueError("Expected a regular file")
    with p.open("rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or b"\0" in data:
        raise ValueError("File is binary or exceeds 1 MB")
    text = data.decode("utf-8")
    return text[:max_chars] + ("\n… (truncated)" if len(text) > max_chars else "")


def list_directory(path: str = ".") -> list[dict]:
    """List up to 200 permitted entries inside MCP_SAFE_ROOT."""
    directory = safe_path(path)
    if not directory.is_dir():
        raise ValueError("Expected a directory")
    result = []
    for child in sorted(directory.iterdir()):
        try:
            p = safe_path(str(child))
        except (ValueError, OSError):
            continue
        result.append({"name": child.name, "type": "directory" if p.is_dir() else "file"})
        if len(result) == 200:
            break
    return result


COMMANDS = {
    "git status": ["status", "--short", "--branch", "--untracked-files=no", "--ignore-submodules=all"],
    "git diff --stat": ["diff", "--stat", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all"],
    "git log -5 --oneline": ["log", "-5", "--oneline", "--no-decorate", "--no-show-signature"],
}


def run_safe_command(command: str) -> dict:
    """Run exactly git status, git diff --stat, or git log -5 --oneline. No shell."""
    key = " ".join(shlex.split(command))
    if key not in COMMANDS:
        raise ValueError("Allowed commands: " + ", ".join(COMMANDS))
    root = safe_root()
    # Ignore inherited Git overrides and global configuration. Disable repo hooks,
    # fsmonitor and external diff; commands are fixed, with no user arguments.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat", GIT_OPTIONAL_LOCKS="0")
    args = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull]
    # Require the repository to belong to the sandbox (no parent repo discovery).
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise ValueError("MCP_SAFE_ROOT must be the Git repository root")
    with tempfile.TemporaryFile() as output:
        proc = subprocess.run(args + ["--git-dir=" + str(root / ".git"), "--work-tree=" + str(root)] + COMMANDS[key], cwd=root, env=env,
                              stdin=subprocess.DEVNULL, stdout=output,
                              stderr=subprocess.STDOUT, timeout=15, shell=False)
        output.seek(0)
        data = output.read(64_001)
    return {"returncode": proc.returncode, "output": data[:64_000].decode("utf-8", "replace"),
            "truncated": len(data) > 64_000}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _target(url: str):
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in {None, 80, 443}):
        raise ValueError("Use a public HTTP(S) URL on port 80 or 443 without credentials")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    ips = [a[4][0] for a in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("Private, loopback, reserved and link-local addresses are blocked")
    return parsed, port, ips[0]


def fetch_url_content(url: str, max_chars: int = 20_000) -> str:
    """Fetch public text/HTML; validate each redirect and pin DNS to a public IP."""
    if not 1 <= max_chars <= 100_000:
        raise ValueError("max_chars must be 1–100,000")
    for _ in range(6):
        parsed, port, ip = _target(url)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = cls(parsed.hostname, port, timeout=10)
        # HTTPSConnection still verifies TLS using the original hostname.
        conn._create_connection = lambda address, timeout, source_address=None, **kw: socket.create_connection(
            (ip, port), timeout, source_address)
        try:
            conn.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                         headers={"User-Agent": "Memory-Dev-MCP/1.0", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect missing Location")
                url = urljoin(url, location)
                continue
            if response.status >= 400:
                raise ValueError(f"HTTP {response.status}")
            ctype = response.getheader("Content-Type", "").lower()
            if not (ctype.startswith("text/") or "application/json" in ctype):
                raise ValueError("Only text, HTML and JSON responses are supported")
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Response exceeds 1 MB")
            text = data.decode("utf-8", "replace")
            if "html" in ctype:
                parser = _Text()
                parser.feed(text)
                text = " ".join(" ".join(parser.parts).split())
            return text[:max_chars] + ("\n… (truncated)" if len(text) > max_chars else "")
        finally:
            conn.close()
    raise ValueError("Too many redirects")
