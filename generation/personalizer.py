"""Побудова персоналізованого системного промпту з профілю користувача."""
from __future__ import annotations

from api.models import UserProfile, LegalStatus, QueryCategory

_DISCLAIMER = (
    "\n\n⚠️ Це інформаційна підтримка, а не офіційна юридична консультація. "
    "Для вирішення конкретної правової ситуації рекомендується звернутися до "
    "кваліфікованого юриста або до безкоштовних міграційних консультаційних центрів."
)

_STATUS_CONTEXT: dict[LegalStatus, str] = {
    LegalStatus.temporary_protection: (
        "Користувач має статус тимчасового захисту відповідно до § 24 AufenthG "
        "(Директива ЄС 2001/55/EG, активована Рішенням Ради ЄС 2022/382 для громадян України). "
        "Цей статус надає право на роботу без окремого дозволу, Bürgergeld (SGB II), "
        "медичне страхування через GKV та доступ до освіти."
    ),
    LegalStatus.residence_permit: (
        "Користувач має Aufenthaltserlaubnis (дозвіл на проживання). "
        "Конкретні права залежать від підстави видачі (§ 16, § 17, § 25 тощо). "
        "Слід уточнити підставу видачі для точної відповіді."
    ),
    LegalStatus.asylum_seeker: (
        "Користувач подав заяву про надання притулку (асилу) через BAMF. "
        "Під час розгляду справи застосовується Asylbewerberleistungsgesetz (AsylbLG). "
        "Доступ до роботи та соціальних послуг обмежений порівняно з § 24 AufenthG."
    ),
    LegalStatus.unknown: (
        "Правовий статус користувача невідомий. "
        "Надайте загальну інформацію і порекомендуйте уточнити статус в Ausländerbehörde."
    ),
}

_REGIONAL_NOTES: dict[str, str] = {
    "Bayern": (
        "Баварія (Bayern): Jobcenter та Ausländerbehörde можуть мати специфічні регіональні правила. "
        "Мюнхен має власне Sozialreferat з окремими процедурами. "
        "Ліміти оренди в Мюнхені значно вищі за середні по Німеччині."
    ),
    "Berlin": (
        "Берлін: Landesamt für Einwanderung (LEA) — центральний орган для іноземців. "
        "Особливі програми підтримки для українців: Ukraineberatungszentrum. "
        "Часті черги — рекомендується онлайн-запис."
    ),
    "NRW": (
        "Північний Рейн-Вестфалія (NRW): виплати через Kommunale Integrationszentren. "
        "Найбільша кількість українців серед усіх земель. "
        "Düsseldorf, Köln, Dortmund мають розвинуту мережу консультаційних центрів."
    ),
    "Hamburg": (
        "Гамбург: Behörde für Arbeit, Soziales, Familie und Integration. "
        "Sprunghilfe та програма InteA для дорослих без шкільних документів."
    ),
    "Sachsen": (
        "Саксонія (Sachsen): Landesdirektion Sachsen видає документи для тимчасового захисту. "
        "Дрезден та Лейпциг мають активні українські громади."
    ),
}

_CATEGORY_FOCUS: dict[QueryCategory, str] = {
    QueryCategory.residence: "Зосередьтесь на питаннях дозволу на проживання, реєстрації, продовженні документів.",
    QueryCategory.social_benefits: "Зосередьтесь на Bürgergeld, SGB II/XII, умовах призначення та розмірах виплат.",
    QueryCategory.employment: "Зосередьтесь на праві на роботу, умовах трудового договору, мінімальній зарплаті.",
    QueryCategory.education: "Зосередьтесь на шкільній освіті, Kita, вищій освіті, визнанні дипломів.",
    QueryCategory.healthcare: "Зосередьтесь на медичному страхуванні (GKV), доступі до лікарів, ліках.",
    QueryCategory.other: "Надайте загальну правову інформацію щодо ситуації українців у Німеччині.",
}

_LANGUAGE_INSTRUCTION: dict[str, str] = {
    "uk": (
        "Відповідайте українською мовою. "
        "ВИНЯТОК: якщо користувач явно просить написати офіційний документ, лист, заяву або текст "
        "іншою мовою (наприклад 'напиши заяву німецькою', 'schreib auf Deutsch', 'in German') — "
        "напишіть цей документ запитаною мовою. Пояснення до документа залиште українською."
    ),
    "de": (
        "Antworten Sie auf Deutsch. "
        "Schreiben Sie offizielle Dokumente in der vom Nutzer explizit gewünschten Sprache."
    ),
    "en": (
        "Respond in English. "
        "Write formal documents in the language explicitly requested by the user."
    ),
}


def build_prompt(
    profile: UserProfile,
    query: str,
    retrieved_chunks: list[dict],
) -> str:
    """Формує персоналізований системний промпт для LLM.

    Args:
        profile: профіль користувача
        query: запит користувача
        retrieved_chunks: релевантні чанки з german_law

    Returns:
        Повний текст промпту (system + контекст + запит)
    """
    parts: list[str] = []

    # Мовна інструкція
    lang_instr = _LANGUAGE_INSTRUCTION.get(profile.language, _LANGUAGE_INSTRUCTION["uk"])
    parts.append(lang_instr)

    # Роль і контекст
    parts.append(
        "\nВи — система правової інформаційної підтримки для українських громадян у Німеччині. "
        "Ваше завдання — надавати точні, зрозумілі відповіді на правові запити "
        "на основі наданих правових документів."
    )

    # Правовий статус
    status_ctx = _STATUS_CONTEXT.get(profile.legal_status, _STATUS_CONTEXT[LegalStatus.unknown])
    parts.append(f"\n📋 ПРАВОВИЙ СТАТУС КОРИСТУВАЧА:\n{status_ctx}")

    # Регіон
    if profile.region and profile.region.lower() != "unknown":
        regional = _REGIONAL_NOTES.get(profile.region, "")
        if regional:
            parts.append(f"\n📍 РЕГІОНАЛЬНІ ОСОБЛИВОСТІ:\n{regional}")
        else:
            parts.append(f"\n📍 Регіон: {profile.region}")

    # Категорія запиту
    category_hint = _CATEGORY_FOCUS.get(profile.query_category, _CATEGORY_FOCUS[QueryCategory.other])
    parts.append(f"\n🎯 ФОКУС ВІДПОВІДІ:\n{category_hint}")

    # Контекст з документів
    if retrieved_chunks:
        parts.append("\n📚 РЕЛЕВАНТНІ ПРАВОВІ ДОКУМЕНТИ:")
        for i, chunk in enumerate(retrieved_chunks, 1):
            source = chunk.get("source_file", "невідомо")
            parts.append(f"\n[Документ {i} — {source}]\n{chunk.get('text', '')}")

    # Запит
    parts.append(f"\n\n❓ ЗАПИТ КОРИСТУВАЧА:\n{query}")

    # Інструкції для відповіді
    parts.append(
        "\n\n📝 ІНСТРУКЦІЇ ДЛЯ ВІДПОВІДІ:"
        "\n1. Відповідайте КОНКРЕТНО і по суті — назвіть право, умову або суму прямо."
        "\n2. БАЗУЙТЕСЬ ВИКЛЮЧНО на наданих документах вище. Якщо потрібна інформація відсутня "
        "в документах — так і скажіть, не додавайте факти зі свого навчання."
        "\n3. Посилайтесь на конкретні параграфи (§ X Закону) з наданих документів."
        "\n4. НЕ обмежуйтесь порадою 'зверніться до органів' як єдиною відповіддю — "
        "спочатку надайте реальну правову інформацію, і лише потім за потреби згадайте куди звернутись."
        "\n5. Якщо документи не дають відповіді — чесно скажіть що саме невідомо."
        "\n6. Уникайте юридичного жаргону — пояснюйте простою мовою."
        "\n7. Відповідь має бути СТРУКТУРОВАНОЮ — виділяйте ключову цифру/факт на початку, "
        "потім деталі та пояснення. Охопіть усі важливі аспекти питання повністю; "
        "не скорочуйте відповідь якщо питання потребує розгорнутого пояснення."
        "\n8. ФОРМАТУВАННЯ — обов'язково дотримуйтесь:"
        "\n   - Для жирного тексту: <b>текст</b> (НЕ **текст**)"
        "\n   - Для курсиву: <i>текст</i> (НЕ _текст_)"
        "\n   - Для коду/термінів: <code>§ 24</code>"
        "\n   - ЗАБОРОНЕНО: **, __, _підкреслення_, ~~~, #заголовки"
        "\n   - Нумеровані та марковані списки — звичайним текстом (1. / •)"
    )

    parts.append(_DISCLAIMER)

    return "\n".join(parts)
