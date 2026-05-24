"""Тести генерації відповідей: personalizer та comparator."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.models import UserProfile, LegalStatus, QueryCategory, Language
from generation.personalizer import build_prompt
from generation.comparator import add_comparison, has_relevant_comparison

class TestPersonalizer:
    def _make_profile(self, status=LegalStatus.temporary_protection, region="Bayern", lang="uk") -> UserProfile:
        return UserProfile(
            legal_status=status,
            region=region,
            query_category=QueryCategory.social_benefits,
            language=lang,
        )

    def test_prompt_contains_legal_status(self):
        """Промпт містить інформацію про правовий статус."""
        profile = self._make_profile(status=LegalStatus.temporary_protection)
        prompt = build_prompt(profile, "Як отримати допомогу?", [])

        assert "§ 24" in prompt
        assert "тимчасов" in prompt.lower()

    def test_prompt_contains_region(self):
        """Промпт містить регіональну інформацію."""
        profile = self._make_profile(region="Berlin")
        prompt = build_prompt(profile, "Питання про Берлін", [])

        assert "Berlin" in prompt or "Берлін" in prompt

    def test_prompt_contains_disclaimer(self):
        """Промпт обов'язково містить застереження про інформаційний характер."""
        profile = self._make_profile()
        prompt = build_prompt(profile, "Запит", [])

        assert "інформаційна підтримка" in prompt.lower() or "не" in prompt.lower()

    def test_prompt_contains_retrieved_chunks(self):
        """Промпт включає текст знайдених чанків."""
        profile = self._make_profile()
        chunks = [
            {"text": "Bürgergeld beträgt 563 Euro monatlich", "source_file": "sgb_ii.txt"},
            {"text": "Krankenkasse ist Pflicht", "source_file": "krankenversicherung.txt"},
        ]
        prompt = build_prompt(profile, "Скільки Bürgergeld?", chunks)

        assert "sgb_ii.txt" in prompt
        assert "563 Euro" in prompt

    def test_prompt_language_instruction_ukrainian(self):
        """Промпт містить інструкцію відповідати українською."""
        profile = self._make_profile(lang="uk")
        prompt = build_prompt(profile, "Запит", [])

        assert "україн" in prompt.lower() or "УКРАЇНСЬКОЮ" in prompt

    def test_prompt_language_instruction_german(self):
        """Промпт містить інструкцію відповідати німецькою."""
        profile = self._make_profile(lang="de")
        prompt = build_prompt(profile, "Запит", [])

        assert "Deutsch" in prompt or "deutsch" in prompt.lower()

    def test_prompt_asylum_seeker_status(self):
        """Промпт для asylum_seeker згадує AsylbLG."""
        profile = self._make_profile(status=LegalStatus.asylum_seeker)
        prompt = build_prompt(profile, "Запит", [])

        assert "AsylbLG" in prompt or "Asylbewerber" in prompt

    def test_prompt_unknown_status_mentions_auslander(self):
        """Промпт для невідомого статусу рекомендує Ausländerbehörde."""
        profile = self._make_profile(status=LegalStatus.unknown)
        prompt = build_prompt(profile, "Запит", [])

        assert "Ausländerbehörde" in prompt

class TestComparator:
    def _ua_chunk(self, text: str, distance: float = 0.3) -> dict:
        return {
            "text": text,
            "source_file": "social_benefits_comparison.txt",
            "category": "ukrainian_context",
            "distance": distance,
        }

    def test_add_comparison_appends_ukrainian_context(self):
        """Якщо є Ukrainian-чанки — до промпту додається блок порівняння."""
        base_prompt = "Системний промпт..."
        ua_chunks = [self._ua_chunk("В Україні діє допомога малозабезпеченим сім'ям")]

        result = add_comparison(base_prompt, ua_chunks)

        assert "Україн" in result or "порівняльний" in result.lower()
        assert len(result) > len(base_prompt)

    def test_add_comparison_no_chunks_returns_unchanged(self):
        """Якщо Ukrainian-чанків немає — промпт не змінюється."""
        base_prompt = "Системний промпт без змін"
        result = add_comparison(base_prompt, [])

        assert result == base_prompt

    def test_has_relevant_comparison_with_close_chunks(self):
        """Чанки з малою відстанню вважаються релевантними."""
        chunks = [self._ua_chunk("Порівняння", distance=0.2)]
        assert has_relevant_comparison(chunks) is True

    def test_has_relevant_comparison_with_distant_chunks(self):
        """Чанки з великою відстанню не вважаються релевантними."""
        chunks = [self._ua_chunk("Нерелевантний текст", distance=0.9)]
        assert has_relevant_comparison(chunks) is False

    def test_has_relevant_comparison_empty_list(self):
        """Порожній список — не релевантно."""
        assert has_relevant_comparison([]) is False

    def test_comparison_includes_source_filename(self):
        """Блок порівняння містить назву файлу джерела."""
        base_prompt = "Промпт"
        ua_chunks = [self._ua_chunk("Текст порівняння")]
        result = add_comparison(base_prompt, ua_chunks)

        assert "social_benefits_comparison.txt" in result

class TestGenerator:
    def test_generate_calls_openai_and_returns_result(self):
        """Повний RAG-пайплайн повертає GenerationResult з mock OpenAI."""
        profile = UserProfile(
            legal_status=LegalStatus.temporary_protection,
            region="Berlin",
            query_category=QueryCategory.social_benefits,
            language=Language.uk,
        )

        mock_german_chunks = [
            {"text": "Bürgergeld 563 Euro", "source_file": "sgb_ii.txt", "category": "german_law", "distance": 0.1}
        ]
        mock_ua_chunks = [
            {"text": "Порівняння соцвиплат UA vs DE", "source_file": "social_benefits_comparison.txt",
             "category": "ukrainian_context", "distance": 0.25}
        ]

        mock_openai_response = MagicMock()
        mock_openai_response.choices[0].message.content = "Відповідь про Bürgergeld для Берліна."

        with (
            patch("generation.generator.retrieve_parallel_enhanced", return_value=(mock_german_chunks, mock_ua_chunks)),
            patch("generation.generator._get_openai_client") as mock_client_factory,
        ):
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_openai_response
            mock_client_factory.return_value = mock_client

            from generation.generator import generate

            result = generate("Скільки Bürgergeld у Берліні?", profile)

        assert result.answer == "Відповідь про Bürgergeld для Берліна."
        assert "sgb_ii.txt" in result.sources
        assert result.has_comparison is True

    def test_generate_no_ua_chunks_no_comparison(self):
        """Без Ukrainian-чанків has_comparison = False."""
        profile = UserProfile()

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Проста відповідь."

        with (
            patch("generation.generator.retrieve_parallel_enhanced", return_value=([], [])),
            patch("generation.generator._get_openai_client") as mock_client_factory,
        ):
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_client_factory.return_value = mock_client

            from generation.generator import generate

            result = generate("Загальне питання", profile)

        assert result.has_comparison is False
