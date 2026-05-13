"""Telegram-бот для системи правової підтримки."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")

# Стани ConversationHandler для /profile
PROFILE_STATUS, PROFILE_REGION, PROFILE_LANGUAGE = range(3)

# Тимчасове сховище профілів та відповідей у пам'яті бота
_user_profiles: dict[int, dict] = {}
_user_last_sources: dict[int, list[str]] = {}


# ─── Клавіатури ──────────────────────────────────────────────────────────────

def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 Тимчасовий захист (§ 24)", callback_data="status_temporary_protection")],
        [InlineKeyboardButton("📄 Дозвіл на проживання", callback_data="status_residence_permit")],
        [InlineKeyboardButton("📋 Заявка на притулок", callback_data="status_asylum_seeker")],
        [InlineKeyboardButton("❓ Не знаю / Інше", callback_data="status_unknown")],
    ])


def _region_keyboard() -> InlineKeyboardMarkup:
    regions = ["Bayern", "Berlin", "NRW", "Hamburg", "Sachsen", "Baden-Württemberg", "Hessen", "Інший регіон"]
    buttons = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    return InlineKeyboardMarkup(buttons)


def _language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_uk")],
        [InlineKeyboardButton("🇩🇪 Deutsch", callback_data="lang_de")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
    ])


# ─── Команди ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"Вітаю, {user.first_name}! 👋\n\n"
        "Я — система правової інформаційної підтримки для українських громадян у Німеччині. "
        "Я можу відповісти на запитання про:\n\n"
        "🏠 Право на проживання (Aufenthaltsrecht)\n"
        "💰 Соціальні виплати (Bürgergeld, SGB II/XII)\n"
        "💼 Трудове право та роботу\n"
        "🎓 Освіту та школу для дітей\n"
        "🏥 Медичне страхування\n\n"
        "Мої відповіді базуються на актуальних правових документах Німеччини.\n\n"
        "📌 Команди:\n"
        "/ask [запитання] — задати правове питання\n"
        "/profile — налаштувати ваш профіль\n"
        "/sources — джерела останньої відповіді\n"
        "/help — довідка\n\n"
        "⚠️ Це інформаційна підтримка, не юридична консультація."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📚 ДОВІДКА\n\n"
        "/start — почати роботу з ботом\n"
        "/ask [питання] — задати правове питання\n"
        "  Приклад: /ask Як отримати Bürgergeld?\n\n"
        "/profile — налаштувати профіль (статус, регіон, мова)\n"
        "  Налаштований профіль дозволяє отримувати більш точні відповіді.\n\n"
        "/sources — показати джерела останньої відповіді\n\n"
        "❓ Також можна просто написати запитання без команди /ask\n\n"
        "⚠️ УВАГА: Відповіді є загальноінформаційними. "
        "Для вирішення конкретної ситуації зверніться до юриста або "
        "безкоштовного міграційного консультаційного центру."
    )


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    sources = _user_last_sources.get(user_id)
    if not sources:
        await update.message.reply_text("Ще немає збережених джерел. Спочатку задайте запитання через /ask.")
        return
    sources_text = "\n".join(f"• {s}" for s in sources)
    await update.message.reply_text(f"📚 Джерела останньої відповіді:\n\n{sources_text}")


# ─── /ask ─────────────────────────────────────────────────────────────────────

async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Будь ласка, введіть запитання після команди.\n"
            "Приклад: /ask Чи маю я право на Bürgergeld?"
        )
        return

    question = " ".join(context.args)
    await _process_question(update, context, question)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробляє довільні текстові повідомлення як правові запити."""
    question = update.message.text.strip()
    if len(question) < 5:
        return
    await _process_question(update, context, question)


async def _process_question(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str) -> None:
    user_id = update.effective_user.id
    profile = _user_profiles.get(user_id, _default_profile())

    thinking_msg = await update.message.reply_text("⏳ Шукаю відповідь у правових документах...")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{API_BASE_URL}/query",
                json={"question": question, "profile": profile},
            )
        response.raise_for_status()
        data = response.json()

        answer = data.get("answer", "Відповідь не отримана.")
        sources = data.get("sources", [])
        has_comparison = data.get("has_comparison", False)

        _user_last_sources[user_id] = sources

        # Формуємо відповідь
        reply = answer
        if has_comparison:
            reply += "\n\n🇺🇦🇩🇪 (відповідь містить порівняльний контекст Україна–Німеччина)"
        if sources:
            reply += f"\n\n📚 Джерела: {', '.join(sources)}"
        reply += "\n\n/sources — щоб переглянути джерела"

        await thinking_msg.edit_text(reply)

    except httpx.HTTPError as exc:
        logger.error("Помилка HTTP при запиті до API: %s", exc)
        await thinking_msg.edit_text(
            "❌ Не вдалося зв'язатися з сервером. Спробуйте пізніше."
        )
    except Exception as exc:
        logger.error("Помилка обробки запиту: %s", exc, exc_info=True)
        await thinking_msg.edit_text(
            "❌ Виникла помилка при обробці запиту. Спробуйте ще раз."
        )


# ─── /profile (ConversationHandler) ──────────────────────────────────────────

async def cmd_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📋 Налаштування профілю (крок 1/3)\n\n"
        "Оберіть ваш правовий статус у Німеччині:",
        reply_markup=_status_keyboard(),
    )
    return PROFILE_STATUS


async def profile_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    status = query.data.replace("status_", "")
    context.user_data["profile_status"] = status

    await query.edit_message_text(
        f"✅ Статус: {status}\n\n"
        "📍 Крок 2/3 — Оберіть вашу федеральну землю:",
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
        f"✅ Регіон: {region}\n\n"
        "🌐 Крок 3/3 — Оберіть мову відповідей:",
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
        "region": context.user_data.get("profile_region", "unknown"),
        "query_category": "other",
        "language": language,
    }
    _user_profiles[user_id] = profile

    status_labels = {
        "temporary_protection": "Тимчасовий захист (§ 24)",
        "residence_permit": "Дозвіл на проживання",
        "asylum_seeker": "Заявка на притулок",
        "unknown": "Невідомо",
    }
    lang_labels = {"uk": "Українська", "de": "Deutsch", "en": "English"}

    await query.edit_message_text(
        f"✅ Профіль збережено!\n\n"
        f"📋 Статус: {status_labels.get(profile['legal_status'], profile['legal_status'])}\n"
        f"📍 Регіон: {profile['region']}\n"
        f"🌐 Мова: {lang_labels.get(language, language)}\n\n"
        "Тепер ваші відповіді будуть персоналізовані. "
        "Задайте питання через /ask або просто напишіть мені."
    )
    return ConversationHandler.END


async def profile_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Налаштування профілю скасовано.")
    return ConversationHandler.END


def _default_profile() -> dict:
    return {
        "legal_status": "unknown",
        "region": "unknown",
        "query_category": "other",
        "language": "uk",
    }


# ─── Запуск ───────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise EnvironmentError(
            "TELEGRAM_BOT_TOKEN не встановлено. Додайте його у змінні оточення або .env файл."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    profile_conv = ConversationHandler(
        entry_points=[CommandHandler("profile", cmd_profile_start)],
        states={
            PROFILE_STATUS: [CallbackQueryHandler(profile_status_callback, pattern="^status_")],
            PROFILE_REGION: [CallbackQueryHandler(profile_region_callback, pattern="^region_")],
            PROFILE_LANGUAGE: [CallbackQueryHandler(profile_language_callback, pattern="^lang_")],
        },
        fallbacks=[CommandHandler("cancel", profile_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(profile_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("Telegram-бот запущено.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
