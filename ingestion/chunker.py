"""Розбиття документів на чанки для векторного індексування.

Розмір чанку вимірюється в ТОКЕНАХ (tiktoken cl100k_base), а не символах.
512 токенів ≈ 350–450 слів для німецького/українського тексту — достатньо
щоб покрити цілий параграф закону або пункт рішення суду.

Після зміни CHUNK_SIZE_TOKENS потрібне повне переіндексування (rebuild_index).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

CHUNK_SIZE_TOKENS    = 512   # цільовий розмір чанку в токенах
CHUNK_OVERLAP_TOKENS = 64    # перекриття між сусідніми чанками в токенах

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")   # те саме, що GPT-4 / text-embedding-3

    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text, disallowed_special=()))

    def _token_split(text: str, chunk_size: int, overlap: int) -> list[str]:
        """Точний розподіл за токенами з перекриттям."""
        tokens = _enc.encode(text, disallowed_special=())
        chunks: list[str] = []
        start = 0
        while start < len(tokens):
            end = min(start + chunk_size, len(tokens))
            chunks.append(_enc.decode(tokens[start:end]))
            if end == len(tokens):
                break
            start += chunk_size - overlap
        return chunks

    _HAS_TIKTOKEN = True
    logger.info("chunker: tiktoken завантажено (cl100k_base), CHUNK_SIZE=%d токенів", CHUNK_SIZE_TOKENS)

except ImportError:
    logger.warning(
        "chunker: tiktoken не встановлено — використовую символьний fallback. "
        "pip install tiktoken для кращого чанкінгу."
    )
    _HAS_TIKTOKEN = False

    def _count_tokens(text: str) -> int:          # type: ignore[misc]
        # Груба апроксимація: 1 токен ≈ 3.5 символи для DE/UK
        return len(text) // 3

    def _token_split(text: str, chunk_size: int, overlap: int) -> list[str]:  # type: ignore[misc]
        char_size    = chunk_size * 3
        char_overlap = overlap    * 3
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + char_size, len(text))
            chunks.append(text[start:end])
            if end == len(text):
                break
            start += char_size - char_overlap
        return chunks

def _split_by_separators(
    text: str,
    separators: list[str],
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """Рекурсивне розбиття тексту за ієрархією роздільників.

    Пробуємо роздільники від найширших (абзац) до найвужчих (слово).
    Розмір вимірюється в токенах.
    """
    for sep in separators:
        if sep and sep in text:
            parts = text.split(sep)
            chunks: list[str] = []
            current = ""

            for part in parts:
                piece = (part + sep).strip()
                if not piece:
                    continue

                if _count_tokens(current) + _count_tokens(piece) + 1 <= chunk_size:
                    current = (current + " " + piece).strip()
                else:
                    if current:
                        chunks.append(current)
                    # Перекриття: беремо кінець попереднього чанку
                    if overlap and current:
                        tail_tokens = _enc.encode(current, disallowed_special=()) if _HAS_TIKTOKEN else []
                        if tail_tokens:
                            tail = _enc.decode(tail_tokens[-overlap:])
                        else:
                            tail = current[-overlap * 3:]
                        current = (tail + " " + piece).strip()
                    else:
                        current = piece

            if current:
                chunks.append(current)
            return chunks

    # Жоден роздільник не спрацював — ділимо за токенами напряму
    return _token_split(text, chunk_size, overlap)


def chunk_document(
    doc: dict,
    chunk_size: int   = CHUNK_SIZE_TOKENS,
    overlap: int      = CHUNK_OVERLAP_TOKENS,
) -> list[dict]:
    """Розбиває один документ на чанки.

    Args:
        doc: словник з полями text, source_file, category, language
        chunk_size: максимальний розмір чанку в ТОКЕНАХ
        overlap: перекриття в ТОКЕНАХ

    Returns:
        Список чанків; кожен чанк — dict з text + metadata.
    """
    text = doc["text"].strip()
    if not text:
        return []

    # Ієрархія роздільників: від великих структурних до малих
    separators = [
        "\n\n",   # порожній рядок між абзацами (найкраща межа для законів)
        "\n",     # перенос рядка
        ". ",     # кінець речення
        " ",      # пробіл
        "",       # крайній випадок: символи
    ]
    raw_chunks = _split_by_separators(text, separators, chunk_size, overlap)

    result: list[dict] = []
    for idx, chunk_text in enumerate(raw_chunks):
        chunk_text = chunk_text.strip()
        # Відкидаємо надто малі чанки (< 10 токенів — зазвичай заголовки або артефакти)
        if _count_tokens(chunk_text) < 10:
            continue
        result.append({
            "text":        chunk_text,
            "chunk_index": idx,
            "source_file": doc["source_file"],
            "category":    doc["category"],
            "language":    doc["language"],
            "token_count": _count_tokens(chunk_text),
        })
    return result


def chunk_documents(
    docs: list[dict],
    chunk_size: int = CHUNK_SIZE_TOKENS,
    overlap: int    = CHUNK_OVERLAP_TOKENS,
) -> list[dict]:
    """Розбиває список документів на чанки."""
    all_chunks: list[dict] = []
    for doc in docs:
        chunks = chunk_document(doc, chunk_size, overlap)
        all_chunks.extend(chunks)
    return all_chunks
