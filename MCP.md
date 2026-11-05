# MCP in this project (A + B)

## B — This project as an MCP **server**

Other MCP hosts (Claude Desktop, Cursor, etc.) can use your RAG tools:

```bash
cd rag-agent-chatbot
venv\Scripts\activate   # Windows
python -m backend.mcp_rag_server
```

### Claude Desktop example (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "rag-agent-tools": {
      "command": "C:\\path\\to\\rag-agent-chatbot\\venv\\Scripts\\python.exe",
      "args": ["-m", "backend.mcp_rag_server"],
      "cwd": "C:\\path\\to\\rag-agent-chatbot"
    }
  }
}
```

**Tools exposed**
- `search_knowledge_base` — RAG search (Chroma)
- `knowledge_base_stats` — chunk count
- `list_generated_reports` — files in `outputs/`
- `create_text_report` — create a Word report

---

## A — This chatbot as an MCP **client**

When the FastAPI app starts and handles chat, it can connect to MCP servers and register their tools for the agent.

- Built-in: connects to **this same** `mcp_rag_server` over stdio
- Optional extra servers via env `MCP_EXTRA_SERVERS` (JSON)

```text
# MCP client is OFF by default (stable chat). Turn on only if needed:
ENABLE_MCP=1
```

Check status:

```text
GET http://127.0.0.1:8000/api/mcp/status
```

In the agent UI, MCP tools appear as names like `mcp_rag_agent_tools_search_knowledge_base`.

---

## Install

```bash
pip install mcp
```

---

## How to explain (viva)

> We implemented MCP in both directions. As a **server**, our RAG and report tools are available to any MCP host. As a **client**, our chatbot can call MCP tools (including our own server) through a standard protocol, so tools are not locked inside one UI.


## Extra MCP servers in this repo

| Server | Module | Tools (examples) |
|--------|--------|------------------|
| RAG | `backend.mcp_rag_server` | search_knowledge_base, create_text_report |
| Filesystem | `backend.mcp_filesystem_server` | list_directory, read_text_file (sandbox: data/, outputs/, uploads/) |
| SQLite | `backend.mcp_sqlite_server` | list_tables, run_select, add_note → `data/app.db` |
| Fetch | `backend.mcp_fetch_server` | fetch_url |

Enable the client in `.env`:

```text
ENABLE_MCP=1
```

Then open `http://127.0.0.1:8000/api/mcp/status` to see connected servers/tools.

Run one server alone for testing:

```bash
python -m backend.mcp_filesystem_server
python -m backend.mcp_sqlite_server
python -m backend.mcp_fetch_server
```

## Memory + Developer server (new)

`backend.mcp_memory_dev_server` exposes these seven tools over stdio:

| Tool | Purpose |
| --- | --- |
| `save_note(content, tags)` | Save a durable fact in `data/memory.db` |
| `search_notes(query, limit=50)` | Literal substring search in facts and tags |
| `get_all_notes(limit=100, offset=0)` | Browse facts, newest first, in pages |
| `read_file(path, max_chars=20000)` | Read UTF-8 project source text |
| `list_directory(path=".")` | List permitted files and directories |
| `run_safe_command(command)` | Run a fixed Git inspection command |
| `fetch_url_content(url, max_chars=20000)` | Fetch public text or HTML |

### Install and start (Windows / VS Code)

Extract the source ZIP over your existing project, keeping your `.env`, virtual
environment, documents, databases, uploads, and reports. This ZIP contains source
code, not those runtime files. Open the `rag-agent-chatbot` folder in VS Code.

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m backend.mcp_memory_dev_server
```

The server waits silently for MCP messages; it is not an interactive terminal
chat. Press Ctrl+C to stop it before starting it through a host.

In VS Code, open `.vscode/mcp.json`, start **memory-dev**, and enter the absolute
path to `venv\Scripts\python.exe` when prompted. The existing Notion server entry
is preserved. On macOS/Linux, use `.venv/bin/python` (or your own environment's
Python executable).

To make the tools available to the chatbot, set `ENABLE_MCP=1` in your existing
`.env` and restart the FastAPI app as usual. Check `/api/mcp/status` for the
`memory-dev` server. Its agent tool names begin with `mcp_memory_dev_`.
MCP stays disabled by default. The bridge uses the running Python interpreter.

Try: “Remember that I prefer Python over JavaScript; tag it preferences.” Then
start a new chat and ask “What programming language do I prefer?” The agent must
search saved notes to recall it. Saving selected facts does not preserve entire
conversations or remove the model's context window limit.

### Scope and limits

- Memory is local, unencrypted, shared by clients of this project, and persists
  across server/chat restarts. It has no user-account isolation. Do not save secrets.
- `MCP_SAFE_ROOT` defaults to this project folder. File paths may be relative or
  absolute but must resolve inside that root, including symlink targets. Private
  folders, `.env` files, key files, and database files are blocked. File and response
  reads are capped at 1 MB; text output and note pages also have limits.
- Exact commands: `git status`, `git diff --stat`, `git log -5 --oneline`.
  Commands run without a shell in the sandbox root, with a 15 second timeout and
  capped returned output. The root must contain a real `.git` directory (worktrees with `.git` pointer files are not supported). `pytest` and arbitrary
  command arguments are intentionally unavailable because they execute code.
- Fetch allows public HTTP(S) text/HTML/JSON on ports 80/443. All resolved IPs
  must be public. Connections use a validated IP while retaining hostname TLS
  verification, and every redirect is revalidated. Requests have a 10 second
  socket timeout and a five-redirect limit. Pages are untrusted data.
- These restrictions apply to the new `memory-dev` server. Existing RAG,
  filesystem, SQLite, and fetch servers retain their previous tool behavior.
- MCP is pinned to `>=1.28,<2` in both dependency manifests to retain the project's
  FastMCP v1 API. Existing server constructors now use its `instructions` keyword.

### Verification

```bash
python -m unittest discover -s tests -v
```

Seven tests cover durable notes, literal search, pagination, path/symlink escapes,
command injection, public-IP checks, redirects, response limits and DNS pinning.
The implementation was also checked using a real MCP stdio client for tool
listing, save/search across process restarts, file reading and rejected calls.
The full RAG chatbot and live external URL fetching require your local setup and
were not tested as part of this change.
