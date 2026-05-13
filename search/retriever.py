"""Семантичний пошук у векторній базі ChromaDB."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-mpnet-base-v2")
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8001"))


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    logger.info("Завантаження моделі ембеддингів: %s", EMBEDDING_MODEL)
    return SentenceTransformer(EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def _get_client() -> chromadb.ClientAPI:
    try:
        client = chromadb.HttpClient(
            host=CHROMA_HOST,
            port=CHROMA_PORT,
            settings=Settings(anonymized_telemetry=False),
        )
        client.heartbeat()
        logger.info("Підключено до ChromaDB: %s:%s", CHROMA_HOST, CHROMA_PORT)
        return client
    except Exception:
        logger.warning("ChromaDB HTTP недоступний, використовую локальний persistent режим.")
        from pathlib import Path
        chroma_dir = Path(__file__).parent.parent / "chroma_db"
        return chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )


def retrieve(
    query: str,
    collection: str,
    top_k: int = 5,
    client: chromadb.ClientAPI | None = None,
) -> list[dict[str, Any]]:
    """Виконує семантичний пошук у вказаній колекції ChromaDB.

    Args:
        query: пошуковий запит (будь-яка мова)
        collection: назва колекції ("german_law" або "ukrainian_context")
        top_k: кількість результатів
        client: клієнт ChromaDB (якщо None — використовується глобальний)

    Returns:
        Список словників: {"text", "source_file", "category", "distance", "chunk_index"}
    """
    if not query.strip():
        return []

    if client is None:
        client = _get_client()

    model = _get_model()

    try:
        col = client.get_collection(collection)
    except Exception as exc:
        logger.error("Колекція '%s' не знайдена: %s", collection, exc)
        return []

    embedding = model.encode([query])[0].tolist()

    results = col.query(
        query_embeddings=[embedding],
        n_results=min(top_k, col.count()),
        include=["documents", "metadatas", "distances"],
    )

    if not results["documents"] or not results["documents"][0]:
        return []

    output: list[dict[str, Any]] = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append({
            "text": doc,
            "source_file": meta.get("source_file", ""),
            "category": meta.get("category", collection),
            "chunk_index": meta.get("chunk_index", 0),
            "distance": round(float(dist), 4),
        })

    return output


def retrieve_parallel(
    query: str,
    top_k_german: int = 5,
    top_k_ukrainian: int = 2,
) -> tuple[list[dict], list[dict]]:
    """Одночасно шукає у двох колекціях.

    Returns:
        (german_chunks, ukrainian_chunks)
    """
    client = _get_client()
    german = retrieve(query, "german_law", top_k=top_k_german, client=client)
    ukrainian = retrieve(query, "ukrainian_context", top_k=top_k_ukrainian, client=client)
    return german, ukrainian
