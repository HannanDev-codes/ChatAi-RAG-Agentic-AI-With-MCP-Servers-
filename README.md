# RAG + Agentic AI Chatbot

A local chatbot that **searches your documents** (RAG) and uses an **agent loop** so the model can decide when and what to search before answering. Replies are **streamed**.

## Features

- **Ingest** PDF / TXT / MD from `data/docs` (or upload in the UI)
- **Chunking + embeddings** (`sentence-transformers` / MiniLM)
- **Chroma** vector database on disk
- **Agentic RAG**: model may call `search_documents` multiple times
- **Streaming** final answer over SSE
- **Sources** shown under each reply
- Simple web UI (no build step)

## Project layout

```
rag-agent-chatbot/
├── backend/
│   ├── main.py      # FastAPI routes + streaming
│   ├── agent.py     # Agentic tool loop (Groq)
│   ├── rag.py       # chunk, embed, Chroma, retrieve
│   └── ingest.py    # CLI ingest
├── data/docs/       # put your files here
├── frontend/        # chat UI
├── requirements.txt
└── .env.example
```

## Setup

```bash
cd rag-agent-chatbot
python -m venv venv

# Windows:
venv\Scripts\activate
# Mac/Linux:
# source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GROQ_API_KEY=gsk_...
```

### 1. Add documents

Put `.pdf`, `.txt`, or `.md` files in `data/docs/`.  
A sample file is already included: `sample_company.txt`.

### 2. Ingest (build the vector index)

```bash
python -m backend.ingest
```

Or use the **Re-ingest** / **Upload** buttons in the UI after starting the server.

### 3. Run the server

```bash
uvicorn backend.main:app --reload
```

Open **http://127.0.0.1:8000**

## Try it

Ask things like:

- “What is the price of the AR-7?”
- “How do employees reset their password?”
- “What was 2024 revenue?”

Watch the purple **Searching: …** line — that is the agent calling the RAG tool.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Key + chunk count |
| GET | `/api/models` | Model list |
| POST | `/api/ingest` | Ingest `data/docs` |
| POST | `/api/upload` | Upload + ingest one file |
| POST | `/api/search` | Raw vector search (debug) |
| POST | `/api/chat` | Agentic chat (SSE stream) |

### Chat SSE events

- `status` — “Thinking…”, “Writing answer…”
- `tool` — agent called `search_documents` with a query
- `delta` — answer token
- `done` — sources + step count
- `error` — failure message

## How the agent works

1. User question goes to Groq with a system prompt that describes the `search_documents` tool.
2. If the model replies with JSON `{"tool":"search_documents","query":"..."}`, we run Chroma search and feed results back.
3. Repeat up to a few steps.
4. When the model gives a normal answer, we **stream** it to the UI.

This is **Agentic RAG**: retrieval is a tool the model chooses to use, not a fixed single search before every answer.

## Notes

- First run downloads the embedding model (~80MB).
- Chroma data lives in `chroma_db/` (gitignored).
- Free Groq models: `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, etc.
- For private docs only — do not commit `.env` or sensitive PDFs.
