"""Модуль порівняльного правового контексту Україна–Німеччина."""
from __future__ import annotations

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
        "\n\nВикористайте ці порівняльні матеріали, щоб ПОЯСНИТИ правила Німеччини "
        "через призму того, до чого звикли в Україні. "
        "Вкажіть ключові відмінності між правовими системами."
    )

    return base_prompt + "\n".join(comparison_parts)


def has_relevant_comparison(ukrainian_chunks: list[dict], distance_threshold: float = 0.42) -> bool:
    """Перевіряє, чи є серед Ukrainian-чанків достатньо релевантні (за відстанню cosine).

    ChromaDB повертає cosine distance (0 = ідентично, 1 = нічого спільного).
    Поріг 0.42 — висока релевантність: уникаємо хибних спрацювань на загальні запити.
    """
    return any(
        chunk.get("distance", 1.0) < distance_threshold
        for chunk in ukrainian_chunks
    )
