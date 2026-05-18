"""RAG-пайплайн: retrieval → personalize → generate (sync + streaming)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncGenerator

import openai
from openai import AsyncOpenAI, OpenAI
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from api.models import UserProfile
from search.retriever import retrieve_parallel
from generation.personalizer import build_prompt
from generation.comparator import add_comparison, has_relevant_comparison

logger = logging.getLogger(__name__)

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Ключ читається лениво — при виклику клієнта, а не при імпорті модуля.
# Це дозволяє load_dotenv() виконатися до того, як ключ буде прочитаний.
def _get_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "OPENAI_API_KEY не встановлено. Додайте ключ у змінні оточення або .env файл."
        )
    return key


@dataclass
class GenerationResult:
    answer: str
    sources: list[str]
    has_comparison: bool


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=_get_api_key())


def _get_async_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=_get_api_key())


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    # Не повторюємо при клієнтських помилках (4xx) — вони не минуть самі по собі
    retry=retry_if_not_exception_type((
        openai.BadRequestError,
        openai.AuthenticationError,
        openai.PermissionDeniedError,
        openai.NotFoundError,
    )),
)
def _call_openai(
    client: OpenAI,
    prompt: str,
    query: str,
    chat_history: list[dict] | None = None,
) -> str:
    """Викликає OpenAI API з повторними спробами при помилках.

    chat_history — список {"role": "user"|"assistant", "content": str}
    вставляється між системним промптом і поточним запитом.
    """
    messages: list[dict] = [{"role": "system", "content": prompt}]
    if chat_history:
        messages.extend(chat_history)
    messages.append({"role": "user", "content": query})

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.3,
        max_tokens=1500,
    )
    return response.choices[0].message.content or ""


def generate(
    query: str,
    profile: UserProfile,
    top_k_german: int = 5,
    top_k_ukrainian: int = 2,
    chat_history: list[dict] | None = None,
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
        chat_history: попередні повідомлення діалогу [{"role":..., "content":...}]

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
    answer = _call_openai(client, prompt, query, chat_history=chat_history)

    sources = list({c["source_file"] for c in german_chunks if c.get("source_file")})

    return GenerationResult(
        answer=answer,
        sources=sources,
        has_comparison=use_comparison,
    )


async def generate_stream(
    query: str,
    profile: UserProfile,
    top_k_german: int = 5,
    top_k_ukrainian: int = 2,
    chat_history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """Streaming RAG-пайплайн — повертає async-генератор SSE-рядків.

    Кожен yield — готовий SSE-рядок виду ``data: <json>\\n\\n``.
    Останній chunk: ``{"done": true, "sources": [...], "has_comparison": bool}``
    """
    logger.info("Streaming генерація: '%s'", query[:80])

    # 1. Retrieval (sync) — виконуємо в executor, щоб не блокувати event loop
    loop = asyncio.get_running_loop()
    german_chunks, ukrainian_chunks = await loop.run_in_executor(
        None,
        lambda: retrieve_parallel(
            query,
            top_k_german=top_k_german,
            top_k_ukrainian=top_k_ukrainian,
        ),
    )

    # 2. Prompt
    prompt = build_prompt(profile, query, german_chunks)
    use_comparison = has_relevant_comparison(ukrainian_chunks)
    if use_comparison:
        prompt = add_comparison(prompt, ukrainian_chunks)

    # 3. Повідомлення для OpenAI
    messages: list[dict] = [{"role": "system", "content": prompt}]
    if chat_history:
        messages.extend(chat_history)
    messages.append({"role": "user", "content": query})

    sources = list({c["source_file"] for c in german_chunks if c.get("source_file")})

    # 4. Streaming generation
    client = _get_async_openai_client()
    stream = await client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.3,
        max_tokens=1500,
        stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield "data: " + json.dumps({"token": delta}, ensure_ascii=False) + "\n\n"

    # Фінальний chunk — метадані
    yield "data: " + json.dumps(
        {"done": True, "sources": sources, "has_comparison": use_comparison},
        ensure_ascii=False,
    ) + "\n\n"
