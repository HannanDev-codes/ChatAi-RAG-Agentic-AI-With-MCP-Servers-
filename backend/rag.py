"""
RAG core: chunking, embeddings, Chroma vector store, retrieval.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import chromadb
from chromadb.config import Settings
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

# Paths relative to project root
ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "data" / "docs"
CHROMA_DIR = ROOT / "chroma_db"
COLLECTION_NAME = "documents"

# Defaults (overridable via env in main/ingest)
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE = 500          # characters (approx; simple & reliable)
CHUNK_OVERLAP = 80
TOP_K = 4

_embedder: SentenceTransformer | None = None
_collection = None


def get_embedder(model_name: str = EMBEDDING_MODEL_NAME) -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(model_name)
    return _embedder


def get_collection():
    """Persistent Chroma collection on disk."""
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


# ── Text loading ──────────────────────────────────────────────

def load_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def load_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        parts.append(t)
    return "\n".join(parts)


def load_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf(path)
    if suffix in {".txt", ".md", ".markdown", ".csv"}:
        return load_text_file(path)
    raise ValueError(f"Unsupported file type: {suffix}")


# ── Chunking ──────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping character chunks. Prefer breaking at paragraph/sentence."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:].strip())
            break
        # try to break at paragraph or sentence near the end
        window = text[start:end]
        break_at = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
        if break_at > chunk_size * 0.4:
            end = start + break_at + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = max(end - overlap, start + 1)
    return chunks


def chunk_id(source: str, index: int, content: str) -> str:
    h = hashlib.md5(f"{source}:{index}:{content[:64]}".encode()).hexdigest()[:12]
    return f"{Path(source).stem}_{index}_{h}"


# ── Ingest ────────────────────────────────────────────────────

def ingest_file(path: Path, collection=None) -> int:
    """Chunk + embed + upsert one file. Returns number of chunks added."""
    collection = collection or get_collection()
    embedder = get_embedder()

    text = load_document(path)
    chunks = chunk_text(text)
    if not chunks:
        return 0

    source = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
    ids = [chunk_id(source, i, c) for i, c in enumerate(chunks)]
    embeddings = embedder.encode(chunks, show_progress_bar=False).tolist()
    metadatas = [{"source": source, "chunk_index": i} for i in range(len(chunks))]

    # upsert so re-ingest updates instead of duplicating
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=chunks,
        metadatas=metadatas,
    )
    return len(chunks)


def ingest_folder(folder: Path | None = None) -> dict:
    """Ingest all supported files under data/docs (or given folder)."""
    folder = folder or DOCS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    collection = get_collection()

    supported = {".pdf", ".txt", ".md", ".markdown", ".csv"}
    results = {"files": 0, "chunks": 0, "details": []}

    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in supported:
            continue
        try:
            n = ingest_file(path, collection=collection)
            results["files"] += 1
            results["chunks"] += n
            results["details"].append({"file": path.name, "chunks": n})
        except Exception as e:
            results["details"].append({"file": path.name, "error": str(e)})

    return results


def collection_stats() -> dict:
    col = get_collection()
    return {"count": col.count(), "path": str(CHROMA_DIR)}


# ── Retrieve ──────────────────────────────────────────────────

def search_documents(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    Embed query, search Chroma, return list of
    { text, source, chunk_index, distance }.
    """
    col = get_collection()
    if col.count() == 0:
        return []

    embedder = get_embedder()
    q_emb = embedder.encode([query], show_progress_bar=False).tolist()

    res = col.query(
        query_embeddings=q_emb,
        n_results=min(top_k, col.count()),
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    docs = res["documents"][0] if res["documents"] else []
    metas = res["metadatas"][0] if res["metadatas"] else []
    dists = res["distances"][0] if res["distances"] else []

    for doc, meta, dist in zip(docs, metas, dists):
        hits.append({
            "text": doc,
            "source": meta.get("source", "unknown"),
            "chunk_index": meta.get("chunk_index", -1),
            "distance": float(dist),
        })
    return hits


def format_context(hits: list[dict]) -> str:
    """Turn retrieval hits into a context string for the LLM."""
    if not hits:
        return "No relevant documents found."
    parts = []
    for i, h in enumerate(hits, 1):
        parts.append(
            f"[{i}] Source: {h['source']} (chunk {h['chunk_index']})\n{h['text']}"
        )
    return "\n\n".join(parts)
