"""Валідація завантажених правових документів перед збереженням."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


_MIN_SIZE_BYTES = 500
_MIN_WORDS = 100
_HTML_SNIFF_BYTES = 1024


@dataclass
class ValidationResult:
    is_valid: bool
    reason: Optional[str] = None

    def __bool__(self) -> bool:
        return self.is_valid


def validate(
    text: str,
    source_kind: str = "unknown",
) -> ValidationResult:
    """Перевіряє текст документа перед збереженням на диск.

    Правила:
    1. Розмір > 500 байт (UTF-8)
    2. Декодується як UTF-8 (вже маємо str, тому перевіряємо через encode)
    3. Містить >= 100 слів
    4. Не містить '<html' у перших 1024 байтах (HTML-помилка)
    5. Для Ради: не починається з "404" або помилкового повідомлення
    """
    if not text:
        return ValidationResult(False, "Порожній текст")

    # 1. Розмір
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) < _MIN_SIZE_BYTES:
        return ValidationResult(
            False, f"Розмір {len(encoded)} байт < мінімум {_MIN_SIZE_BYTES} байт"
        )

    # 2. UTF-8 (перевіряємо round-trip)
    try:
        text.encode("utf-8").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError) as exc:
        return ValidationResult(False, f"Некоректне кодування UTF-8: {exc}")

    # 3. Кількість слів
    word_count = len(text.split())
    if word_count < _MIN_WORDS:
        return ValidationResult(
            False, f"Лише {word_count} слів < мінімум {_MIN_WORDS}"
        )

    # 4. Перевірка на HTML-помилку (для текстових файлів)
    sniff = text[:_HTML_SNIFF_BYTES].lower()
    if "<html" in sniff:
        return ValidationResult(False, "Файл містить HTML-розмітку (можлива помилкова сторінка)")

    # 5. Рада: специфічні помилки
    if source_kind == "rada":
        stripped = text.strip().lower()
        if stripped.startswith("404"):
            return ValidationResult(False, "Відповідь Ради починається з '404'")
        error_markers = ["помилку не знайдено", "не знайдено", "error", "forbidden"]
        if any(marker in stripped[:200] for marker in error_markers):
            return ValidationResult(
                False, f"Відповідь Ради схожа на повідомлення про помилку: {text[:100]!r}"
            )

    return ValidationResult(True)
