"""Тести helper-функцій Telegram-бота (без реального з'єднання з Telegram)."""
from __future__ import annotations

import os

import pytest

# Бот потребує токен при імпорті через dotenv; встановлюємо заглушку
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy:token")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")

from bot.telegram_bot import (
    _to_html,
    _is_greeting,
    _detect_status,
    _default_profile,
    _CHECKLIST_PROCEDURES,
    _REMINDER_THRESHOLDS,
    _REMINDER_TEXT,
    _MAX_HISTORY,
)

class TestToHtml:
    def test_bold_double_asterisk(self):
        assert _to_html("**жирний**") == "<b>жирний</b>"

    def test_bold_double_underscore(self):
        assert _to_html("__жирний__") == "<b>жирний</b>"

    def test_italic_single_asterisk(self):
        result = _to_html("*курсив*")
        assert "<i>курсив</i>" in result

    def test_code_backtick(self):
        assert _to_html("`§ 24`") == "<code>§ 24</code>"

    def test_heading_hash(self):
        result = _to_html("### Заголовок")
        assert "<b>Заголовок</b>" in result

    def test_heading_single_hash(self):
        result = _to_html("# Розділ")
        assert "<b>Розділ</b>" in result

    def test_html_entities_escaped(self):
        """< > & у тексті мають бути екрановані."""
        result = _to_html("a < b & c > d")
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&amp;" in result

    def test_mixed_formatting(self):
        """Комбінація форматування."""
        result = _to_html("**bold** та `code`")
        assert "<b>bold</b>" in result
        assert "<code>code</code>" in result

    def test_no_markdown_unchanged_text(self):
        """Звичайний текст без markdown повертається без змін."""
        result = _to_html("Звичайний текст без розмітки")
        assert "Звичайний текст без розмітки" in result

    def test_multiline_bold(self):
        """**bold** на кількох рядках."""
        result = _to_html("**рядок 1\nрядок 2**")
        assert "<b>рядок 1\nрядок 2</b>" in result

    def test_paragraph_stays_intact(self):
        """Абзаци зберігаються."""
        text = "Перший абзац.\n\nДругий абзац."
        result = _to_html(text)
        assert "Перший абзац." in result
        assert "Другий абзац." in result


class TestIsGreeting:
    def test_single_word_hi(self):
        assert _is_greeting("Привіт") is True

    def test_single_word_hello(self):
        assert _is_greeting("hello") is True

    def test_hallo(self):
        assert _is_greeting("hallo") is True

    def test_greeting_with_exclamation(self):
        assert _is_greeting("Привіт!") is True

    def test_dobry_den(self):
        assert _is_greeting("добрий день") is True

    def test_long_message_not_greeting(self):
        """Довге повідомлення з привітанням — не привітання."""
        assert _is_greeting("Привіт, чи можу я отримати Bürgergeld якщо маю §24?") is False

    def test_legal_question_not_greeting(self):
        assert _is_greeting("Як отримати медичну страховку?") is False

    def test_empty_not_greeting(self):
        assert _is_greeting("") is False

    def test_vітання(self):
        assert _is_greeting("Вітання") is True

class TestDetectStatus:
    def test_detects_temporary_protection_paragraph(self):
        assert _detect_status("Я маю §24") == "temporary_protection"

    def test_detects_temporary_protection_keyword(self):
        assert _detect_status("У мене тимчасовий захист") == "temporary_protection"

    def test_detects_asylum_seeker(self):
        assert _detect_status("Я подав заяву на притулок") == "asylum_seeker"

    def test_detects_asyl_german(self):
        assert _detect_status("Ich habe asyl beantragt") == "asylum_seeker"

    def test_detects_residence_permit(self):
        assert _detect_status("У мене aufenthaltserlaubnis") == "residence_permit"

    def test_detects_residence_permit_ukrainian(self):
        assert _detect_status("Маю дозвіл на проживання") == "residence_permit"

    def test_returns_none_for_unrelated(self):
        assert _detect_status("Де знайти Jobcenter?") is None

    def test_returns_none_for_empty(self):
        assert _detect_status("") is None

    def test_case_insensitive(self):
        """Пошук без урахування регістру."""
        assert _detect_status("У мене AUFENTHALTSERLAUBNIS") == "residence_permit"

class TestDefaultProfile:
    def test_has_required_keys(self):
        p = _default_profile()
        assert "legal_status" in p
        assert "region" in p
        assert "query_category" in p
        assert "language" in p

    def test_defaults_to_unknown_status(self):
        assert _default_profile()["legal_status"] == "unknown"

    def test_defaults_to_ukrainian_language(self):
        assert _default_profile()["language"] == "uk"

    def test_returns_independent_copies(self):
        """Кожен виклик повертає новий словник."""
        p1 = _default_profile()
        p2 = _default_profile()
        p1["region"] = "Berlin"
        assert p2["region"] == "unknown"

class TestReminderConstants:
    def test_thresholds_sorted_descending(self):
        """Пороги йдуть від найбільшого до найменшого (60 → 30 → 7)."""
        assert _REMINDER_THRESHOLDS == sorted(_REMINDER_THRESHOLDS, reverse=True)

    def test_all_languages_present(self):
        """Шаблони нагадувань є для uk, de, en."""
        assert "uk" in _REMINDER_TEXT
        assert "de" in _REMINDER_TEXT
        assert "en" in _REMINDER_TEXT

    def test_template_has_placeholders(self):
        """Кожен шаблон містить необхідні плейсхолдери."""
        required = {"{icon}", "{label}", "{doc_type}", "{days_left}", "{valid_until}"}
        for lang, template in _REMINDER_TEXT.items():
            for placeholder in required:
                assert placeholder in template, f"Missing {placeholder} in {lang} template"

    def test_template_formatting_works(self):
        """Шаблон форматується без помилок."""
        tmpl = _REMINDER_TEXT["uk"]
        result = tmpl.format(
            icon="⚠️",
            label="Нагадування",
            doc_type="Test Bescheid",
            days_left=25,
            valid_until="2026-09-01",
        )
        assert "Test Bescheid" in result
        assert "25" in result

class TestChecklistProcedures:
    def test_procedures_not_empty(self):
        assert len(_CHECKLIST_PROCEDURES) >= 5

    def test_burgergeld_present(self):
        assert "burgergeld" in _CHECKLIST_PROCEDURES

    def test_aufenthalt_present(self):
        assert "aufenthalt" in _CHECKLIST_PROCEDURES

    def test_all_values_non_empty(self):
        for key, value in _CHECKLIST_PROCEDURES.items():
            assert value, f"Empty procedure description for key: {key}"

class TestBotConstants:
    def test_max_history_even(self):
        """_MAX_HISTORY має бути парним (пари user+assistant)."""
        assert _MAX_HISTORY % 2 == 0

    def test_max_history_reasonable(self):
        assert 2 <= _MAX_HISTORY <= 20
