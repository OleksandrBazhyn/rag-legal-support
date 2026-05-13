"""Векторизація чанків та збереження в ChromaDB."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from ingestion.loader import load_directory
from ingestion.chunker import chunk_documents

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-mpnet-base-v2")
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8001"))

COLLECTION_GERMAN = "german_law"
COLLECTION_UKRAINIAN = "ukrainian_context"
DATA_DIR = Path(__file__).parent.parent / "data"


def _get_chroma_client() -> chromadb.HttpClient | chromadb.PersistentClient:
    """Повертає клієнт ChromaDB (HTTP або локальний persistent)."""
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
        chroma_dir = Path(__file__).parent.parent / "chroma_db"
        return chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )


def _get_embedding_model() -> SentenceTransformer:
    logger.info("Завантаження моделі ембеддингів: %s", EMBEDDING_MODEL)
    return SentenceTransformer(EMBEDDING_MODEL)


def _get_or_create_collection(client: chromadb.ClientAPI, name: str) -> chromadb.Collection:
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def add_documents(chunks: list[dict], client: chromadb.ClientAPI | None = None) -> int:
    """Додає чанки до відповідних колекцій ChromaDB без повного переіндексування.

    Returns:
        Кількість доданих документів.
    """
    if client is None:
        client = _get_chroma_client()

    model = _get_embedding_model()
    german_col = _get_or_create_collection(client, COLLECTION_GERMAN)
    ukrainian_col = _get_or_create_collection(client, COLLECTION_UKRAINIAN)

    german_chunks = [c for c in chunks if c["category"] == "german_law"]
    ukrainian_chunks = [c for c in chunks if c["category"] == "ukrainian_context"]

    total = 0
    for col, col_chunks in [(german_col, german_chunks), (ukrainian_col, ukrainian_chunks)]:
        if not col_chunks:
            continue
        texts = [c["text"] for c in col_chunks]
        embeddings = model.encode(texts, show_progress_bar=True).tolist()
        ids = [str(uuid.uuid4()) for _ in col_chunks]
        metadatas = [
            {
                "source_file": c["source_file"],
                "category": c["category"],
                "chunk_index": c["chunk_index"],
                "language": c["language"],
            }
            for c in col_chunks
        ]
        col.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
        total += len(col_chunks)
        logger.info("Додано %d чанків до колекції '%s'", len(col_chunks), col.name)

    return total


def rebuild_index(data_dir: Path | str | None = None) -> int:
    """Повне переіндексування: очищає колекції та індексує всі документи заново.

    Returns:
        Загальна кількість проіндексованих чанків.
    """
    if data_dir is None:
        data_dir = DATA_DIR

    data_dir = Path(data_dir)
    client = _get_chroma_client()

    # Видалення та повторне створення колекцій
    for name in [COLLECTION_GERMAN, COLLECTION_UKRAINIAN]:
        try:
            client.delete_collection(name)
            logger.info("Колекцію '%s' видалено.", name)
        except Exception:
            pass
        client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
        logger.info("Колекцію '%s' створено.", name)

    docs = list(load_directory(data_dir))
    logger.info("Завантажено %d документів.", len(docs))

    chunks = chunk_documents(docs)
    logger.info("Розбито на %d чанків.", len(chunks))

    total = add_documents(chunks, client=client)
    logger.info("Індексування завершено. Всього: %d чанків.", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = rebuild_index()
    print(f"Проіндексовано {count} чанків.")
