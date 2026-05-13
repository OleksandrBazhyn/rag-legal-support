"""RAG-пайплайн: retrieval → personalize → generate."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from api.models import UserProfile
from search.retriever import retrieve_parallel
from generation.personalizer import build_prompt
from generation.comparator import add_comparison, has_relevant_comparison

logger = logging.getLogger(__name__)

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


@dataclass
class GenerationResult:
    answer: str
    sources: list[str]
    has_comparison: bool


def _get_openai_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise EnvironmentError(
            "OPENAI_API_KEY не встановлено. Додайте ключ у змінні оточення або .env файл."
        )
    return OpenAI(api_key=OPENAI_API_KEY)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _call_openai(client: OpenAI, prompt: str, query: str) -> str:
    """Викликає OpenAI API з повторними спробами при помилках."""
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": query},
        ],
        temperature=0.3,
        max_tokens=1500,
    )
    return response.choices[0].message.content or ""


def generate(
    query: str,
    profile: UserProfile,
    top_k_german: int = 5,
    top_k_ukrainian: int = 2,
) -> GenerationResult:
    """Повний RAG-пайплайн для генерації персоналізованої відповіді.

    Етапи:
    1. Семантичний пошук у german_law та ukrainian_context
    2. Побудова персоналізованого промпту
    3. Додавання порівняльного контексту (якщо релевантний)
    4. Генерація відповіді через OpenAI

    Args:
        query: запит користувача
        profile: профіль користувача
        top_k_german: кількість чанків з german_law
        top_k_ukrainian: кількість чанків з ukrainian_context

    Returns:
        GenerationResult з відповіддю, джерелами та прапорцем порівняння
    """
    logger.info("Генерація відповіді на запит: '%s'", query[:80])

    # 1. Retrieval
    german_chunks, ukrainian_chunks = retrieve_parallel(
        query,
        top_k_german=top_k_german,
        top_k_ukrainian=top_k_ukrainian,
    )
    logger.info(
        "Знайдено: %d чанків german_law, %d чанків ukrainian_context",
        len(german_chunks), len(ukrainian_chunks),
    )

    # 2. Персоналізований промпт
    prompt = build_prompt(profile, query, german_chunks)

    # 3. Порівняльний контекст
    use_comparison = has_relevant_comparison(ukrainian_chunks)
    if use_comparison:
        prompt = add_comparison(prompt, ukrainian_chunks)
        logger.info("Додано блок порівняльного контексту.")

    # 4. Генерація
    client = _get_openai_client()
    answer = _call_openai(client, prompt, query)

    sources = list({c["source_file"] for c in german_chunks if c.get("source_file")})

    return GenerationResult(
        answer=answer,
        sources=sources,
        has_comparison=use_comparison,
    )
