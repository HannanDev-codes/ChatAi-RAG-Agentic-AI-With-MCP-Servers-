# Troubleshooting: Chat Attachments, Greetings & MCP

Last updated: November 2026  

This document records **recent bugs**, **root causes**, and **fixes** in the RAG + Agentic + MCP chatbot.

---

## 1. Overview

| # | Symptom | User impact |
|---|---------|-------------|
| A | After attaching a file, saying `hi` / `hello` claimed the file was “updated” | Confusing; fake updates |
| B | Asking “how is MCP implemented?” got a wrong definition (“Multi-Channel Prompting”) | Incorrect viva / demo answers |
| C | Asking for an **example** of MCP produced a random **PDF report** | Unwanted file generation |

All three are addressed mainly in `backend/agent.py` (prompts + routing logic).

---

## 2. Error A — Greetings triggered file updates

### What happened
1. User attached a PDF in chat (paperclip).  
2. User asked to edit (e.g. add name, keep content).  
3. Later user said only: `hi`, `hello`, `i said HI`.  
4. Bot replied: *“The file has been updated”* and showed a download link again.

### Root cause
- Chat **attachment stayed active** for the next messages.  
- Edit detection was too loose, and/or the model **claimed** an update without a real edit intent.  
- Greetings were not treated as a separate path.

### Fix
In `agent_stream`, when `attachment` is set:

1. **Greeting short-circuit**  
   Messages like `hi`, `hello`, `thanks`, etc. → normal short reply.  
   **No** file read/edit and **no** “file updated” claim.

2. **Stricter edit detection**  
   Edit only if the message clearly matches patterns such as:  
   `update`, `edit`, `append`, `rewrite`, `add my name`, etc.  
   Not on every message while a file is attached.

3. **Non-edit + attachment**  
   Summarize / answer about the file only; do not claim a save/update.

### How to verify
1. Attach a PDF.  
2. `add my name abdul hannan khalid but keep all the file as it is` → real update + Download.  
3. `hi` → greeting only, **no** update.  
4. Optional: clear attachment with **X** so later chat is free of the file.

### User tip
After finishing an edit, clear the attachment bar so the next messages are normal chat.

---

## 3. Error B — Wrong MCP explanation

### What happened
User asked how **MCP** is implemented.  
The model answered with **“Multi-Channel Prompting”** and invented sub-models/channels.  
That is **not** MCP in this project.

### Correct definition (this project)

**MCP = Model Context Protocol**

A **standard protocol** so an AI application can:

- Discover tools from MCP servers  
- Call those tools in a uniform way  
- Optionally **expose** its own tools as an MCP server for other hosts (Claude Desktop, Cursor, etc.)

### How MCP is implemented here

| Direction | Role | Code |
|-----------|------|------|
| **A — Client** | Chatbot loads tools from MCP servers and can call them | `backend/mcp_client_bridge.py` + wiring in `agent.py` |
| **B — Server** | This project exposes RAG-related tools over MCP (stdio) | `backend/mcp_rag_server.py` |

**Server tools (examples):**

- `search_knowledge_base` — RAG search over Chroma  
- `knowledge_base_stats` — chunk count  
- `list_generated_reports` — files under `outputs/`  
- `create_text_report` — create a Word report  

**Client status:** `GET /api/mcp/status`  

**Run server alone:**

```bash
python -m backend.mcp_rag_server
```

### Fix
System prompt in `agent.py` now states:

- MCP means **Model Context Protocol**  
- This app is **both client and server**  
- Do **not** invent other meanings (e.g. Multi-Channel Prompting)

### How to verify
Ask: *“How is MCP implemented in you?”*  

Expect: Model Context Protocol, client + server, RAG tools — **not** multi-channel prompting.

---

## 4. Error C — Example request created a PDF

### What happened
User asked for an **example** of how MCP works / “tell me how you are doing this”.  
The agent **created** `MCP_Example_Report_....pdf` and offered Download.

### Root cause
1. **Create-file fast path** or **create_pdf_report** tool was too eager.  
2. Model treated “example” as a reason to **generate a report file**.  
3. No clear rule: *explanation is not the same as file output*.

### Fix
1. **Prompt rules**  
   - Create PDF/Word/Excel/CSV only if the user **explicitly** asks for a file  
     (e.g. create a pdf, make a word doc, export excel).  
   - Explain / example / how it works / architecture → **chat text only**.

2. **Fast-path guards**  
   - `wants_file` must match create/export + file type language.  
   - `explain_only` skips auto file creation for explain/example/how questions.

3. **Tool descriptions**  
   - `create_pdf_report` / `create_word_report` say: only when the user explicitly wants a file.

### How to verify

| User message | Expected |
|--------------|----------|
| `How is MCP implemented? Give an example` | Text explanation only |
| `Create a PDF that explains MCP with an example` | PDF + Download card |

---

## 5. Related: PDF edit lost original text (earlier fix)

### Symptom
Edit produced a **new** PDF that did not contain the original lecture text.

### Cause
Model generated a **short new body** instead of transforming the **full extracted text**.

### Fix
1. Always start from **full** extracted text.  
2. Apply mechanical changes (add name, lowercase, remove blank lines) on that text.  
3. Rebuild PDF from the **full** edited text.  
4. Note: layout/images of the original PDF are not preserved (text rebuild).

---

## 6. File storage reminder

| Upload type | Folder | Vector DB? | Purpose |
|-------------|--------|------------|---------|
| Sidebar knowledge upload | `data/docs/` | Yes (after ingest) | RAG |
| Chat attach | `uploads/chat/` | No | Read / edit in conversation |
| Agent-generated reports | `outputs/` | No | Downloadable reports |

---

## 7. Quick recovery checklist

1. Replace latest `backend/agent.py` (and `chat_files.py` if using PDF edit fixes).  
2. Restart: `python -m uvicorn backend.main:app --reload`  
3. Browser hard refresh: Ctrl+Shift+R  
4. Test:
   - Attach PDF → edit with name → then `hi` (no fake update)  
   - Ask about MCP (correct definition)  
   - Ask for MCP example (no PDF unless a file is requested)

---

## 8. Viva / defense one-liners

**Greetings bug:**  
“While a chat attachment was sticky, short messages were mishandled as edits. We short-circuit greetings and only edit on explicit edit language.”

**MCP:**  
“MCP is Model Context Protocol. We implemented a server that exposes RAG tools and a client so the chatbot can call MCP tools.”

**Unwanted PDF:**  
“File tools only run when the user explicitly asks for a file; explanations and examples stay in the chat.”

---

*End of document*
