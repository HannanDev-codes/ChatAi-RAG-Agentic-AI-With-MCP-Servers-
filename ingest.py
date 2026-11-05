"""
CLI: ingest documents from data/docs into Chroma.

Usage (from project root):
    python -m backend.ingest
    python -m backend.ingest path/to/folder
"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.rag import DOCS_DIR, collection_stats, ingest_folder


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else DOCS_DIR
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        print(f"Created {folder}. Put PDF/TXT/MD files there and run again.")
        return

    print(f"Ingesting from: {folder}")
    results = ingest_folder(folder)
    print(f"Files processed: {results['files']}")
    print(f"Chunks stored:   {results['chunks']}")
    for d in results["details"]:
        if "error" in d:
            print(f"  ✗ {d['file']}: {d['error']}")
        else:
            print(f"  ✓ {d['file']}: {d['chunks']} chunks")

    stats = collection_stats()
    print(f"\nVector DB total chunks: {stats['count']}")
    print(f"DB path: {stats['path']}")


if __name__ == "__main__":
    main()
