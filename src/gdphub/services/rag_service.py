# Human-In-The-Loop RAG service backed by ChromaDB.
# Stores manual classification and ROPA-mapping corrections as vector
# embeddings so the LLM can retrieve similar past corrections as dynamic
# few-shot examples during pipeline runs.

import logging
from pathlib import Path

import chromadb
import ollama

from gdphub.core.database import DB_FILE
from gdphub.core.config_manager import get_config

EMBEDDING_MODEL = "nomic-embed-text"
MAX_SNIPPET_CHARS = 300

_client: chromadb.ClientAPI | None = None


def _get_ollama_host() -> str:
    cfg = get_config("classify_text.py", {})
    return cfg.get("ollama_url", "http://localhost:11434")


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        chroma_dir = DB_FILE.parent / "chromadb"
        chroma_dir.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(chroma_dir))
        logging.info(f"ChromaDB initialized at {chroma_dir}")
    return _client


def _embed(texts: list[str]) -> list[list[float]]:
    host = _get_ollama_host()
    client = ollama.Client(host=host)
    results = []
    for text in texts:
        resp = client.embeddings(model=EMBEDDING_MODEL, prompt=text[:MAX_SNIPPET_CHARS])
        results.append(resp["embedding"])
    return results


# ── Classification corrections ──────────────────────────────────────

def upsert_classification(
    document_id: str,
    file_name: str,
    text_snippet: str,
    corrected_type: str,
    corrected_description: str,
) -> None:
    """Store a manual classification correction for future retrieval."""
    col = _get_client().get_or_create_collection(
        name="manual_classifications",
        metadata={"hnsw:space": "cosine"},
    )
    snippet = text_snippet[:MAX_SNIPPET_CHARS]
    doc_text = f"File: {file_name}\nText: {snippet}"
    embedding = _embed([doc_text])[0]
    col.upsert(
        ids=[document_id],
        embeddings=[embedding],
        documents=[doc_text],
        metadatas=[{
            "file_name": file_name,
            "corrected_type": corrected_type,
            "corrected_description": corrected_description,
        }],
    )
    logging.info(f"RAG: upserted classification correction for {document_id}")


def query_classification_examples(
    text_snippet: str,
    n_results: int = 2,
) -> list[dict]:
    """Retrieve the most similar past classification corrections."""
    client = _get_client()
    try:
        col = client.get_collection("manual_classifications")
    except Exception:
        return []
    if col.count() == 0:
        return []
    embedding = _embed([text_snippet[:MAX_SNIPPET_CHARS]])[0]
    results = col.query(query_embeddings=[embedding], n_results=min(n_results, col.count()))
    examples = []
    for i, meta in enumerate(results["metadatas"][0]):
        examples.append({
            "document_text": (results["documents"][0][i] if results["documents"] else ""),
            "corrected_type": meta.get("corrected_type", ""),
            "corrected_description": meta.get("corrected_description", ""),
        })
    return examples


# ── ROPA mapping corrections ────────────────────────────────────────

def upsert_ropa_mapping(
    mapping_id: int,
    document_id: str,
    classification: str,
    description: str,
    text_snippet: str,
    corrected_ropa_id: str,
    corrected_ropa_activity: str,
) -> None:
    """Store a manual ROPA mapping correction for future retrieval."""
    col = _get_client().get_or_create_collection(
        name="manual_ropa_mappings",
        metadata={"hnsw:space": "cosine"},
    )
    snippet = text_snippet[:MAX_SNIPPET_CHARS]
    doc_text = f"Classification: {classification}\nDescription: {description}\nText: {snippet}"
    embedding = _embed([doc_text])[0]
    col.upsert(
        ids=[str(mapping_id)],
        embeddings=[embedding],
        documents=[doc_text],
        metadatas=[{
            "document_id": document_id,
            "classification": classification,
            "description": description,
            "corrected_ropa_id": corrected_ropa_id,
            "corrected_ropa_activity": corrected_ropa_activity,
        }],
    )
    logging.info(f"RAG: upserted ROPA mapping correction for mapping {mapping_id}")


def query_ropa_mapping_examples(
    classification: str,
    description: str,
    text_snippet: str,
    n_results: int = 2,
) -> list[dict]:
    """Retrieve the most similar past ROPA mapping corrections."""
    client = _get_client()
    try:
        col = client.get_collection("manual_ropa_mappings")
    except Exception:
        return []
    if col.count() == 0:
        return []
    snippet = text_snippet[:MAX_SNIPPET_CHARS]
    query_text = f"Classification: {classification}\nDescription: {description}\nText: {snippet}"
    embedding = _embed([query_text])[0]
    results = col.query(query_embeddings=[embedding], n_results=min(n_results, col.count()))
    examples = []
    for i, meta in enumerate(results["metadatas"][0]):
        examples.append({
            "document_text": (results["documents"][0][i] if results["documents"] else ""),
            "corrected_ropa_id": meta.get("corrected_ropa_id", ""),
            "corrected_ropa_activity": meta.get("corrected_ropa_activity", ""),
        })
    return examples
