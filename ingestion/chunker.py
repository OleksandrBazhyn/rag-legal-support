"""Розбиття документів на чанки для векторного індексування."""
from __future__ import annotations

import re

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def _split_by_separators(text: str, separators: list[str], chunk_size: int, overlap: int) -> list[str]:
    """Рекурсивне розбиття тексту за списком роздільників."""
    for sep in separators:
        if sep and sep in text:
            parts = text.split(sep)
            chunks: list[str] = []
            current = ""
            for part in parts:
                piece = (part + sep).strip()
                if not piece:
                    continue
                if len(current) + len(piece) + 1 <= chunk_size:
                    current = (current + " " + piece).strip()
                else:
                    if current:
                        chunks.append(current)
                    # перекриття: беремо хвіст попереднього чанку
                    tail = current[-overlap:] if overlap and current else ""
                    current = (tail + " " + piece).strip() if tail else piece
            if current:
                chunks.append(current)
            return chunks

    # Якщо жоден роздільник не спрацював — ділимо за розміром
    return _split_by_size(text, chunk_size, overlap)


def _split_by_size(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Просте розбиття за кількістю символів із перекриттям."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def chunk_document(doc: dict, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """Розбиває документ на чанки.

    Args:
        doc: словник з полями text, source_file, category, language
        chunk_size: максимальний розмір чанку у символах
        overlap: розмір перекриття між сусідніми чанками

    Returns:
        Список чанків; кожен чанк — dict з text + metadata.
    """
    text = doc["text"].strip()
    if not text:
        return []

    separators = ["\n\n", "\n", ". ", " ", ""]
    raw_chunks = _split_by_separators(text, separators, chunk_size, overlap)

    result: list[dict] = []
    for idx, chunk_text in enumerate(raw_chunks):
        chunk_text = chunk_text.strip()
        if len(chunk_text) < 20:
            continue
        result.append({
            "text": chunk_text,
            "chunk_index": idx,
            "source_file": doc["source_file"],
            "category": doc["category"],
            "language": doc["language"],
        })
    return result


def chunk_documents(docs: list[dict], chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """Розбиває список документів на чанки."""
    all_chunks: list[dict] = []
    for doc in docs:
        chunks = chunk_document(doc, chunk_size, overlap)
        all_chunks.extend(chunks)
    return all_chunks
