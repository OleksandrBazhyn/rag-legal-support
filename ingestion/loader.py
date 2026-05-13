"""Завантаження документів з директорій."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def _load_txt(path: Path) -> str:
    """Зчитує текстовий файл."""
    return path.read_text(encoding="utf-8")


def _load_pdf(path: Path) -> str:
    """Зчитує PDF через pdfplumber."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError("Встановіть pdfplumber: pip install pdfplumber") from exc

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    return "\n".join(pages)


LOADERS: dict[str, callable] = {
    ".txt": _load_txt,
    ".pdf": _load_pdf,
}


def load_document(path: Path) -> dict:
    """Завантажує один документ і повертає словник з текстом та метаданими.

    Returns:
        {"text": str, "source_file": str, "category": str, "language": str}
    Raises:
        ValueError: якщо формат файлу не підтримується.
    """
    suffix = path.suffix.lower()
    loader = LOADERS.get(suffix)
    if loader is None:
        raise ValueError(f"Непідтримуваний формат файлу: {suffix} ({path})")

    text = loader(path)
    category = _detect_category(path)
    language = _detect_language(category)

    return {
        "text": text,
        "source_file": path.name,
        "category": category,
        "language": language,
    }


def load_directory(directory: Path | str) -> Iterator[dict]:
    """Рекурсивно завантажує всі підтримувані документи з директорії."""
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Директорія не знайдена: {directory}")

    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in LOADERS:
            try:
                doc = load_document(path)
                logger.info("Завантажено: %s (%s)", path.name, doc["category"])
                yield doc
            except Exception as exc:
                logger.error("Помилка завантаження %s: %s", path, exc)


def _detect_category(path: Path) -> str:
    """Визначає категорію документа за розташуванням у директорії."""
    parts = [p.lower() for p in path.parts]
    if "german_law" in parts:
        return "german_law"
    if "ukrainian_context" in parts:
        return "ukrainian_context"
    return "unknown"


def _detect_language(category: str) -> str:
    """Визначає мову документа за категорією."""
    mapping = {
        "german_law": "de",
        "ukrainian_context": "uk",
    }
    return mapping.get(category, "uk")
