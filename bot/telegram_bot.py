"""Telegram-бот для системи правової підтримки."""
from __future__ import annotations

import asyncio
import base64
import datetime
import io
import json
import logging
import os
import re
from html import escape
from pathlib import Path

import httpx
import pdfplumber
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

from bot import db

# Імпортуємо validator напряму (бот не ходить через FastAPI middleware)
import sys, os as _os
sys.path.insert(0, str(Path(__file__).parent.parent))
from api.security import InjectionDetected, sanitize_for_log, validate_question as _validate_q

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# ── Ліміти безпеки ────────────────────────────────────────────────────────────
_MAX_QUESTION_LEN   = 2000          # символів у текстовому питанні
_MAX_PHOTO_SIZE_MB  = 8             # МБ для фото/зображень
_MAX_DOC_SIZE_MB    = 5             # МБ для PDF/TXT
_MAX_PDF_PAGES      = 20            # сторінок PDF (захист від zip-bomb)
_PDF_TEXT_LIMIT     = 8000          # символів тексту з PDF

# Стани ConversationHandler
PROFILE_STATUS, PROFILE_REGION, PROFILE_LANGUAGE = range(3)

# ─── Сховище в пам'яті (кеш поверх SQLite) ───────────────────────────────────
_user_profiles: dict[int, dict] = {}       # user_id → profile dict (кеш)
_user_last_sources: dict[int, list[str]] = {}
_chat_history: dict[int, list[dict]] = {}  # user_id → [{role, content}]

_MAX_HISTORY = 6   # 3 пари (user + assistant)

# Привітання — не йдуть до RAG
_GREETINGS = {
    "привіт", "вітаю", "добрий", "доброго", "hello", "hi", "hey", "hallo",
    "guten", "добрий день", "добрий ранок", "добрий вечір", "хай", "вітання",
}

# Ключові слова для авто-визначення статусу
_STATUS_KEYWORDS: dict[str, list[str]] = {
    "temporary_protection": [
        "§24", "§ 24", "тимчасовий захист", "temporary protection",
        "aufenthaltsgestattung", "vorübergehender schutz",
    ],
    "asylum_seeker": [
        "притулок", "асил", "asylum", "asyl", "bamf заява", "duldung",
    ],
    "residence_permit": [
        "aufenthaltserlaubnis", "дозвіл на проживання", "residence permit",
        "niederlassungserlaubnis",
    ],
}

# Debounce
_pending_messages: dict[int, list[str]] = {}
_pending_tasks: dict[int, asyncio.Task] = {}
_DEBOUNCE_SECONDS = 2.5

# Процедури для генератора чеклистів
_CHECKLIST_PROCEDURES: dict[str, str] = {
    "burgergeld": "оформлення Bürgergeld (SGB II — базове соціальне забезпечення)",
    "aufenthalt": "продовження або отримання Aufenthaltstitel (дозвіл на проживання, §24 AufenthG)",
    "work": "офіційного працевлаштування (трудовий договір, реєстрація)",
    "school": "запису дитини до школи або Kita (дитячого садка)",
    "health": "оформлення медичного страхування (GKV — gesetzliche Krankenversicherung)",
    "wohngeld": "отримання Wohngeld (субсидія на оренду житла)",
    "recognition": "визнання українського диплому або кваліфікації в Німеччині",
}


# ─── HTML-конвертер ───────────────────────────────────────────────────────────

def _to_html(text: str) -> str:
    """Конвертує Markdown-розмітку GPT у Telegram HTML."""
    # Якщо LLM згенерував HTML-теги — конвертуємо їх у markdown до escape()
    text = re.sub(r'<code>(.*?)</code>', r'`\1`',     text, flags=re.DOTALL)
    text = re.sub(r'<b>(.*?)</b>',       r'**\1**',   text, flags=re.DOTALL)
    text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<i>(.*?)</i>',       r'_\1_',     text, flags=re.DOTALL)
    text = re.sub(r'<em>(.*?)</em>',     r'_\1_',     text, flags=re.DOTALL)
    text = re.sub(r'</?[a-zA-Z][^>]*>', '', text)   # прибираємо решту HTML-тегів (тільки валідні теги)
    text = escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__',     r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)',   r'<i>\1</i>', text)
    text = re.sub(r'`([^`\n]+?)`', r'<code>\1</code>', text)
    text = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    return text


# ─── Профіль: кешований доступ до SQLite ─────────────────────────────────────

def _get_profile(user_id: int) -> dict:
    """Повертає профіль з кешу або завантажує з БД."""
    if user_id not in _user_profiles:
        saved = db.load_profile(user_id)
        _user_profiles[user_id] = saved if saved is not None else _default_profile()
    return _user_profiles[user_id]


def _save_profile(user_id: int, profile: dict, first_name: str | None = None) -> None:
    """Зберігає профіль у кеші та БД."""
    _user_profiles[user_id] = profile
    db.save_profile(user_id, profile, first_name)


def _default_profile() -> dict:
    return {
        "legal_status": "unknown",
        "region": "unknown",
        "query_category": "other",
        "language": "uk",
    }


# ─── Клавіатури ──────────────────────────────────────────────────────────────

def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 Тимчасовий захист (§ 24)", callback_data="status_temporary_protection")],
        [InlineKeyboardButton("📄 Дозвіл на проживання",     callback_data="status_residence_permit")],
        [InlineKeyboardButton("📋 Заявка на притулок",       callback_data="status_asylum_seeker")],
        [InlineKeyboardButton("❓ Не знаю / Інше",           callback_data="status_unknown")],
    ])


def _region_keyboard() -> InlineKeyboardMarkup:
    regions = [
        "Bayern", "Berlin", "NRW", "Hamburg",
        "Sachsen", "Baden-Württemberg", "Hessen", "Інший регіон",
    ]
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    )


def _language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_uk")],
        [InlineKeyboardButton("🇩🇪 Deutsch",    callback_data="lang_de")],
        [InlineKeyboardButton("🇬🇧 English",    callback_data="lang_en")],
    ])


def _checklist_keyboard() -> InlineKeyboardMarkup:
    labels = {
        "burgergeld":  "💰 Bürgergeld",
        "aufenthalt":  "🏠 Aufenthaltstitel / §24",
        "work":        "💼 Працевлаштування",
        "school":      "🎓 Школа / Kita",
        "health":      "🏥 Медична страховка",
        "wohngeld":    "🏘 Wohngeld (субсидія)",
        "recognition": "📜 Визнання диплому",
    }
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"checklist_{key}")]
         for key, label in labels.items()]
    )


def _answer_keyboard() -> InlineKeyboardMarkup:
    """Inline-кнопки швидких дій після кожної відповіді."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Джерела",   callback_data="action_sources"),
            InlineKeyboardButton("📋 Чеклист",   callback_data="action_checklist"),
        ],
        [
            InlineKeyboardButton("🔄 Уточнити відповідь", callback_data="action_clarify"),
        ],
    ])


# ─── Команди ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Зберігаємо ім'я у БД при першому старті
    profile = _get_profile(user.id)
    db.save_profile(user.id, profile, first_name=user.first_name)

    await update.message.reply_text(
        f"Вітаю, {user.first_name}! 👋\n\n"
        "Я — система правової інформаційної підтримки для українських громадян у Німеччині. "
        "Відповідаю на питання про:\n\n"
        "🏠 Право на проживання (Aufenthaltsrecht)\n"
        "💰 Соціальні виплати (Bürgergeld, SGB II/XII)\n"
        "💼 Трудове право та роботу\n"
        "🎓 Освіту та школу для дітей\n"
        "🏥 Медичне страхування\n\n"
        "<b>На відміну від загальних чат-ботів:</b>\n"
        "• Відповіді базуються на <b>актуальних текстах законів Німеччини</b> (SGB II, AufenthG тощо)\n"
        "• Враховую <b>ваш правовий статус і регіон</b> — відповідь для §24 і для Asylbewerber різна\n"
        "• Зберігаю ваші дані між розмовами — не треба пояснювати ситуацію щоразу заново\n"
        "• Можу прочитати ваш <b>Bescheid</b> і зберегти терміни та суми\n\n"
        "📌 <b>Команди:</b>\n"
        "/profile — налаштувати профіль (статус, регіон, мова)\n"
        "/checklist — список документів для конкретної процедури\n"
        "/mydata — мої збережені документи та терміни\n"
        "/usage — ліміт запитів на сьогодні\n"
        "/sources — джерела останньої відповіді\n"
        "/clear — нова розмова (скинути контекст)\n"
        "/help — довідка\n\n"
        "⚠️ Це інформаційна підтримка, не юридична консультація.",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📚 <b>ДОВІДКА</b>\n\n"
        "/start — почати роботу\n"
        "/profile — налаштувати профіль (статус, регіон, мова)\n"
        "/checklist — <b>генератор чеклистів документів</b> для Bürgergeld, роботи, школи тощо\n"
        "/mydata — переглянути збережені дані ваших документів\n"
        "/usage — ліміт запитів на сьогодні\n"
        "/sources — джерела останньої відповіді\n"
        "/clear — скинути контекст розмови\n\n"
        "❓ Просто <b>напишіть питання</b> — відповім без /ask\n\n"
        "📷 <b>Надішліть фото Bescheid або документа</b> — прочитаю і збережу терміни/суми\n"
        "📄 <b>Надішліть .pdf або .txt</b> — проаналізую\n\n"
        "⚠️ Відповіді загальноінформаційні. Для конкретної ситуації — "
        "зверніться до юриста або міграційного центру.",
        parse_mode="HTML",
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    _chat_history.pop(user_id, None)
    await update.message.reply_text("🗑 Контекст розмови очищено. Починаємо нову.")


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    sources = _user_last_sources.get(user_id)
    if not sources:
        await update.message.reply_text(
            "Ще немає джерел. Спочатку задайте питання."
        )
        return
    src_list = "\n".join(f"• <code>{s}</code>" for s in sources)
    await update.message.reply_text(
        f"📚 <b>Джерела останньої відповіді:</b>\n\n{src_list}",
        parse_mode="HTML",
    )


async def cmd_mydata(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує збережені профіль і дані документів."""
    user_id = update.effective_user.id
    profile = _get_profile(user_id)
    documents = db.get_documents(user_id)

    status_labels = {
        "temporary_protection": "🛡 Тимчасовий захист (§24)",
        "residence_permit":     "📄 Дозвіл на проживання",
        "asylum_seeker":        "📋 Заявник на притулок",
        "unknown":              "❓ Невідомо",
    }
    lang_labels = {"uk": "Українська", "de": "Deutsch", "en": "English"}

    lines = [
        "<b>👤 Ваш профіль:</b>",
        f"Статус: {status_labels.get(profile.get('legal_status', 'unknown'), '?')}",
        f"Регіон: {profile.get('region', 'unknown')}",
        f"Мова:   {lang_labels.get(profile.get('language', 'uk'), '?')}",
    ]

    if documents:
        lines.append("\n<b>📂 Збережені документи:</b>")
        for doc in documents:
            doc_lines = [f"\n<b>{doc['doc_type']}</b>"]
            if doc.get("valid_until"):
                from datetime import date
                try:
                    expiry = date.fromisoformat(doc["valid_until"])
                    days_left = (expiry - date.today()).days
                    warning = ""
                    if days_left < 0:
                        warning = " ⚠️ <b>ПРОСТРОЧЕНО!</b>"
                    elif days_left <= 30:
                        warning = f" ⚠️ <b>залишилось {days_left} дн.!</b>"
                    elif days_left <= 60:
                        warning = f" (залишилось {days_left} дн.)"
                    doc_lines.append(f"Дійсний до: {doc['valid_until']}{warning}")
                except ValueError:
                    doc_lines.append(f"Дійсний до: {doc['valid_until']}")
            if doc.get("monthly_amount"):
                doc_lines.append(f"Сума: {doc['monthly_amount']:.2f} €/міс.")
            if doc.get("case_number"):
                doc_lines.append(f"Номер справи: <code>{doc['case_number']}</code>")
            if doc.get("issuing_office"):
                doc_lines.append(f"Видав: {doc['issuing_office']}")
            lines.extend(doc_lines)
    else:
        lines.append(
            "\n<i>Збережених документів немає. "
            "Надішліть фото Bescheid — я збережу дані автоматично.</i>"
        )

    lines.append("\n/profile — змінити профіль")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─── /checklist ──────────────────────────────────────────────────────────────

async def cmd_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує меню вибору процедури для генерації чеклисту."""
    await update.message.reply_text(
        "📋 <b>Генератор чеклистів документів</b>\n\n"
        "Оберіть процедуру — я сформую <b>персоналізований список</b> потрібних документів "
        "з урахуванням вашого статусу та регіону.\n\n"
        "<i>Це не просто загальний список — він адаптований під вашу ситуацію.</i>",
        reply_markup=_checklist_keyboard(),
        parse_mode="HTML",
    )


async def checklist_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Генерує персоналізований чеклист через RAG-пайплайн."""
    query = update.callback_query
    await query.answer()

    procedure_key = query.data.replace("checklist_", "")
    procedure = _CHECKLIST_PROCEDURES.get(procedure_key, procedure_key)

    user_id = update.effective_user.id
    profile = _get_profile(user_id)

    # Позначаємо категорію запиту для personalizer
    category_map = {
        "burgergeld":  "social_benefits",
        "aufenthalt":  "residence",
        "work":        "employment",
        "school":      "education",
        "health":      "healthcare",
        "wohngeld":    "social_benefits",
        "recognition": "education",
    }
    profile["query_category"] = category_map.get(procedure_key, "other")

    status_msg = await query.edit_message_text(
        f"⏳ Формую персоналізований чеклист для <b>{procedure}</b>…\n\n"
        "<i>Враховую ваш статус та регіон…</i>",
        parse_mode="HTML",
    )

    checklist_query = (
        f"Надай детальний покроковий чеклист документів, необхідних для {procedure}. "
        "Для кожного пункту вкажи:\n"
        "• Назву документа (українською та німецькою)\n"
        "• Де отримати (яка установа, чи можна онлайн)\n"
        "• Чи потрібен переклад або нотаріальне засвідчення\n\n"
        "Враховуй конкретний правовий статус та регіон користувача. "
        "Вкажи якщо деякі документи не потрібні для поточного статусу. "
        "Формат: нумерований список, конкретно і практично."
    )

    await _process_question(
        update, context, checklist_query,
        status_msg=status_msg,
        profile_override=profile,
    )


# ─── /ask ─────────────────────────────────────────────────────────────────────

async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Введіть запитання після команди.\n"
            "Приклад: /ask Чи маю я право на Bürgergeld?\n\n"
            "Або просто напишіть питання — відповім без /ask."
        )
        return
    question = " ".join(context.args)
    await _process_question(update, context, question)


# ─── Debounce ─────────────────────────────────────────────────────────────────

async def _debounced_process(
    user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await asyncio.sleep(_DEBOUNCE_SECONDS)
    messages = _pending_messages.pop(user_id, [])
    _pending_tasks.pop(user_id, None)
    if not messages:
        return
    await _process_question(update, context, "\n".join(messages))


def _enqueue_message(
    user_id: int, text: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    _pending_messages.setdefault(user_id, []).append(text)
    existing = _pending_tasks.get(user_id)
    if existing and not existing.done():
        existing.cancel()
    task = asyncio.create_task(_debounced_process(user_id, update, context))
    _pending_tasks[user_id] = task


def _is_greeting(text: str) -> bool:
    words = set(text.lower().strip().rstrip("!?.").split())
    return bool(words & _GREETINGS) and len(words) <= 5


def _detect_status(text: str) -> str | None:
    lower = text.lower()
    for status, keywords in _STATUS_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return status
    return None


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if len(text) < 3:
        return

    user_id = update.effective_user.id
    logger.info("[user=%d] текст: %s", user_id, text[:120])

    if _is_greeting(text):
        logger.info("[user=%d] привітання — без RAG", user_id)
        await update.message.reply_text(
            "Вітаю! 👋 Я — правовий помічник для українців у Німеччині.\n\n"
            "Напишіть питання — наприклад:\n"
            "• <i>Чи маю я право на Bürgergeld?</i>\n"
            "• <i>Як оформити медичне страхування?</i>\n"
            "• <i>Які документи потрібні для роботи?</i>\n\n"
            "Або надішліть фото Bescheid / PDF-файл.",
            parse_mode="HTML",
        )
        return

    # ── Валідація та injection guard ──────────────────────────────────────────
    try:
        text = _validate_q(text, max_length=_MAX_QUESTION_LEN)
    except InjectionDetected:
        logger.warning("[user=%d] injection blocked: %s", user_id, sanitize_for_log(text))
        await update.message.reply_text(
            "⛔ Запит містить недозволені інструкції.\n"
            "Система призначена виключно для правових питань щодо перебування в Німеччині."
        )
        return
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    detected = _detect_status(text)
    if detected:
        profile = _get_profile(user_id)
        if profile.get("legal_status") != detected:
            profile["legal_status"] = detected
            _save_profile(user_id, profile)
            status_labels = {
                "temporary_protection": "тимчасовий захист (§24)",
                "asylum_seeker": "заявник на притулок",
                "residence_permit": "дозвіл на проживання",
            }
            await update.message.reply_text(
                f"✅ Зрозумів — ваш статус: <b>{status_labels[detected]}</b>. "
                "Враховуватиму це у відповідях.",
                parse_mode="HTML",
            )
            if len(text.split()) <= 6:
                return

    _enqueue_message(user_id, text, update, context)


# ─── Фото: OpenAI Vision + Bescheid extractor ────────────────────────────────

_BESCHEID_EXTRACT_PROMPT = """
Ти — асистент, що аналізує офіційні документи для українців у Німеччині.
Якщо на фото є офіційний документ (Bescheid, Aufenthaltstitel, лист, форма) —
витягни структуровані дані у форматі JSON:

{
  "doc_type": "назва документа (наприклад: Bürgergeld Bescheid, Aufenthaltstitel §24, Krankenkassenkarte, Duldung)",
  "valid_until": "РРРР-ММ-ДД або null",
  "monthly_amount": число або null (сума в євро, якщо є),
  "case_number": "рядок або null",
  "issuing_office": "назва установи або null",
  "description": "короткий людиночитний опис того, що ти бачиш (2-3 речення)"
}

Якщо це НЕ офіційний документ — поверни:
{"doc_type": null, "description": "опис що на фото"}

Відповідай ТІЛЬКИ валідним JSON, без markdown-обгортки.
"""


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Читає документ/фото через OpenAI Vision, витягує структуровані дані, зберігає у БД."""
    user_id = update.effective_user.id
    logger.info("[user=%d] отримано фото", user_id)
    # Перевіряємо розмір фото перед завантаженням
    photo_meta = update.message.photo[-1]
    if photo_meta.file_size and photo_meta.file_size > _MAX_PHOTO_SIZE_MB * 1024 * 1024:
        await update.message.reply_text(
            f"⚠️ Зображення завелике ({photo_meta.file_size // (1024*1024)} МБ). "
            f"Максимум: {_MAX_PHOTO_SIZE_MB} МБ."
        )
        return

    msg = await update.message.reply_text("📷 Читаю документ на фото…")

    try:
        photo = update.message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        raw = await tg_file.download_as_bytearray()
        b64 = base64.b64encode(bytes(raw)).decode()
        caption = update.message.caption or ""

        # Async клієнт — не блокує event loop під час Vision API виклику
        async_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

        # ── Крок 1: Структурований витяг ──────────────────────────────────────
        vision_resp = await async_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": _BESCHEID_EXTRACT_PROMPT},
                ],
            }],
            max_tokens=500,
        )
        raw_json = vision_resp.choices[0].message.content or "{}"

        # Очищаємо від можливих markdown-обгорток
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json.strip())
        raw_json = re.sub(r"\s*```$", "", raw_json)

        try:
            extracted = json.loads(raw_json)
        except json.JSONDecodeError:
            extracted = {"doc_type": None, "description": raw_json[:300]}

        doc_type = extracted.get("doc_type")
        description = extracted.get("description", "")
        logger.info("[user=%d] фото витяг: doc_type=%s", user_id, doc_type)

        # ── Крок 2: Зберігаємо у БД якщо це документ ─────────────────────────
        if doc_type:
            db.save_document(
                user_id=user_id,
                doc_type=doc_type,
                valid_until=extracted.get("valid_until"),
                monthly_amount=extracted.get("monthly_amount"),
                case_number=extracted.get("case_number"),
                issuing_office=extracted.get("issuing_office"),
                raw_description=description,
            )
            logger.info("[user=%d] документ збережено: %s", user_id, doc_type)

            # ── Формуємо повідомлення про витягнуті дані ──────────────────────
            save_lines = [f"📷 <b>Розпізнано: {escape(doc_type)}</b>"]
            if extracted.get("valid_until"):
                from datetime import date
                try:
                    expiry = date.fromisoformat(extracted["valid_until"])
                    days_left = (expiry - date.today()).days
                    if days_left < 0:
                        warn = " ⚠️ <b>ПРОСТРОЧЕНО</b>"
                    elif days_left <= 30:
                        warn = f" ⚠️ <b>{days_left} дн. до закінчення!</b>"
                    else:
                        warn = f" ({days_left} дн.)"
                    save_lines.append(f"📅 Дійсний до: <b>{extracted['valid_until']}</b>{warn}")
                except ValueError:
                    save_lines.append(f"📅 Дійсний до: {extracted['valid_until']}")
            if extracted.get("monthly_amount"):
                save_lines.append(f"💶 Сума: <b>{extracted['monthly_amount']:.2f} €/міс.</b>")
            if extracted.get("case_number"):
                save_lines.append(f"🔢 Номер справи: <code>{escape(str(extracted['case_number']))}</code>")
            if extracted.get("issuing_office"):
                save_lines.append(f"🏛 Видав: {escape(str(extracted['issuing_office']))}")
            save_lines.append("✅ <i>Дані збережено (/mydata — переглянути)</i>")
            save_lines.append("")
            save_lines.append("⏳ Аналізую правові аспекти документа…")

            await msg.edit_text("\n".join(save_lines), parse_mode="HTML")
        else:
            await msg.edit_text(
                f"📷 {escape(description[:200])}\n\n⏳ Шукаю правову інформацію…",
                parse_mode="HTML",
            )

        # ── Крок 3: Передаємо опис у RAG для правового аналізу ───────────────
        query = f"[Документ на фото: {doc_type or 'невідомий тип'}]\n{description}"
        if caption:
            query += f"\n[Питання]: {caption}"
        elif doc_type:
            query += "\n[Питання]: Що мені важливо знати про цей документ? Які мої права та обов'язки?"

        await _process_question(update, context, query, status_msg=msg)

    except Exception as exc:
        logger.error("[user=%d] Помилка обробки фото: %s", user_id, exc, exc_info=True)
        await msg.edit_text("❌ Не вдалося прочитати фото. Спробуйте надіслати текстом.")


# ─── Файли: .txt і .pdf ───────────────────────────────────────────────────────

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    doc = update.message.document
    file_name = doc.file_name or "document"
    ext = Path(file_name).suffix.lower()
    logger.info("[user=%d] файл: %s (%d bytes)", user_id, file_name, doc.file_size or 0)

    if ext not in (".txt", ".pdf"):
        await update.message.reply_text(
            f"⚠️ Формат {ext or 'невідомий'} не підтримується.\n"
            "Надішліть .txt або .pdf."
        )
        return

    # ── Перевірка розміру файлу ───────────────────────────────────────────────
    max_mb = _MAX_DOC_SIZE_MB
    if doc.file_size and doc.file_size > max_mb * 1024 * 1024:
        await update.message.reply_text(
            f"⚠️ Файл завеликий ({doc.file_size // (1024*1024)} МБ). "
            f"Максимум: {max_mb} МБ для {ext}."
        )
        return

    msg = await update.message.reply_text(f"📄 Читаю {file_name}…")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        raw = bytes(await tg_file.download_as_bytearray())

        text = ""  # ініціалізуємо заздалегідь — захист від undefined якщо виняток у PDF-гілці
        if ext == ".txt":
            text = raw.decode("utf-8", errors="replace")
        else:
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                n_pages = len(pdf.pages)
                if n_pages > _MAX_PDF_PAGES:
                    await msg.edit_text(
                        f"⚠️ PDF має {n_pages} сторінок. "
                        f"Читаю тільки перші {_MAX_PDF_PAGES}."
                    )
                text = "\n\n".join(
                    p.extract_text() or "" for p in pdf.pages[:_MAX_PDF_PAGES]
                )

        text = text.strip()
        if not text:
            await msg.edit_text("⚠️ Не вдалося прочитати текст з файлу.")
            return

        truncated = text[:_PDF_TEXT_LIMIT]
        caption = update.message.caption or ""

        await msg.edit_text(f"📄 Прочитав {file_name} ({len(text):,} симв.). Аналізую…")

        query = (
            f"[Файл: {file_name}]\n{truncated}"
            + (f"\n\n[Питання]: {caption}" if caption
               else "\n\n[Питання]: Яка правова інформація в цьому документі? Що важливо знати?")
        )
        await _process_question(update, context, query, status_msg=msg)

    except Exception as exc:
        logger.error("[user=%d] Помилка файлу %s: %s", user_id, file_name, exc, exc_info=True)
        await msg.edit_text("❌ Помилка при читанні файлу. Спробуйте ще раз.")


# ─── Основна функція генерації відповіді ─────────────────────────────────────

async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує поточний стан денного ліміту запитів."""
    user_id = update.effective_user.id
    u = db.get_usage(user_id)
    effective = u["limit"] + u["bonus"]
    bar_filled = round(u["count"] / effective * 10) if effective else 0
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    bonus_line = f"\n🎁 Бонусних запитів: <b>{u['bonus']}</b>" if u["bonus"] else ""
    status = "✅ Доступно" if u["remaining"] > 0 else "🔴 Вичерпано"
    await update.message.reply_text(
        f"📊 <b>Денний ліміт запитів</b>\n\n"
        f"[{bar}] {u['count']}/{effective}\n\n"
        f"Використано сьогодні: <b>{u['count']}</b>\n"
        f"Базовий ліміт: <b>{u['limit']}</b>{bonus_line}\n"
        f"Залишилось: <b>{u['remaining']}</b>\n"
        f"Статус: {status}\n\n"
        f"<i>Ліміт оновлюється щодня опівночі.</i>",
        parse_mode="HTML",
    )


async def _process_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    question: str,
    status_msg: Message | None = None,
    profile_override: dict | None = None,
) -> None:
    """Надсилає запит до RAG API і повертає відповідь.

    status_msg — вже існуюче повідомлення (редагуємо); якщо None — надсилаємо нове.
    profile_override — профіль для цього конкретного запиту (напр., з /checklist).
    """
    user_id = update.effective_user.id

    # ── Перевірка денного ліміту ──────────────────────────────────────────────
    allowed, count, effective = db.check_and_increment(user_id)
    if not allowed:
        u = db.get_usage(user_id)
        msg_target = update.message or (update.callback_query.message if update.callback_query else None)
        if msg_target:
            await msg_target.reply_text(
                f"🔴 <b>Денний ліміт вичерпано</b>\n\n"
                f"Ви використали всі <b>{effective}</b> запитів на сьогодні.\n\n"
                f"⏰ Ліміт оновиться завтра о <b>00:00</b>.\n\n"
                f"/usage — переглянути статистику",
                parse_mode="HTML",
            )
        elif status_msg:
            await status_msg.edit_text(
                f"🔴 Денний ліміт вичерпано ({effective} запитів). "
                f"Оновиться завтра о 00:00.\n/usage — деталі",
                parse_mode="HTML",
            )
        return

    profile = profile_override if profile_override is not None else _get_profile(user_id)
    history = _chat_history.get(user_id, [])

    logger.info(
        "[user=%d] запит: %s | %s/%s | history=%d",
        user_id, question[:80],
        profile.get("legal_status"), profile.get("region"),
        len(history),
    )

    # Визначаємо повідомлення-заглушку
    if status_msg is None:
        # reply_text доступний тільки якщо є update.message
        if update.message:
            thinking_msg = await update.message.reply_text(
                "⏳ Шукаю відповідь у правових документах…"
            )
        else:
            # callback без message — нічого не робимо
            return
    else:
        thinking_msg = status_msg

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{API_BASE_URL}/query",
                json={
                    "question": question,
                    "profile": profile,
                    "chat_history": history,
                },
            )
        resp.raise_for_status()
        data = resp.json()

        answer = data.get("answer", "Відповідь не отримана.")
        sources = data.get("sources", [])

        logger.info(
            "[user=%d] відповідь: %d симв. | джерела: %s",
            user_id, len(answer), sources,
        )

        _user_last_sources[user_id] = sources

        # Оновлюємо history (не override-профілі — лише основний)
        if profile_override is None:
            history.append({"role": "user",      "content": question})
            history.append({"role": "assistant",  "content": answer})
            _chat_history[user_id] = history[-_MAX_HISTORY:]

        # Скорочуємо plain text ДО конвертації у HTML — щоб не розрізати теги посередині
        if len(answer) > 3500:
            answer = answer[:3500] + "…"
        reply = _to_html(answer)
        if sources:
            src_names = [Path(s).stem for s in sources]  # тільки ім'я файлу
            src = ", ".join(f"<code>{escape(n)}</code>" for n in src_names)
            reply += f"\n\n📚 <i>Джерела: {src}</i>"

        reply += (
            "\n\n<i>⚠️ Відповідь згенерована штучним інтелектом на основі правових документів. "
            "Перевіряйте актуальність інформації в офіційних джерелах або у кваліфікованого юриста.</i>"
        )

        # Inline-кнопки швидких дій (не для override-профілів типу /checklist)
        markup = _answer_keyboard() if profile_override is None else None

        await thinking_msg.edit_text(reply, parse_mode="HTML", reply_markup=markup)

    except httpx.HTTPError as exc:
        logger.error("[user=%d] HTTP помилка: %s", user_id, exc)
        await thinking_msg.edit_text(
            "❌ Не вдалося зв'язатися з сервером. Спробуйте пізніше."
        )
    except Exception as exc:
        logger.error("[user=%d] помилка: %s", user_id, exc, exc_info=True)
        await thinking_msg.edit_text(
            "❌ Помилка при обробці запиту. Спробуйте ще раз."
        )


# ─── Action callbacks (post-answer inline кнопки) ────────────────────────────

async def action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробляє натискання на inline-кнопки після відповіді."""
    query = update.callback_query
    action = query.data  # "action_sources" | "action_checklist" | "action_clarify"
    user_id = update.effective_user.id

    if action == "action_sources":
        sources = _user_last_sources.get(user_id)
        if not sources:
            # answer() з show_alert — єдиний виклик для цієї гілки
            await query.answer("Немає збережених джерел.", show_alert=True)
            return
        await query.answer()  # підтверджуємо натискання без alert
        src_list = "\n".join(f"• <code>{escape(Path(s).stem)}</code>" for s in sources)
        await query.message.reply_text(
            f"📚 <b>Джерела останньої відповіді:</b>\n\n{src_list}",
            parse_mode="HTML",
        )

    elif action == "action_checklist":
        await query.answer()
        await query.message.reply_text(
            "📋 <b>Генератор чеклистів</b>\n\nОберіть процедуру:",
            reply_markup=_checklist_keyboard(),
            parse_mode="HTML",
        )

    elif action == "action_clarify":
        await query.answer()
        profile = _get_profile(user_id)
        lang = profile.get("language", "uk")
        prompts = {
            "uk": "Будь ласка, уточніть або розширте попередню відповідь. "
                  "Яка саме частина потребує пояснення?",
            "de": "Bitte präzisieren oder erweitern Sie die vorherige Antwort. "
                  "Welcher Teil benötigt weitere Erklärung?",
            "en": "Please clarify or expand on the previous answer. "
                  "Which part needs more explanation?",
        }
        await query.message.reply_text(prompts.get(lang, prompts["uk"]))


# ─── /profile (ConversationHandler) ──────────────────────────────────────────

async def cmd_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    profile = _get_profile(update.effective_user.id)
    status_labels = {
        "temporary_protection": "Тимчасовий захист (§24)",
        "residence_permit": "Дозвіл на проживання",
        "asylum_seeker": "Заявник на притулок",
        "unknown": "Не вказано",
    }
    current = status_labels.get(profile.get("legal_status", "unknown"), "Не вказано")
    await update.message.reply_text(
        f"📋 <b>Налаштування профілю</b> (крок 1/3)\n\n"
        f"Поточний статус: <i>{current}</i>\n\n"
        "Оберіть ваш правовий статус у Німеччині:",
        reply_markup=_status_keyboard(),
        parse_mode="HTML",
    )
    return PROFILE_STATUS


async def profile_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    status = query.data.replace("status_", "")
    context.user_data["profile_status"] = status
    await query.edit_message_text(
        f"✅ Статус обрано.\n\n📍 Крок 2/3 — Оберіть вашу федеральну землю:",
        reply_markup=_region_keyboard(),
    )
    return PROFILE_REGION


async def profile_region_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    region = query.data.replace("region_", "")
    if region == "Інший регіон":
        region = "unknown"
    context.user_data["profile_region"] = region
    await query.edit_message_text(
        f"✅ Регіон: {region}\n\n🌐 Крок 3/3 — Оберіть мову відповідей:",
        reply_markup=_language_keyboard(),
    )
    return PROFILE_LANGUAGE


async def profile_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    language = query.data.replace("lang_", "")
    user_id = update.effective_user.id

    profile = {
        "legal_status": context.user_data.get("profile_status", "unknown"),
        "region":       context.user_data.get("profile_region", "unknown"),
        "query_category": "other",
        "language":     language,
    }
    first_name = update.effective_user.first_name
    _save_profile(user_id, profile, first_name)

    status_labels = {
        "temporary_protection": "Тимчасовий захист (§24)",
        "residence_permit":     "Дозвіл на проживання",
        "asylum_seeker":        "Заявник на притулок",
        "unknown":              "Не вказано",
    }
    lang_labels = {"uk": "Українська", "de": "Deutsch", "en": "English"}

    await query.edit_message_text(
        "✅ <b>Профіль збережено!</b>\n\n"
        f"📋 Статус: {status_labels.get(profile['legal_status'], profile['legal_status'])}\n"
        f"📍 Регіон: {profile['region']}\n"
        f"🌐 Мова: {lang_labels.get(language, language)}\n\n"
        "Тепер відповіді будуть персоналізовані.\n"
        "/checklist — список документів для вашої ситуації",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def profile_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Налаштування профілю скасовано.")
    return ConversationHandler.END


# ─── Проактивні нагадування ───────────────────────────────────────────────────

# Пороги нагадувань (в днях). Надсилається по одному разу на кожен поріг.
_REMINDER_THRESHOLDS = [60, 30, 7]

_REMINDER_URGENCY = {
    60: ("ℹ️", "Нагадування"),
    30: ("⚠️", "Нагадування"),
    7:  ("🚨", "ТЕРМІНОВЕ нагадування"),
}

_REMINDER_TEXT = {
    "uk": (
        "{icon} <b>{label}</b>\n\n"
        "Термін дії вашого документа <b>{doc_type}</b> "
        "спливає через <b>{days_left} днів</b> — {valid_until}.\n\n"
        "Рекомендуємо завчасно розпочати процедуру продовження.\n\n"
        "/checklist — список потрібних документів\n"
        "/mydata — всі ваші документи"
    ),
    "de": (
        "{icon} <b>{label}</b>\n\n"
        "Ihr Dokument <b>{doc_type}</b> läuft in <b>{days_left} Tagen</b> ab — {valid_until}.\n\n"
        "Wir empfehlen, das Verlängerungsverfahren rechtzeitig einzuleiten.\n\n"
        "/checklist — benötigte Dokumente\n"
        "/mydata — alle Ihre Dokumente"
    ),
    "en": (
        "{icon} <b>{label}</b>\n\n"
        "Your document <b>{doc_type}</b> expires in <b>{days_left} days</b> — {valid_until}.\n\n"
        "We recommend starting the renewal process in advance.\n\n"
        "/checklist — required documents list\n"
        "/mydata — all your documents"
    ),
}


async def check_and_send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Щоденна задача: перевіряє документи що спливають і надсилає нагадування.

    Для кожного порогу (60/30/7 днів) надсилає по одному повідомленню.
    Повторні надсилання виключені через таблицю reminders_sent.
    """
    logger.info("[reminder] Перевірка документів що спливають…")
    total_sent = 0

    for threshold in _REMINDER_THRESHOLDS:
        docs = db.get_docs_needing_reminders(threshold)
        if not docs:
            continue

        for doc in docs:
            user_id  = doc["user_id"]
            doc_type = doc["doc_type"]
            days_left = doc["days_left"]
            valid_until = doc["valid_until"]
            language = doc.get("language", "uk")

            icon, label = _REMINDER_URGENCY[threshold]
            template = _REMINDER_TEXT.get(language, _REMINDER_TEXT["uk"])
            text = template.format(
                icon=icon,
                label=label,
                doc_type=escape(doc_type),
                days_left=days_left,
                valid_until=valid_until,
            )

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="HTML",
                )
                db.mark_reminder_sent(user_id, doc_type, threshold)
                logger.info(
                    "[reminder][user=%d] відправлено: %s, поріг %d дн.",
                    user_id, doc_type, threshold,
                )
                total_sent += 1
            except Exception as exc:
                logger.error(
                    "[reminder][user=%d] помилка надсилання: %s", user_id, exc
                )

    logger.info("[reminder] Готово. Відправлено %d нагадувань.", total_sent)


# ─── Запуск ───────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise EnvironmentError(
            "TELEGRAM_BOT_TOKEN не встановлено. Додайте у .env файл."
        )

    db.init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    profile_conv = ConversationHandler(
        entry_points=[CommandHandler("profile", cmd_profile_start)],
        states={
            PROFILE_STATUS: [CallbackQueryHandler(profile_status_callback, pattern="^status_")],
            PROFILE_REGION: [CallbackQueryHandler(profile_region_callback, pattern="^region_")],
            PROFILE_LANGUAGE: [CallbackQueryHandler(profile_language_callback, pattern="^lang_")],
        },
        fallbacks=[CommandHandler("cancel", profile_cancel)],
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("ask",       cmd_ask))
    app.add_handler(CommandHandler("sources",   cmd_sources))
    app.add_handler(CommandHandler("clear",     cmd_clear))
    app.add_handler(CommandHandler("mydata",    cmd_mydata))
    app.add_handler(CommandHandler("usage",     cmd_usage))
    app.add_handler(CommandHandler("checklist", cmd_checklist))
    app.add_handler(profile_conv)

    # Callbacks — ПЕРЕД загальним text handler
    app.add_handler(CallbackQueryHandler(checklist_callback, pattern="^checklist_"))
    app.add_handler(CallbackQueryHandler(action_callback,    pattern="^action_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.PHOTO,        handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # ── Планувальник нагадувань ────────────────────────────────────────────────
    if app.job_queue is not None:
        # Щодня о 09:00 UTC
        app.job_queue.run_daily(
            check_and_send_reminders,
            time=datetime.time(hour=9, minute=0, tzinfo=datetime.timezone.utc),
            name="daily_reminder_check",
        )
        # Також через 60 секунд після старту (на випадок пропущених нагадувань)
        app.job_queue.run_once(check_and_send_reminders, when=60, name="startup_reminder_check")
        logger.info("Планувальник нагадувань зареєстровано (щодня о 09:00 UTC + старт +60с).")
    else:
        logger.warning(
            "job_queue недоступний — нагадування вимкнено. "
            "Встановіть: pip install 'python-telegram-bot[job-queue]'"
        )

    logger.info("Telegram-бот запущено.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
