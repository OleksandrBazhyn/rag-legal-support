"""Модуль порівняльного правового контексту Україна–Німеччина."""
from __future__ import annotations

# Файли-джерела з порівняльним контентом UA↔DE
_COMPARISON_SOURCE_FILES = {
    "social_benefits_comparison.txt",
    "employment_comparison.txt",
    "residence_comparison.txt",
}

_COMPARISON_INTRO = (
    "\n\n🇺🇦🇩🇪 ПОРІВНЯЛЬНИЙ ПРАВОВИЙ КОНТЕКСТ (Україна → Німеччина):\n"
    "Нижче наведено порівняння відповідних норм, щоб допомогти зрозуміти "
    "правові реалії Німеччини через призму звичного для вас українського права:\n"
)


def add_comparison(base_prompt: str, ukrainian_chunks: list[dict]) -> str:
    """Додає блок порівняльного контексту до промпту, якщо є релевантні чанки.

    Формат: «В Україні діє [норма], в Німеччині це відповідає [норма],
    ключова відмінність: [відмінність]»

    Args:
        base_prompt: вже сформований персоналізований промпт
        ukrainian_chunks: чанки з колекції ukrainian_context

    Returns:
        Доповнений промпт або вихідний, якщо чанків немає.
    """
    if not ukrainian_chunks:
        return base_prompt

    comparison_parts = [_COMPARISON_INTRO]
    for i, chunk in enumerate(ukrainian_chunks, 1):
        source = chunk.get("source_file", "")
        text = chunk.get("text", "")
        comparison_parts.append(f"[Порівняльний матеріал {i} — {source}]\n{text}")

    comparison_parts.append(
        "\n\nОБОВ'ЯЗКОВО додайте в кінці відповіді окремий розділ у форматі:\n"
        "<b>🇺🇦🇩🇪 Порівняння: Україна → Німеччина</b>\n"
        "У цьому розділі: поясніть правила Німеччини через призму українського права, "
        "назвіть 2–3 ключові відмінності між системами. "
        "Використовуйте матеріали вище як джерело для порівняння."
    )

    return base_prompt + "\n".join(comparison_parts)


def has_relevant_comparison(ukrainian_chunks: list[dict], distance_threshold: float = 0.50) -> bool:
    """Перевіряє, чи є серед Ukrainian-чанків достатньо релевантні порівняльні матеріали.

    Тригер спрацьовує ТІЛЬКИ якщо чанк з файлу порівняння UA↔DE має
    cosine distance < distance_threshold (0.50).
    ChromaDB повертає cosine distance (0 = ідентично, 1 = нічого спільного).

    Свідоме рішення: НЕ тригерувати за назвою файлу без перевірки якості збігу —
    щоб LLM не генерував порівняння без верифікованого контексту з бази знань.
    """
    for chunk in ukrainian_chunks:
        src = chunk.get("source_file", "")
        dist = chunk.get("distance", 1.0)
        # Порівняння тільки якщо файл є порівняльним І семантично близький до запиту
        if src in _COMPARISON_SOURCE_FILES and dist < distance_threshold:
            return True
    return False
